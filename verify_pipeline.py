#!/usr/bin/env python3
"""Run every check in verify/ against a dataset, cheapest first.

Each check is also a normal script you can run alone; this is only the sequencer.

  tests        pytest over tests/: the patcher's rules and the run layer.
  reward       verify/check_reward.py - each task's own verifier scores the
               reference 1 and nonsense 0, on this machine.
  images       verify/check_images.py - how many distinct container images the
               dataset needs (build time and image-cache pressure).
  reproduce    verify/check_reproducible.py - the patcher still produces exactly
               the published tasks. Needs --parquet (what you patched) and
               --reference (the published parquets, downloaded).
  sandbox      verify/check_reward_harbor.py - builds the image, runs the task's
               own tests/test.sh in the container, reference 1 and garbage 0.
               Needs a bridge, so outside a job it submits hpc/<cluster>/
               checks.sbatch, which runs it and the isolation check together.
  isolation    verify/check_isolation.py - two containers at once cannot see
               each other's files, cgroups or loopback. Runs in that same job.
  model        lets an agent solve tasks: submits a real run (default coder-30b,
               8 attempts per task) with this cluster's GPU request and
               concurrency. --time HH:MM:SS is required, because a job that
               reserves more than it needs waits longer in the queue.
               --dry-run prints the command instead of submitting.

  python verify_pipeline.py <check|all> <dir with tasks/> [options]

`tests`, `reward` and `images` need nothing but this repo. `reproduce` needs the
patched parquets and the published ones to compare with. `sandbox` and
`isolation` need a running bridge, so they belong inside a Slurm job. `model`
submits one. Under `all`, a check whose inputs are missing is skipped with a
reason instead of failing.
"""
from __future__ import annotations
import argparse
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from config.clusters import CLUSTERS, detect_cluster  # noqa: E402
from config.models import resolve  # noqa: E402
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


def needs_bridge(a):
    return needs_tasks(a)


def check_tests(a):
    return run([PY, '-m', 'pytest', HERE / 'tests', '-q'], env={**os.environ, 'PYTHONPATH': str(HERE)})


def dataset_of(a):
    """Dataset name from --dataset, else from the first task (crosscodeeval-python-0001)."""
    if a.dataset:
        return a.dataset
    tasks = sorted((a.tasks / 'tasks').iterdir()) if a.tasks and (a.tasks / 'tasks').is_dir() else []
    return tasks[0].name.split('-')[0] if tasks else ''


def check_reward(a):
    return run([PY, HERE / 'verify/check_reward.py', a.tasks, *(['--limit', a.limit] if a.limit else [])])


def check_images(a):
    return run([PY, HERE / 'verify/check_images.py', a.tasks,
                *(['--max-images', a.max_images] if a.max_images else [])])


def check_reproduce(a):
    if not (a.parquet and a.reference):
        return SKIP, 'needs --parquet (what you patched) and --reference (the published parquets)'
    return run([PY, HERE / 'verify/check_reproducible.py', *a.parquet, '--reference', *a.reference])


def submit_checks(a):
    """Both sandbox checks need a bridge, which only exists inside a job: submit one."""
    cluster, name = cluster_for(a)
    sbatch = HERE / f'hpc/{name}/checks.sbatch' if name else None
    if not sbatch or not sbatch.exists():
        return SKIP, f'no checks job for cluster {name}'
    extra = [a.gres] if a.gres else (cluster.submit_args(1) if cluster else [])
    extra += ['--time', a.time or '00:30:00']
    if 'PILOT_ROOT' in os.environ:
        logs = Path(os.environ['PILOT_ROOT']) / 'logs'
        logs.mkdir(parents=True, exist_ok=True)
        extra += [f'--output={logs}/slurm-%j.out']
    cmd = ['sbatch', *extra, sbatch, a.tasks, str(a.limit or 3)]
    if a.dry_run:
        print('\n$ ' + ' '.join(str(c) for c in cmd))
        return 0
    return run(cmd, env={**os.environ, 'OT_NEXT_DATA': str(HERE)})


def check_sandbox(a):
    if 'APPTAINER_BRIDGE_URL' not in os.environ:      # not inside a job: submit one
        return submit_checks(a)
    return run([PY, HERE / 'verify/check_reward_harbor.py', a.tasks, a.out / 'sandbox', '--extras',
                *(['--limit', a.limit] if a.limit else [])])


