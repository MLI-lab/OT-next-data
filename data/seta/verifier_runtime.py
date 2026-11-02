"""SETA plain Python3.13 uvx verifiers; task setup remains source-specific."""
import ast
import json
import re
import shlex
from data.utils.verifier_runtime import preparation_command, runtime_reference, runtime_spec
from validation.contract import files_digest

SCRIPT = b'#!/bin/bash\n\nif ! command -v uv &> /dev/null; then\n    apt-get update && apt-get install -y curl\n    (set -o pipefail; curl --fail --retry 3 --retry-delay 2 --connect-timeout 30 --max-time 180 --retry-max-time 180 -LsSf https://astral.sh/uv/0.10.11/install.sh | sh) || exit $?\nfi\nsource $HOME/.local/bin/env\n\nif [ "$PWD" = "/" ]; then\n    echo "Error: No working directory set."\n    exit 1\nfi\n\nuvx \\\n  -p 3.13 \\\n  -w pytest==8.4.1 \\\n  -w pytest-json-ctrf==0.3.5 \\\n  pytest --ctrf /logs/verifier/ctrf.json /tests/test_outputs.py -rA\n\nif [ $? -eq 0 ]; then\n  echo 1 > /logs/verifier/reward.txt\nelse\n  echo 0 > /logs/verifier/reward.txt\nfi\n'
# Deliberately limited to imports whose stdlib behavior was reviewed for this
# family. Package imports, dynamic imports and task-Python launchers need review.
REVIEWED_STDLIB = set("os sys pathlib subprocess json re hashlib shutil tempfile time datetime collections itertools functools math random string csv io tarfile zipfile gzip bz2 lzma base64 socket stat glob fnmatch difflib textwrap typing unittest urllib xml configparser sqlite3 decimal fractions struct pwd grp signal uuid itertools statistics contextlib inspect platform argparse shlex logging codecs ast filecmp pty termios pickle threading html ipaddress select fcntl errno".split())

# The seven pinned Debian/Ubuntu bases in the published SETA v1/v2/v3 inputs.
# Other libc families require an independent runtime/platform review.
GNU_BASES = set('''
FROM node@sha256:3d0f05455dea2c82e2f76e7e2543964c30f6b7d673fc1a83286736d44fe4c41c
FROM python@sha256:3e2de9c40ca4e3d73240059f9d48baff27908f10293e985a2f382a0378e6df4a
FROM python@sha256:44ff437bba879d4941b710a369a8f19266aea34b29002807f0c487fabc9eec9b
FROM python@sha256:cc9dffa47c8294ba9bb795a8dfaeb7b76f2b30acade2c52a461a2999d127eb00
FROM python@sha256:d1053354624536b044162aaab1e418bd000ea35184fb1ae098ab3166b1072e72
FROM ubuntu@sha256:281c5745f657873d78e5531fc5ba8575f46ab7769b94550ac99543f122679986
FROM ubuntu@sha256:496754492fb28b4d3049432f2ca787449331e23fb14f0dd3fffea86bf5a93eb4
'''.strip().splitlines())


ROBUST_UVX_PREFIX = '''mkdir -p /logs/verifier
rm -f /logs/verifier/reward.txt /logs/verifier/junit.xml
export PATH="$HOME/.local/bin:$PATH"
if ! command -v uvx >/dev/null 2>&1; then
(set -o pipefail; curl --fail --retry 3 --retry-delay 2 --connect-timeout 30 --max-time 180 --retry-max-time 180 -LsSf https://astral.sh/uv/0.10.11/install.sh | sh) || exit $?
fi'''.splitlines()
ROBUST_UVX_REWARD = '''rc=$?
case "$rc" in
0) echo 1 > /logs/verifier/reward.txt ;;
1) if grep -q '<testcase' /logs/verifier/junit.xml 2>/dev/null; then
echo 0 > /logs/verifier/reward.txt
else
echo "SETA_VERIFIER_ERROR no pytest test cases" >&2; exit 1
fi ;;
*) echo "SETA_VERIFIER_ERROR pytest_exit=$rc" >&2; exit "$rc" ;;
esac'''.splitlines()


def canonical_installer_line(line):
    """Recognize the pinned upstream bootstrap before historical curl hardening.

    This is parser normalization only. Runtime migration removes the bootstrap
    and does not change grading commands or reward handling.
    """
    for version in ('0.7.13', '0.10.11'):
        original = f'curl -LsSf https://astral.sh/uv/{version}/install.sh | sh'
        if line == original:
            return (f'(set -o pipefail; curl --fail --retry 3 --retry-delay 2 --connect-timeout 30 '
                    f'--max-time 180 --retry-max-time 180 -LsSf https://astral.sh/uv/{version}/install.sh | sh) || exit $?')
    return line


