"""CalibForge exact image-installed verifier family, preserving task imports."""
import ast
import json
from data.utils.verifier_runtime import preparation_command, runtime_reference, runtime_spec

SCRIPT = b'#!/bin/bash\nset -u\n\nmkdir -p /logs/verifier\n\n# Check if we\'re in a valid working directory\nif [ "$PWD" = "/" ]; then\n    echo "Error: No working directory set. Please set a WORKDIR in your Dockerfile before running this script."\n    echo 0 > /logs/verifier/reward.txt\n    exit 1\nfi\n\nif [ -x /opt/verifier/bin/python ]; then\n  PYTHON=/opt/verifier/bin/python\nelif command -v python3 >/dev/null 2>&1; then\n  PYTHON=python3\nelif command -v python >/dev/null 2>&1; then\n  PYTHON=python\nelse\n  echo "Error: no Python interpreter found. Use the TB2 verifier base image."\n  echo 0 > /logs/verifier/reward.txt\n  exit 1\nfi\n\nif ! "$PYTHON" -c "import pytest" >/dev/null 2>&1; then\n  echo "Error: pytest is not installed for $PYTHON. Use the TB2 verifier base image."\n  echo 0 > /logs/verifier/reward.txt\n  exit 1\nfi\n\n"$PYTHON" -m pytest --ctrf /logs/verifier/ctrf.json /tests/test_outputs.py -rA\n\nif [ $? -eq 0 ]; then\n  echo 1 > /logs/verifier/reward.txt\nelse\n  echo 0 > /logs/verifier/reward.txt\nfi\n\n'


def plan(files, image, probe, binding, bundle):
    from validation.contract import files_digest
    if files_digest(files.items()) != binding['content_sha256']:
        raise ValueError('CalibForge published task binding differs')
    environments = binding['environments']
    if (len(environments) != 1 or environments[0]['path'] != 'environment'
            or environments[0]['environment_sha256'] != image['environment_sha256']
            or environments[0]['bundle'] != image['bundle']
            or bundle['files'][bundle['name']]['sha256'] != image['sif_sha256']
            or probe['sif_sha256'] != image['sif_sha256']):
        raise ValueError('CalibForge image/probe identity differs')
    if image.get('return_code') != 0 or probe.get('returncode') != 0:
        raise ValueError('CalibForge needs successful original image/pytest probes')
    if files.get('tests/test.sh') != SCRIPT or 'tests/setup.sh' in files:
        raise ValueError('CalibForge verifier entrypoint requires review')
    if {k for k in files if k.startswith('tests/') and k.endswith('.py')} != {'tests/test_outputs.py'}:
        raise ValueError('CalibForge test modules require review')
    text = files['tests/test_outputs.py'].decode()
    if any(t in text for t in ('sys.executable','sys.path','importlib','__import__','site-packages','dist-packages')):
        raise ValueError('CalibForge task interpreter/import semantics require review')
    imports = set()
    for node in ast.walk(ast.parse(text)):
        if isinstance(node,ast.Import):imports.update(a.name.split('.')[0] for a in node.names)
        elif isinstance(node,ast.ImportFrom):
            if node.level or not node.module:raise ValueError('CalibForge relative imports require review')
            imports.add(node.module.split('.')[0])
    unknown = imports - set(image['stdlib']) - {'pytest'}
    if unknown:raise ValueError('CalibForge task package imports require review: '+','.join(sorted(unknown)))
    observed=json.loads(probe['stdout'].split('OT_RUNTIME_PROBE=',1)[1])
    if (observed['python'] != image['version'] or observed['executable'] != '/opt/verifier/bin/python'
            or observed['prefix'] != '/opt/verifier' or observed['resolved_executable'] != image['python3'][1]):
        raise ValueError('CalibForge pytest does not use measured task interpreter')
    pins = observed['requirements']
    if ({p.split('==')[0] for p in pins} != {'pytest','pytest-json-ctrf','Pygments','iniconfig','packaging','pluggy'}
            or not {'pytest==8.4.1','pytest-json-ctrf==0.3.5'} <= set(pins)):
        raise ValueError('CalibForge resolved grading tools require review')
    spec=runtime_spec(python=observed['python'],requirements=pins,tools=['pytest'],image_python={
        'image_sha256':image['sif_sha256'],'executable':observed['resolved_executable']})
    return dict(spec=spec,content_sha256=binding['content_sha256'],imports=sorted(imports))


def apply(files,planned,catalog):
    from validation.contract import files_digest
    if files_digest(files.items()) != planned['content_sha256']:
        raise ValueError('CalibForge files changed after runtime planning')
    updates=runtime_reference(planned['spec'],catalog)
    updates['tests/setup.sh']=('#!/bin/bash\nset -e\n'+preparation_command(planned['spec'],phase='verifier')+
                              '\n: "${OT_VERIFIER_PYTHON:?prepared verifier runtime required}"\n').encode()
    script = SCRIPT.decode()
    start = script.index('if [ -x /opt/verifier/bin/python ]; then')
    end = script.index('if ! "$PYTHON" -c "import pytest"',start)
    script = script[:start] + 'PYTHON="${OT_VERIFIER_PYTHON:?prepared verifier runtime required}"\n\n' + script[end:]
    script = script.replace('\"$PYTHON\" -c ', '\"$PYTHON\" -I -c ').replace('\"$PYTHON\" -m ', '\"$PYTHON\" -I -m ')
    updates['tests/test.sh'] = script.encode()
    return {**files,**updates}


def migrate_published(source, output, *, revision, subset, image, probe, cache):
    """Preserve every published member, adapting only the reviewed CalibForge image families."""
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
    from harbor_patches.verifier_bundle import spec_id, verify, validate_archive
    from validation.contract import files_digest
    if not re.fullmatch('[0-9a-f]{40}', revision) or subset not in ('calibforge_v1', 'calibforge_v2', 'calibforge_v3'):
        raise ValueError('CalibForge requires immutable source revision and explicit subset')
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
        task_setup='unchanged; per-task offline compatibility unverified',
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
                        changed = sorted(k for k in updated if updated[k] != files.get(k))
                        if changed != ['tests/ot-verifier-runtime.json', 'tests/setup.sh', 'tests/test.sh']:
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
                                         'preserve task initialization and test bodies. Current task validation remains pending.')
                        status = 'adapted, validation pending'
                if 'stages_passed' in row:
                    row['stages_passed'] = ''
                counts[status] += 1
                records.append(dict(path=name, status=status, old_content_sha256=before,
                                    content_sha256=row['content_sha256']))
            writer.write_table(pa.Table.from_pylist(rows, schema=reader.schema_arrow))
        metadata[b'ot.images.v1'] = json.dumps(images).encode()
        writer.add_key_value_metadata({**metadata, b'ARROW:schema': base64.b64encode(
            reader.schema_arrow.with_metadata(metadata).serialize())})
    (output / 'historical-evidence.json').write_text(json.dumps(dict(revision=revision, subset=subset,
        status='historical, not validation of migrated definitions', metadata=historical_metadata, tasks=history), indent=1)+'\n')
    report = dict(source_revision=revision, source_subset=subset, validation='pending', effective_network='unverified',
        task_setup='unchanged; per-task offline compatibility unverified',
                  counts=dict(counts), tasks=records, runtime_ids=sorted(specs))
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
