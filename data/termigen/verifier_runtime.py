"""Prepare clean Termigen verifiers from measured image dependencies.

No task setup runtime is introduced. Task executables (including R/Rscript) keep
their original resolution. Package-using verifiers require their own pristine
image inventory; submitted modules remain in the task filesystem.
"""
import ast
import json
from data.utils.verifier_runtime import preparation_command, runtime_reference, runtime_spec

SCRIPT = b'''#!/bin/bash

# Run tests (test dependencies already installed in Dockerfile)
pytest /tests/test_outputs.py -rA

_EXIT_CODE=$?
if [ $_EXIT_CODE -eq 0 ]; then
    echo 1 > /logs/verifier/reward.txt
else
    echo 0 > /logs/verifier/reward.txt
fi
exit $_EXIT_CODE
'''
PACKAGES = ('pytest', 'iniconfig', 'packaging', 'pluggy', 'Pygments')
# Every published Termigen image is built FROM this base and pins pytest==8.4.1. Tasks
# whose own image was not probed may reuse the runtime planned from a probed sibling image
# of this base (same distro Python 3.12.3), only for stdlib-only verifiers (plan rules).
SHARED_BASE = 'ghcr.io/laude-institute/t-bench/ubuntu-24-04:20250624'


def import_role(files):
    import hashlib
    from pathlib import Path
    digest = hashlib.sha256(files.get('tests/test_outputs.py', b'')).hexdigest()
    records = json.loads(Path(__file__).with_name('import_roles.json').read_text())
    return next((record for record in records.values() if record['test_sha256'] == digest), {})


def installed_subject_paths(files, image):
    """Grade explicitly reviewed packages the agent must install or modify.

    Bind the exception to the inspected original tests. Only this package may
    resolve from the original interpreter's site directories; those directories
    never enter the verifier's sys.path and their .pth files are not executed.
    """
    modules = import_role(files).get('installed', [])
    if not modules:
        return {}
    paths = [path for path in image['path'] if path.startswith('/')
             and path.endswith(('/site-packages', '/dist-packages'))]
    if not paths:
        raise ValueError('Termigen installed subject paths require review')
    return {module: paths for module in modules}


def subject_import_hook(paths):
    return ("# Preserve explicitly reviewed agent-installed subject packages.\n"
            "import sys\nfrom importlib.machinery import PathFinder\n"
            "SUBJECT_PATHS = " + repr(paths) + "\n"
            "class SubjectPackages:\n"
            "    @staticmethod\n"
            "    def find_spec(fullname, path=None, target=None):\n"
            "        if path is None and fullname in SUBJECT_PATHS:\n"
            "            return PathFinder.find_spec(fullname, SUBJECT_PATHS[fullname])\n"
            "        return None\n"
            "sys.meta_path.insert(0, SubjectPackages)\n").encode()