def uvx_script(script):
    """Recognize SETA's boilerplate while retaining the original pytest arguments.

    Comments and whitespace do not create a new dependency family. Extra shell
    commands do: reject them rather than silently dropping initialization.
    """
    def lines(text):
        return [line.strip() for line in text.replace('\\\n', ' ').splitlines()
                if line.strip() and not line.lstrip().startswith('#')]
    original = lines(SCRIPT.decode())
    candidate = [canonical_installer_line(line) for line in lines(script)]
    candidate = ['source $HOME/.local/bin/env' if line ==
                 'source $HOME/.local/bin/env 2>/dev/null || true' else line
                 for line in candidate]
    candidate = ['apt-get update && apt-get install -y curl' if line == 'dnf install -y curl' else line
                 for line in candidate]
    optional_env = ['if [ -f "$HOME/.local/bin/env" ]; then', 'source $HOME/.local/bin/env', 'fi']
    for index in range(len(candidate) - 2):
        if candidate[index:index + 3] == optional_env:
            candidate[index:index + 3] = ['source $HOME/.local/bin/env']
            break
    commands = [line for line in candidate if line.startswith('uvx ')]
    if len(commands) != 1:
        raise ValueError('SETA verifier family requires review')
    command = commands[0]
    canonical = next(line for line in original if line.startswith('uvx '))
    normalized = [canonical if line == command else line for line in candidate]
    workdir_guard = original[original.index('if [ "$PWD" = "/" ]; then'):original.index(canonical)]
    robust = [
        ['set -o pipefail', *ROBUST_UVX_PREFIX, *workdir_guard, canonical, *ROBUST_UVX_REWARD],
        ['set -uo pipefail', *ROBUST_UVX_PREFIX, canonical, *ROBUST_UVX_REWARD],
    ]
    without_guard = original[:original.index(workdir_guard[0])] + original[original.index(canonical):]
    # Reviewed bootstrap spellings only. The workdir/grading suffix must still
    # match exactly; do not discard arbitrary commands between bootstrap and uvx.
    prefix = original[:original.index(workdir_guard[0])]
    path_export = 'export PATH="$HOME/.local/bin:$PATH"'
    env_source = 'source $HOME/.local/bin/env'
    bootstrap_variants = [
        [path_export, *prefix[:-2], path_export, 'fi'],
        [*prefix[:-1], env_source + ' 2>/dev/null || ' + path_export],
        [*prefix, path_export],
        [*prefix[:-1], path_export],
        [*prefix[:-2], env_source, 'fi'],
    ]
    variants = [p + original[len(prefix):] for p in bootstrap_variants]
    if normalized not in [original, without_guard, *robust, *variants]:
        raise ValueError('SETA verifier initialization requires review')
    argv = shlex.split(command)
    versions = [argv[i+1] for i, value in enumerate(argv[:-1]) if value in ('-p', '--python')]
    if len(versions) != 1 or versions[0] not in ('3.11', '3.12', '3.13'):
        raise ValueError('SETA explicit uvx Python selection requires review')
    # Redirections and shell expansion are not tool arguments. Keep those
    # families separate, so a quoted replacement cannot change their behavior.
    if any(token in command for token in ('$', '`', ';', '|', '&', '<', '>')):
        raise ValueError('SETA verifier shell arguments require review')
    return argv


