"""FACET verifier dependencies as prepared runtimes, shared per dependency set.

Upstream grading ran through uvx: an isolated environment on the image's Python
3.10.12 with the --with packages, resolved during grading. A task's runtime is
that environment fixed ahead of time, on a clean Python of the same version.
runtime-locks.json holds the resolved closure of each dependency set. Scripts
that do not reduce to a reviewed form raise; they are never left online silently.
"""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile

from data.utils.verifier_runtime import preparation_command, runtime_reference, runtime_spec

PYTHON = '3.10.12'
LOCKS = Path(__file__).with_name('runtime-locks.json')
# Day before the last full FACET validation, so unpinned --with packages get the
# versions that grading resolved then, not whatever is newest at preparation.
EXCLUDE_NEWER = '2026-10-17T00:00:00Z'
LOCK_PLATFORM = 'x86_64-manylinux_2_35'  # ubuntu:22.04, the base of every FACET image.
TOOLS = ('pytest==8.4.1', 'pytest-json-ctrf==0.3.5')
UVX = re.compile(r'^uvx \\\n((?:  \S[^\n]*? \\\n)*)  pytest (.+)$', re.M)
SYSTEM_PYTEST = re.compile(r'^(?:python3? -m pytest|pytest|"\$VENV_DIR/bin/pytest") (.+)$', re.M)
SYSTEM_PATH = re.compile(r'^export PYTHONPATH="/usr/(?:local/)?lib/python3(?:\.10)?/dist-packages:\$\{PYTHONPATH\}"$', re.M)
# Verifier setup lines with no effect on the clean runtime, each checked below:
# installs into the agent-side Python or a venv (now the runtime's), a poppler
# install guard for images that already have it, and installs for task code that
# ran before grading (task_000239 libpango, task_005134 gTTS), which uvx never saw.
DROPPED_SETUP = re.compile(
    r'(?:if ! (?:python3 -c "import (?:pytest|yaml)" 2>/dev/null|command -v pdftotext >/dev/null 2>&1); then|fi'
    r'|(?:python3 -m pip|pip3?|uv pip) install [^|]*?(?: \|\| .+)?'
    r'|python3 -m venv (?:--system-site-packages )?/[\w./-]+|uv venv /[\w./-]+|source /[\w./-]+/bin/activate'
    r'|apt-get update && apt-get install -y (?:poppler-utils|libpango-1\.0-0 libpangoft2-1\.0-0) && rm -rf /var/lib/apt/lists/\*'
    r'|if \[ ! -d "\$VENV_DIR" \]; then)')
DROPPED_TEST = re.compile(r'#!/bin/bash|set \+e|mkdir -p /logs/verifier|status=\$\?|if \[ "\$status" -eq 0 \]; then'
                          r'|echo [01] > /logs/verifier/reward\.txt|else|fi|exit 0|source /[\w./-]+/bin/activate')
# task_003021 installed pytest into its own venv inside test.sh, with retries.
VENV_INSTALL_TEST = '9f2d7413'
# Exits before initialization, which would otherwise run on the preparation host.
NO_AGENT_DEPENDENCIES = ('# Task dependencies are in the image; nothing to prepare.\n'
                         'if [ "${1:-}" = "--prepare" ]; then exit 0; fi\n')


def normalize(requirement):
    name, rest = re.fullmatch(r'([A-Za-z0-9][A-Za-z0-9._-]*)(.*)', requirement.strip()).groups()
    return re.sub(r'[-_.]+', '-', name).lower() + rest.replace(' ', '')


def key(requested):
    return ' '.join(sorted({normalize(r) for r in requested}))


