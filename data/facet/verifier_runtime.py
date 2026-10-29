"""FACET's measured plain uvx pytest family, with conservative review gates.

Other uvx recipes, task-environment imports and interpreter-dependent tests need
separate plans. This adapter never alters task initialization or test bodies.
"""
import ast
import json

from data.utils.verifier_runtime import preparation_command, runtime_reference, runtime_spec

COMMAND = ('uvx \\\n  --with pytest==8.4.1 \\\n  --with pytest-json-ctrf==0.3.5 \\\n  pytest ')
# Shared base family: every reviewed FACET image is ubuntu:22.04 + apt python3 (3.10.12) +
# this exact uv 0.9.7 instruction, so uvx resolves the distro interpreter. Unprobed images
# built the same way, with no other Python-selecting instruction, reuse the probed plan.
SHARED_BASE = 'ubuntu:22.04'
UV_INSTRUCTION = ('RUN (set +e ; export HOME=/root ; unset XDG_BIN_HOME XDG_CACHE_HOME XDG_DATA_HOME UV_CACHE_DIR '
                  'UV_INSTALL_DIR UV_TOOL_DIR UV_PYTHON_INSTALL_DIR ; cd / ; if ! command -v curl >/dev/null 2>&1; then '
                  'apt-get update && apt-get install -y curl ca-certificates && rm -rf /var/lib/apt/lists/* ; elif ! dpkg -s '
                  'ca-certificates >/dev/null 2>&1; then apt-get update && apt-get install -y ca-certificates && rm -rf '
                  '/var/lib/apt/lists/* ; fi ; if [ -f "$HOME/.local/bin/env" ]; then . "$HOME/.local/bin/env" ; fi ; if ! '
                  'command -v uvx >/dev/null 2>&1; then curl -LsSf https://astral.sh/uv/0.9.7/install.sh | sh ; . '
                  '"$HOME/.local/bin/env" ; fi ; uvx --with pytest==8.4.1 --with pytest-json-ctrf==0.3.5 pytest --version)')
INTERPRETER_SELECTION = r'python3\.\d|deadsnakes|pyenv|conda|uv python|update-alternatives|python-is-python3|UV_PYTHON|python:3|PYTHON'


def shared_base_eligible(files):
    import re
    docker = files.get('environment/Dockerfile', b'').decode(errors='replace')
    instructions = [l.strip() for l in docker.replace('\\\n', ' ').splitlines() if l.strip()]
    others = [l for l in instructions if l != UV_INSTRUCTION]
    return (re.findall(r'^FROM\s+(\S+)', docker, re.M) == [SHARED_BASE]
            and instructions.count(UV_INSTRUCTION) == 1
            and not any('astral.sh/uv' in l or re.search(INTERPRETER_SELECTION, l) for l in others)
            and any(re.search(r'apt-get install.*\bpython3\b', l) for l in others))


def original_scripts():
    from data.facet.patch import CURL_BLOCK, ENV_BLOCK, UV_BLOCKS, VERIFIER_HEAD, VERIFIER_TAIL
    setup = '#!/bin/bash\nset +e\n\n' + CURL_BLOCK + '\n' + ENV_BLOCK + '\n' + UV_BLOCKS[0] + '\nexit 0\n'
    test = VERIFIER_HEAD + '\n' + ENV_BLOCK + '\n' + COMMAND + '--ctrf /logs/verifier/ctrf.json /tests/test_state.py -rA\n' + VERIFIER_TAIL
    return setup.encode(), test.encode()


