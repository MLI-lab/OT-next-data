"""Reproduce the pinned pymethods2test TaskTrove source with reviewed repairs.

R1 (stage 1): pin pytest in environment/Dockerfile and tests/test.sh, and drop
the `2>/dev/null` that makes check-pip-pinning.sh report the package as unpinned.
The version is the one the unpinned install resolved in the pilot stage-5 logs.

R2 (stage 4): add solution/solve.sh, which copies the existing solution/solution.py
to /app/solution.py.

Tests and reward logic are never edited.

R3: replace the ignored verifier.restart_environment=false field with the
supported environment_mode="shared" setting.

R4: repair the missing cmp_to_key import in reference 1059 and install the
missing numpy==2.2.6 dependency during verifier preparation for task 3412.

R5: bake numpy==2.2.6 into the task image for 18 further verified NumPy failures.
The pinned upstream image installs pytest only; no NumPy dependency was removed.

R6: add missing standard-library imports to 37 audited reference solutions.

R7: replace `solution.py` with `/app/solution.py` in 15 instructions, at the
positions the validation path resolver observed and applied.
"""

# Support both direct execution and python -m data.<source>.patch.
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


import argparse
import copy
import gzip
import hashlib
import io
import json
import re
import tarfile
import tomllib
from pathlib import Path

REVISION = '946884702046be1a7dcea2638186ad6b0d2ea103'
PATCH_VERSION = 'pymethods2test-numpy18-import37-paths15-v7'
REPAIRED_PYTEST_VERSION = '9.0.2'
EXPECTED_TASK_COUNT = 4990

DOCKER_LINE = 'RUN pip install --no-cache-dir pytest'
DOCKER_LINE_FIXED = 'RUN pip install --no-cache-dir pytest==9.1.1'
TEST_LINE = 'pip3 install --quiet pytest 2>/dev/null || true'
TEST_LINE_FIXED = 'pip3 install --quiet pytest==9.1.1 || true'
SOLVE_SH = b'#!/bin/bash\nset -euo pipefail\ncp /solution/solution.py /app/solution.py\n'

from data.utils.task_archive import read_members
from data.utils.patch_reporting import write_patch_report

REFERENCE_HASHES = {
    'pymethods2test-1059': '487e92ef39067b0649af5f61a1435fdcb5e6ddde4693c076977a5c169d14bde8',
    'pymethods2test-3412': 'e76b0b9ba081cda01d4eef0ccaee180c951c9ea66c433ed96401307b1e593f34',
}
REPAIR_REASONS = {
    'pymethods2test-1059': 'Added the missing functools.cmp_to_key import to the supplied reference solution; its seven unchanged tests pass locally.',
    'pymethods2test-3412': 'Added numpy==2.2.6 to tests/setup.sh because the supplied reference imports NumPy; its six unchanged tests pass locally. The shared Dockerfile is unchanged by this repair.',
}
NUMPY_REFERENCE_HASHES = json.loads((Path(__file__).parent / 'repairs' / 'numpy_repairs.json').read_text())
IMPORT_REPAIRS = json.loads((Path(__file__).parent / 'repairs' / 'import_repairs.json').read_text())
PATH_REPAIRS = json.loads((Path(__file__).parent / 'repairs' / 'path_repairs.json').read_text())
REPAIR_REASONS.update({task: 'Baked numpy==2.2.6 into the task image to repair an undeclared upstream dependency; reference, tests and reward logic unchanged.'
                       for task in NUMPY_REFERENCE_HASHES})
REPAIR_REASONS.update({task: 'Added missing standard-library imports to the supplied reference: '
                       + '; '.join(repair['imports']) + '. Function bodies, instructions, tests and reward logic unchanged.'
                       for task, repair in IMPORT_REPAIRS.items()})
for task in NUMPY_REFERENCE_HASHES.keys() | IMPORT_REPAIRS.keys():
    REPAIR_REASONS[task] += ' Pinned pytest to 9.0.2, the version used for local verification, before image preparation.'


