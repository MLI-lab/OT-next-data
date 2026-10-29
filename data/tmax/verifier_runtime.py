"""Prepared verifier runtimes for TMAX (.agents/skills/prepare-task-runtimes).

Upstream grades every task with the image's own interpreter (`python3 -m pytest`)
after the agent had root access to it, so the agent could replace pytest, its
plugins or the packages the tests import. Each task instead gets a read-only
prepared runtime that holds only what its test file needs:

* Python: the exact version the image's python3 runs (the verifier's version).
* Packages: pytest, every pytest plugin installed in the image and every
  distribution the test file imports, with their requirements (and the optional
  ones the image has installed), all pinned to the versions in the image.

The plan comes from the read-only image inventory (hpc/offline_prep/image_python_inventory.py),
never from task text alone. Exceptions keep grading semantics:

* task environment: a test imports code the agent writes or installs, a
  distribution installed from the task's own files (for example /app/...), or a
  package its instruction asks the agent to install or change. pytest still
  comes from the runtime; tests/conftest.py appends the image's package
  directories. pytest imports it after plugin autoload (so packages there
  cannot register pytest plugins) and before the test module;
* sys.executable means the task's interpreter (the agent's scripts run there),
  so it is replaced by the image's python3 path;
* python3 wrapper tasks start their services on the first python3 call, which
  upstream was the verifier; test.sh makes that call before grading.
"""
import ast
import json
import re
import sys

from data.utils.verifier_runtime import preparation_command, runtime_reference, runtime_spec

TEST = "tests/test_final_state.py"
TEST_SH = "tests/test.sh"
CONFTEST = "tests/conftest.py"
UPSTREAM_COMMAND = "python3 -m pytest test_final_state.py -v 2>&1 | tee /logs/verifier/test-stdout.txt"
RUNTIME = '"${OT_VERIFIER_PYTHON:?prepared verifier runtime required}" -I -m pytest'
LABEL = "prepared-verifier-runtime"
TASK_ENVIRONMENT_LABEL = "verifier-imports-task-environment"
EXECUTABLE_LABEL = "verifier-task-interpreter"
WRAPPER_START_LABEL = "verifier-starts-python3-wrapper"
SYSTEM_SITE = re.compile(r"^/usr/(local/)?lib/python3(\.\d+)?/(dist|site)-packages/?$")
# Instructions that ask for a package to be installed, pinned or changed.
CHANGE_VERBS = r"(?:install|upgrade|downgrade|uninstall|reinstall|pin|patch|vendor|fix|repair|modify|rebuild|compile)\w*"
VERSION = re.compile(r"^\d+(\.\d+)*((a|b|rc)\d+)?(\.post\d+)?(\.dev\d+)?$")

CONFTEST_SOURCE = '''"""Imports of the task environment for grading (prepared verifier runtime).

The test imports code the agent writes or installs. pytest and the packages the
runtime provides stay first on sys.path; the image's package directories are
appended here, after plugin autoload, so packages there cannot add pytest plugins.
"""
import os
import sys

for path in {paths!r}:
    if os.path.isdir(path) and path not in sys.path:
        sys.path.append(path)
'''


def normalize(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def test_imports(source):
    """Top-level names of absolute imports anywhere in the test file."""
    names = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module.split(".")[0])
    return names


def dynamic_imports(source):
    """Tests that load modules by path or edit sys.path import task code."""
    return bool(re.search(r"sys\.path\.(insert|append|extend)|spec_from_file_location|importlib\.import_module|"
                          r"__import__\(|SourceFileLoader|PYTHONPATH", source))


def stdlib(image):
    # The image's own standard library (3.9 has no stdlib_module_names; use ours there).
    return set(image.get("stdlib") or sys.stdlib_module_names) | {"__future__"}