def plan(files, image, probe, binding, bundle, *, shared=False):
    """Require a successful original uvx probe for this exact pristine image.

    shared=True: image/probe are the probed SHARED_BASE image; this task's own image must be
    built the same way (shared_base_eligible). All verifier review rules still apply."""
    if image.get('return_code') != 0 or probe.get('returncode') != 0:
        raise ValueError('FACET requires successful image and original uvx probes')
    from validation.contract import files_digest
    if files_digest(files.items()) != binding['content_sha256']:
        raise ValueError('FACET task differs from its published image binding')
    environments = binding['environments']
    if len(environments) != 1 or environments[0]['path'] != 'environment':
        raise ValueError('FACET task image differs from measured interpreter image')
    if shared:
        if not shared_base_eligible(files):
            raise ValueError('FACET image base/uv/interpreter instructions require review')
    elif (environments[0]['environment_sha256'] != image['environment_sha256']
            or environments[0]['bundle'] != image['bundle']
            or bundle['files'][bundle['name']]['sha256'] != image['sif_sha256']):
        raise ValueError('FACET task image differs from measured interpreter image')
    if probe['sif_sha256'] != image['sif_sha256']:
        raise ValueError('FACET uvx probe belongs to another image')
    setup, test = original_scripts()
    if files.get('tests/setup.sh') != setup or files.get('tests/test.sh') != test:
        raise ValueError('FACET verifier family requires separate review')
    python_files = {name for name in files if name.startswith('tests/') and name.endswith('.py')}
    if python_files != {'tests/test_state.py'}:
        raise ValueError('FACET additional test modules require review')
    source = files['tests/test_state.py'].decode()
    tree = ast.parse(source)
    # Preserve task interpreter/import semantics by leaving these cases for a
    # source-specific reviewed adapter, rather than changing sys.executable.
    if any(token in source for token in ('sys.executable', 'sys.path', 'importlib', '__import__', 'site-packages', 'dist-packages')):
        raise ValueError('FACET task interpreter or dynamic imports require review')
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name.split('.')[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level or not node.module:
                raise ValueError('FACET relative imports require review')
            imports.add(node.module.split('.')[0])
    unknown = imports - set(image['stdlib']) - {'pytest'}
    if unknown:
        raise ValueError('FACET task-environment imports require review: ' + ', '.join(sorted(unknown)))
    observed = json.loads(probe['stdout'].split('OT_RUNTIME_PROBE=', 1)[1])
    if observed['python'] != image['version']:
        raise ValueError('FACET uvx interpreter differs from measured image interpreter')
    pins = observed['requirements']
    if not {'pytest==8.4.1', 'pytest-json-ctrf==0.3.5'} <= set(pins):
        raise ValueError('FACET observed grading tools differ')
    spec = runtime_spec(python=observed['python'], requirements=pins, tools=['pytest'],
                        image_python={'image_sha256': image['sif_sha256'], 'executable': image['python3'][1]})
    return {'spec': spec, 'imports': sorted(imports), 'family': 'plain-uvx-pytest-ctrf',
            'content_sha256': binding['content_sha256']}


def apply(files, planned, catalog):
    """Return only verifier updates; prepared setup is a trusted fallback recipe."""
    from validation.contract import files_digest
    if files_digest(files.items()) != planned['content_sha256']:
        raise ValueError('FACET files changed after runtime planning')
    setup, test = original_scripts()
    if files.get('tests/setup.sh') != setup or files.get('tests/test.sh') != test:
        raise ValueError('FACET files changed after runtime planning')
    updates = runtime_reference(planned['spec'], catalog)
    updates['tests/setup.sh'] = ('#!/bin/bash\nset -e\n' + preparation_command(planned['spec'], phase='verifier')
                                + '\n: "${OT_VERIFIER_PYTHON:?prepared verifier runtime required}"\n').encode()
    # Do not source the agent-modifiable uv activation script or resolve uvx.
    from data.facet.patch import ENV_BLOCK
    script = test.decode().replace(ENV_BLOCK + '\n', '', 1)
    script = script.replace(COMMAND, '"${OT_VERIFIER_PYTHON:?prepared verifier runtime required}" -I -m pytest ', 1)
    updates['tests/test.sh'] = script.encode()
    return {**files, **updates}


def migrate_published(source, output, *, revision, subset, image, probe, cache, shared=False):
    """Preserve every published member, adapting only this reviewed image family.

    shared=True also adapts unprobed images that pass shared_base_eligible (plan(shared=True))."""
    import base64
    import copy
    import io
    from pathlib import Path
    import re
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
        existing_scores='historical, evaluated before runtime migration',
        coverage='partial; see runtime-migration.json', historical_evidence='historical-evidence.json')).encode()
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
                members, files, modes = read_task(row['task_binary'], name)
                before = files_digest(files.items())
                binding = images['tasks'][name]
                if before != row['content_sha256'] or before != binding['content_sha256']:
                    raise ValueError(f'{name}: published content/image binding differs')
                history.append({k: v for k, v in row.items() if k != 'task_binary'})
                environment = binding['environments']
                if len(environment) != 1 or environment[0]['path'] != 'environment':
                    raise ValueError(f'{name}: unexpected published environment layout')
                bundle = images['bundles'][environment[0]['bundle']]
                sibling = environment[0]['bundle'] != image['bundle']
                if sibling and not shared:
                    status = 'unmodified: image not yet reviewed'
                else:
                    # Identity/probe failures are errors, never silent exclusions.
                    if ((not sibling and (environment[0]['environment_sha256'] != image['environment_sha256']
                                          or bundle['files'][bundle['name']]['sha256'] != image['sif_sha256']))
                            or probe['sif_sha256'] != image['sif_sha256']
                            or probe.get('returncode') != 0 or image.get('return_code') != 0):
                        raise ValueError(f'{name}: runtime evidence identity differs')
                    try:
                        planned = plan(files, image, probe, binding, bundle, shared=sibling)
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
                            for original in members:
                                member = copy.copy(original)
                                member.size = len(updated[member.name])
                                archive.addfile(member, io.BytesIO(updated[member.name]))
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
                                         'preserve task initialization and test bodies. Current validation pending.'
                                         + (' Runtime from the probed image of the same build recipe (shared base family).' if sibling else ''))
                        status = ('adapted (shared base family), validation pending' if sibling
                                  else 'adapted, validation pending')
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
                  counts=dict(counts), tasks=records, runtime_ids=sorted(specs))
    (output / 'runtime-migration.json').write_text(json.dumps(report, indent=1)+'\n')
    (output / 'verifier-bundle-specs.json').write_text(json.dumps(specs, indent=1)+'\n')
    write_patch_report(Path(source), target, source=dict(dataset=subset,
        url='https://huggingface.co/datasets/FWeindel/validated-tasks', revision=revision), dropped={},
        change_labels=labels, change_reasons=reasons, patcher=Path(__file__).with_name('patch.py'))
    return report