def pin_repaired_pytest(task_id, files):
    """Freeze the verified pytest version before cache verification and validation."""
    if task_id not in NUMPY_REFERENCE_HASHES and task_id not in IMPORT_REPAIRS:
        return {}
    edits = {}
    for name in ('environment/Dockerfile', 'tests/test.sh', 'tests/setup.sh'):
        if name not in files:
            continue
        lines = files[name].decode().splitlines(keepends=True)
        for index, line in enumerate(lines):
            if re.search(r'\bpip[0-9]*\s+install\b', line):
                lines[index] = re.sub(r'\bpytest(?:==9\.1\.1)?(?=\s|$)',
                                      'pytest==' + REPAIRED_PYTEST_VERSION, line)
                # Keep pip-check parsing unambiguous, as in the existing R1 fix.
                if 'pytest==' + REPAIRED_PYTEST_VERSION in lines[index]:
                    lines[index] = lines[index].replace(' 2>/dev/null', '')
        updated = ''.join(lines).encode()
        if updated != files[name]:
            edits[name] = updated
    return edits


def repair_numpy_environment(task_id, files):
    """Repair only audited reference payloads; keep existing image instructions."""
    if task_id not in NUMPY_REFERENCE_HASHES:
        return {}
    if hashlib.sha256(files['solution/solution.py']).hexdigest() != NUMPY_REFERENCE_HASHES[task_id]:
        raise ValueError(f'{task_id}: unexpected NumPy reference solution')
    dockerfile = files['environment/Dockerfile']
    if b'numpy' in dockerfile.lower():
        raise ValueError(f'{task_id}: unexpected existing NumPy image declaration')
    return {'environment/Dockerfile': dockerfile.rstrip(b'\n') + b'\nRUN python -m pip install --no-cache-dir numpy==2.2.6\n'}


def repair_reference(task_id, files):
    """Return targeted edits and additions, rejecting unexpected source payloads."""
    if task_id in IMPORT_REPAIRS:
        repair = IMPORT_REPAIRS[task_id]
        raw = files['solution/solution.py']
        if hashlib.sha256(raw).hexdigest() != repair['reference_sha256']:
            raise ValueError(f'{task_id}: unexpected reference solution for import repair')
        prefix = ('\n'.join(repair['imports']) + '\n\n').encode()
        return {'solution/solution.py': prefix + raw}, {}
    if task_id not in REFERENCE_HASHES:
        return {}, {}
    if hashlib.sha256(files['solution/solution.py']).hexdigest() != REFERENCE_HASHES[task_id]:
        raise ValueError(f'{task_id}: unexpected reference solution')
    if task_id == 'pymethods2test-1059':
        return {'solution/solution.py': b'from functools import cmp_to_key\n\n' + files['solution/solution.py']}, {}
    if 'tests/setup.sh' in files:
        raise ValueError(f'{task_id}: unexpected existing verifier setup')
    return {}, {'tests/setup.sh': b'#!/bin/bash\nset -euo pipefail\npython -m pip install --quiet numpy==2.2.6\n'}


def repair_instruction_paths(task_id, files):
    """Apply the resolver's recorded absolute-path edits to the unchanged instruction."""
    if task_id not in PATH_REPAIRS:
        return {}
    repair = PATH_REPAIRS[task_id]
    raw = files['instruction.md']
    if hashlib.sha256(raw).hexdigest() != repair['instruction_sha256']:
        raise ValueError(f'{task_id}: unexpected instruction for path repair')
    text = raw.decode()
    for edit in sorted(repair['edits'], key=lambda e: e['start'], reverse=True):
        if text[edit['start']:edit['end']] != edit['old']:
            raise ValueError(f'{task_id}: path repair does not match the instruction')
        text = text[:edit['start']] + edit['new'] + text[edit['end']:]
    return {'instruction.md': text.encode()}


def replace_single_line(text, old, new, name):
    lines = text.split('\n')
    if lines.count(old) != 1:
        raise ValueError(f'{name}: expected exactly one {old!r} line, got {lines.count(old)}')
    return '\n'.join(new if line == old else line for line in lines)


def pin_pytest(files):
    """R1. Raises unless each file has exactly one matching line and no other pip line."""
    for name, old, new in (('environment/Dockerfile', DOCKER_LINE, DOCKER_LINE_FIXED),
                           ('tests/test.sh', TEST_LINE, TEST_LINE_FIXED)):
        text = files[name].decode()
        others = [line for line in text.split('\n') if 'pip' in line and line != old]
        if others:
            raise ValueError(f'{name}: unexpected pip lines {others!r}')
        files[name] = replace_single_line(text, old, new, name).encode()


def add_oracle_wrapper(files):
    """R2. Raises if solve.sh already exists or solution.py is missing."""
    if 'solution/solve.sh' in files:
        raise ValueError('solution/solve.sh already exists')
    if 'solution/solution.py' not in files:
        raise ValueError('solution/solution.py is missing')
    return SOLVE_SH