def dependency_requirements(files, image):
    """Lock verifier imports to versions measured in the pristine task image.

    Submitted modules stay in their task paths. Dynamic module loading needs
    the image's installed Python packages as well, since the submitted module
    can import dependencies not mentioned in the tests themselves.
    """
    from pathlib import PurePosixPath
    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name
    distributions = {canonicalize_name(name): dict(value, name=name)
                     for name, value in image['distributions'].items()}
    imports, local, dynamic = set(), set(), False
    syntax_inputs = []
    for name, body in files.items():
        if not name.endswith('.py'):
            continue
        local.add(PurePosixPath(name).stem)
        if PurePosixPath(name).name == '__init__.py':
            local.add(PurePosixPath(name).parent.name)
        if not name.startswith('tests/'):
            continue
        try:
            tree = ast.parse(body)
        except SyntaxError:
            if name == 'tests/test_outputs.py':
                raise
            # Additional .py files may be intentionally invalid fixtures. They
            # are unchanged; include the image packages instead of guessing.
            syntax_inputs.append(name)
            dynamic = True
            continue
        if any(nested_pytest_argv(tree)):
            dynamic = True
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name.split('.')[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module.split('.')[0])
            elif isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.endswith('.py'):
                local.add(PurePosixPath(node.value).stem)
            elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                  and node.func.attr in {'spec_from_file_location', 'import_module', 'module_from_spec'}):
                dynamic = True
            elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                  and node.func.id in {'__import__', 'exec', 'eval'}):
                dynamic = True
    role = import_role(files)
    imports.update(role.get('grader_imports', []))
    local.update(role.get('submitted', []))
    roots = {canonicalize_name(name) for name in PACKAGES}
    submitted = []
    absent_imports = []
    for module in sorted(imports - set(image['stdlib']) - {'pytest'}):
        owners = image['modules'].get(module, [])
        if owners:
            roots.update(canonicalize_name(name) for name in owners)
        elif module in local:
            submitted.append(module)
            dynamic = True
        elif module in role.get('absent', []):
            absent_imports.append(module)
        else:
            raise ValueError('Termigen imported module requires review: ' + module)
    # Optional dependencies may be selected by data (pandas parquet engines,
    # for example) rather than a literal import in the test. Preserve the
    # image's installed pip set for these package-using verifiers too.
    if dynamic or imports - set(image['stdlib']) - {'pytest'}:
        roots.update(name for name, value in distributions.items() if value.get('installer') == 'pip')
    pending = list(roots)
    selected, absent = set(), set()
    environment = {'python_version': '.'.join(image['version'].split('.')[:2]),
                   'python_full_version': image['version'], 'sys_platform': 'linux',
                   'platform_system': 'Linux', 'platform_machine': 'x86_64', 'extra': ''}
    while pending:
        name = pending.pop()
        if name in selected:
            continue
        if name not in distributions:
            raise ValueError('Termigen installed distribution requires review: ' + name)
        selected.add(name)
        for text in distributions[name].get('requires', []):
            requirement = Requirement(text)
            if requirement.marker and not requirement.marker.evaluate(environment):
                continue
            dependency = canonicalize_name(requirement.name)
            if dependency in distributions:
                pending.append(dependency)
            else:
                # Preserve the originally installed set; do not invent missing
                # packages or upgrade an intentionally inconsistent image.
                absent.add(text)
    return {'requirements': sorted(distributions[name]['name'] + '==' + distributions[name]['version']
                                   for name in selected),
            'submitted_modules': submitted, 'dynamic_imports': dynamic,
            'original_missing_imports': absent_imports,
            'unparsed_test_inputs': syntax_inputs,
            'original_missing_requirements': sorted(absent)}


def nested_pytest_argv(tree):
    """Find direct Python/pytest child graders, excluding submitted programs."""
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name) and node.func.value.id == 'subprocess'
                and node.func.attr in {'run', 'Popen', 'call', 'check_call', 'check_output'}
                and node.args and isinstance(node.args[0], (ast.List, ast.Tuple))):
            continue
        argv = node.args[0].elts
        values = [item.value if isinstance(item, ast.Constant) else None for item in argv]
        if values and values[0] == 'pytest':
            yield argv, 1
        elif (len(argv) >= 3 and values[1:3] == ['-m', 'pytest']
              and (values[0] in ('python', 'python3') or
                   (isinstance(argv[0], ast.Attribute) and isinstance(argv[0].value, ast.Name)
                    and argv[0].value.id == 'sys' and argv[0].attr == 'executable'))):
            yield argv, 3


def task_interpreter_calls(source, executable, *, clean_call_lines=()):
    """Keep subprocesses testing submitted code on the original task Python.

    Moving pytest must not silently move the programs it launches. Only direct
    subprocess argv lists qualify; other uses of sys.executable need review.
    Byte offsets preserve comments, formatting and every assertion.
    """
    tree = ast.parse(source)
    occurrences = [node for node in ast.walk(tree) if isinstance(node, ast.Attribute)
                   and isinstance(node.value, ast.Name) and node.value.id == 'sys'
                   and node.attr == 'executable']
    allowed = set()
    clean = set()
    reviewed_lines = set(clean_call_lines)
    matched_lines = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name) and node.func.value.id == 'subprocess'
                and node.func.attr in {'run', 'Popen', 'call', 'check_call', 'check_output'}
                and node.args and isinstance(node.args[0], (ast.List, ast.Tuple))
                and len(node.args[0].elts) >= 2):
            argv = node.args[0].elts
            allowed.add(argv[0])
            values = [item.value if isinstance(item, ast.Constant) else None for item in argv]
            if node.lineno in reviewed_lines:
                if argv[0] not in occurrences:
                    raise ValueError('Termigen reviewed clean call no longer uses sys.executable')
                matched_lines.add(node.lineno)
            if node.lineno in reviewed_lines or values[1:3] == ['-m', 'py_compile']:
                clean.add(argv[0])
    if matched_lines != reviewed_lines:
        raise ValueError('Termigen reviewed clean call location changed')
    if any(node not in allowed for node in occurrences):
        raise ValueError('Termigen task interpreter usage requires review')
    lines = source.splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    edits = []
    nested = list(nested_pytest_argv(tree))
    nested_heads = {argv[0] for argv, _ in nested}
    for node in occurrences:
        if node in nested_heads:
            continue
        start = offsets[node.lineno - 1] + node.col_offset
        end = offsets[node.end_lineno - 1] + node.end_col_offset
        replacement = b"sys.executable, '-I'" if node in clean else repr(executable).encode()
        edits.append((start, end, replacement))
    for argv, prefix_length in nested:
        first, last = argv[0], argv[prefix_length - 1]
        start = offsets[first.lineno - 1] + first.col_offset
        end = offsets[last.end_lineno - 1] + last.end_col_offset
        edits.append((start, end, b"__import__('sys').executable, '-I', '-m', 'pytest'"))
    for start, end, replacement in sorted(edits, reverse=True):
        source = source[:start] + replacement + source[end:]
    return source


