#!/usr/bin/env python3
"""Shared-verifier repair for SETA from the documented TaskTrove revision."""
import argparse
import hashlib
import io
import json
import re
import shutil
import subprocess
from pathlib import Path, PurePosixPath
import tarfile
import tomllib

REPOSITORY = 'open-thoughts/TaskTrove'
DISCUSSION = 'https://huggingface.co/datasets/open-thoughts/TaskTrove/discussions/4'
REVISION = '9262d5628e13ec20ac75b7d897f94b93f6be0594'
UPSTREAM_PATH = 'camel-ai__SETA-Env/tasks.parquet'
PATCH_VERSION = 'seta-shared-verifier-v2'
TARGET = 'nl2bash__synth__001'

TEST_SH = '''#!/bin/bash
set -uo pipefail
mkdir -p /logs/verifier
rm -f /logs/verifier/reward.txt
/usr/bin/python3 -m pytest --ctrf /logs/verifier/ctrf.json \\
  --junitxml=/logs/verifier/junit.xml /tests/test_outputs.py -rA
rc=$?
# pytest 1 means assertions failed. Collection/configuration/internal failures
# must stay errors, with no reward, rather than masquerading as a valid NOP zero.
case "$rc" in
  0) echo 1 > /logs/verifier/reward.txt ;;
  1) echo 0 > /logs/verifier/reward.txt ;;
  *) echo "SETA_VERIFIER_ERROR pytest_exit=$rc" >&2; exit "$rc" ;;
esac
'''

def unpack(blob):
    files = {}
    with tarfile.open(fileobj=io.BytesIO(blob), mode='r:*') as archive:
        for member in archive:
            name = PurePosixPath(member.name)
            if name.is_absolute() or '..' in name.parts or not (member.isfile() or member.isdir()):
                raise ValueError(f'Unsafe archive member: {member.name}')
            if member.isfile():
                key = name.as_posix()
                if key in files:
                    raise ValueError(f'Duplicate archive member: {key}')
                files[key] = (archive.extractfile(member).read(), member.mode & 0o777)
    return files


def pack(files):
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode='w', format=tarfile.PAX_FORMAT) as archive:
        for name, (data, mode) in sorted(files.items()):
            member = tarfile.TarInfo(name)
            member.size, member.mode = len(data), mode
            archive.addfile(member, io.BytesIO(data))
    return out.getvalue()


def patch_task(blob):
    files = unpack(blob)
    config = tomllib.loads(files['task.toml'][0].decode())
    if config.get('verifier', {}).get('environment_mode') not in (None, 'shared'):
        raise ValueError('Expected a shared-verifier task')
    dockerfile = files['environment/Dockerfile'][0].decode()
    if 'seta-shared-verifier-v2' in dockerfile:
        raise ValueError('Task has already been patched')
    dockerfile += """
# seta-shared-verifier-v2: NOP does not execute the agent's setup script.
RUN apt-get update && apt-get install -y --no-install-recommends python3-pip && rm -rf /var/lib/apt/lists/*
RUN /usr/bin/python3 -m pip install --no-cache-dir --break-system-packages pytest==8.4.1 pytest-json-ctrf==0.3.5
"""
    files['environment/Dockerfile'] = (dockerfile.encode(), files['environment/Dockerfile'][1])
    files['tests/test.sh'] = (TEST_SH.encode(), 0o755)
    return pack(files)


