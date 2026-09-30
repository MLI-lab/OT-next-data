"""Preserve a running validation checkpoint before cancelling its allocation."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tarfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from validation.contract import read, task_records, task_digest
from validation.static_resume import sha


def capture(contract_path, summary_path, code, destination):
    if not any(destination.resolve().is_relative_to(p) for p in ('/data/horse', '/data/ws', '/data/cat')):
        raise ValueError('checkpoint must be in a data workspace')
    contract = read(contract_path)
    destination.mkdir(parents=True, exist_ok=False)
    # The old writer is not atomic: read one fully parseable checkpoint snapshot.
    for attempt in range(20):
        raw = summary_path.read_bytes()
        try:
            summary = json.loads(raw)
            break
        except json.JSONDecodeError:
            time.sleep(1)
    else:
        raise ValueError('could not read a complete checkpoint')
    (destination / 'summary.json').write_bytes(raw)
    shutil.copyfile(contract_path, destination / 'contract.json')
    manifest = contract['task_manifest']['path']
    shutil.copyfile(contract_path.parent / manifest, destination / manifest)
    shutil.copyfile(code / 'validation/verify/check_terminal_bench.py', destination / 'checker.py')
    print(f"Preserving {len(summary['tasks'])} checkpointed tasks", flush=True)
    # Preserve the already normalized inputs; original scratch may be deleted on exit.
    source = Path(contract['arguments']['tasks'])
    from validation.data.materialize import parquet_files, materialize
    if parquet_files(source):
        materialize(source, destination / 'tasks', selected_ids={r['task_id'] for r in task_records(contract)})
    else:
        shutil.copytree(source, destination / 'tasks')
    print('Inputs copied; verifying all task hashes', flush=True)
    for item in task_records(contract):
        if task_digest(destination / 'tasks' / item['task_id']) != item['sha256']:
            raise ValueError(f"task hash mismatch: {item['task_id']}")
    print('Task hashes verified; packing checkpoint logs', flush=True)
    with tarfile.open(destination / 'logs.tar.gz', 'w:gz', compresslevel=1) as archive:
        for i, item in enumerate(summary['tasks']):
            for check in item['checks']:
                if check.get('log'):
                    archive.add(check['log'], arcname=f"{item['task']}/{check['check']}.log", recursive=False)
            if i % 1000 == 0:
                print(f'Packed logs for {i} tasks', flush=True)
    files = ['summary.json', 'contract.json', manifest, 'checker.py', 'logs.tar.gz']
    meta = {'source_contract_sha256': contract['sha256'], 'checkpoint_tasks': len(summary['tasks']),
            'files': {name: sha(destination / name) for name in files}}
    (destination / 'checkpoint.json').write_text(json.dumps(meta, indent=2) + '\n')
    print(f'Checkpoint ready: {destination}', flush=True)


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    for name in ('contract', 'summary', 'code', 'destination'):
        ap.add_argument(name, type=Path)
    args = ap.parse_args()
    capture(args.contract, args.summary, args.code, args.destination)
