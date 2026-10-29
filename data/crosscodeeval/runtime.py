"""Published CrossCodeEval verifier integrity migration, with no added packages."""
import argparse
import base64
import copy
import hashlib
import gzip
import io
import json
from pathlib import Path
import re
import tarfile
import tomllib

from data.utils.task_archive import read_members
from data.utils.verifier_runtime import runtime_reference, runtime_spec, preparation_command

SUBSETS = ('crosscodeeval_v1', 'crosscodeeval_v2')
PYTHON = '3.10.22'
EXECUTABLE = '/usr/local/bin/python3'
SCORER = 'tests/cceval_verifier.py'


def migrate_verifier_mode(content):
    """Replace the observed legacy false flag with explicit shared verification."""
    original = tomllib.loads(content.decode())
    verifier = original['verifier']
    if verifier.get('restart_environment') is not False:
        raise ValueError('Expected verifier.restart_environment = false')
    if 'environment_mode' in verifier or 'environment' in verifier:
        raise ValueError('Unexpected existing verifier environment configuration')
    section = re.search(rb'(?ms)^[ \t]*\[verifier\][^\n]*\n(.*?)(?=^[ \t]*\[|\Z)', content)
    if section is None:
        raise ValueError('Expected a verifier section')
    body, count = re.subn(rb'(?m)^([ \t]*)restart_environment[ \t]*=[ \t]*false[ \t]*(#[^\n]*)?$',
                         rb'\1environment_mode = "shared" \2', section.group(1))
    if count != 1:
        raise ValueError('Expected one verifier restart flag')
    updated = content[:section.start(1)] + body + content[section.end(1):]
    expected = copy.deepcopy(original)
    del expected['verifier']['restart_environment']
    expected['verifier']['environment_mode'] = 'shared'
    if tomllib.loads(updated.decode()) != expected:
        raise ValueError('Unexpected configuration change')
    return updated


def prepare_runtime(blob, observed, runtime, catalog):
    """Keep scorer/answer semantics; protect Python from agent startup hooks."""
    files = read_members(blob)
    if (not observed.get('verified_bundle') or observed.get('probe_returncode') != 0
            or observed.get('python') != PYTHON or observed.get('executable') != EXECUTABLE):
        raise ValueError('Expected a verified original CrossCodeEval interpreter probe')
    if hashlib.sha256(files['environment/Dockerfile']).hexdigest() != observed['dockerfile_sha256']:
        raise ValueError('Interpreter evidence does not match the task Dockerfile')
    if 'tests/setup.sh' in files or 'tests/ot-verifier-runtime.json' in files:
        raise ValueError('Review existing verifier setup before migration')
    if b'sys.executable' in files[SCORER]:
        raise ValueError('Review task-interpreter subprocess semantics first')
    spec = runtime_spec(python=PYTHON, requirements=[], image_python=runtime['spec']['image_python'])
    if spec != runtime['spec'] or spec['image_python']['executable'] != EXECUTABLE:
        raise ValueError('Runtime must preserve the original interpreter and contain no added packages')
    old = ('python3 /' + SCORER).encode()
    if files['tests/test.sh'].count(old) != 1:
        raise ValueError('Unexpected CrossCodeEval verifier invocation')
    updates = runtime_reference(spec, catalog)
    updates['task.toml'] = migrate_verifier_mode(files['task.toml'])
    # -I without -S: the fresh runtime's own site-packages holds only the
    # verifier setup's execution recorder; -I still drops PYTHON* and user site.
    updates['tests/test.sh'] = files['tests/test.sh'].replace(old,
        ('"${OT_VERIFIER_PYTHON:?trusted prepared verifier runtime required}" -I /' + SCORER).encode())
    updates['tests/setup.sh'] = ('#!/bin/bash\nset -euo pipefail\n'
        + preparation_command(spec, phase='verifier') + '\n'
        + '"${OT_VERIFIER_PYTHON:?trusted prepared verifier runtime required}" -I -S -c '
        + '"import platform; assert platform.python_version() == ' + repr(PYTHON) + '"\n').encode()
    output = io.BytesIO()
    with tarfile.open(fileobj=io.BytesIO(blob), mode='r:*') as source, \
            gzip.GzipFile(fileobj=output, mode='wb', filename='', mtime=0) as compressed, \
            tarfile.open(fileobj=compressed, mode='w') as target:
        for member in source:
            info = copy.copy(member)
            if member.name in updates:
                body = updates.pop(member.name); info.size = len(body)
                target.addfile(info, io.BytesIO(body))
            else:
                target.addfile(info, source.extractfile(member) if member.isfile() else None)
        for name, body in updates.items():
            info = tarfile.TarInfo(name); info.mode = 0o644; info.size = len(body)
            target.addfile(info, io.BytesIO(body))
    return output.getvalue()