def requirement_closure(seeds, image, optional=()):
    """Seeds plus their requirements. For the distributions in `optional` (the ones a test
    imports) this includes optional requirements the image has installed, which they may
    import lazily (pandas -> openpyxl); development extras of the rest are left out."""
    from packaging.markers import default_environment
    from packaging.requirements import InvalidRequirement, Requirement
    installed = {normalize(name): name for name in image["distributions"]}
    environment = default_environment()
    major, minor, *_ = image["version"].split(".")
    environment.update(python_version=f"{major}.{minor}", python_full_version=image["version"],
                       implementation_version=image["version"], sys_platform="linux",
                       platform_system="Linux", platform_machine="x86_64", os_name="posix")
    optional_keys = {normalize(name) for name in optional}
    selected, pending = set(), [normalize(seed) for seed in seeds]
    while pending:
        key = pending.pop()
        if key in selected or key not in installed:
            continue
        selected.add(key)
        for text in image["distributions"][installed[key]]["requires"]:
            try:
                requirement = Requirement(text)
            except InvalidRequirement:
                continue
            dependency = normalize(requirement.name)
            marker = requirement.marker
            needed = marker is None or marker.evaluate({**environment, "extra": ""})
            extra = (key in optional_keys and marker is not None and not needed
                     and "extra" in str(marker) and dependency in installed)
            if (needed or extra) and dependency in installed:
                if marker is not None and not needed:
                    # An optional requirement whose other markers do not hold on this platform.
                    extras = re.findall(r"extra\s*==\s*['\"]([^'\"]+)['\"]", str(marker))
                    if not any(marker.evaluate({**environment, "extra": extra}) for extra in extras):
                        continue
                pending.append(dependency)
    return {installed[key] for key in selected}


def mentioned_changes(instruction, names):
    """Distributions or import names the instruction asks the agent to install or change."""
    found = set()
    for name in names:
        word = re.escape(name)
        near = (rf"\b{CHANGE_VERBS}\b[^.\n]{{0,60}}(?<![\w./-]){word}(?![\w-])"
                rf"|(?<![\w./-]){word}(?![\w-])[^.\n]{{0,40}}\b{CHANGE_VERBS}\b"
                rf"|(?<![\w./-]){word}\s*(==|>=|<=|~=|!=)\s*\d")
        if re.search(near, instruction, re.I):
            found.add(name)
    return found


