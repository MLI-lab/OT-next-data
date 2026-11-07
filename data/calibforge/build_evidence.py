"""Bind completed legacy image preparations to preserved inputs and Slurm steps.

This adapter does not execute builds or decide verdicts. It rejects incomplete or
ambiguous joins and emits the measured records accepted by the standard importer.
"""
import argparse
import csv
from datetime import datetime
import io
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
from zoneinfo import ZoneInfo

from validation.stages.image_preparation import sha


def bind_step(record, step, timezone):
    """Require agreement between the actual subprocess outcome and accounting."""
    command = record['command']
    if command[0] != 'srun' or '-w' not in command:
        raise ValueError('Expected a recorded node-bound srun invocation')
    code, signal = map(int, step['ExitCode'].split(':'))
    if signal or step['State'] not in ('FAILED', 'COMPLETED'):
        raise ValueError('Build step did not reach an ordinary process exit')
    if record['status'] == 'built':
        if code != 0:
            raise ValueError('Successful preparation contradicts accounting')
    elif record['status'] == 'error':
        if code <= 0 or record['error'] != str(subprocess.CalledProcessError(code, command)):
            raise ValueError('Preparation error contradicts recorded process exit')
    else:
        raise ValueError('Unexpected invoked preparation status')
    if float(step['ElapsedRaw']) > record['seconds'] + 2:
        raise ValueError('Step duration exceeds preparation duration')
    started = datetime.fromisoformat(step['Start']).replace(tzinfo=ZoneInfo(timezone))
    return dict(returncode=code, started_at=started.isoformat(),
                node=command[command.index('-w') + 1], slurm_step_id=step['JobID'],
                slurm_accounting=dict(step), timestamp_source='Slurm step Start')


def reconstruct(source, source_sha256, code, evidence, accounting, array_job, out,
                timezone='Europe/Berlin'):
    import pyarrow.parquet as pq
    from harbor_patches.image_context import task_dockerfiles, environment_dir_hash
    from validation.contract import task_digest
    source, code, evidence, accounting, out = map(Path, (source, code, evidence, accounting, out))
    if sha(source) != source_sha256:
        raise ValueError('Preserved preparation source checksum mismatch')
    rows = {r['path']: r for r in pq.read_table(source).to_pylist()}
    steps = list(csv.DictReader(accounting.open(), delimiter='|'))
    files = {'builder_sha256': code / 'hpc/offline_prep/image_cache.py',
             'bridge_patch_sha256': code / 'harbor_patches/bridge_worker.py',
             'directory_builder_sha256': code / 'harbor_patches/image_build.py'}
    bindings = [source, accounting, *files.values(), code / 'data/calibforge/runtime_workflow.py']
    base = {key: sha(path) for key, path in files.items()}
    records, seen = [], set()
    for shard in sorted(evidence.glob('shard-*.json')):
        index = int(shard.stem.split('-')[1])
        document = json.loads(shard.read_text())
        actual = [(row['task'], item) for row in document['tasks']
                  for item in row.get('images', []) if item.get('command')]
        prefix = f'{array_job}_{index}.'
        selected = sorted((s for s in steps if s['JobID'].startswith(prefix)
                           and s['JobID'][len(prefix):].isdigit()),
                          key=lambda s: int(s['JobID'][len(prefix):]))
        if len(actual) != len(selected):
            raise ValueError('Ambiguous preparation/Slurm step coverage: ' + str(shard))
        for (task_id, original), step in zip(actual, selected, strict=True):
            if task_id not in rows or task_id in seen:
                raise ValueError('Missing or duplicate preparation task')
            seen.add(task_id)
            measured = bind_step(original, step, timezone)
            if original['status'] != 'error':
                continue
            log = evidence / 'evidence' / Path(original['log']).parent.name / Path(original['log']).name
            with tempfile.TemporaryDirectory() as directory:
                task = Path(directory)
                with tarfile.open(fileobj=io.BytesIO(rows[task_id]['task_binary'])) as archive:
                    archive.extractall(task, filter='data')
                hashes = [environment_dir_hash(p.parent) for p in task_dockerfiles(task)]
                matches = [h for h in hashes if h[:12] == original['key']]
                if len(matches) != 1:
                    raise ValueError('Preparation environment does not match preserved input')
                record = dict(original, **base, **measured, task_id=task_id,
                              source_task_sha256=task_digest(task), environment_sha256=matches[0],
                              log=str(log.resolve()), log_sha256=sha(log))
            record['reconstructed_provenance'] = {
                'method': 'ordered-srun-steps-with-matching-exits-v1',
                'bindings': [dict(path=str(p.resolve()), sha256=sha(p)) for p in [*bindings, shard]],
                'original_log_path': original['log'],
                'limitation': 'Builder hashes recovered from the preserved job snapshot after execution'}
            records.append(record)
    with out.open('x') as stream:
        stream.write(json.dumps(records, indent=2) + '\n')
    return records


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('source', 'source-sha256', 'code', 'evidence', 'accounting', 'array-job', 'out'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--timezone', default='Europe/Berlin')
    args = parser.parse_args()
    print(json.dumps({'error_records': len(reconstruct(**vars(args)))}))
