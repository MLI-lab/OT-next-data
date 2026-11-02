"""Published NL2Bash verifier integrity migration, with no added packages."""
import argparse
import copy
import hashlib
import io
import json
from pathlib import Path
import re
import tarfile

from data.utils.task_archive import read_members
from data.utils.verifier_runtime import runtime_reference, runtime_spec, preparation_command

SUBSETS = ('nl2bash_v1', 'nl2bash_v2', 'nl2bash_v3')
PYTHON = '3.12.3'
EXECUTABLE = '/usr/bin/python3'
SCORER = 'tests/verifier.py'
# Seeds ship with the task, so the agent setup has nothing to save.
PREPARE_NOTHING = 'if [ "${1:-}" = "--prepare" ]; then exit 0; fi\n'
STRICT = b'set -euo pipefail\n'


def prepare_runtime(blob, observed, runtime, catalog):
    """Keep scorer/answer semantics; protect Python from agent startup hooks."""
    files = read_members(blob)
    if (not observed.get('verified_bundle') or observed.get('probe_returncode') != 0
            or observed.get('python') != PYTHON or observed.get('executable') != EXECUTABLE):
        raise ValueError('Expected a verified original NL2Bash interpreter probe')
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
        raise ValueError('Unexpected NL2Bash verifier invocation')
    if files['setup_files/setup.sh'].count(STRICT) != 1 or b'--prepare' in files['setup_files/setup.sh']:
        raise ValueError('Unexpected NL2Bash agent setup')
    updates = runtime_reference(spec, catalog)
    # Upstream leaves allow_internet unset; the prepared runtime covers everything the task needs.
    if b'[environment]' in files['task.toml'] or b'allow_internet' in files['task.toml']:
        raise ValueError('Review the task network declaration before migration')
    updates['task.toml'] = files['task.toml'].rstrip(b'\n') + b'\n\n[environment]\nallow_internet = false\n'
    updates['setup_files/setup.sh'] = files['setup_files/setup.sh'].replace(STRICT, STRICT + PREPARE_NOTHING.encode())
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
            tarfile.open(fileobj=output, mode='w') as target:
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


def load_conversion(image_evidence, runtime_builds):
    """The one verified image probe and runtime, for tasks built from upstream."""
    evidence = json.loads(Path(image_evidence).read_text())
    runtime = next(record for record in json.loads(Path(runtime_builds).read_text()) if record['subset'] == SUBSETS[0])
    if len(evidence) != 1:
        raise ValueError('Expected one image probe')
    return evidence[0], runtime, {runtime['artifact']['id']: runtime['artifact']}


def convert_task(blob, observed, runtime, catalog):
    dockerfile = read_members(blob)['environment/Dockerfile']
    return prepare_runtime(blob, {**observed, 'dockerfile_sha256': hashlib.sha256(dockerfile).hexdigest()}, runtime, catalog)


def export_part(source, destination, evidence, runtime, catalog, labels, reasons):
    """Rewrite one published Parquet; only task bytes and their digests change."""
    import pyarrow as pa
    import pyarrow.parquet as pq
    from validation.contract import files_digest
    table = pq.read_table(source)
    metadata = dict(table.schema.metadata)
    images = json.loads(metadata[b'ot.images.v1'])
    probes = {record['bundle']: record for record in evidence}
    rows = table.to_pylist()
    for row in rows:
        name = row['path']
        # Only tasks a release kept have an image binding; archived payloads share the one image.
        entry = images['tasks'].get(name)
        bindings = entry['environments'] if entry else [{'bundle': next(iter(probes))}]
        if len(bindings) != 1:
            raise ValueError('Expected one task image')
        bundle = images['bundles'].get(bindings[0]['bundle'])
        observed = dict(probes[bindings[0]['bundle']])
        if bundle and 'environment_sha256' in bindings[0] and bundle['provenance']['environment_sha256'] != bindings[0]['environment_sha256']:
            raise ValueError('Task image environment identity mismatch')
        if bundle and observed['image_sha256'] != bundle['files'][bundle['name']]['sha256']:
            raise ValueError('Image probe source bytes differ from published image')
        files = read_members(row['task_binary'])
        if files_digest(files.items()) != (entry['content_sha256'] if entry else row['content_sha256']):
            raise ValueError('Source payload differs from published content binding')
        observed['dockerfile_sha256'] = hashlib.sha256(files['environment/Dockerfile']).hexdigest()
        row['task_binary'] = prepare_runtime(row['task_binary'], observed, runtime, catalog)
        row['content_sha256'] = files_digest(read_members(row['task_binary']).items())
        if entry:
            entry['content_sha256'] = row['content_sha256']
        labels[name] = ['verifier-runtime-integrity']
        reasons[name] = ('Run the unchanged text scorer with a clean exact-version Python from the verifier setup; '
                         'agent setup gains a no-op --prepare; the task runs without internet. Answers, references and scores are unchanged.')
    metadata[b'ot.images.v1'] = json.dumps(images).encode()
    pq.write_table(pa.Table.from_pylist(rows, schema=table.schema).replace_schema_metadata(metadata),
                   destination, compression='zstd')


def export(source, output, *, revision, subset, evidence, runtime, catalog):
    """Migrate one published folder: tasks.parquet and archive.parquet."""
    from data.utils.patch_reporting import write_patch_report
    if subset not in SUBSETS or not re.fullmatch('[0-9a-f]{40}', revision):
        raise ValueError('Expected a supported NL2Bash subset and immutable source revision')
    source, output = Path(source), Path(output)
    if output.exists() and any(output.iterdir()):
        raise ValueError('Use a new empty runtime output directory')
    output.mkdir(parents=True, exist_ok=True)
    labels, reasons = {}, {}
    export_part(source/'archive.parquet', output/'archive.parquet', evidence, runtime, catalog, {}, {})
    export_part(source/'tasks.parquet', output/'tasks.parquet', evidence, runtime, catalog, labels, reasons)
    write_patch_report(source/'tasks.parquet', output/'tasks.parquet', source={'dataset': 'FWeindel/validated-tasks',
        'revision': revision, 'subset': subset,
        'url': f'https://huggingface.co/datasets/FWeindel/validated-tasks/tree/{revision}/{subset}'},
        dropped={}, change_labels=labels, change_reasons=reasons, patcher=Path(__file__).with_name('patch.py'))
    return {'rows': len(labels), 'runtime': runtime['artifact']['id']}


def cli():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True, help='folder with the published tasks and archive Parquets')
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
