"""Run a pinned ten-task SWE-Gym or AlgoTune conversion, without containers."""
import argparse
import ast
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import random
import subprocess
import sys
import tomllib

HARBOR_REVISION = '8d7e24e4e90c7e0b00eb283ceb554b09b9e7ecad'
SWEGYM_REVISION = 'bb94ed9e39bbeb96a7fcbfb533b80f25a7fd59cb'
ALGOTUNE_REVISION = 'dff9914c10800c7a031c9e8c3d4d1c8cd1b38906'


def hashes(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob('*')) if p.is_file() and '__pycache__' not in p.parts}


def save(path, data):
    path.write_text(json.dumps(data, indent=2) + '\n')


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('dataset', choices=['swegym', 'algotune'])
    ap.add_argument('--base', type=Path, default=Path('/data/horse/ws/frwe188h-trp-shared/conversion-pilots'))
    ap.add_argument('--out', type=Path, required=True)
    args = ap.parse_args()
    base, out = args.base.resolve(), args.out.resolve()
    for path in (base, out):
        if not any(path.is_relative_to(p) for p in map(Path, ['/data/horse', '/data/ws', '/data/cat'])):
            raise ValueError('Use cluster workspace storage')
    out.mkdir(parents=True, exist_ok=False)
    tasks = out / 'tasks'
    tasks.mkdir()
    adapter_root = base / 'upstream/adapters' / args.dataset
    provenance = {'dataset': args.dataset, 'adapter_revision': HARBOR_REVISION,
                  'adapter_files_sha256': hashes(adapter_root), 'seed': 42,
                  'validation': 'conversion and file checks only; no container or reward validation'}
    if args.dataset == 'swegym':
        sys.path.insert(0, str(adapter_root))
        import adapter
        from datasets import load_dataset
        source = base / 'upstream/swegym/train.parquet'
        # Supply the pinned local upstream data to the otherwise unchanged adapter.
        adapter.load_dataset = lambda name: load_dataset('parquet', data_files={'train': str(source)})
        converter = adapter.SWEGymToHarbor(tasks, dataset='full')
        buckets = defaultdict(list)
        for record in converter.loader.all_records():
            buckets[record['repo']].append(record['instance_id'])
        rng = random.Random(42)
        repos = sorted(buckets)
        rng.shuffle(repos)
        selected = []
        for repo in repos:
            ids = sorted(buckets[repo])
            rng.shuffle(ids)
            buckets[repo] = ids
        while len(selected) < 10:
            for repo in repos:
                if buckets[repo] and len(selected) < 10:
                    selected.append(buckets[repo].pop())
        provenance.update(source='SWE-Gym/SWE-Gym', source_revision=SWEGYM_REVISION,
                          source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                          selection='seeded round-robin across source repositories',
                          source_repositories={i: converter.loader.get_raw(i)['repo'] for i in selected})
        def generate(name):
            converter.generate_task(name, name)
    else:
        sys.path.insert(0, str(adapter_root / 'src'))
        from algotune.adapter import AlgoTuneAdapter
        source = base / 'upstream/algotune'
        revision = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip()
        if revision != ALGOTUNE_REVISION:
            raise ValueError(f'Unexpected AlgoTune revision: {revision}')
        converter = AlgoTuneAdapter(task_dir=tasks, cloned_repo_root=source, generate_size=False)
        families = converter.algotune_data.get_task_list()
        selected = random.Random(42).sample(sorted(families), 10)
        provenance.update(source='https://github.com/oripress/AlgoTune', source_revision=revision,
                          selection='ten distinct problem families, seeded sample',
                          calibration='upstream sizes; Docker recalibration disabled')
        def generate(name):
            converter.generate_task(name, source / 'AlgoTuneTasks' / name / f'{name}.py')
    provenance['selected_task_ids'] = selected
    save(out / 'source.json', provenance)
    errors = []
    for name in selected:
        try:
            generate(name)
            print('Generated', name, flush=True)
        except Exception as exc:
            errors.append({'task': name, 'error': f'{type(exc).__name__}: {exc}'})
            print('FAILED', name, errors[-1]['error'], flush=True)
    checks = []
    for task in sorted(tasks.iterdir()):
        failures = []
        for name in ['instruction.md', 'task.toml', 'environment/Dockerfile', 'tests/test.sh', 'solution/solve.sh']:
            if not (task / name).is_file() or not (task / name).stat().st_size:
                failures.append(f'missing/empty {name}')
        try:
            tomllib.loads((task / 'task.toml').read_text())
            for script in task.rglob('*.sh'):
                result = subprocess.run(['bash', '-n', str(script)], capture_output=True, text=True)
                if result.returncode:
                    failures.append(f'{script.relative_to(task)}: {result.stderr}')
            for script in task.rglob('*.py'):
                ast.parse(script.read_text(), filename=str(script))
        except Exception as exc:
            failures.append(str(exc))
        checks.append({'task': task.name, 'failures': failures, 'sha256': hashes(task)})
    report = {'selected': len(selected), 'generated': len(checks), 'conversion_errors': errors,
              'file_checks_passed': sum(not c['failures'] for c in checks), 'tasks': checks,
              'container_validation_run': False}
    save(out / 'conversion-report.json', report)
    print(json.dumps({k: v for k, v in report.items() if k != 'tasks'}, indent=2))
    return int(bool(errors) or len(checks) != 10 or any(c['failures'] for c in checks))


if __name__ == '__main__':
    raise SystemExit(main())