def shared_base_eligible(files):
    import re
    docker = files.get('environment/Dockerfile', b'').decode(errors='replace')
    return (re.findall(r'^FROM\s+(\S+)', docker, re.M) == [SHARED_BASE]
            and any('pytest==8.4.1' in line.split() for line in re.findall(r'pip3? install ([^\n]+)', docker)))


def plan_from_inventory(files, image, binding, bundle, base_image):
    """Extend migration using the task's own pristine package inventory.

    Reuse only the measured Python of the same Ubuntu base and exact version.
    Package versions come from this task's image, never the sibling image.
    """
    from validation.contract import files_digest
    if files_digest(files.items()) != binding['content_sha256']:
        raise ValueError('Termigen published task binding differs')
    environments = [entry for entry in binding['environments'] if entry['path'] == 'environment']
    if (len(environments) != 1 or environments[0]['bundle'] != image['bundle']
            or environments[0]['environment_sha256'] != image['environment_sha256']
            or bundle['files'][bundle['name']]['sha256'] != image['sif_sha256']
            or image['return_code'] != 0):
        raise ValueError('Termigen image inventory identity differs')
    if not shared_base_eligible(files) or image['version'] != base_image['version']:
        raise ValueError('Termigen interpreter compatibility requires review')
    entry = image['pytest_entry']['source']
    if (not entry.startswith('#!' + image['python3'][0] + '\n')
            or 'from pytest import console_main' not in entry
            or image['executable'] != image['python3'][0]):
        raise ValueError('Termigen pytest interpreter requires review')
    if files.get('tests/test.sh') != SCRIPT or 'tests/setup.sh' in files:
        raise ValueError('Termigen verifier entrypoint requires review')
    restored = import_role(files).get('restore_grader_files', {})
    protected = {}
    for source, target in restored.items():
        if source not in files or not source.startswith('environment/') or not target.startswith('/'):
            raise ValueError('Termigen restored grader input requires review')
        name = 'tests/ot_original_' + target.rsplit('/', 1)[-1]
        if name in files or name in protected:
            raise ValueError('Termigen restored grader name conflict requires review')
        protected[name] = files[source]
    dependencies = dependency_requirements({**files, **protected}, image)
    subjects = installed_subject_paths(files, image)
    dependencies['agent_installed_subjects'] = subjects
    if 'pytest==8.4.1' not in dependencies['requirements']:
        raise ValueError('Termigen original pytest version requires review')
    updates = dict(protected)
    for name, body in files.items():
        if name.startswith('tests/') and name.endswith('.py') and name not in dependencies['unparsed_test_inputs']:
            updates[name] = task_interpreter_calls(body, image['python3'][0],
                clean_call_lines=import_role(files).get('clean_call_lines', ()) if name == 'tests/test_outputs.py' else ())
    if subjects:
        if 'tests/conftest.py' in files:
            raise ValueError('Termigen subject import hook conflict requires review')
        updates['tests/conftest.py'] = subject_import_hook(subjects)
    from pathlib import Path
    sources = json.loads(Path(__file__).with_name('dependency_sources.json').read_text())
    requirements = [sources[pin]['requirement'] if pin in sources else pin
                    for pin in dependencies['requirements']]
    spec = runtime_spec(python=image['version'], requirements=requirements, tools=['pytest'],
                        install_no_deps=True, image_python={
                            'image_sha256': base_image['sif_sha256'], 'executable': base_image['python3'][1]})
    return dict(spec=spec, content_sha256=binding['content_sha256'],
                test_source=updates['tests/test_outputs.py'], test_updates=updates,
                restore_grader_files={'/tests/ot_original_' + target.rsplit('/', 1)[-1]: target
                                      for target in restored.values()},
                dependency_review=dependencies, image_inventory_sha256=image['sif_sha256'],
                image_source=base_image.get('source_url'))


