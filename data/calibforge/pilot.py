"""Freeze the original CalibForge ten-task CPU pilot and submit stages 1,3,4,5."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import random
import shlex
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
REVISION = 'fb1e75441a94b8bb0ced08acd6b59e711704d70a'
SOURCE = 'AweAI-Team/CalibForge'
CATEGORIES = ['software-engineering', 'system-administration', 'scientific-computing',
              'security', 'data-science', 'file-operations', 'debugging',
              'data-processing', 'mathematics', 'data-querying']


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--base', type=Path, default=Path('/data/horse/ws/frwe188h-trp-shared/calibforge'))
    ap.add_argument('--source', type=Path, help='original task tree; defaults to upstream/repo')
    ap.add_argument('--input', type=Path, help='explicit patched ten-task directory for reruns')
    ap.add_argument('--submit', action='store_true')
    ap.add_argument('--cluster', choices=['barnard', 'romeo', 'julia'], default='romeo')
    options = ap.parse_args()
    base = options.base.resolve()
    if not any(base.is_relative_to(p) for p in map(Path, ['/data/horse', '/data/ws', '/data/cat'])):
        raise ValueError('Use a cluster data workspace')
    upstream = base / 'upstream'
    source = (options.source or upstream / 'repo').resolve()
    repository = json.loads((upstream / 'repository.json').read_text())
    if repository['sha'] != REVISION:
        raise ValueError('Unexpected source revision')
    rows = [json.loads(s) for s in (upstream / 'metadata.jsonl').read_text().splitlines()]
    rng = random.Random(42)
    selected = []
    for i, category in enumerate(CATEGORIES):
        subset = 'multi_solver' if i % 2 else 'contrastive_solver'
        candidates = sorted((r for r in rows if r['category'] == category and r['subset'] == subset),
                            key=lambda r: r['task_id'])
        selected.append(rng.choice(candidates))
    run = base / 'runs' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    run.mkdir(parents=True)
    tasks = run / 'input/tasks'
    tasks.mkdir(parents=True)
    records = []
    for row in selected:
        src = options.input / row['task_id'] if options.input else source / row['task_path']
        dest = tasks / row['task_id']
        shutil.copytree(src, dest)
        hashes = {}
        for file in sorted(dest.rglob('*')):
            if not file.is_file():
                continue
            content = file.read_bytes()
            if content.startswith(b'version https://git-lfs.github.com/spec/v1\n'):
                raise ValueError(f'Unfetched LFS object: {file}')
            hashes[str(file.relative_to(dest))] = hashlib.sha256(content).hexdigest()
        records.append({**row, 'files_sha256': hashes,
                        'has_oracle': (dest / 'solution/solve.sh').is_file()})
    manifest = {'source': SOURCE, 'revision': REVISION, 'seed': 42,
                'method': 'one random task per listed CPU-oriented category; alternating subsets; no outcome filtering',
                'categories': CATEGORIES, 'tasks': records, 'source_tree': str(source),
                'patched_input': str(options.input) if options.input else None}
    (run / 'selection.json').write_text(json.dumps(manifest, indent=2) + '\n')
    sys.path.insert(0, str(ROOT))
    from hpc.helma.validation_submit import snapshot_code
    code = snapshot_code(run)
    (code / 'data/calibforge').mkdir(parents=True)
    shutil.copy2(__file__, code / 'data/calibforge/pilot.py')
    (code / 'hpc/zih').mkdir(parents=True)
    for name in ['calibforge_validation.py', 'calibforge_validation.sbatch', 'storage.sh', 'runtime_storage.py']:
        shutil.copy2(ROOT / 'hpc/zih' / name, code / 'hpc/zih' / name)
    command = ['sbatch', '--parsable', f'--partition={options.cluster}',
               f'--chdir={code}', f'--output={run}/slurm-%j.out',
               str(code / 'hpc/zih/calibforge_validation.sbatch'), str(run), str(base)]
    if options.cluster != 'barnard':
        host = 'login1.romeo.hpc.tu-dresden.de' if options.cluster == 'romeo' else 'julia.hpc.tu-dresden.de'
        command = ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8', host, shlex.join(command)]
    record = {'run': str(run), 'command': command, 'stages': [1, 3, 4, 5],
              'cpus': 4, 'concurrency': 1, 'tasks': 10, 'status': 'prepared', 'cluster': options.cluster}
    (run / 'submission.json').write_text(json.dumps(record, indent=2) + '\n')
    if options.submit:
        record.update(job_id=subprocess.check_output(command, text=True).strip(), status='submitted')
        (run / 'submission.json').write_text(json.dumps(record, indent=2) + '\n')
    print(json.dumps(record, indent=2))


if __name__ == '__main__':
    main()