def check_isolation(a):
    if 'APPTAINER_BRIDGE_URL' not in os.environ:
        return SKIP, 'submitted together with the sandbox check (hpc/<cluster>/checks.sbatch)'
    task = sorted((a.tasks / 'tasks').iterdir())[0]
    return run([PY, HERE / 'verify/check_isolation.py', task, a.out / 'isolation'])


def cluster_for(a):
    cluster = detect_cluster() if a.cluster is None else next(
        (c for c in CLUSTERS if c.name == a.cluster), None)
    return cluster, (a.cluster or (cluster.name if cluster else None))


def check_model(a):
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
    if not (Path(otagent) / 'data/teacher_ranking_proxy/generate_trajectories.py').exists():
        return SKIP, (f'OTAGENT_ROOT={otagent} has no data/teacher_ranking_proxy/'
                      'generate_trajectories.py (source env.sh, or set it to the checkout)')
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
    cmd = ['sbatch', *extra, sbatch, a.model, a.stage]
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
              f'  python {HERE}/verify/pass_at_k.py $PILOT_ROOT/runs/<run-id> --k 1 {a.attempts}\n'
              f'  python {HERE}/verify/plot_pass_rates.py "<model>=<run dir>" -o pass_rates.png')
    return rc


# name -> (function, precondition)
CHECKS = {
    'tests': (check_tests, lambda a: None),
    'reward': (check_reward, needs_tasks),
    'images': (check_images, needs_tasks),
    'reproduce': (check_reproduce, lambda a: None),
    'sandbox': (check_sandbox, needs_bridge),
    'isolation': (check_isolation, needs_bridge),
    'model': (check_model, lambda a: None),
}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('check', choices=[*CHECKS, 'all'])
    ap.add_argument('tasks', nargs='?', type=Path, help='directory containing tasks/')
    ap.add_argument('--parquet', type=Path, nargs='+', help='patched parquet(s), for the reproduce check')
    ap.add_argument('--reference', type=Path, nargs='+', help='published parquet(s) the reproduce check compares against')
    ap.add_argument('--out', type=Path, default=Path('verify-out'), help='where check outputs are written')
    ap.add_argument('--limit', type=int, help='grade only the first N tasks')
    ap.add_argument('--max-images', type=int, help='fail if the dataset needs more distinct images than this')
    ap.add_argument('--cluster', help='which hpc/<cluster>/ to submit to (default: detected from the hostname)')
    ap.add_argument('--dataset', help='defaults to the part of the task name before the first "-"')
    ap.add_argument('--dataset-config', type=Path, help='JSON dataset inputs for teacher runs')
    ap.add_argument('--model', default='coder-30b', help='model key, see config/models.py')
    ap.add_argument('--attempts', type=int, default=8, help='attempts per task (default 8)')
    ap.add_argument('--stage', default='diag', choices=['smoke', 'diag', 'sweep', 'full'], help='1, 5, 25 or all tasks per group')
    ap.add_argument('--gres', help="override the cluster's GPU request, e.g. '--gres=gpu:h200:2'")
    ap.add_argument('--time', help='sbatch time limit, HH:MM:SS - required to submit')
    ap.add_argument('--dry-run', action='store_true', help='print the submit command instead of running it')
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)

    names = [*CHECKS] if a.check == 'all' else [a.check]
    skipped = []
    for name in names:
        fn, precondition = CHECKS[name]
        why = precondition(a)
        if why:
            if a.check != 'all':
                sys.exit(f'{name}: {why}')
            print(f'\n=== {name}: SKIPPED ({why})')
            skipped.append(name)
            continue
        print(f'\n=== {name}')
        rc = fn(a)
        if isinstance(rc, tuple):      # (SKIP, reason)
            if a.check != 'all':
                sys.exit(f'{name}: {rc[1]}')
            print(f'{name}: SKIPPED ({rc[1]})')
            skipped.append(name)
        elif rc != 0:
            sys.exit(f'{name} FAILED (exit {rc})')
    done = [n for n in names if n not in skipped]
    print(f'\nPassed: {", ".join(done) or "nothing"}'
          + (f'. Skipped: {", ".join(skipped)}' if skipped else ''))


if __name__ == '__main__':
    main()