def plan(files, image, probe, binding, bundle, *, shared=False):
    """shared=True: image/probe come from a probed sibling of SHARED_BASE, not this task's image."""
    from validation.contract import files_digest
    if files_digest(files.items()) != binding['content_sha256']:
        raise ValueError('Termigen published task binding differs')
    environments = binding['environments']
    if len(environments) != 1 or environments[0]['path'] != 'environment':
        raise ValueError('Termigen image/probe identity differs')
    if shared:
        if probe['sif_sha256'] != image['sif_sha256']:
            raise ValueError('Termigen image/probe identity differs')
        if not shared_base_eligible(files):
            raise ValueError('Termigen image base/pytest pin requires review')
    elif (environments[0]['environment_sha256'] != image['environment_sha256']
            or environments[0]['bundle'] != image['bundle']
            or bundle['files'][bundle['name']]['sha256'] != image['sif_sha256']
            or probe['sif_sha256'] != image['sif_sha256']):
        raise ValueError('Termigen image/probe identity differs')
    if image.get('return_code') != 0 or probe.get('returncode') != 0:
        raise ValueError('Termigen needs successful original image/pytest probes')
    if files.get('tests/test.sh') != SCRIPT or 'tests/setup.sh' in files:
        raise ValueError('Termigen verifier entrypoint requires review')
    if {k for k in files if k.startswith('tests/') and k.endswith('.py')} != {'tests/test_outputs.py'}:
        raise ValueError('Termigen test modules require review')
    if import_role(files).get('grader_imports') or any(nested_pytest_argv(ast.parse(files['tests/test_outputs.py']))):
        raise ValueError('Termigen generated grader dependencies require image inventory review')
    test_source = task_interpreter_calls(files['tests/test_outputs.py'], image['python3'][0])
    text = test_source.decode()
    if any(t in text for t in ('sys.path','importlib','__import__','site-packages','dist-packages')):
        raise ValueError('Termigen task interpreter/import semantics require review')
    imports = set()
    for node in ast.walk(ast.parse(text)):
        if isinstance(node,ast.Import):imports.update(a.name.split('.')[0] for a in node.names)
        elif isinstance(node,ast.ImportFrom):
            if node.level or not node.module:raise ValueError('Termigen relative imports require review')
            imports.add(node.module.split('.')[0])
    unknown = imports - set(image['stdlib']) - {'pytest'}
    if unknown:raise ValueError('Termigen task package imports require review: '+','.join(sorted(unknown)))
    observed=json.loads(probe['stdout'].split('OT_RUNTIME_PROBE=',1)[1])
    if observed['python'] != image['version'] or observed['executable'] != image['python3'][0]:
        raise ValueError('Termigen pytest does not use measured task interpreter')
    # This reviewed family is pytest8.4.1 on Python>=3.11, whose installed
    # runtime dependencies are these four tools. Older Python needs extra pins.
    if tuple(map(int,observed['python'].split('.')[:2])) < (3,11):
        raise ValueError('Termigen older pytest interpreter requires review')
    pins=[name+'=='+image['distributions'][name]['version'] for name in PACKAGES]
    if 'pytest==8.4.1' not in pins or not set(pins) <= set(observed['requirements']):
        raise ValueError('Termigen grading package evidence differs')
    spec=runtime_spec(python=observed['python'],requirements=pins,tools=['pytest'],image_python={
        'image_sha256':image['sif_sha256'],'executable':image['python3'][1]})
    return dict(spec=spec,content_sha256=binding['content_sha256'],imports=sorted(imports),
                test_source=test_source, image_source=image.get('source_url'))


