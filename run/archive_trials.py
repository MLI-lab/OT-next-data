"""Save completed Harbor trials from a node-local run directory into compressed batches.

Shared storage has a small file-count quota, so trial directories never live
there. This loop finds finished trials (a top-level result.json that has not
changed for --settle seconds), writes them into <dest>/archives/trials-NNNN.tar.gz
with paths rooted at <run-id>/ (the layout of the smoke archives), verifies
the batch, and records progress in <dest>/progress.json. --final also archives
everything else in the work directory (task copies, oracle results, metadata,
vLLM/Harbor logs) as final.tar.gz. Batches are independent: work since the
last batch is lost if the node dies before the next one.
"""
from __future__ import annotations
import argparse
import datetime as dt
import hashlib
import json
import os
import tarfile
import time
from pathlib import Path


def now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec='seconds')


def finished_trials(work: Path, settle: float) -> list[Path]:
    out = []
    cutoff = time.time() - settle
    for job in work.glob('traces/*/*/harbor_jobs/*'):
        if not job.is_dir():
            continue
        for trial in job.iterdir():
            result = trial / 'result.json'
            if trial.is_dir() and result.is_file() and result.stat().st_mtime <= cutoff:
                out.append(trial)
    return sorted(out)


def reward_of(result_path: Path):
    try:
        data = json.loads(result_path.read_text())
    except (OSError, json.JSONDecodeError):
        return 'unreadable'
    if data.get('exception_info'):
        return 'error'
    reward = ((data.get('verifier_result') or {}).get('rewards') or {}).get('reward')
    return reward if reward is not None else 'none'


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def write_batch(work: Path, run_id: str, paths: list[Path], target: Path, exclude: set[str] = frozenset()) -> dict:
    """Tar the given paths (relative to work) under run_id/, then verify by re-reading."""
    tmp = target.with_name(target.name + '.partial')
    with tarfile.open(tmp, 'w:gz', compresslevel=6) as tar:
        for path in paths:
            rel = path.relative_to(work)
            tar.add(path, arcname=f'{run_id}/{rel}', recursive=True,
                    filter=lambda m: None if any(m.name.startswith(f'{run_id}/{e}') for e in exclude) else m)
    members = files = 0
    with tarfile.open(tmp, 'r:gz') as tar:
        for m in tar:
            members += 1
            if m.isfile():
                files += 1
                source = work / Path(m.name).relative_to(run_id)
                assert hashlib.sha256(tar.extractfile(m).read()).hexdigest() == sha256_file(source), m.name
    tmp.rename(target)
    return {'file': target.name, 'sha256': sha256_file(target), 'bytes': target.stat().st_size,
            'members': members, 'files': files, 'created': now()}


def load_progress(dest: Path, run_id: str) -> dict:
    path = dest / 'progress.json'
    if path.is_file():
        return json.loads(path.read_text())
    return {'run_id': run_id, 'batches': [], 'archived_trials': [], 'rewards': {}, 'final': None, 'updated': None}


def save_progress(dest: Path, progress: dict) -> None:
    progress['updated'] = now()
    progress['n_archived_trials'] = len(progress['archived_trials'])
    tmp = dest / 'progress.json.tmp'
    tmp.write_text(json.dumps(progress, indent=2) + '\n')
    tmp.rename(dest / 'progress.json')


def archive_once(work: Path, dest: Path, run_id: str, settle: float) -> int:
    progress = load_progress(dest, run_id)
    archived = set(progress['archived_trials'])
    new = [t for t in finished_trials(work, settle) if str(t.relative_to(work)) not in archived]
    if not new:
        return 0
    archives = dest / 'archives'
    archives.mkdir(exist_ok=True)
    target = archives / f'trials-{len(progress["batches"]):04d}.tar.gz'
    entry = write_batch(work, run_id, new, target)
    entry['trials'] = [str(t.relative_to(work)) for t in new]
    entry['rewards'] = {str(t.name): reward_of(t / 'result.json') for t in new}
    for reward in entry['rewards'].values():
        progress['rewards'][str(reward)] = progress['rewards'].get(str(reward), 0) + 1
    progress['batches'].append(entry)
    progress['archived_trials'].extend(entry['trials'])
    save_progress(dest, progress)
    print(f'{now()} archived {len(new)} trials into {target.name}', flush=True)
    return len(new)


def archive_final(work: Path, dest: Path, run_id: str) -> None:
    archive_once(work, dest, run_id, settle=0)
    progress = load_progress(dest, run_id)
    archives = dest / 'archives'
    archives.mkdir(exist_ok=True)
    exclude = set(progress['archived_trials'])
    paths = sorted(p for p in work.iterdir())
    entry = write_batch(work, run_id, paths, archives / 'final.tar.gz', exclude=exclude)
    entry['excluded_archived_trials'] = len(exclude)
    progress['final'] = entry
    save_progress(dest, progress)
    print(f'{now()} final archive written: {entry}', flush=True)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--work', type=Path, required=True)
    p.add_argument('--dest', type=Path, required=True, help='shared-storage run directory')
    p.add_argument('--run-id', default=None)
    p.add_argument('--interval', type=float, default=600)
    p.add_argument('--settle', type=float, default=60, help='seconds a result.json must be unchanged')
    p.add_argument('--once', action='store_true')
    p.add_argument('--final', action='store_true')
    a = p.parse_args()
    run_id = a.run_id or a.dest.name
    if a.final:
        archive_final(a.work, a.dest, run_id)
        return
    while True:
        archive_once(a.work, a.dest, run_id, a.settle)
        if a.once:
            return
        time.sleep(a.interval)


if __name__ == '__main__':
    main()
