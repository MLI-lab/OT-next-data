"""Preserve a running validation checkpoint before cancelling its allocation."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tarfile
import time
from contextlib import ExitStack

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from validation.contract import read, task_records, task_digest
from validation.static_resume import sha


def pack_logs(tasks, destination):
    """Preserve shared logs once, including logs inherited from an older resume."""
    with ExitStack() as stack:
        output = stack.enter_context(tarfile.open(destination, 'w:gz', compresslevel=1))
        sources, saved = {}, {}
        for item in tasks:
            for check in item['checks']:
                source = check.get('log')
                if not source:
                    continue
                name = f"{item['task']}/{check['check']}.log"
                if source in saved:
                    link = tarfile.TarInfo(name)
                    link.type, link.linkname = tarfile.LNKTYPE, saved[source]
                    output.addfile(link)
                elif '!/' in source:
                    path, member = source.split('!/', 1)
                    if path not in sources:
                        sources[path] = stack.enter_context(tarfile.open(path, 'r:*'))
                    info = sources[path].getmember(member)
                    payload = sources[path].extractfile(info)
                    if payload is None:
                        raise ValueError(f'Checkpoint log is not a file: {source}')
                    with payload:
                        info = tarfile.TarInfo(name)
                        info.size = sources[path].getmember(member).size
                        # extractfile follows hard links, whose own size is zero.
                        if sources[path].getmember(member).islnk():
                            payload.seek(0, 2); info.size = payload.tell(); payload.seek(0)
                        output.addfile(info, payload)
                else:
                    output.add(source, arcname=name, recursive=False)
                saved.setdefault(source, name)


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
    journal = summary_path.parent / 'outcomes.jsonl'
    if journal.is_file():
        from validation.verify.check_terminal_bench import rebuild_summary
        shutil.copyfile(journal, destination / journal.name)
        summary = rebuild_summary(destination)
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
    pack_logs(summary['tasks'], destination / 'logs.tar.gz')
    files = ['summary.json', 'contract.json', manifest, 'checker.py', 'logs.tar.gz']
    if journal.is_file():
        files.append('outcomes.jsonl')
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
