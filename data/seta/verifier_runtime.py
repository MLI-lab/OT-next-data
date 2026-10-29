"""SETA plain Python3.13 uvx verifiers; task setup remains source-specific."""
import ast
import json
from data.utils.verifier_runtime import preparation_command, runtime_reference, runtime_spec
from validation.contract import files_digest

SCRIPT = b'#!/bin/bash\n\nif ! command -v uv &> /dev/null; then\n    apt-get update && apt-get install -y curl\n    (set -o pipefail; curl --fail --retry 3 --retry-delay 2 --connect-timeout 30 --max-time 180 --retry-max-time 180 -LsSf https://astral.sh/uv/0.10.11/install.sh | sh) || exit $?\nfi\nsource $HOME/.local/bin/env\n\nif [ "$PWD" = "/" ]; then\n    echo "Error: No working directory set."\n    exit 1\nfi\n\nuvx \\\n  -p 3.13 \\\n  -w pytest==8.4.1 \\\n  -w pytest-json-ctrf==0.3.5 \\\n  pytest --ctrf /logs/verifier/ctrf.json /tests/test_outputs.py -rA\n\nif [ $? -eq 0 ]; then\n  echo 1 > /logs/verifier/reward.txt\nelse\n  echo 0 > /logs/verifier/reward.txt\nfi\n'
# Deliberately limited to imports whose stdlib behavior was reviewed for this
# family. Package imports, dynamic imports and task-Python launchers need review.
REVIEWED_STDLIB = set("os sys pathlib subprocess json re hashlib shutil tempfile time datetime collections itertools functools math random string csv io tarfile zipfile gzip bz2 lzma base64 socket stat glob fnmatch difflib textwrap typing unittest urllib xml configparser sqlite3 decimal fractions struct pwd grp signal uuid itertools statistics contextlib inspect platform argparse shlex logging codecs".split())


def plan(files, image, probe, binding, bundle):
    if files_digest(files.items()) != binding['content_sha256']:
        raise ValueError('SETA published task binding differs')
    env = binding['environments']
    if (len(env) != 1 or env[0]['path'] != 'environment'
            or env[0]['bundle'] != image['bundle']
            or env[0]['environment_sha256'] != image['environment_sha256']
            or bundle['files'][bundle['name']]['sha256'] != image['sif_sha256']
            or probe['sif_sha256'] != image['sif_sha256']):
        raise ValueError('SETA image/probe identity differs')
    if image.get('return_code') != 0 or probe.get('returncode') != 0:
        raise ValueError('SETA requires successful original interpreter probes')
    if files.get('tests/test.sh') != SCRIPT or 'tests/setup.sh' in files:
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
    if imports - REVIEWED_STDLIB - {'pytest'}:
        raise ValueError('SETA task package imports require review')
    observed = json.loads(probe['stdout'].split('OT_RUNTIME_PROBE=',1)[1])
    if not observed['python'].startswith('3.13.') or 'uvx -p 3.13 ' not in probe['command']:
        raise ValueError('SETA original uvx interpreter selection differs')
    pins = observed['requirements']
    names = {p.split('==')[0] for p in pins}
    if names != {'pytest','pytest-json-ctrf','Pygments','iniconfig','packaging','pluggy'} or not {'pytest==8.4.1','pytest-json-ctrf==0.3.5'} <= set(pins):
        raise ValueError('SETA resolved grading tools require review')
    spec = runtime_spec(python=observed['python'],requirements=pins,tools=['pytest'])
    return dict(spec=spec,content_sha256=binding['content_sha256'],imports=sorted(imports),
                task_setup='unchanged; offline preparation incomplete')


def apply(files, planned, catalog):
    if files_digest(files.items()) != planned['content_sha256']:
        raise ValueError('SETA files changed after runtime planning')
    updates = runtime_reference(planned['spec'],catalog)
    updates['tests/setup.sh'] = ('#!/bin/bash\nset -e\n'+preparation_command(planned['spec'],phase='verifier')+
        '\n: "${OT_VERIFIER_PYTHON:?prepared verifier runtime required}"\n').encode()
    # Keep the workdir guard, exact pytest arguments, and original reward branch.
    original = SCRIPT.decode()
    start = original.index('if [ "$PWD"')
    command_start = original.index('uvx \\\n', start)
    arguments = original.index('pytest --ctrf', command_start)
    script = '#!/bin/bash\n\n'+original[start:command_start]
    script += '"${OT_VERIFIER_PYTHON:?prepared verifier runtime required}" -I -m '+original[arguments:]
    updates['tests/test.sh'] = script.encode()
    return {**files,**updates}