def export(source, output, *, revision, subset, evidence, runtime, catalog):
    import pyarrow as pa
    import pyarrow.parquet as pq
    from data.utils.patch_reporting import write_patch_report
    from validation.contract import files_digest
    if subset not in SUBSETS or not re.fullmatch('[0-9a-f]{40}', revision):
        raise ValueError('Expected a supported CrossCodeEval subset and immutable source revision')
    source, output = Path(source), Path(output)
    if output.exists() and any(output.iterdir()):
        raise ValueError('Use a new empty runtime output directory')
    p = pq.ParquetFile(source); schema = p.schema_arrow
    for name in ('content_sha256', 'stages_passed'):
        if name not in schema.names: schema = schema.append(pa.field(name, pa.string()))
    metadata = dict(p.schema_arrow.metadata or {})
    historical_schema = {k.decode(): base64.b64encode(v).decode() for k, v in metadata.items()}
    images = json.loads(metadata[b'ot.images.v1'])
    probes = {record['bundle']: record for record in evidence if record['dataset'] == SUBSETS[0]}
    for key in (b'ot.environment_reliable.v1', b'ot.full_release.v1', b'ot.release.v2', b'ot.release.v3'):
        metadata.pop(key, None)
    metadata[b'ot.runtime_migration.v1'] = json.dumps({'source_revision': revision, 'source_subset': subset,
        'validation': 'pending', 'effective_network': 'unverified',
        'existing_scores': 'historical, before runtime migration',
        'historical_evidence': 'historical-evidence.json'}).encode()
    output.mkdir(parents=True, exist_ok=True); destination = output/'tasks.parquet'
    history, labels, reasons, checked_images = [], {}, {}, {}
    with pq.ParquetWriter(destination, schema.with_metadata(metadata), compression='zstd') as writer:
        for batch in p.iter_batches(batch_size=16):
            rows = batch.to_pylist()
            for row in rows:
                name = row['path']
                if name in labels: raise ValueError('Duplicate task: ' + name)
                entry = images['tasks'][name]; bindings = entry['environments']
                if len(bindings) != 1: raise ValueError('Expected one task image')
                binding = bindings[0]; bundle = images['bundles'][binding['bundle']]
                observed = dict(probes[binding['bundle']])
                if bundle['provenance']['environment_sha256'] != binding['environment_sha256']:
                    raise ValueError('Task image environment identity mismatch')
                # Probe files were checksum-verified before probing; independently
                # bind this invocation to those same original image bytes.
                if observed['image'] not in checked_images:
                    with Path(observed['image']).open('rb') as f:
                        checked_images[observed['image']] = hashlib.file_digest(f, 'sha256').hexdigest()
                image_sha = checked_images[observed['image']]
                if image_sha != bundle['files'][bundle['name']]['sha256']:
                    raise ValueError('Image probe source bytes differ from published image')
                files = read_members(row['task_binary'])
                if files_digest(files.items()) != entry['content_sha256']:
                    raise ValueError('Source payload differs from published content binding')
                observed['dockerfile_sha256'] = hashlib.sha256(files['environment/Dockerfile']).hexdigest()
                history.append({k: v for k, v in row.items() if k != 'task_binary'})
                row['task_binary'] = prepare_runtime(row['task_binary'], observed, runtime, catalog)
                row['content_sha256'] = files_digest(read_members(row['task_binary']).items())
                row['stages_passed'] = ''; entry['content_sha256'] = row['content_sha256']
                labels[name] = ['verifier-runtime-integrity', 'static:task-toml-schema']
                reasons[name] = ('Run the unchanged text scorer with a pristine exact-version Python and standard library, '
                    'ignoring agent startup hooks. No additional packages or task setup changes. '
                    'Replace unsupported verifier.restart_environment=false with explicit shared mode, preserving timeouts and resources. '
                    'Preserve submitted answers, references and historical scores; fresh validation remains required.')
            writer.write_table(pa.Table.from_pylist(rows, schema=schema))
        metadata[b'ot.images.v1'] = json.dumps(images).encode()
        writer.add_key_value_metadata({**metadata, b'ARROW:schema': base64.b64encode(schema.with_metadata(metadata).serialize())})
    (output/'historical-evidence.json').write_text(json.dumps({'revision': revision, 'subset': subset,
        'status': 'historical, not migrated-task validation', 'tasks': history}, indent=2)+'\n')
    (output/'historical-schema-metadata.json').write_text(json.dumps(historical_schema, indent=2)+'\n')
    (output/'verifier-bundle-specs.json').write_text(json.dumps([runtime['spec']], indent=2)+'\n')
    # The public entrypoint and this source-specific implementation are both retained.
    implementation = output/'runtime.patch.py'; implementation.write_bytes(Path(__file__).read_bytes())
    write_patch_report(source, destination, source={'dataset': 'FWeindel/validated-tasks', 'revision': revision,
        'subset': subset, 'url': f'https://huggingface.co/datasets/FWeindel/validated-tasks/tree/{revision}/{subset}'},
        dropped={}, change_labels=labels, change_reasons=reasons, patcher=Path(__file__).with_name('patch.py'))
    manifest_path = destination.with_suffix('.manifest.json')
    manifest = json.loads(manifest_path.read_text())
    manifest['patcher_dependencies'] = [{'path': implementation.name,
        'sha256': hashlib.sha256(implementation.read_bytes()).hexdigest()}]
    manifest_path.write_text(json.dumps(manifest, indent=2)+'\n')
    return {'rows': len(labels), 'runtime': runtime['artifact']['id'], 'validation': 'pending'}


def cli():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--source-revision', required=True)
    parser.add_argument('--subset', choices=SUBSETS, required=True)
    parser.add_argument('--image-evidence', type=Path, required=True)
    parser.add_argument('--runtime-builds', type=Path, required=True)
    args = parser.parse_args()
    runtime = next(record for record in json.loads(args.runtime_builds.read_text()) if record['subset'] == SUBSETS[0])
    catalog = {runtime['artifact']['id']: runtime['artifact']}
    print(json.dumps(export(args.source, args.output, revision=args.source_revision, subset=args.subset,
        evidence=json.loads(args.image_evidence.read_text()), runtime=runtime, catalog=catalog), indent=2))