def digest(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def audit_rewards(pilot, image, output):
    """Run the original shared grader against correct and incorrect filesystem states."""
    import pyarrow.parquet as pq
    import xml.etree.ElementTree as ET
    rows = pq.read_table(pilot).to_pylist()
    if len(rows) != 1 or rows[0]['path'] != TARGET:
        raise ValueError('This artifact audit applies only to the patched NL2Bash pilot')
    files = unpack(rows[0]['task_binary'])
    setup = files['setup_files/setup.sh'][0].decode()
    # Read literal COPY payloads from the pinned task. Never execute task
    # setup or solution shell commands on the host.
    copies = re.findall(r'tar --extract --file "\$setup_dir/(copy-\d+\.tar)"[^\n]*\ndestination=(/opt/sensor_data/[^\n]+)', setup)
    sources = {}
    for archive_name, destination in copies:
        payloads = unpack(files['setup_files/' + archive_name][0])
        sources[destination.removeprefix('/opt/sensor_data/')] = payloads['payload'][0]
    if sum(name.endswith('.data') for name in sources) != 27:
        raise ValueError('Unexpected pinned source fixture')
    output.mkdir(parents=True, exist_ok=False)
    tests = output / 'tests'
    tests.mkdir()
    for name, (payload, mode) in files.items():
        if name.startswith('tests/'):
            target = output / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
            target.chmod(mode)
    image_hash, pilot_hash = digest(image), digest(pilot)
    results = []
    variants = ['gold', 'empty', 'garbage', 'missing-file', 'wrong-name',
                'unsorted-manifest', 'wrong-content']
    for variant in variants:
        case = output / variant
        source, archive, logs = case / 'source', case / 'archive', case / 'logs'
        flat = archive / 'flat'
        for directory in (source, flat, logs):
            directory.mkdir(parents=True, exist_ok=True)
        for name, content in sources.items():
            path = source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        for path in source.rglob('*.data'):
            shutil.copyfile(path, flat / '_'.join(path.relative_to(source).parts))
        manifest = archive / 'manifest.txt'
        entries = sorted('/opt/sensor_archive/flat/' + p.name for p in flat.iterdir())
        manifest.write_text('\n'.join(entries) + '\n')
        first = flat / 'alpha_unit1_temp.data'
        if variant == 'empty':
            for path in flat.iterdir():
                path.unlink()
            manifest.write_text('')
        elif variant == 'garbage':
            for path in flat.iterdir():
                path.write_text('WRONG\n')
        elif variant == 'missing-file':
            first.unlink()
        elif variant == 'wrong-name':
            first.rename(flat / 'wrong_name.data')
        elif variant == 'unsorted-manifest':
            manifest.write_text('\n'.join(reversed(entries)) + '\n')
        elif variant == 'wrong-content':
            first.write_text('WRONG\n')
        command = ['apptainer', 'exec', '--containall', '--cleanenv', '--no-home',
                   '--no-mount', 'hostfs,bind-paths,cwd', '--writable-tmpfs', '--fakeroot',
                   '--bind', f'{source}:/opt/sensor_data:ro',
                   '--bind', f'{archive}:/opt/sensor_archive:ro',
                   '--bind', f'{logs}:/logs/verifier', '--bind', f'{tests}:/tests:ro', '--pwd', '/app',
                   str(image), 'bash', '/tests/test.sh']
        with (case / 'execution.log').open('w') as log:
            process = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=180)
        reward_file = logs / 'reward.txt'
        reward = float(reward_file.read_text()) if reward_file.exists() else None
        junit = logs / 'junit.xml'
        count = len(ET.parse(junit).findall('.//testcase')) if junit.exists() else 0
        expected = 1.0 if variant == 'gold' else 0.0
        record = {'variant': variant, 'expected': expected, 'reward': reward,
                  'testcases': count, 'exit_code': process.returncode,
                  'passed': process.returncode == 0 and reward == expected and count == 10}
        results.append(record)
        print(json.dumps(record), flush=True)
        (output / 'summary.json').write_text(json.dumps({'image': str(image),
             'image_sha256': image_hash, 'pilot_sha256': pilot_hash,
             'results': results, 'passed': len(results) == len(variants) and all(r['passed'] for r in results)}, indent=2) + '\n')
    return 0 if all(r['passed'] for r in results) else 1


def main():
    import pyarrow as pa
    import pyarrow.parquet as pq
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--input', type=Path, required=True)
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--pilot-output', type=Path, help='also write the one repaired task for smoke validation')
    args = ap.parse_args()
    outputs = [args.output, args.output.with_suffix('.report.json')]
    if args.pilot_output:
        outputs.append(args.pilot_output)
    if len({p.resolve() for p in outputs}) != len(outputs) or any(p.exists() for p in outputs):
        raise ValueError('Use distinct new output paths; existing artifacts are never overwritten')
    for p in outputs:
        p.parent.mkdir(parents=True, exist_ok=True)
    source = pq.ParquetFile(args.input)
    if source.metadata.num_rows != 3153:
        raise ValueError('Unexpected task coverage')
    report = {'patch_version': PATCH_VERSION, 'patcher_sha256': digest(Path(__file__)),
              'repository': REPOSITORY, 'discussion': DISCUSSION, 'revision': REVISION,
              'upstream_path': UPSTREAM_PATH, 'input_sha256': digest(args.input),
              'patched': [], 'unchanged': [], 'dropped': [], 'validation_status': 'not_validated'}
    pilot = None
    with pq.ParquetWriter(args.output, source.schema_arrow) as writer:
        for batch in source.iter_batches(batch_size=32):
            rows = batch.to_pylist()
            for row in rows:
                if row['path'] == TARGET:
                    before = hashlib.sha256(row['task_binary']).hexdigest()
                    row['task_binary'] = patch_task(row['task_binary'])
                    report['patched'].append({'task_id': TARGET, 'before_sha256': before,
                         'after_sha256': hashlib.sha256(row['task_binary']).hexdigest(),
                         'changes': ['preinstall pytest and pytest-json-ctrf in the shared image',
                                     'no zero reward on pytest infrastructure errors']})
                    pilot = dict(row)
                else:
                    report['unchanged'].append({'task_id': row['path'], 'reason': 'outside this targeted repair'})
            writer.write_table(pa.Table.from_pylist(rows, schema=source.schema_arrow))
    if len(report['patched']) != 1:
        raise ValueError('Expected exactly one reviewed target')
    if args.pilot_output:
        pq.write_table(pa.Table.from_pylist([pilot], schema=source.schema_arrow), args.pilot_output)
    report['output_sha256'] = digest(args.output)
    args.output.with_suffix('.report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(f'Patched 1 task; preserved 3152 unchanged; dropped 0. Report: {args.output.with_suffix(".report.json")}')


if __name__ == '__main__':
    main()