def plan(files, image, probe, binding, bundle, *, shared_uvx=False):
    if files_digest(files.items()) != binding['content_sha256']:
        raise ValueError('SETA published task binding differs')
    env = binding['environments']
    if (len(env) != 1 or env[0]['path'] != 'environment'
            or env[0]['bundle'] != image['bundle']
            or env[0]['environment_sha256'] != image['environment_sha256']
            or bundle['files'][bundle['name']]['sha256'] != image['sif_sha256']
            or (not shared_uvx and probe['sif_sha256'] != image['sif_sha256'])):
        raise ValueError('SETA image/probe identity differs')
    argv = uvx_script(files.get('tests/test.sh', b'').decode())
    selected_python = next(argv[i+1] for i, value in enumerate(argv[:-1]) if value in ('-p', '--python'))
    if shared_uvx:
        # Share explicit uvx selection only when the image does not already
        # provide that Python minor version. A matching image interpreter needs
        # its own probe and clean image-derived runtime.
        if (bundle.get('provenance', {}).get('architecture') != 'x86_64'
                or 'https://astral.sh/uv/0.10.11/install.sh' not in probe['command']
                or files.get('environment/Dockerfile', b'').decode().partition('\n')[0] not in GNU_BASES):
            raise ValueError('SETA shared uvx platform or bootstrap requires review')
        if image.get('version', '').startswith(selected_python + '.') and probe['sif_sha256'] != image['sif_sha256']:
            raise ValueError('SETA matching image Python requires its own uvx selection probe')
    if image.get('return_code') != 0 or probe.get('returncode') != 0:
        raise ValueError('SETA requires successful original interpreter probes')
    if 'tests/setup.sh' in files:
        raise ValueError('SETA verifier family requires review')
    if {k for k in files if k.startswith('tests/') and k.endswith('.py')} != {'tests/test_outputs.py'}:
        raise ValueError('SETA additional test modules require review')
    source = files['tests/test_outputs.py'].decode()
    if any(token in source for token in ('sys.executable','sys.path','importlib','__import__','site-packages','dist-packages')):
        raise ValueError('SETA task interpreter/import semantics require review')
    imports = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imports.update(a.name.split('.')[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level or not node.module:
                raise ValueError('SETA relative import requires review')
            imports.add(node.module.split('.')[0])
    observed = json.loads(probe['stdout'].split('OT_RUNTIME_PROBE=',1)[1].splitlines()[0])
    if not observed['python'].startswith(selected_python + '.') or 'uvx -p ' + selected_python + ' ' not in probe['command']:
        raise ValueError('SETA original uvx interpreter selection differs')
    pins = observed['requirements']
    names = {p.split('==')[0] for p in pins}
    if names != {'pytest','pytest-json-ctrf','Pygments','iniconfig','packaging','pluggy'} or not {'pytest==8.4.1','pytest-json-ctrf==0.3.5'} <= set(pins):
        raise ValueError('SETA resolved grading tools require review')
    from data.utils.verifier_runtime import prepare_uvx
    declared, command = prepare_uvx(argv, python=observed['python'])
    if declared['tools'] != ['pytest']:
        raise ValueError('SETA verifier executable requires review')
    if not shared_uvx and not set(declared['requirements']) <= set(pins):
        raise ValueError('SETA original uvx requirements differ from measured tools')
    spec = (runtime_spec(python=observed['python'],requirements=pins,tools=['pytest'])
            if set(declared['requirements']) <= set(pins) else declared)
    if image.get('version', '').startswith(selected_python + '.'):
        executable = image.get('executable', '')
        if (observed['python'] != image['version'] or not executable
                or observed.get('base_executable') not in (executable, executable + '.' + selected_python.split('.')[1])):
            raise ValueError('SETA image-native uvx interpreter selection requires review')
        spec = runtime_spec(python=observed['python'], requirements=spec['requirements'], tools=['pytest'],
                            image_python=dict(image_sha256=image['sif_sha256'], executable=executable))
    aliases = {'scikit-learn': 'sklearn', 'pyyaml': 'yaml', 'pillow': 'PIL', 'beautifulsoup4': 'bs4',
               'scikit-image': 'skimage', 'opencv-python-headless': 'cv2'}
    packages = {aliases.get(pin.split('==')[0].lower(), pin.split('==')[0].replace('-', '_'))
                for pin in declared['requirements']}
    if imports - REVIEWED_STDLIB - packages:
        raise ValueError('SETA task package imports require review')
    return dict(spec=spec,content_sha256=binding['content_sha256'],imports=sorted(imports),
                test_command=command,
                interpreter_evidence=('own-image uvx probe' if probe['sif_sha256'] == image['sif_sha256']
                                      else 'shared explicit uvx selection'),
                interpreter_probe_sif_sha256=probe['sif_sha256'],
                task_setup='unchanged; offline preparation incomplete')


def apply(files, planned, catalog):
    if files_digest(files.items()) != planned['content_sha256']:
        raise ValueError('SETA files changed after runtime planning')
    updates = runtime_reference(planned['spec'],catalog)
    updates['tests/setup.sh'] = ('#!/bin/bash\nset -e\n'+preparation_command(planned['spec'],phase='verifier')+
        '\n: "${OT_VERIFIER_PYTHON:?prepared verifier runtime required}"\n').encode()
    # Keep the workdir guard, exact pytest arguments, and original reward branch.
    script = planned.get('test_script')
    if script is None:
        original = files['tests/test.sh'].decode()
        if 'rm -f /logs/verifier/reward.txt /logs/verifier/junit.xml' in original:
            start = original.index('export PATH="$HOME/.local/bin:$PATH"')
            end = original.index('\nfi\n', start) + len('\nfi\n')
            original = original[:start] + original[end:]
            start = 0
        else:
            if 'if [ "$PWD"' in original:
                start = original.index('if [ "$PWD"')
            else:
                bootstrap_end = re.search(r'^source \$HOME/\.local/bin/env(?: 2>/dev/null \|\| true)?\n', original, re.M)
                if bootstrap_end is None:
                    raise ValueError('SETA bootstrap boundary requires review')
                start = bootstrap_end.end()
        command_start = re.search(r'^uvx ', original, re.M).start()
        tool = re.search(r'\bpytest\s+', original[command_start:])
        if tool is None:
            raise ValueError('SETA pytest command spelling requires review')
        arguments = tool.start() + command_start
        script = ('' if start == 0 else '#!/bin/bash\n\n') + original[start:command_start]
        script += '"${OT_VERIFIER_PYTHON:?prepared verifier runtime required}" -I -m '+original[arguments:]
    updates['tests/test.sh'] = script.encode()
    if 'instruction.md' in files:
        from data.seta.patch import SETUP_GUIDANCE
        instruction = files['instruction.md'].decode().replace(SETUP_GUIDANCE, '', 1).replace(
            'run `source /setup_files/context.sh` after setup.',
            'run `source /setup_files/context.sh`.', 1)
        if '/setup_files/setup.sh' in instruction:
            raise ValueError('SETA unexpected setup guidance requires review')
        updates['instruction.md'] = instruction.encode()
    return {**files,**updates}


PROJECT_LINES = '''apt-get update
apt-get install -y curl
(set -o pipefail; curl --fail --retry 3 --retry-delay 2 --connect-timeout 30 --max-time 180 --retry-max-time 180 -LsSf https://astral.sh/uv/0.7.13/install.sh | sh) || exit $?
source $HOME/.local/bin/env
if [ "$PWD" = "/" ]; then
echo "Error: No working directory set. Please set a WORKDIR in your Dockerfile before running this script."
exit 1
fi
uv init
uv add pytest==8.4.1
uv run pytest /tests/test_outputs.py -rA
if [ $? -eq 0 ]; then
echo 1 > /logs/verifier/reward.txt
else
echo 0 > /logs/verifier/reward.txt
fi'''.splitlines()


def project_plan(files, image, probe, binding, bundle):
    """Migrate the plain project bootstrap only when the task is not a project.

    The original uv init/add/run selection is measured on the same pristine
    image. Tasks changing Python selection or project metadata need other plans.
    """
    if (files_digest(files.items()) != binding['content_sha256']
            or binding['environments'] != [dict(path='environment', bundle=image['bundle'],
                                                environment_sha256=image['environment_sha256'])]
            or bundle['files'][bundle['name']]['sha256'] != image['sif_sha256']
            or probe['sif_sha256'] != image['sif_sha256']
            or image.get('return_code') != 0 or probe.get('returncode') != 0):
        raise ValueError('SETA project image/probe identity differs')
    import hashlib
    if hashlib.sha256(files['setup_files/setup.sh']).hexdigest() not in probe.get('reviewed_setup_sha256', []):
        raise ValueError('SETA project interpreter needs a reviewed task initialization')
    script = files['tests/test.sh'].decode()
    lines = [line.strip() for line in script.splitlines() if line.strip() and not line.lstrip().startswith('#')]
    if [canonical_installer_line(line) for line in lines] != PROJECT_LINES or 'tests/setup.sh' in files:
        raise ValueError('SETA project verifier family requires review')
    metadata = json.loads(files['setup_files/operations.json'])
    if any(re.search(r'\b(?:python\S*|pip\S*|uv|uvx|conda|mamba|update-alternatives)\b', op.get('command', ''))
           for op in metadata['operations'] if op['op'] == 'RUN'):
        raise ValueError('SETA task Python selection requires review')
    text = '\n'.join(files.get(name, b'').decode() for name in ('tests/test_outputs.py', 'instruction.md'))
    if any(token in text for token in ('sys.executable', 'sys.path', '__import__', 'importlib', 'pyproject.toml', '.venv', '.python-version')):
        raise ValueError('SETA task project/import semantics require review')
    if any(name.endswith(('/pyproject.toml', '/.python-version')) for name in files):
        raise ValueError('SETA task project files require review')
    if {name for name in files if name.startswith('tests/') and name.endswith('.py')} != {'tests/test_outputs.py'}:
        raise ValueError('SETA additional test modules require review')
    imports = set()
    for node in ast.walk(ast.parse(files['tests/test_outputs.py'])):
        if isinstance(node, ast.Import):
            imports.update(alias.name.split('.')[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level or not node.module:
                raise ValueError('SETA relative import requires review')
            imports.add(node.module.split('.')[0])
    if imports - REVIEWED_STDLIB - {'pytest'}:
        raise ValueError('SETA task package imports require review')
    observed = json.loads(probe['stdout'].split('OT_RUNTIME_PROBE=', 1)[1].splitlines()[0])
    if (observed['python'] != image['version']
            or 'https://astral.sh/uv/0.7.13/install.sh' not in probe['command']
            or 'uv init; uv add pytest==8.4.1; uv run pytest' not in probe['command']
            or 'pytest==8.4.1' not in observed['requirements']):
        raise ValueError('SETA original project interpreter selection differs')
    spec = runtime_spec(python=observed['python'], requirements=observed['requirements'], tools=['pytest'],
                        image_python=dict(image_sha256=image['sif_sha256'], executable=image['executable']))
    script = '#!/bin/bash\n\n' + script[script.index('if [ "$PWD"'):]
    script = script.replace('uv init\n', '').replace('uv add pytest==8.4.1\n', '')
    script = script.replace('uv run pytest ', '"${OT_VERIFIER_PYTHON:?prepared verifier runtime required}" -I -m pytest ', 1)
    return dict(spec=spec, content_sha256=binding['content_sha256'], imports=sorted(imports),
                test_script=script, interpreter_evidence='own-image original uv init/add/run probe',
                interpreter_probe_sif_sha256=probe['sif_sha256'],
                task_setup='unchanged; offline preparation incomplete')


def migrate_published(source, output, *, revision, subset, image, probe, cache, task_input_manifest=None, shared_uvx=False, project_probes=(), uvx_probes=(), source_repository="FWeindel/validated-tasks", source_path=None):
    """Preserve every published member, adapting only the reviewed SETA image families."""
    import base64
    import copy
    import io
    from pathlib import Path
    import re
    import tarfile
    from collections import Counter
    import pyarrow as pa
    import pyarrow.parquet as pq
    def read_task(binary):
        with tarfile.open(fileobj=io.BytesIO(binary)) as archive:
            return [(member, archive.extractfile(member).read() if member.isfile() else None)
                    for member in archive.getmembers()]
    from data.utils.patch_reporting import write_patch_report
    from harbor_patches.verifier_bundle import spec_id, verify, validate_archive, validate_offline_archive, OFFLINE_FORMAT
    from validation.contract import files_digest
    if not re.fullmatch('[0-9a-f]{40}', revision) or subset not in ('seta_v1', 'seta_v2', 'seta_v3'):
        raise ValueError('SETA requires immutable source revision and explicit subset')
    task_input_records = {}
    used_task_input_ids = set()
    if task_input_manifest is not None:
        manifests = (task_input_manifest if isinstance(task_input_manifest, (list, tuple))
                     else [task_input_manifest])
        for manifest in manifests:
            manifest = Path(manifest)
            record = json.loads(manifest.read_text())
            if record.get('id') != spec_id(record.get('spec')):
                raise ValueError('SETA setup input artifact identity does not match its recipe')
            validate_offline_archive(verify(record, manifest.parent, OFFLINE_FORMAT))
            previous = task_input_records.get(record['id'])
            if previous and previous['archive_sha256'] != record['archive_sha256']:
                raise ValueError('Conflicting SETA setup input artifact bytes')
            task_input_records[record['id']] = record
    setup_status = ('reviewed exact setup may use prepared apt/uv inputs; other setups unchanged; isolated validation pending'
                    if task_input_records else 'unchanged; offline preparation incomplete')
    setup_count = 0
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    target = output / 'tasks.parquet'
    if target.exists():
        raise FileExistsError(target)
    reader = pq.ParquetFile(source)
    schema = reader.schema_arrow
    if 'content_sha256' not in schema.names:
        schema = schema.append(pa.field('content_sha256', pa.string()))
    metadata = dict(schema.metadata or {})
    historical_metadata = {k.decode(): v.decode() for k, v in metadata.items()}
    images = json.loads(metadata[b'ot.images.v1'])
    for key in (b'ot.environment_reliable.v1', b'ot.review.v1'):
        metadata.pop(key, None)
    metadata[b'ot.runtime_migration.v1'] = json.dumps(dict(
        source_revision=revision, source_subset=subset, source_repository=source_repository, source_path=source_path, validation='pending', effective_network='unverified',
        task_setup=setup_status,
        existing_scores='historical, evaluated before runtime migration',
        coverage='partial; see runtime-migration.json', historical_evidence='historical-evidence.json')).encode()
    image_records = image if isinstance(image, list) else [image]
    probe_records = probe if isinstance(probe, list) else [probe]
    reviewed = {item['bundle']: item for item in image_records}
    uvx_measurements = {}
    for item in uvx_probes:
        key = (item['bundle'], item['selector'])
        if key in uvx_measurements:
            raise ValueError('Duplicate SETA uvx interpreter evidence')
        uvx_measurements[key] = item
    project_measurements = {}
    for item in project_probes:
        for setup_sha in item.get('reviewed_setup_sha256', []):
            key = (item['bundle'], setup_sha)
            if key in project_measurements:
                raise ValueError('Duplicate SETA project interpreter evidence')
            project_measurements[key] = item
    measured = {item['bundle']: item for item in probe_records if 'bundle' in item}
    if len(image_records) == len(probe_records) == 1:
        measured[image_records[0]['bundle']] = probe_records[0]
    elif shared_uvx and len(probe_records) == 1:
        measured = {key: probe_records[0] for key in reviewed}
    if set(reviewed) != set(measured):
        raise ValueError('SETA every reviewed image needs an original verifier probe')
    generalized_setup_used = False
    history, records, labels, reasons, specs, catalog = [], [], {}, {}, {}, {}
    counts = Counter()
    with pq.ParquetWriter(target, schema.with_metadata(metadata)) as writer:
        for batch in reader.iter_batches(batch_size=16):
            rows = batch.to_pylist()
            for row in rows:
                name = row['path']
                # Historical row labels describe the previous patch. Override
                # report labels even for unchanged members of this migration.
                labels[name] = []
                setup_adapted = False
                setup_needs_no_inputs = False
                members = read_task(row['task_binary'])
                files = {member.name: data for member, data in members if data is not None}
                if len(files) != sum(data is not None for _, data in members):
                    raise ValueError(f'{name}: duplicate task files')
                before = files_digest(files.items())
                binding = images['tasks'][name]
                if before != row.get('content_sha256', before) or before != binding['content_sha256']:
                    raise ValueError(f'{name}: published content/image binding differs')
                history.append({k: v for k, v in row.items() if k != 'task_binary'})
                row['content_sha256'] = before
                environment = binding['environments']
                standard_environment = len(environment) == 1 and environment[0]['path'] == 'environment'
                image = reviewed.get(environment[0]['bundle']) if standard_environment else None
                if image is None:
                    status = ('unmodified: image not yet reviewed' if standard_environment
                              else 'unmodified: environment layout requires review')
                else:
                    bundle = images['bundles'][environment[0]['bundle']]
                    probe = measured[environment[0]['bundle']]
                    # Identity/probe failures are errors, never silent exclusions.
                    if (environment[0]['environment_sha256'] != image['environment_sha256']
                            or bundle['files'][bundle['name']]['sha256'] != image['sif_sha256']
                            or (not shared_uvx and probe['sif_sha256'] != image['sif_sha256'])
                            or probe.get('returncode') != 0 or image.get('return_code') != 0):
                        raise ValueError(f'{name}: runtime evidence identity differs')
                    try:
                        import hashlib
                        project_probe = project_measurements.get((image['bundle'], hashlib.sha256(
                            files.get('setup_files/setup.sh', b'')).hexdigest()))
                        if project_probe is not None:
                            planned = project_plan(files, image, project_probe, binding, bundle)
                        else:
                            argv = uvx_script(files.get('tests/test.sh', b'').decode())
                            selector = next(argv[i+1] for i, value in enumerate(argv[:-1]) if value in ('-p', '--python'))
                            probe = uvx_measurements.get((image['bundle'], selector), probe)
                            share_platform = shared_uvx and files.get('environment/Dockerfile', b'').decode().partition('\n')[0] in GNU_BASES
                            planned = plan(files, image, probe, binding, bundle, shared_uvx=share_platform)
                    except (ValueError, SyntaxError) as error:
                        if 'review' not in str(error):
                            raise
                        status = 'unmodified: ' + str(error)
                    else:
                        key = spec_id(planned['spec'])
                        if key not in catalog:
                            record = json.loads((Path(cache) / (key + '.json')).read_text())
                            runtime_reference(planned['spec'], {key: record})
                            validate_archive(verify(record, cache))
                            catalog[key] = record
                            specs[key] = planned['spec']
                        updated = apply(files, planned, catalog)
                        expected_changes = ['tests/ot-verifier-runtime.json', 'tests/setup.sh', 'tests/test.sh']
                        if updated.get('instruction.md') != files.get('instruction.md'):
                            expected_changes.append('instruction.md')
                        request = reviewed_setup_request(files.get('setup_files/setup.sh'))
                        task_input_record = (task_input_records.get(spec_id(dict(request, image_sha256=SETUP_IMAGE_SHA256)))
                                             if request is not None else None)
                        if task_input_record is not None and image['sif_sha256'] == SETUP_IMAGE_SHA256:
                            updated = prepare_task_inputs(updated, image, task_input_record)
                            setup_adapted = True
                            setup_count += 1
                            used_task_input_ids.add(task_input_record['id'])
                            expected_changes += ['setup_files/ot-task-runtime.json', 'setup_files/setup.sh']
                        elif task_input_records:
                            from data.seta import setup_runtime
                            try:
                                request, _ = setup_runtime.dependency_request(files)
                            except ValueError:
                                pass  # Report remains explicitly incomplete for this task.
                            else:
                                input_key = spec_id(dict(request, image_sha256=image['sif_sha256']))
                                empty = not request['apt'] and not request['python'] and not request.get('http')
                                if empty or input_key in task_input_records:
                                    updated = setup_runtime.apply(updated, image['sif_sha256'], task_input_records)
                                    setup_adapted = True
                                    setup_needs_no_inputs = empty
                                    setup_count += 1
                                    generalized_setup_used = True
                                    expected_changes.append('setup_files/setup.sh')
                                    if not empty:
                                        used_task_input_ids.add(input_key)
                                        expected_changes.append('setup_files/ot-task-runtime.json')
                        changed = sorted(k for k in updated if updated[k] != files.get(k))
                        if changed != sorted(expected_changes):
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
                        reasons[name] = ('Use the measured original Python and pinned pytest tools in a fresh private runtime; '
                                         'preserve task initialization and test bodies. Task setup offline preparation remains incomplete; current validation pending.')
                        if setup_adapted:
                            labels[name].append('prepared-task-inputs')
                            reasons[name] = ('Use fresh verifier tools and prepared apt/uv download inputs at original task install points; '
                                             'preserve initialization, tests and historical evidence. Isolated validation pending.')
                        if setup_needs_no_inputs:
                            labels[name][-1] = 'offline-setup-no-dependencies'
                            reasons[name] = ('Use fresh verifier tools; task initialization has no dependency downloads, '
                                             'so --prepare saves no empty runtime. Isolated validation pending.')
                        status = 'adapted, validation pending'
                if 'stages_passed' in row:
                    row['stages_passed'] = ''
                counts[status] += 1
                records.append(dict(path=name, status=status, old_content_sha256=before,
                                    content_sha256=row['content_sha256'], task_inputs_prepared=setup_adapted,
                                    task_inputs_not_needed=setup_needs_no_inputs))
            writer.write_table(pa.Table.from_pylist(rows, schema=schema))
        metadata[b'ot.images.v1'] = json.dumps(images).encode()
        writer.add_key_value_metadata({**metadata, b'ARROW:schema': base64.b64encode(
            schema.with_metadata(metadata).serialize())})
    (output / 'historical-evidence.json').write_text(json.dumps(dict(revision=revision, subset=subset,
        status='historical, not validation of migrated definitions', metadata=historical_metadata, tasks=history), indent=1)+'\n')
    report = dict(source_revision=revision, source_subset=subset, source_repository=source_repository, source_path=source_path, validation='pending', effective_network='unverified',
        task_setup=setup_status,
                  counts=dict(counts), tasks=records, runtime_ids=sorted(specs),
                  task_inputs_prepared=setup_count, task_input_runtime_ids=sorted(used_task_input_ids))
    (output / 'runtime-migration.json').write_text(json.dumps(report, indent=1)+'\n')
    (output / 'verifier-bundle-specs.json').write_text(json.dumps(specs, indent=1)+'\n')
    source_url = 'https://huggingface.co/datasets/' + source_repository
    if source_path:
        source_url += '/blob/' + revision + '/' + source_path
    write_patch_report(Path(source), target, source=dict(dataset=source_repository,
        url=source_url, revision=revision), dropped={},
        change_labels=labels, change_reasons=reasons, patcher=Path(__file__).with_name('patch.py'))
    # The public patcher imports this source-specific implementation. Preserve
    # both snapshots so publication never substitutes a later helper revision.
    import hashlib
    helper = Path(__file__).read_bytes()
    helper_path = output / 'runtime.patch.py'
    with helper_path.open('xb') as stream:
        stream.write(helper)
    manifest_path = target.with_suffix('.manifest.json')
    manifest = json.loads(manifest_path.read_text())
    manifest['patcher_dependencies'] = [dict(path=helper_path.name, sha256=hashlib.sha256(helper).hexdigest())]
    if generalized_setup_used:
        setup_helper = Path(__file__).with_name('setup_runtime.py').read_bytes()
        setup_path = output / 'setup-runtime.patch.py'
        setup_path.write_bytes(setup_helper)
        manifest['patcher_dependencies'].append(dict(path=setup_path.name,
            sha256=hashlib.sha256(setup_helper).hexdigest()))
    manifest_path.write_text(json.dumps(manifest, indent=2)+'\n')
    return report


SETUP_SCRIPT = b'#!/bin/bash\nset -eu\numask 022\nsetup_dir=$(cd -- "$(dirname -- "$0")" && pwd)\nstate="$setup_dir/.state"\nmkdir -p "$state"\nif ! mkdir "$state/lock" 2>/dev/null; then echo "SETA_SETUP_BUSY" >&2; exit 75; fi\ntrap \'rmdir "$state/lock"\' EXIT\nidentity=afa65ee94a723b50e767a9215467f6d445620be61dd2b648c971597623d4912b\nif [ -f "$state/complete" ]; then\n  [ "$(cat "$state/complete")" = "$identity" ] || { echo SETA_SETUP_IDENTITY_MISMATCH >&2; exit 76; }\n  echo SETA_SETUP_ALREADY_COMPLETE; exit 0\nfi\nif [ -e "$state/started" ]; then echo "SETA_SETUP_PARTIAL: use a fresh trial" >&2; exit 77; fi\nprintf "%s\\n" "$identity" > "$state/started"\ntrap \'rc=$?; echo "SETA_SETUP_FAILED operation=${operation:-preflight} rc=$rc" >&2; exit "$rc"\' ERR\noperation=0\necho \'SETA_SETUP_OPERATION 0 WORKDIR line=3\'\nmkdir -p -- /app\noperation=1\necho \'SETA_SETUP_OPERATION 1 RUN line=5\'\n(cd -- /app && export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin; /bin/sh -c \'apt-get update && apt-get install -y build-essential git curl tmux wget vim htop tree net-tools && rm -rf /var/lib/apt/lists/*\')\noperation=2\necho \'SETA_SETUP_OPERATION 2 RUN line=18\'\n(cd -- /app && export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin; /bin/sh -c \'curl -LsSf https://astral.sh/uv/0.10.11/install.sh | sh\')\nprintf "%s\\n" "$identity" > "$state/complete.tmp"\nmv -- "$state/complete.tmp" "$state/complete"\necho SETA_SETUP_COMPLETE\n'
SETUP_IMAGE_SHA256 = "e009029c82e282d78afaf4761dccddf110645ac7745539271f05e36809f0556b"
SETUP_REQUEST = dict(apt='build-essential git curl tmux wget vim htop tree net-tools'.split(),
                     python=[], uv=False, http=dict(home='/root', workdir='/app',
                     commands=['curl -LsSf https://astral.sh/uv/0.10.11/install.sh | sh']))


# Reviewed minimal family: only WORKDIR, four apt packages, then pinned uv.
# Preserve each task's state identity while matching every other setup byte.
MINIMAL_SETUP_SCRIPT = (SETUP_SCRIPT
    .replace(b'WORKDIR line=3', b'WORKDIR line=2')
    .replace(b'RUN line=5', b'RUN line=3')
    .replace(b'RUN line=18', b'RUN line=4')
    .replace(b'build-essential git curl tmux wget vim htop tree net-tools && rm -rf /var/lib/apt/lists/*',
             b'build-essential git curl tmux'))
MINIMAL_SETUP_REQUEST = dict(SETUP_REQUEST, apt='build-essential git curl tmux'.split())

# ask_ubuntu__evolve__1000__b1 installs five packages, then pinned uv,
# copies three tools and two service definitions, and adjusts tool modes.
# Match its complete reviewed script, including COPY hashes and state identity.
SYSADMIN_SETUP_SHA256 = '4a8ef90319d1a4bbdfbb3593ec28f622344a1b4bc668b3334f5cb0ed40128385'
SYSADMIN_SETUP_REQUEST = dict(SETUP_REQUEST, apt='build-essential git curl tmux systemd'.split())


def reviewed_setup_request(script):
    import hashlib
    import re
    if script == SETUP_SCRIPT:
        return SETUP_REQUEST
    if not isinstance(script, bytes):
        return None
    if hashlib.sha256(script).hexdigest() == SYSADMIN_SETUP_SHA256:
        return SYSADMIN_SETUP_REQUEST
    identities = re.findall(rb'^identity=([a-f0-9]{64})$', script, re.M)
    if len(identities) != 1:
        return None
    canonical = re.sub(rb'^identity=[a-f0-9]{64}$',
                       b'identity=afa65ee94a723b50e767a9215467f6d445620be61dd2b648c971597623d4912b',
                       script, flags=re.M)
    if canonical == SETUP_SCRIPT:
        return SETUP_REQUEST
    return MINIMAL_SETUP_REQUEST if canonical == MINIMAL_SETUP_SCRIPT else None


def prepare_task_inputs(files, image, record):
    """Reviewed SETA setup families, preserving original install points and state."""
    from data.utils.verifier_runtime import offline_preparation_command, task_offline_reference
    request = reviewed_setup_request(files.get('setup_files/setup.sh'))
    if (request is None
            or image.get('sif_sha256') != SETUP_IMAGE_SHA256 or image.get('return_code') != 0):
        raise ValueError('SETA task setup/image requires separate review')
    if record.get('spec') != dict(request, image_sha256=SETUP_IMAGE_SHA256):
        raise ValueError('SETA setup input artifact does not match the reviewed recipe')
    updates = task_offline_reference(request, record)
    updates['setup_files/ot-task-runtime.json'] = updates.pop('setup_files/ot-task-offline.json')
    preparation = offline_preparation_command(request, image_sha256=SETUP_IMAGE_SHA256, task_reference=True)
    setup = files['setup_files/setup.sh'].decode()
    setup = setup.replace('set -eu\n', 'set -eu\n'+preparation+'\n'
        'if [ -n "${OT_TASK_OFFLINE:-}" ]; then\n'
        '  export APT_CONFIG="$OT_TASK_OFFLINE/apt.conf"\nfi\n', 1)
    # The original operations reset PATH. Insert the prepared curl at those same
    # install points; absent staged inputs, the original online path is retained.
    setup = setup.replace('export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin;',
        'export PATH="${OT_TASK_OFFLINE:+$OT_TASK_OFFLINE/http/bin:}/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin";')
    updates['setup_files/setup.sh'] = setup.encode()
    return {**files, **updates}