def plan(files, image, *, wrapper=False):
    """How this task's verifier gets its tools, or None when upstream grading is kept."""
    test = files[TEST][0].decode()
    if files[TEST_SH][0].decode().count(UPSTREAM_COMMAND) != 1:
        raise ValueError("unexpected test.sh")
    if image is None or image.get("return_code") != 0:
        raise ValueError("no image inventory for this task")
    instruction = files["instruction.md"][0].decode(errors="replace")
    distributions = image["distributions"]
    by_module = image["modules"]
    reasons = []
    local = {name for name, info in distributions.items()
             if not SYSTEM_SITE.match(info["location"].rstrip("/") + "/")}
    unpinnable = {name for name, info in distributions.items() if not VERSION.match(info["version"])}
    imports = test_imports(test) - stdlib(image) - {"pytest", "_pytest"}
    provided = {module: set(by_module[module]) & set(distributions) for module in imports if by_module.get(module)}
    unresolved = sorted(imports - set(provided))
    seeds = {"pytest"} | {name for name in distributions if normalize(name).startswith("pytest-")}
    seeds |= {name for names in provided.values() for name in names}
    if not any(normalize(name) == "pytest" for name in distributions):
        raise ValueError("image has no pytest")
    closure = requirement_closure(seeds, image, optional={name for names in provided.values() for name in names})
    changed_modules = mentioned_changes(instruction, set(provided))
    changed = {name for module in changed_modules for name in provided[module]}
    changed |= mentioned_changes(instruction, closure)
    # Ubuntu-packaged modules are built for the image's own libraries (and may carry Debian-only
    # names); they come from the task environment. pytest and its requirements must start before
    # tests/conftest.py, so they are always pinned from PyPI (pure Python).
    apt = {name for name, info in distributions.items()
           if info["installer"] != "pip" and info["location"].startswith("/usr/lib/python3/dist-packages")}
    grading = requirement_closure({"pytest"} | {name for name in seeds if normalize(name).startswith("pytest-")}, image)
    excluded = (closure & (local | unpinnable | changed | apt)) - grading
    # A runtime package needing an excluded one would make the builder fetch that one from
    # PyPI and shadow the task's copy; it comes from the task environment as well.
    excluded |= {name for name in closure - grading - excluded
                 if (requirement_closure({name}, image) - {name}) & excluded}
    if unresolved:
        reasons.append("the test imports modules the image does not install (" + ", ".join(unresolved)
                       + "), written or installed by the agent")
    if dynamic_imports(test):
        reasons.append("the test loads task code by path")
    if excluded & local:
        reasons.append("distributions installed from the task's own files (" + ", ".join(sorted(excluded & local)) + ")")
    if excluded & changed:
        reasons.append("the instruction asks the agent to install or change " + ", ".join(sorted(excluded & changed)))
    if excluded & apt - changed:
        reasons.append("Ubuntu-packaged Python modules (" + ", ".join(sorted(excluded & apt - changed)) + ")")
    if excluded & unpinnable - local - apt:
        reasons.append("image distributions without a public release version (" + ", ".join(sorted(excluded & unpinnable - local)) + ")")
    pins = sorted(f"{name}=={distributions[name]['version']}" for name in closure - excluded)
    spec = runtime_spec(python=image["version"], requirements=pins)
    paths = [path for path in image["path"] if SYSTEM_SITE.match(path.rstrip("/") + "/")] if reasons else []
    return {"spec": spec, "task_environment": bool(reasons), "reasons": reasons, "paths": paths,
            "interpreter": image["python3"][0], "executable": "sys.executable" in test,
            "wrapper": wrapper or wrapped_interpreter(image), "excluded": sorted(excluded),
            "unresolved": unresolved}


def wrapped_interpreter(image):
    """python3 is a script (for example one that starts services) instead of the interpreter."""
    return not re.search(r"/python3\.\d+$", image["python3"][1])


def replace_executable(source, interpreter):
    """sys.executable -> the task interpreter, checked node by node."""
    tree = ast.parse(source)
    spans = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Attribute) and node.attr == "executable" and isinstance(node.value, ast.Name)
                and node.value.id == "sys" and isinstance(node.ctx, ast.Load)):
            spans.append((node.lineno, node.col_offset, node.end_lineno, node.end_col_offset))
    if not spans:
        return source
    lines = source.splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    # ast offsets count UTF-8 bytes within a line.
    def position(line, column):
        return offsets[line - 1] + len(lines[line - 1].encode()[:column].decode())
    text = source
    for start_line, start_col, end_line, end_col in sorted(spans, reverse=True):
        start, end = position(start_line, start_col), position(end_line, end_col)
        if text[start:end] != "sys.executable":
            raise ValueError("unexpected sys.executable span")
        # A literal of the quote kind not used around it stays valid inside f-strings before 3.12.
        before = text[:start]
        quote = "'" if before.count('"') % 2 else '"'
        text = before + quote + interpreter + quote + text[end:]
    expected = ast.dump(tree).replace(
        "Attribute(value=Name(id='sys', ctx=Load()), attr='executable', ctx=Load())",
        f"Constant(value={interpreter!r})")
    if ast.dump(ast.parse(text)) != expected:
        raise ValueError("sys.executable rewrite changed more than the interpreter")
    return text