def imports(files):
    found = set()
    for name, body in files.items():
        if name.startswith('tests/') and name.endswith('.py'):
            for node in ast.walk(ast.parse(body.decode())):
                if isinstance(node, ast.Import):
                    found.update(alias.name.split('.')[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                    found.add(node.module.split('.')[0])
    return found - set(sys.stdlib_module_names)


def uvx_requirements(options):
    tokens = shlex.split(options.replace('\\\n', ' '))
    requested = []
    while tokens:
        option = tokens.pop(0)
        value = tokens.pop(0) if tokens else ''
        if option == '--with':
            requested.append(value)
        elif (option, value) not in (('--index-strategy', 'unsafe-best-match'), ('--python', 'python3')):
            raise ValueError('FACET uvx option requires review: ' + option)
    return requested


# Archived from a rule result, so only --no-repairs reaches it: test.sh writes a missing input PDF
# before the tests. The block stays, on the clean runtime's Python.
FIXTURE_TASK = 'task_005981'
FIXTURE_START = '# Ensure the reference PDF exhibit exists'


def split_fixture(files, task_id):
    """(files without the verifier's fixture block, the block for the clean runtime)."""
    if task_id != FIXTURE_TASK:
        return files, ''
    test = files['tests/test.sh'].decode()
    start, end = test.index(FIXTURE_START), test.index('uvx \\\n')
    block = test[start:end]
    if block.count('python3 -c "') != 1:
        raise ValueError(f'{task_id}: verifier fixture requires review')
    return ({**files, 'tests/test.sh': (test[:start] + test[end:]).encode()},
            '\n' + block.replace('python3 -c "', '"$OT_VERIFIER_PYTHON" -I -c "'))


def verifier_scripts(files):
    from data.facet.patch import CURL_BLOCK, ENV_BLOCK, UV_BLOCKS
    setup = files.get('tests/setup.sh', b'').decode()
    for block in (CURL_BLOCK, ENV_BLOCK, *UV_BLOCKS):
        setup = setup.replace(block, '')
    left = [line.strip() for line in setup.splitlines()
            if line.strip() and not line.lstrip().startswith('#') and line not in ('set +e', 'exit 0')]
    unknown = [line for line in left if not DROPPED_SETUP.fullmatch(line)]
    if unknown:
        raise ValueError('FACET verifier setup requires review: ' + unknown[0])
    if any('poppler-utils' in line for line in left) and b'poppler-utils' not in files.get('environment/Dockerfile', b''):
        raise ValueError('FACET verifier setup requires review: image lacks poppler-utils')
    test = files['tests/test.sh'].decode()
    if hashlib.sha256(test.encode()).hexdigest()[:8] == VENV_INSTALL_TEST:
        return test
    from data.facet.patch import ENV_BLOCK
    rest = SYSTEM_PATH.sub('', UVX.sub('', SYSTEM_PYTEST.sub('', test.replace(ENV_BLOCK, ''))))
    unknown = [line.strip() for line in rest.splitlines() if line.strip()
               and not line.lstrip().startswith('#') and not DROPPED_TEST.fullmatch(line.strip())]
    if unknown:
        raise ValueError('FACET test script requires review: ' + unknown[0])
    return test


def plan(files):
    """Requested dependency set and pytest arguments of one task's verifier."""
    test = verifier_scripts(files)
    uvx, system = UVX.findall(test), SYSTEM_PYTEST.findall(test)
    used = imports(files)
    if len(uvx) + len(system) != 1:
        raise ValueError('FACET grading command requires review')
    if uvx:
        requested, arguments = uvx_requirements(uvx[0][0]), uvx[0][1]
        if SYSTEM_PATH.search(test):
            # The uvx environment imported the image's yaml through this PYTHONPATH.
            if used - {'pytest', 'yaml'}:
                raise ValueError('FACET task-environment imports require review')
            requested = [*requested, 'pyyaml']
    else:
        # System-Python tests saw only these packages; yaml came from the image.
        if used - {'pytest', 'yaml'}:
            raise ValueError('FACET task-environment imports require review')
        requested, arguments = [*TOOLS, *(['pyyaml'] if 'yaml' in used else [])], system[0]
    return {'requested': key(requested), 'arguments': arguments}


def spec_for(requested, locks):
    return runtime_spec(python=PYTHON, requirements=locks[requested], tools=['pytest'])


def apply(files, planned, catalog, locks, fixture=''):
    from data.facet.patch import SETUP_SCRIPT, VERIFIER_HEAD, VERIFIER_TAIL
    spec = spec_for(planned['requested'], locks)
    updates = runtime_reference(spec, catalog)
    updates['tests/setup.sh'] = ('#!/bin/bash\nset -e\n' + preparation_command(spec, phase='verifier')
                                 + '\n: "${OT_VERIFIER_PYTHON:?prepared verifier runtime required}"\n').encode()
    # A missing runtime is rebuilt on the host; the container's Python is never used.
    updates['tests/test.sh'] = (VERIFIER_HEAD + fixture + '\n"${OT_VERIFIER_PYTHON:?prepared verifier runtime required}" -I -m pytest '
                                + planned['arguments'] + '\n' + VERIFIER_TAIL).encode()
    if SETUP_SCRIPT in files:
        setup = files[SETUP_SCRIPT].decode()
        if setup.count('set -euo pipefail\n') != 1:
            raise ValueError('FACET agent setup requires review')
        updates[SETUP_SCRIPT] = setup.replace('set -euo pipefail\n', 'set -euo pipefail\n' + NO_AGENT_DEPENDENCIES).encode()
    return {**files, **updates}


def load_catalog(cache, locks):
    """Verified runtime records of every locked dependency set."""
    from harbor_patches.verifier_bundle import spec_id, verify, validate_archive
    catalog = {}
    for requested in locks:
        spec = spec_for(requested, locks)
        runtime = spec_id(spec)
        record = json.loads((Path(cache) / (runtime + '.json')).read_text())
        runtime_reference(spec, {runtime: record})
        validate_archive(verify(record, cache))
        catalog[runtime] = record
    return catalog


def prepared(files, task_id, catalog, locks):
    """One patched task graded by its prepared runtime."""
    base, fixture = split_fixture(files, task_id)
    return apply(base, plan(base), catalog, locks, fixture)


def lock(requested, *, uv='uv'):
    """Resolve one dependency set; every package in the closure becomes a pin."""
    with tempfile.TemporaryDirectory() as tmp:
        inputs = Path(tmp) / 'requirements.in'
        inputs.write_text('\n'.join(requested.split(' ')) + '\n')
        result = subprocess.run([uv, 'pip', 'compile', '--quiet', '--no-header', '--no-annotate', '--no-config',
                                 '--python-version', '3.10', '--python-platform', LOCK_PLATFORM,
                                 '--only-binary', ':all:', '--exclude-newer', EXCLUDE_NEWER, str(inputs)],
                                check=True, capture_output=True, text=True)
    return sorted(line.strip() for line in result.stdout.splitlines() if line.strip())


def migrate_published(source, output, *, revision, subset, cache, locks=None):
    """Adapt every published task; membership, payloads and scores are otherwise kept."""
    import base64
    import copy
    import io
    import tarfile
    from collections import Counter
    import pyarrow as pa
    import pyarrow.parquet as pq
    from data.facet.patch import read_task
    from data.utils.patch_reporting import write_patch_report
    from harbor_patches.verifier_bundle import spec_id, verify, validate_archive
    from validation.contract import files_digest
    if not re.fullmatch('[0-9a-f]{40}', revision) or subset not in ('facet_v1', 'facet_v2', 'facet_v3'):
        raise ValueError('FACET requires immutable source revision and explicit subset')
    locks = locks if locks is not None else json.loads(LOCKS.read_text())
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    target = output / 'tasks.parquet'
    if target.exists():
        raise FileExistsError(target)
    reader = pq.ParquetFile(source)
    metadata = dict(reader.schema_arrow.metadata or {})
    historical_metadata = {k.decode(): v.decode() for k, v in metadata.items()}
    images = json.loads(metadata[b'ot.images.v1'])
    for name in (b'ot.environment_reliable.v1', b'ot.review.v1'):
        metadata.pop(name, None)
    metadata[b'ot.runtime_migration.v1'] = json.dumps(dict(
        source_revision=revision, source_subset=subset, validation='sampled',
        existing_scores='historical, evaluated before runtime migration',
        historical_evidence='historical-evidence.json')).encode()
    history, records, labels, reasons, specs, catalog = [], [], {}, {}, {}, {}
    counts = Counter()
    with pq.ParquetWriter(target, reader.schema_arrow.with_metadata(metadata)) as writer:
        for batch in reader.iter_batches(batch_size=16):
            rows = batch.to_pylist()
            for row in rows:
                name = row['path']
                members, files, _ = read_task(row['task_binary'], name)
                before = files_digest(files.items())
                binding = images['tasks'][name]
                if before != row['content_sha256'] or before != binding['content_sha256']:
                    raise ValueError(f'{name}: published content/image binding differs')
                history.append({k: v for k, v in row.items() if k != 'task_binary'})
                planned = plan(files)
                spec = spec_for(planned['requested'], locks)
                runtime = spec_id(spec)
                if runtime not in catalog:
                    record = json.loads((Path(cache) / (runtime + '.json')).read_text())
                    runtime_reference(spec, {runtime: record})
                    validate_archive(verify(record, cache))
                    catalog[runtime] = record
                    specs[runtime] = spec
                updated = apply(files, planned, catalog, locks)
                buffer = io.BytesIO()
                with tarfile.open(fileobj=buffer, mode='w') as archive:
                    for original in members:
                        member = copy.copy(original)
                        member.size = len(updated[member.name])
                        archive.addfile(member, io.BytesIO(updated[member.name]))
                    for added in sorted(set(updated) - set(files)):
                        member = tarfile.TarInfo(added)
                        member.size, member.mode = len(updated[added]), 0o644
                        archive.addfile(member, io.BytesIO(updated[added]))
                row['task_binary'] = buffer.getvalue()
                row['content_sha256'] = files_digest(updated.items())
                binding['content_sha256'] = row['content_sha256']
                labels[name] = ['prepared-verifier-runtime']
                reasons[name] = ('Grade with a clean prepared Python 3.10.12 and the dependency set the verifier already '
                                 'used, resolved ahead of time instead of during grading. Test bodies are unchanged.')
                if 'stages_passed' in row:
                    row['stages_passed'] = ''
                counts[planned['requested']] += 1
                records.append(dict(path=name, runtime_id=runtime, requested=planned['requested'],
                                    old_content_sha256=before, content_sha256=row['content_sha256']))
            writer.write_table(pa.Table.from_pylist(rows, schema=reader.schema_arrow))
        metadata[b'ot.images.v1'] = json.dumps(images).encode()
        writer.add_key_value_metadata({**metadata, b'ARROW:schema': base64.b64encode(
            reader.schema_arrow.with_metadata(metadata).serialize())})
    (output / 'historical-evidence.json').write_text(json.dumps(dict(revision=revision, subset=subset,
        status='historical, evaluated before the runtime migration', metadata=historical_metadata, tasks=history), indent=1)+'\n')
    report = dict(source_revision=revision, source_subset=subset, counts=dict(counts), tasks=records, runtime_ids=sorted(specs))
    (output / 'runtime-migration.json').write_text(json.dumps(report, indent=1)+'\n')
    (output / 'verifier-bundle-specs.json').write_text(json.dumps(specs, indent=1)+'\n')
    write_patch_report(Path(source), target, source=dict(dataset=subset,
        url='https://huggingface.co/datasets/FWeindel/validated-tasks', revision=revision), dropped={},
        change_labels=labels, change_reasons=reasons, patcher=Path(__file__).with_name('patch.py'))
    return report


def main():
    """Resolve runtime-locks.json from published tasks and write the build specs."""
    import pyarrow.parquet as pq
    from data.facet.patch import read_task
    from harbor_patches.verifier_bundle import spec_id
    parser = argparse.ArgumentParser(description=main.__doc__)
    parser.add_argument('parquet', type=Path, nargs='+')
    parser.add_argument('--specs', type=Path, required=True)
    args = parser.parse_args()
    locks = json.loads(LOCKS.read_text()) if LOCKS.is_file() else {}
    for path in args.parquet:
        for batch in pq.ParquetFile(path).iter_batches(batch_size=64, columns=['path', 'task_binary']):
            for row in batch.to_pylist():
                requested = plan(split_fixture(read_task(row['task_binary'], row['path'])[1], row['path'])[0])['requested']
                if requested not in locks:
                    locks[requested] = lock(requested)
    LOCKS.write_text(json.dumps(locks, indent=1, sort_keys=True) + '\n')
    specs = {spec_id(spec): spec for spec in (spec_for(requested, locks) for requested in locks)}
    args.specs.write_text(json.dumps(specs, indent=1) + '\n')
    print(len(locks), 'dependency sets,', len(specs), 'runtimes')


if __name__ == '__main__':
    main()
