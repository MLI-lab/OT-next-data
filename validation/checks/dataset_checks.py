#!/usr/bin/env python3
"""Run image-diversity, reproduction and container-isolation checks."""
from __future__ import annotations
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(HERE))
from config.clusters import detect_cluster  # noqa: E402
PY = sys.executable
SKIP = 'skip'


def run(cmd, **kw):
    print(f'\n$ {" ".join(str(c) for c in cmd)}', flush=True)
    return subprocess.run([str(c) for c in cmd], **kw).returncode


def needs_tasks(a):
    if not a.tasks:
        return 'no task directory given'
    if not (a.tasks / 'tasks').is_dir():
        return f'{a.tasks}/tasks does not exist'
    return None


def check_images(a):
    return run([PY, HERE / 'validation/checks/check_images.py', a.tasks,
                *(['--max-images', a.max_images] if a.max_images else [])])


def check_reproduce(a):
    if not (a.parquet and a.reference):
        return SKIP, 'needs --parquet (what you patched) and --reference (the published parquets)'
    return run([PY, HERE / 'validation/checks/check_reproducible.py', *a.parquet, '--reference', *a.reference])


def submit_checks(a):
    """The isolation check needs a bridge, which only exists inside a job: submit one."""
    name = a.cluster
    if name is None:
        cluster = detect_cluster()
        name = cluster.name if cluster else None
    sbatch = HERE / f'hpc/{name}/isolation.sbatch' if name else None
    if not sbatch or not sbatch.exists():
        return SKIP, f'no checks job for cluster {name}'
    extra = [a.gres] if a.gres else []
    extra += ['--time', a.time or '00:30:00']
    if 'OT_WORKSPACE' in os.environ:
        logs = Path(os.environ['OT_WORKSPACE']) / 'logs'
        logs.mkdir(parents=True, exist_ok=True)
        extra += [f'--output={logs}/slurm-%j.out']
    cmd = ['sbatch', *extra, sbatch, a.tasks]
    if a.dry_run:
        print('\n$ ' + ' '.join(str(c) for c in cmd))
        return 0
    return run(cmd, env={**os.environ, 'OT_NEXT_DATA': str(HERE)})


def check_isolation(a):
    if 'APPTAINER_BRIDGE_URL' not in os.environ:      # not inside a job: submit one
        return submit_checks(a)
    task = sorted((a.tasks / 'tasks').iterdir())[0]
    return run([PY, HERE / 'validation/checks/check_isolation.py', task, a.out / 'isolation'])


# name -> (function, precondition)
CHECKS = {
    'images': (check_images, needs_tasks),
    'reproduce': (check_reproduce, lambda a: None),
    'isolation': (check_isolation, needs_tasks),
}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('check', choices=[*CHECKS, 'all'])
    ap.add_argument('tasks', nargs='?', type=Path, help='directory containing tasks/')
    ap.add_argument('--parquet', type=Path, nargs='+', help='patched parquet(s), for the reproduce check')
    ap.add_argument('--reference', type=Path, nargs='+', help='published parquet(s) the reproduce check compares against')
    ap.add_argument('--out', type=Path, default=Path('verify-out'), help='where check outputs are written')
    ap.add_argument('--max-images', type=int, help='fail if the dataset needs more distinct images than this')
    ap.add_argument('--fail-fast', action='store_true', help='stop all at its first failed stage (default: collect results)')
    ap.add_argument('--cluster', help='which hpc/<cluster>/ to submit to (default: detected from the hostname)')
    ap.add_argument('--gres', help="override the cluster's GPU request, e.g. '--gres=gpu:h200:2'")
    ap.add_argument('--time', help='sbatch time limit for the isolation job, HH:MM:SS (default 00:30:00)')
    ap.add_argument('--dry-run', action='store_true', help='print the submit command instead of running it')
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)

    names = [*CHECKS] if a.check == 'all' else [a.check]
    report = {'requested': a.check, 'stages': [], 'complete': False}
    summary = a.out / 'pipeline-summary.json'
    def save():
        summary.write_text(json.dumps(report, indent=2) + '\n')
    save()
    for name in names:
        fn, precondition = CHECKS[name]
        entry = {'stage': name}
        print(f'\n=== {name}')
        try:
            why = precondition(a)
            rc = (SKIP, why) if why else fn(a)
            if isinstance(rc, tuple):
                entry.update(status='skipped', reason=rc[1])
            elif rc != 0:
                entry.update(status='failed', exit_code=rc)
            else:
                status = 'passed'
                if name == 'isolation' and 'APPTAINER_BRIDGE_URL' not in os.environ:
                    status = 'previewed' if a.dry_run else 'submitted'
                entry.update(status=status, exit_code=0)
        except Exception as exc:
            entry.update(status='error', reason=f'{type(exc).__name__}: {exc}')
        report['stages'].append(entry)
        save()
        print(f"{name}: {entry['status']}" + (f" ({entry['reason']})" if 'reason' in entry else ''))
        if entry['status'] in ('failed', 'error') and a.fail_fast:
            break
    report['complete'] = len(report['stages']) == len(names)
    report['has_failures'] = any(e['status'] in ('failed', 'error') for e in report['stages'])
    save()
    print('\nResults: ' + ', '.join(f"{e['stage']}={e['status']}" for e in report['stages']))
    print(f'Report: {summary}')
    if report['has_failures'] or (a.check != 'all' and report['stages'][0]['status'] == 'skipped'):
        sys.exit(1)


if __name__ == '__main__':
    main()