def apply(files, planned, catalog):
    """Write the runtime reference and the verifier changes; return change labels."""
    labels = [LABEL]
    # Read-only: grading never writes to the runtime (stated explicitly; the helper's default has changed).
    for name, content in runtime_reference(planned["spec"], catalog, mount_mode="readonly").items():
        files[name] = (content, 0o644)
    # The controller reads this trusted recipe before verifier execution. Cache
    # failures rebuild privately rather than invoking agent-modified Python/pip.
    setup, setup_mode = files.get('tests/setup.sh', (b'', 0o755))
    if b'OT_RUNTIME_RECIPE:' in setup:
        raise ValueError('task already has a runtime preparation recipe')
    prefix = '#!/bin/bash\nset -e\n' + preparation_command(planned['spec'], phase='verifier') + '\n'
    files['tests/setup.sh'] = (prefix.encode() + setup, setup_mode)
    command = RUNTIME
    if planned["task_environment"]:
        if CONFTEST in files:
            raise ValueError("task already has tests/conftest.py")
        files[CONFTEST] = (CONFTEST_SOURCE.format(paths=planned["paths"]).encode(), 0o644)
        labels.append(TASK_ENVIRONMENT_LABEL)
    script, mode = files[TEST_SH]
    replacement = command + " test_final_state.py -v 2>&1 | tee /logs/verifier/test-stdout.txt"
    if planned["wrapper"]:
        # Upstream's first python3 call (the verifier) started the services.
        replacement = "python3 -c '' >/dev/null 2>&1 || true\n" + replacement
        labels.append(WRAPPER_START_LABEL)
    files[TEST_SH] = (script.decode().replace(UPSTREAM_COMMAND, replacement).encode(), mode)
    if planned["executable"]:
        source, test_mode = files[TEST]
        files[TEST] = (replace_executable(source.decode(), planned["interpreter"]).encode(), test_mode)
        labels.append(EXECUTABLE_LABEL)
    return labels


def reason(planned):
    text = ("The tests ran with the image's python3 and pytest after the agent had root access to them. "
            "They now run with a prepared read-only runtime (Python " + planned["spec"]["python"]
            + ", pytest and the " + str(len(planned["spec"]["requirements"]) - 1)
            + " other packages they need, at the image's versions), staged after the agent.")
    if planned["task_environment"]:
        text += (" The image's package directories are added after the runtime's, because "
                 + "; ".join(planned["reasons"]) + ".")
    if planned["executable"]:
        text += (" sys.executable is replaced by " + planned["interpreter"]
                 + ", the task interpreter the tests use to run the agent's scripts.")
    if planned["wrapper"]:
        text += " test.sh first calls python3 once, which starts the task's services as the upstream verifier did."
    return text


def load_inventory(directory):
    """{environment_sha256: image record} from hpc/offline_prep/image_python_inventory.py output."""
    from pathlib import Path
    return {record["environment_sha256"]: record
            for record in (json.loads(path.read_text()) for path in sorted(Path(directory).glob("*.json")))}