def apply(files,planned,catalog):
    from data.termigen.network import offline_config
    from validation.contract import files_digest
    if files_digest(files.items()) != planned['content_sha256']:
        raise ValueError('Termigen files changed after runtime planning')
    updates=runtime_reference(planned['spec'],catalog,image_source=planned.get('image_source'))
    updates['tests/setup.sh']=('#!/bin/bash\nset -e\n'+preparation_command(
                              planned['spec'],phase='verifier',image_source=planned.get('image_source'))+
                              '\n: "${OT_VERIFIER_PYTHON:?prepared verifier runtime required}"\n').encode()
    updates['tests/test.sh']=SCRIPT.replace(b'pytest /tests/test_outputs.py -rA',
        b'"${OT_VERIFIER_PYTHON:?prepared verifier runtime required}" -I -m pytest /tests/test_outputs.py -rA')
    if planned.get('restore_grader_files'):
        import shlex
        script = 'from pathlib import Path; ' + '; '.join(
            'Path(' + repr(target) + ').write_bytes(Path(' + repr(source) + ').read_bytes())'
            for source, target in planned['restore_grader_files'].items())
        command = ('"${OT_VERIFIER_PYTHON:?prepared verifier runtime required}" -I -c '
                   + shlex.quote(script) + ' || exit $?\n').encode()
        updates['tests/test.sh'] = updates['tests/test.sh'].replace(b'# Run tests', command + b'\n# Run tests', 1)
    updates['tests/test_outputs.py'] = planned['test_source']
    updates.update(planned.get('test_updates', {}))
    if 'task.toml' in files:
        updates['task.toml'] = offline_config(files['task.toml'])
    return {**files,**updates}


