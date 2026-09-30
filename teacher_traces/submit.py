#!/usr/bin/env python3
"""Submit a teacher run: an agent solves a dataset's tasks to generate trajectories.

Submits hpc/<cluster>/teacher_traces.sbatch with this cluster's GPU request and
concurrency. --time HH:MM:SS is required, because a job that reserves more than
it needs waits longer in the queue. --dry-run prints the command instead.

  python teacher_traces/submit.py --model coder-30b --tasks-per-group 1 --time 00:45:00
  python teacher_traces/submit.py --dataset-config /path/to/dataset.json ...
"""
from __future__ import annotations
import argparse
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from config.clusters import CLUSTERS, detect_cluster  # noqa: E402
from config.models import resolve  # noqa: E402
SKIP = 'skip'


def run(cmd, **kw):
    print(f'\n$ {" ".join(str(c) for c in cmd)}', flush=True)
    return subprocess.run([str(c) for c in cmd], **kw).returncode


def cluster_for(a):
    cluster = detect_cluster() if a.cluster is None else next(
        (c for c in CLUSTERS if c.name == a.cluster), None)
    return cluster, (a.cluster or (cluster.name if cluster else None))


def submit(a):
    dataset_config = getattr(a, 'dataset_config', None)
    if dataset_config:
        from run.dataset_config import load_dataset
        dataset_config = dataset_config.resolve()
        cfg = load_dataset(dataset_config)
        if a.dataset and a.dataset != cfg['dataset']:
            raise ValueError('--dataset disagrees with --dataset-config')
    elif a.dataset and a.dataset != 'crosscodeeval':
        raise ValueError('Teacher runs for a new dataset require --dataset-config')
    cluster, name = cluster_for(a)
    if not name:
        return SKIP, 'no cluster matched this host; pass --cluster'
    sbatch = HERE / f'hpc/{name}/teacher_traces.sbatch'
    if not sbatch.exists():
        return SKIP, f'no launcher for cluster {name}'
    # The launcher needs both of these on the node, and Slurm only passes on what
    # this shell has. Checking here turns a wasted queue slot into an error now.
    if 'PILOT_ROOT' not in os.environ:
        return SKIP, 'PILOT_ROOT is not set (source env.sh)'
    otagent = os.environ.get('OTAGENT_ROOT') or str(Path(os.environ['PILOT_ROOT']) / 'code')
    if not (Path(otagent) / 'data/local/run_tracegen.py').exists():
        return SKIP, (f'OTAGENT_ROOT={otagent} has no data/local/'
                      'run_tracegen.py (source env.sh, or set it to the checkout)')
    # Resources are submit-time arguments, not header lines: config/clusters.py holds
    # them per cluster (Helma rejects a GPU-partition job without --gres).
    gpus = resolve(a.model)[1].gpus
    extra = [a.gres] if a.gres else (cluster.submit_args(gpus) if cluster else [])
    if not a.time:
        return SKIP, 'pass --time HH:MM:SS (a job that reserves more than it needs waits longer)'
    extra += ['--time', a.time]
    # The launcher archives and stops when Slurm signals that time is nearly up.
    # The header's 900 s lead would fire 4 minutes into a 20 minute job (872626),
    # so scale it: a fifth of the limit, at most 15 minutes, at least a minute.
    parts = [int(x) for x in a.time.split(':')]
    total = parts[0] * 3600 + parts[1] * 60 + (parts[2] if len(parts) > 2 else 0)
    extra += [f'--signal=B:USR1@{max(60, min(900, total // 5))}']
    # Without this Slurm scatters the job log; keep them all in one place.
    if 'PILOT_ROOT' in os.environ:
        logs = Path(os.environ['PILOT_ROOT']) / 'logs'
        logs.mkdir(parents=True, exist_ok=True)
        extra += [f'--output={logs}/slurm-%j.out']
    env = {**os.environ, 'PILOT_ATTEMPTS': str(a.attempts), 'OTAGENT_ROOT': otagent,
           'OT_NEXT_DATA': str(HERE)}
    if cluster and 'PILOT_CONCURRENCY' not in os.environ:
        # A measurement for this model on this hardware beats the arithmetic
        # ceiling; it is already the total for this model's allocation.
        measured = resolve(a.model)[1].concurrency.get(cluster.hardware)
        env['PILOT_CONCURRENCY'] = str(measured.trials if measured else cluster.trials_in_flight(
            gpus, int(os.environ.get('PILOT_TRIAL_CPUS', 1))))
    cmd = ['sbatch', *extra, sbatch, a.model, a.tasks_per_group]
    if dataset_config:
        cmd.append(dataset_config)
    if a.dry_run:
        print('\n$ ' + ' '.join(str(c) for c in cmd)
              + f"   (PILOT_ATTEMPTS={env['PILOT_ATTEMPTS']}"
              + (f", PILOT_CONCURRENCY={env['PILOT_CONCURRENCY']}" if 'PILOT_CONCURRENCY' in env else '') + ')')
        return 0
    rc = run(cmd, env=env)
    if rc == 0:
        print(f'\nSubmitted. When it finishes:\n'
              f'  python {HERE}/validation/verify/pass_at_k.py $PILOT_ROOT/runs/<run-id> --k 1 {a.attempts}\n'
              f'  python {HERE}/validation/verify/plot_pass_rates.py "<model>=<run dir>" -o pass_rates.png')
    return rc


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--cluster', help='which hpc/<cluster>/ to submit to (default: detected from the hostname)')
    ap.add_argument('--dataset', help='must match --dataset-config when both are given')
    ap.add_argument('--dataset-config', type=Path, help='JSON dataset inputs; default is the CrossCodeEval setup')
    ap.add_argument('--model', default='coder-30b', help='model key, see config/models.py')
    ap.add_argument('--attempts', type=int, default=8, help='attempts per task (default 8)')
    ap.add_argument('--tasks-per-group', default='all', help='how many tasks of each group to run, from the start of the group; a number or all (default)')
    ap.add_argument('--gres', help="override the cluster's GPU request, e.g. '--gres=gpu:h200:2'")
    ap.add_argument('--time', help='sbatch time limit, HH:MM:SS - required to submit')
    ap.add_argument('--dry-run', action='store_true', help='print the submit command instead of running it')
    a = ap.parse_args()
    if not (a.tasks_per_group == 'all' or a.tasks_per_group.isdigit() and int(a.tasks_per_group) > 0):
        ap.error('--tasks-per-group must be a positive number or all')
    try:
        rc = submit(a)
    except ValueError as exc:
        ap.error(str(exc))
    if isinstance(rc, tuple):
        sys.exit('Not submitted: ' + rc[1])
    sys.exit(rc)


if __name__ == '__main__':
    main()