def migrate_published(source, output, inventory_dir, cache, revision, subset):
    """Migrate an existing TMAX subset without rerunning its upstream repair rules."""
    import base64
    from collections import Counter
    from pathlib import Path
    import pyarrow as pa
    import pyarrow.parquet as pq
    from data.tmax.patch import read_task, runtime_catalog
    from data.utils.harbor_parquet import pack_task
    from data.utils.patch_reporting import write_patch_report
    from harbor_patches.verifier_bundle import spec_id, verify, validate_archive
    from validation.contract import files_digest
    if not re.fullmatch('[0-9a-f]{40}', revision):
        raise ValueError('Published migration requires an immutable source revision')
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    target = output / 'tasks.parquet'
    if target.exists():
        raise FileExistsError(target)
    reader = pq.ParquetFile(source)
    metadata = dict(reader.schema_arrow.metadata or {})
    historical_metadata = {k.decode(): v.decode() for k, v in metadata.items()}
    images = json.loads(metadata[b'ot.images.v1'])
    inventory = load_inventory(inventory_dir)
    metadata.pop(b'ot.environment_reliable.v1', None)
    metadata.pop(b'ot.review.v1', None)
    metadata[b'ot.runtime_migration.v1'] = json.dumps({
        'source_revision': revision, 'source_subset': subset, 'validation': 'pending',
        'effective_network': 'unverified', 'existing_scores': 'historical, evaluated before runtime migration',
        'historical_evidence': 'historical-evidence.json',
    }).encode()
    history, migrations, catalogs, specs, labels, reasons = [], [], {}, {}, {}, {}
    counts = Counter()
    with pq.ParquetWriter(target, reader.schema_arrow.with_metadata(metadata)) as writer:
        for batch in reader.iter_batches(batch_size=16):
            rows = batch.to_pylist()
            for row in rows:
                task = row['path']
                files = read_task(row['task_binary'])
                before = files_digest((name, value[0]) for name, value in files.items())
                binding = images['tasks'][task]
                if before != row['content_sha256'] or binding['content_sha256'] != before:
                    raise ValueError(f'{task}: published task/image identity differs')
                environments = binding['environments']
                if len(environments) != 1 or environments[0]['path'] != 'environment':
                    raise ValueError(f'{task}: unexpected TMAX image layout')
                environment = environments[0]
                observed = inventory[environment['environment_sha256']]
                image = images['bundles'][environment['bundle']]
                if observed['sif_sha256'] != image['files'][image['name']]['sha256']:
                    raise ValueError(f'{task}: interpreter inventory does not match the published image')
                planned = plan(files, observed)
                key = spec_id(planned['spec'])
                if key not in catalogs:
                    catalogs[key] = runtime_catalog(cache, planned['spec'])
                    validate_archive(verify(catalogs[key][key], cache))
                    specs[key] = planned['spec']
                history.append({k: v for k, v in row.items() if k != 'task_binary'})
                original = dict(files)
                labels[task] = apply(files, planned, catalogs[key])
                reasons[task] = reason(planned) + ' Setup declares a trusted private rebuild recipe. Current validation is pending.'
                changed = sorted(name for name in set(original) | set(files) if original.get(name) != files.get(name))
                if any(not name.startswith('tests/') for name in changed):
                    raise ValueError(f'{task}: migration changed task inputs')
                row['task_binary'] = pack_task(files)
                after = files_digest((name, value[0]) for name, value in files.items())
                row['content_sha256'] = after
                if 'stages_passed' in row:
                    row['stages_passed'] = ''
                binding['content_sha256'] = after
                migrations.append(dict(path=task, old_content_sha256=before, new_content_sha256=after,
                                       changed_files=changed, runtime_id=key))
                counts[key] += 1
            writer.write_table(pa.Table.from_pylist(rows, schema=reader.schema_arrow))
            if len(history) % 1024 == 0:
                print(f'Migrated {len(history)} TMAX tasks, {len(specs)} verified runtimes', flush=True)
        metadata[b'ot.images.v1'] = json.dumps(images).encode()
        writer.add_key_value_metadata({**metadata, b'ARROW:schema': base64.b64encode(
            reader.schema_arrow.with_metadata(metadata).serialize())})
    (output / 'historical-evidence.json').write_text(json.dumps(dict(
        revision=revision, subset=subset, status='historical, not validation of migrated definitions',
        metadata=historical_metadata, tasks=history), indent=1) + '\n')
    (output / 'runtime-migration.json').write_text(json.dumps(dict(source_revision=revision, source_subset=subset,
        validation='pending', effective_network='unverified', tasks=migrations, runtime_reuse=dict(counts)), indent=1) + '\n')
    (output / 'verifier-bundle-specs.json').write_text(json.dumps(specs, indent=1) + '\n')
    write_patch_report(Path(source), target,
                       source={'dataset': subset, 'url': 'https://huggingface.co/datasets/FWeindel/validated-tasks',
                               'revision': revision}, dropped={}, change_labels=labels, change_reasons=reasons,
                       patcher=Path(__file__).with_name('patch.py'))