def migrate_published(source, output, *, revision, subset, image, probe, cache, task_input_manifest=None):
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
            if record.get('spec') not in [dict(request, image_sha256=SETUP_IMAGE_SHA256)
                                         for request in (SETUP_REQUEST, MINIMAL_SETUP_REQUEST, SYSADMIN_SETUP_REQUEST)]:
                raise ValueError('SETA setup input artifact does not match the reviewed recipe')
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
    metadata = dict(reader.schema_arrow.metadata or {})
    historical_metadata = {k.decode(): v.decode() for k, v in metadata.items()}
    images = json.loads(metadata[b'ot.images.v1'])
    for key in (b'ot.environment_reliable.v1', b'ot.review.v1'):
        metadata.pop(key, None)
    metadata[b'ot.runtime_migration.v1'] = json.dumps(dict(
        source_revision=revision, source_subset=subset, validation='pending', effective_network='unverified',
        task_setup=setup_status,
        existing_scores='historical, evaluated before runtime migration',
        coverage='partial; see runtime-migration.json', historical_evidence='historical-evidence.json')).encode()
    reviewed = {image['bundle']: image}
    measured = {image['bundle']: probe}
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
                setup_adapted = False
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
                            or probe['sif_sha256'] != image['sif_sha256']
                            or probe.get('returncode') != 0 or image.get('return_code') != 0):
                        raise ValueError(f'{name}: runtime evidence identity differs')
                    try:
                        planned = plan(files, image, probe, binding, bundle)
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
                        request = reviewed_setup_request(files.get('setup_files/setup.sh'))
                        task_input_record = (task_input_records.get(spec_id(dict(request, image_sha256=SETUP_IMAGE_SHA256)))
                                             if request is not None else None)
                        if task_input_record is not None and image['sif_sha256'] == SETUP_IMAGE_SHA256:
                            updated = prepare_task_inputs(updated, image, task_input_record)
                            setup_adapted = True
                            setup_count += 1
                            used_task_input_ids.add(task_input_record['id'])
                            expected_changes += ['setup_files/ot-task-offline.json', 'setup_files/setup.sh']
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
                        status = 'adapted, validation pending'
                if 'stages_passed' in row:
                    row['stages_passed'] = ''
                counts[status] += 1
                records.append(dict(path=name, status=status, old_content_sha256=before,
                                    content_sha256=row['content_sha256'], task_inputs_prepared=setup_adapted))
            writer.write_table(pa.Table.from_pylist(rows, schema=reader.schema_arrow))
        metadata[b'ot.images.v1'] = json.dumps(images).encode()
        writer.add_key_value_metadata({**metadata, b'ARROW:schema': base64.b64encode(
            reader.schema_arrow.with_metadata(metadata).serialize())})
    (output / 'historical-evidence.json').write_text(json.dumps(dict(revision=revision, subset=subset,
        status='historical, not validation of migrated definitions', metadata=historical_metadata, tasks=history), indent=1)+'\n')
    report = dict(source_revision=revision, source_subset=subset, validation='pending', effective_network='unverified',
        task_setup=setup_status,
                  counts=dict(counts), tasks=records, runtime_ids=sorted(specs),
                  task_inputs_prepared=setup_count, task_input_runtime_ids=sorted(used_task_input_ids))
    (output / 'runtime-migration.json').write_text(json.dumps(report, indent=1)+'\n')
    (output / 'verifier-bundle-specs.json').write_text(json.dumps(specs, indent=1)+'\n')
    write_patch_report(Path(source), target, source=dict(dataset=subset,
        url='https://huggingface.co/datasets/FWeindel/validated-tasks', revision=revision), dropped={},
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
    preparation = offline_preparation_command(request, image_sha256=SETUP_IMAGE_SHA256)
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