def migrate_verifier_mode(content):
    """Migrate only the upstream false flag, preserving shared verification."""
    verifier = tomllib.loads(content.decode())["verifier"]
    if verifier.get("restart_environment") is not False:
        raise ValueError("expected verifier.restart_environment = false")
    if "environment_mode" in verifier or "environment" in verifier:
        raise ValueError("unexpected existing verifier environment configuration")
    updated, count = re.subn(
        rb'(?m)^([ \t]*)restart_environment[ \t]*=[ \t]*false[ \t]*$',
        rb'\1environment_mode = "shared"', content)
    if count != 1:
        raise ValueError("expected exactly one legacy verifier restart flag")
    return updated


def repack(blob, edits, additions):
    """Rewrite edited members in place, append each addition after solution/solution.py.

    Added members copy ownership from the anchor member, with mode 0755 and mtime 0.
    """
    out = io.BytesIO()
    compress = blob[:2] == b'\x1f\x8b'
    stream = gzip.GzipFile(filename='', fileobj=out, mode='wb', compresslevel=9, mtime=0) if compress else out
    anchor = 'solution/solution.py'
    with tarfile.open(fileobj=io.BytesIO(blob), mode='r:*') as src, \
            tarfile.open(fileobj=stream, mode='w', format=src.format) as dst:
        for member in src:
            info = copy.copy(member)
            info.mtime = 0
            info.pax_headers = {k: v for k, v in member.pax_headers.items()
                                if k not in ('mtime', 'atime', 'ctime')}
            if info.name in edits:
                info.size = len(edits[info.name])
                dst.addfile(info, io.BytesIO(edits[info.name]))
            elif member.isfile():
                dst.addfile(info, src.extractfile(member))
            else:
                dst.addfile(info)
            if member.name == anchor:
                for name, data in additions.items():
                    new = tarfile.TarInfo(name)
                    new.size = len(data)
                    new.mode = 0o755
                    new.mtime = 0
                    new.uid, new.gid = member.uid, member.gid
                    new.uname, new.gname = member.uname, member.gname
                    dst.addfile(new, io.BytesIO(data))
    if compress:
        stream.close()
    return out.getvalue()


def patch_task(blob, task_id=None):
    files = read_members(blob)
    additions = {'solution/solve.sh': add_oracle_wrapper(files)}
    pin_pytest(files)
    edits = {name: files[name] for name in ('environment/Dockerfile', 'tests/test.sh')}
    edits['task.toml'] = migrate_verifier_mode(files['task.toml'])
    reference_edits, reference_additions = repair_reference(task_id, files)
    edits.update(reference_edits)
    additions.update(reference_additions)
    edits.update(repair_numpy_environment(task_id, files))
    edits.update(pin_repaired_pytest(task_id, {**files, **edits, **additions}))
    edits.update(repair_instruction_paths(task_id, files))
    return repack(blob, edits, additions)


def verifier_conftest(site_packages):
    """Expose task packages without executing their interpreter startup hooks.

    Ordinary .pth paths and setuptools PEP 660's literal editable mappings are
    import data. Execute neither arbitrary .pth code nor the generated finder.
    The finder is appended after normal resolution, keeping trusted tools first.
    pytest loads this rootdir conftest after its own imports and plugins, before
    collecting test_solution.py; the verifier stays a `-m pytest` process, which
    is what the execution recorder instruments.
    """
    return ('''import ast, importlib.abc, importlib.machinery, importlib.util
import re, sys
from pathlib import Path
sys.path.append("/app")
site_packages = Path(SITE_PACKAGES)
sys.path.append(str(site_packages))
editable = {}
namespaces = {}
for pth in sorted(site_packages.glob("*.pth")):
    for line in pth.read_text(errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith(("import ", "import\\t")):
            match = re.fullmatch(r"import (__editable__[A-Za-z0-9_]+_finder); \\1.install\\(\\)", line)
            if not match:
                continue
            tree = ast.parse((site_packages / (match[1] + ".py")).read_text())
            for node in tree.body:
                target = node.target if isinstance(node, ast.AnnAssign) else (
                    node.targets[0] if isinstance(node, ast.Assign) and len(node.targets) == 1 else None)
                if isinstance(target, ast.Name) and target.id in ("MAPPING", "NAMESPACES"):
                    value = ast.literal_eval(node.value)
                    if not isinstance(value, dict) or not all(isinstance(k, str) for k in value):
                        raise ValueError("Invalid editable import mapping")
                    if target.id == "MAPPING":
                        if not all(isinstance(v, str) for v in value.values()):
                            raise ValueError("Invalid editable module paths")
                        editable.update(value)
                    else:
                        if not all(isinstance(v, list) and all(isinstance(p, str) for p in v) for v in value.values()):
                            raise ValueError("Invalid editable namespace paths")
                        namespaces.update(value)
            continue
        path = site_packages / line
        if path.exists():
            sys.path.append(str(path))
class TaskEditableFinder(importlib.abc.MetaPathFinder):
    @classmethod
    def find_spec(cls, fullname, path=None, target=None):
        if fullname in editable:
            location = Path(editable[fullname])
            candidates = [location / "__init__.py"] + [Path(str(location) + suffix) for suffix in importlib.machinery.all_suffixes()]
            for candidate in candidates:
                if candidate.is_file():
                    return importlib.util.spec_from_file_location(fullname, candidate)
        if fullname in namespaces:
            spec = importlib.machinery.ModuleSpec(fullname, loader=None, is_package=True)
            spec.submodule_search_locations = namespaces[fullname]
            return spec
        return None
sys.meta_path.append(TaskEditableFinder)
'''.replace('SITE_PACKAGES', repr(site_packages))).encode()