def migrate_published(source, output, *, revision, subset, images_reviewed, probes, cache, shared_bundle=None,
                      package_images=(), input_provenance=None):
    """Preserve every published member, adapting only the reviewed Termigen image families.

    shared_bundle: a reviewed/probed bundle of SHARED_BASE whose plan other unprobed
    SHARED_BASE tasks reuse (plan(shared=True)); None keeps per-image review only."""
    import base64
    import copy
    import io
    from pathlib import Path
    import re
    import tarfile
    from collections import Counter
    import pyarrow as pa
    import pyarrow.parquet as pq
    from data.termigen.patch import read_task
    from data.utils.patch_reporting import write_patch_report
    from harbor_patches.verifier_bundle import spec_id, verify, validate_archive
    from validation.contract import files_digest
    if not re.fullmatch('[0-9a-f]{40}', revision) or subset not in ('termigen_v1', 'termigen_v2', 'termigen_v3'):
        raise ValueError('Termigen requires immutable source revision and explicit subset')
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    target = output / 'tasks.parquet'
    if target.exists():
        raise FileExistsError(target)
    reader = pq.ParquetFile(source)
    metadata = dict(reader.schema_arrow.metadata or {})
    historical_metadata = {k.decode(): v.decode() for k, v in metadata.items()}
    images = json.loads(metadata[b'ot.images.v1'])
    provenance = input_provenance or dict(dataset=subset,
        url='https://huggingface.co/datasets/FWeindel/validated-tasks', revision=revision)
    for key in (b'ot.environment_reliable.v1', b'ot.review.v1'):
        metadata.pop(key, None)
    metadata[b'ot.runtime_migration.v1'] = json.dumps(dict(
        source_revision=provenance['revision'], image_source_revision=revision,
        source_subset=subset, validation='pending', effective_network='unverified',
        existing_scores='historical, evaluated before runtime migration',
        coverage='partial; see runtime-migration.json', historical_evidence='historical-evidence.json')).encode()
    reviewed = {item['bundle']: dict(item) for item in images_reviewed}
    for item in reviewed.values():
        if item.get('sif'):
            # These measured source images are published with Termigen v1;
            # v2/v3 may reuse their interpreter without retaining that task.
            item['source_url'] = ('https://huggingface.co/datasets/FWeindel/validated-tasks/resolve/'
                                  + revision + '/termigen_v1/cached_images/bundles-v1/'
                                  + item['bundle'] + '/' + item['sif'] + '/' + item['sif'])
    measured = {item['bundle']: item for item in probes}
    if len(reviewed) != len(images_reviewed) or len(measured) != len(probes) or set(reviewed) != set(measured):
        raise ValueError('Termigen requires unique matching image/probe bundles')
    if shared_bundle is not None and shared_bundle not in reviewed:
        raise ValueError('Termigen shared base bundle must be reviewed and probed')
    package_inventory = {item['bundle']: item for item in package_images}
    if package_inventory and shared_bundle is None:
        raise ValueError('Termigen package inventories require a measured shared Python base')
    history, records, labels, reasons, specs, catalog = [], [], {}, {}, {}, {}
    counts = Counter()
    with pq.ParquetWriter(target, reader.schema_arrow.with_metadata(metadata)) as writer:
        for batch in reader.iter_batches(batch_size=16):
            rows = batch.to_pylist()
            for row in rows:
                name = row['path']
                # Historical row labels describe the previous patch. Override
                # report labels even for unchanged members of this migration.
                labels[name] = []
                members = read_task(row['task_binary'])
                files = {member.name: data for member, data in members if data is not None}
                if len(files) != sum(data is not None for _, data in members):
                    raise ValueError(f'{name}: duplicate task files')
                before = files_digest(files.items())
                binding = images['tasks'][name]
                if before != row['content_sha256'] or before != binding['content_sha256']:
                    raise ValueError(f'{name}: published content/image binding differs')
                history.append({k: v for k, v in row.items() if k != 'task_binary'})
                environment = binding['environments']
                primary = [entry for entry in environment if entry['path'] == 'environment']
                standard_environment = len(environment) == 1 and len(primary) == 1
                package_image = package_inventory.get(primary[0]['bundle']) if len(primary) == 1 else None
                image = reviewed.get(environment[0]['bundle']) if standard_environment else None
                shared = image is None and standard_environment and shared_bundle is not None
                if shared:
                    image = reviewed[shared_bundle]
                planned = None
                if package_image is not None:
                    bundle = images['bundles'][primary[0]['bundle']]
                    try:
                        planned = plan_from_inventory(files, package_image, binding, bundle, reviewed[shared_bundle])
                    except (ValueError, SyntaxError) as error:
                        if isinstance(error, ValueError) and 'review' not in str(error):
                            raise
                        status = 'unmodified: ' + str(error)
                elif image is None:
                    status = ('unmodified: image not yet reviewed' if standard_environment
                              else 'unmodified: environment layout requires review')
                else:
                    bundle = images['bundles'][environment[0]['bundle']]
                    probe = measured[shared_bundle if shared else environment[0]['bundle']]
                    # Identity/probe failures are errors, never silent exclusions.
                    if ((not shared and (environment[0]['environment_sha256'] != image['environment_sha256']
                                         or bundle['files'][bundle['name']]['sha256'] != image['sif_sha256']))
                            or probe['sif_sha256'] != image['sif_sha256']
                            or probe.get('returncode') != 0 or image.get('return_code') != 0):
                        raise ValueError(f'{name}: runtime evidence identity differs')
                    try:
                        planned = plan(files, image, probe, binding, bundle, shared=shared)
                    except (ValueError, SyntaxError) as error:
                        if 'review' not in str(error):
                            raise
                        status = 'unmodified: ' + str(error)
                if planned is not None:
                    key = spec_id(planned['spec'])
                    if key not in catalog:
                        record = json.loads((Path(cache) / (key + '.json')).read_text())
                        runtime_reference(planned['spec'], {key: record})
                        validate_archive(verify(record, cache))
                        catalog[key] = record
                        specs[key] = planned['spec']
                    updated = apply(files, planned, catalog)
                    changed = sorted(k for k in updated if updated[k] != files.get(k))
                    expected = ['tests/ot-verifier-runtime.json', 'tests/setup.sh', 'tests/test.sh']
                    if updated.get('task.toml') != files.get('task.toml'):
                        expected.append('task.toml')
                    expected += [name for name in updated if name.startswith('tests/') and name.endswith('.py')
                                 and updated[name] != files.get(name)]
                    if changed != sorted(expected):
                        raise ValueError(f'{name}: unexpected runtime changes')
                    buffer = io.BytesIO()
                    with tarfile.open(fileobj=buffer, mode='w') as archive:
                        seen = set()
                        for original, data in members:
                            member = copy.copy(original)
                            if data is not None:
                                member.size = len(updated[member.name])
                            archive.addfile(member, io.BytesIO(updated[member.name]) if data is not None else None)
                            seen.add(member.name)
                        for added in sorted(set(updated) - seen):
                            member = tarfile.TarInfo(added)
                            member.size, member.mode = len(updated[added]), 0o644
                            archive.addfile(member, io.BytesIO(updated[added]))
                    row['task_binary'] = buffer.getvalue()
                    row['content_sha256'] = files_digest(updated.items())
                    binding['content_sha256'] = row['content_sha256']
                    labels[name] = ['prepared-verifier-runtime']
                    reasons[name] = ('Set the task offline for the initial audit. '
                                     'Use the measured original Python and pinned pytest tools in a fresh private runtime; '
                                     'preserve task initialization and assertions, and keep submitted-code subprocesses '
                                     'on the original task interpreter. Current validation pending.'
                                     + (' Runtime from a probed image of the same base (shared base family).' if shared else ''))
                    status = ('adapted (shared base family), validation pending' if shared
                              else 'adapted, validation pending')
                elif 'task.toml' in files:
                    from data.termigen.network import offline_config
                    from data.termigen.patch import write_task
                    offline = offline_config(files['task.toml'])
                    if offline != files['task.toml']:
                        files['task.toml'] = offline
                        row['task_binary'] = write_task([(member, files[member.name] if data is not None else None)
                                                        for member, data in members])
                        row['content_sha256'] = files_digest(files.items())
                        binding['content_sha256'] = row['content_sha256']
                        labels[name] = ['offline-first']
                        reasons[name] = 'Set the task offline for the initial audit; clean verifier migration remains incomplete.'
                if 'stages_passed' in row:
                    row['stages_passed'] = ''
                counts[status] += 1
                records.append(dict(path=name, status=status, old_content_sha256=before,
                                    content_sha256=row['content_sha256'],
                                    dependency_review=planned.get('dependency_review') if planned else None))
            writer.write_table(pa.Table.from_pylist(rows, schema=reader.schema_arrow))
        metadata[b'ot.images.v1'] = json.dumps(images).encode()
        migration = json.loads(metadata[b'ot.runtime_migration.v1'])
        migration['coverage'] = ('complete migration; validation pending' if all(
            key.startswith('adapted') for key in counts) else 'partial; see runtime-migration.json')
        metadata[b'ot.runtime_migration.v1'] = json.dumps(migration).encode()
        writer.add_key_value_metadata({**metadata, b'ARROW:schema': base64.b64encode(
            reader.schema_arrow.with_metadata(metadata).serialize())})
    (output / 'historical-evidence.json').write_text(json.dumps(dict(revision=revision, subset=subset,
        status='historical, not validation of migrated definitions', metadata=historical_metadata, tasks=history), indent=1)+'\n')
    report = dict(source_revision=provenance['revision'], image_source_revision=revision,
                  source_subset=subset, validation='pending', effective_network='unverified',
                  counts=dict(counts), tasks=records, runtime_ids=sorted(specs))
    (output / 'runtime-migration.json').write_text(json.dumps(report, indent=1)+'\n')
    (output / 'verifier-bundle-specs.json').write_text(json.dumps(specs, indent=1)+'\n')
    write_patch_report(Path(source), target, source=provenance, dropped={},
        change_labels=labels, change_reasons=reasons, patcher=Path(__file__).with_name('patch.py'))
    # The public patcher imports this source-specific implementation. Preserve
    # these snapshots so publication never substitutes later implementation or
    # reviewed import-role definitions.
    import hashlib
    helper = Path(__file__).read_bytes()
    helper_path = output / 'runtime.patch.py'
    with helper_path.open('xb') as stream:
        stream.write(helper)
    manifest_path = target.with_suffix('.manifest.json')
    manifest = json.loads(manifest_path.read_text())
    manifest['patcher_dependencies'] = [dict(path=helper_path.name, sha256=hashlib.sha256(helper).hexdigest())]
    roles = Path(__file__).with_name('import_roles.json').read_bytes()
    (output / 'import_roles.json').write_bytes(roles)
    manifest['patcher_dependencies'].append(dict(path='import_roles.json', sha256=hashlib.sha256(roles).hexdigest()))
    network = Path(__file__).with_name('network.py').read_bytes()
    (output / 'network.py').write_bytes(network)
    manifest['patcher_dependencies'].append(dict(path='network.py', sha256=hashlib.sha256(network).hexdigest()))
    sources = Path(__file__).with_name('dependency_sources.json').read_bytes()
    (output / 'dependency_sources.json').write_bytes(sources)
    manifest['patcher_dependencies'].append(dict(path='dependency_sources.json', sha256=hashlib.sha256(sources).hexdigest()))
    manifest_path.write_text(json.dumps(manifest, indent=2)+'\n')
    return report