def prepare_verifier_runtime(blob, *, python, interpreter_evidence, catalog=None, runtime_image=None):
    """Adapt this datasource's published verifier without changing its tests/reward.

    This is optional: the agent's image already contains its dependencies. The
    separate verifier runtime replaces the best-effort install into that shared
    image. Its version must match the observed image interpreter exactly. This
    optional adapter still requires task-environment semantics validation before
    publication, especially for dependencies the agent installs itself.
    """
    from data.utils.verifier_runtime import (
        preparation_command, python_install_or_prepared, runtime_reference, runtime_spec,
    )
    files = read_members(blob)
    script = files['tests/test.sh'].decode()
    installs = re.findall(r'(?m)^pip3 install --quiet pytest==([0-9.]+) \|\| true$', script)
    if len(installs) != 1 or installs[0] not in ('9.1.1', '9.0.2'):
        raise ValueError('Unexpected pymethods2test pytest installation')
    requirements = ['pytest==' + installs[0]]
    existing_setup = files.get('tests/setup.sh', b'')
    if existing_setup not in (b'', b'#!/bin/bash\nset -euo pipefail\npython -m pip install --quiet numpy==2.2.6\n'):
        raise ValueError('Unexpected pymethods2test verifier setup')
    docker = files['environment/Dockerfile']
    observed = interpreter_evidence[hashlib.sha256(docker).hexdigest()]
    if (not docker.startswith(b'FROM python:3.10-slim\n')
            or observed.get('python') != python
            or not observed.get('executable', '').startswith('/')
            or not re.fullmatch(r'[0-9a-f]{64}', observed.get('image_sha256', ''))):
        raise ValueError('Prepared Python must match the observed original image interpreter exactly')
    if existing_setup or b'numpy==2.2.6' in docker:
        requirements.append('numpy==2.2.6')
    # Preserve the original image's complete resolved Python distribution set,
    # not just pytest's top-level pin. The extra NumPy setup remains explicit.
    distributions = dict(observed['distributions'])
    for requirement in requirements:
        name, version = requirement.split('==')
        if name in distributions and distributions[name] != version:
            raise ValueError('Observed image package disagrees with verifier pin: ' + name)
        distributions[name] = version
    requirements = [name + '==' + version for name, version in distributions.items()]
    runtime_image = observed if runtime_image is None else runtime_image
    if (runtime_image.get('python') != observed['python']
            or runtime_image.get('platform') != observed.get('platform')):
        raise ValueError('Runtime source image must match observed Python and platform')
    spec = runtime_spec(python=python, requirements=requirements, tools=['pytest'], image_python={
        'image_sha256': runtime_image['image_sha256'],
        'executable': runtime_image.get('resolved_executable', runtime_image['executable']),
    })
    setup = ('#!/bin/bash\nset -euo pipefail\n'
             + preparation_command(spec, phase='verifier') + '\n'
             + ': "${OT_VERIFIER_PYTHON:?prepared verifier runtime required}"\n'
             + python_install_or_prepared(spec['requirements'], phase='verifier') + '\n')
    script = replace_single_line(script, 'pip3 install --quiet pytest==' + installs[0] + ' || true',
                                 '# Dependencies are prepared by tests/setup.sh.', 'tests/test.sh')
    script = replace_single_line(
        script, 'pytest /tests/test_solution.py -v --tb=short 2>&1 | tee /logs/verifier/test_output.txt',
        'PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 "${OT_VERIFIER_PYTHON:?prepared verifier runtime required}" -I -m pytest '
        '/tests/test_solution.py -v --tb=short 2>&1 | tee /logs/verifier/test_output.txt', 'tests/test.sh')
    # Import trusted pytest before exposing the submitted solution's directory.
    # -I ignores agent PYTHONPATH but tests must still evaluate /app/solution.py.
    # The source images have no pytest entry-point plugins. Keep pristine
    # grading imports first; permit extra solution dependencies installed in
    # the task interpreter's existing site-packages. Do not execute its .pth
    # startup hooks, which could replace grading tools before test collection.
    site_packages = observed['site_packages']
    if not site_packages.startswith('/'):
        raise ValueError('Expected absolute observed task site-packages path')
    if 'tests/conftest.py' in files:
        raise ValueError('Review existing pymethods2test conftest before migration')
    conftest = verifier_conftest(site_packages)
    updates = runtime_reference(spec, catalog, mount_mode='writable')
    updates.update({'tests/setup.sh': setup.encode(), 'tests/test.sh': script.encode(),
                    'tests/conftest.py': conftest})
    edits = {name: body for name, body in updates.items() if name in files}
    additions = {name: body for name, body in updates.items() if name not in files}
    return repack(blob, edits, additions), spec


def main():
    import pyarrow as pa
    import pyarrow.parquet as pq
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--runtime-python", help="Optionally prepare a clean verifier, exact Python 3.10 patch version")
    parser.add_argument("--runtime-catalog", type=Path, help="Ready runtime records keyed by recipe ID")
    parser.add_argument("--interpreter-evidence", type=Path,
                        help="Observed python/executable/image_sha256 records keyed by Dockerfile SHA256")
    parser.add_argument('--runtime-image-source',
                        help='Optional Dockerfile SHA256 in interpreter evidence for a compatible shared runtime source image')
    parser.add_argument("--published-input", action="store_true", help="Apply only runtime preparation to existing patched tasks")
    parser.add_argument("--published-revision", help="Immutable source HF revision for --published-input")
    parser.add_argument("--published-subset", help="Source pymethods2test_v1, v2 or v3 directory")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    source = pq.ParquetFile(args.source)
    if args.published_input and not args.runtime_python:
        parser.error('--published-input requires --runtime-python')
    if args.published_input and (not re.fullmatch(r'[0-9a-f]{40}', args.published_revision or '')
                                 or args.published_subset not in ('pymethods2test_v1', 'pymethods2test_v2', 'pymethods2test_v3')):
        parser.error('--published-input requires --published-revision and --published-subset')
    if args.runtime_catalog and not args.runtime_python:
        parser.error('--runtime-catalog requires --runtime-python')
    if args.runtime_python and not args.interpreter_evidence:
        parser.error('--runtime-python requires --interpreter-evidence from the original task images')
    catalog = json.loads(args.runtime_catalog.read_text()) if args.runtime_catalog else None
    interpreter_evidence = json.loads(args.interpreter_evidence.read_text()) if args.interpreter_evidence else None
    if args.runtime_image_source and not interpreter_evidence:
        parser.error('--runtime-image-source requires --interpreter-evidence')
    runtime_image = interpreter_evidence[args.runtime_image_source] if args.runtime_image_source else None
    specs = {}
    historical = []
    metadata = dict(source.schema_arrow.metadata or {})
    images = json.loads(metadata[b'ot.images.v1']) if b'ot.images.v1' in metadata else None
    if args.published_input:
        # Existing scores describe the old definitions. Preserve them explicitly,
        # but do not carry validation passes onto the changed task payloads.
        metadata.pop(b'ot.environment_reliable.v1', None)
        metadata[b'ot.runtime_migration.v1'] = json.dumps({
            'source_revision': args.published_revision, 'source_subset': args.published_subset,
            'validation': 'pending', 'effective_network': 'unverified',
            'existing_scores': 'historical, evaluated before runtime migration',
            'historical_evidence': 'historical-evidence.json',
        }).encode()
    if not args.published_input and source.metadata.num_rows != EXPECTED_TASK_COUNT:
        raise ValueError(f'Expected {EXPECTED_TASK_COUNT} tasks, got {source.metadata.num_rows}')
    patched = 0
    labels, reasons = {}, {}
    with pq.ParquetWriter(args.output / "tasks.parquet", source.schema_arrow.with_metadata(metadata)) as writer:
        for batch in source.iter_batches(batch_size=32):
            rows = batch.to_pylist()
            for row in rows:
                task_id = row['path']
                if args.published_input:
                    historical.append({k: v for k, v in row.items() if k != 'task_binary'})
                if not args.published_input:
                    row['task_binary'] = patch_task(row['task_binary'], task_id)
                labels[task_id] = ['pymethods-preparation-repairs']
                reasons[task_id] = ('Pinned pytest, added the reference entry point and migrated the shared verifier schema. '
                                    + REPAIR_REASONS.get(task_id, 'The supplied reference solution is unchanged.'))
                if args.runtime_python:
                    from harbor_patches.verifier_bundle import spec_id
                    row['task_binary'], spec = prepare_verifier_runtime(
                        row['task_binary'], python=args.runtime_python,
                        interpreter_evidence=interpreter_evidence, catalog=catalog, runtime_image=runtime_image)
                    specs[spec_id(spec)] = spec
                    runtime_reason = ('Replaced the shared-image best-effort pytest install with an independently '
                                      'prepared verifier runtime; preserved test bodies, submitted solution imports '
                                      'and reward handling. Runtime execution validation remains required.')
                    if args.published_input:
                        labels[task_id] = ['verifier-runtime-preparation']
                        reasons[task_id] = runtime_reason
                    else:
                        reasons[task_id] += ' ' + runtime_reason
                if args.published_input:
                    from validation.contract import files_digest
                    digest = files_digest(read_members(row['task_binary']).items())
                    if 'content_sha256' in row:
                        row['content_sha256'] = digest
                    if 'stages_passed' in row:
                        row['stages_passed'] = ''
                    if images is not None:
                        images['tasks'][task_id]['content_sha256'] = digest
                patched += 1
            writer.write_table(pa.Table.from_pylist(rows, schema=source.schema_arrow))
        if args.published_input and images is not None:
            import base64
            metadata[b'ot.images.v1'] = json.dumps(images).encode()
            # Arrow restores its serialized schema preferentially over the
            # Parquet footer keys, so keep both metadata representations equal.
            writer.add_key_value_metadata({
                **metadata,
                b'ARROW:schema': base64.b64encode(source.schema_arrow.with_metadata(metadata).serialize()),
            })
    if args.published_input:
        (args.output / 'historical-evidence.json').write_text(json.dumps({
            'revision': args.published_revision, 'subset': args.published_subset,
            'status': 'historical, not validation of migrated definitions', 'tasks': historical,
        }, indent=2) + '\n')
    if patched != source.metadata.num_rows:
        raise ValueError(f'Patched {patched} tasks, expected {EXPECTED_TASK_COUNT}')
    manifest = {'patch_version': PATCH_VERSION, 'revision': REVISION,
                'r1_pin_pytest_tasks': patched, 'r2_oracle_wrapper_tasks': patched,
                'r3_shared_verifier_schema_tasks': patched,
                'reference_repairs': {task: reason for task, reason in REPAIR_REASONS.items() if task in labels}}
    if args.published_input:
        manifest = {'patch_version': PATCH_VERSION + '-verifier-runtime-v1',
                    'revision': args.published_revision, 'subset': args.published_subset,
                    'verifier_runtime_tasks': patched, 'runtime_count': len(specs)}
    (args.output / 'manifest.json').write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n')
    if args.runtime_python:
        (args.output / 'verifier-bundle-specs.json').write_text(json.dumps(list(specs.values()), indent=2) + '\n')
    source_info = {'dataset': 'open-thoughts/TaskTrove', 'revision': REVISION,
                               'url': 'https://huggingface.co/datasets/open-thoughts/TaskTrove/tree/' + REVISION + '/DCAgent__exp_rpt_pymethods2test-large-v2',
                               'subset': 'DCAgent__exp_rpt_pymethods2test-large-v2'}
    if args.published_input:
        source_info = {'dataset': 'FWeindel/validated-tasks', 'revision': args.published_revision,
                       'subset': args.published_subset,
                       'url': 'https://huggingface.co/datasets/FWeindel/validated-tasks/tree/'
                              + args.published_revision + '/' + args.published_subset}
    write_patch_report(args.source, args.output / 'tasks.parquet', source=source_info,
                       dropped={}, change_labels=labels, change_reasons=reasons,
                       patcher=__file__)


if __name__ == "__main__":
    main()
