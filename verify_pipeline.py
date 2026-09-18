#!/usr/bin/env python3
"""Run every check in verify/ against a dataset, cheapest first.

Each check is also a normal script you can run alone; this is only the sequencer.

  tests        pytest over tests/: the patcher's rules and the run layer.
  reward       verify/check_reward.py - each task's own verifier scores the
               reference 1 and nonsense 0, on this machine.
  images       verify/check_images.py - how many distinct container images the
               dataset needs (build time and image-cache pressure).
  reproduce    verify/check_reproducible.py - the patcher still produces exactly
               the published tasks. Needs --parquet and the dataset's
               published_digests.json.
  sandbox      verify/check_reward_harbor.py - builds the image, runs the task's
               own tests/test.sh in the container, reference 1 and garbage 0.
  isolation    verify/check_isolation.py - two containers at once cannot see
               each other's files, cgroups or loopback.
  model        submits a real teacher run (default Qwen3-Coder-30B-A3B-Instruct,
               8 attempts per task) and says how to report pass@k afterwards.

  python verify_pipeline.py <check|all> <dir with tasks/> [options]

`tests`, `reward` and `images` need nothing but this repo. `reproduce` needs the
patched parquet. `sandbox` and
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
from hpc.clusters import CLUSTERS, detect_cluster  # noqa: E402
from teacher_traces.models import resolve  # noqa: E402
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
    return needs_tasks(a) or (None if 'APPTAINER_BRIDGE_URL' in os.environ
                              else 'no bridge running (submit through hpc/<cluster>/run_pilot.sbatch)')


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
    if not a.parquet:
        return SKIP, 'no --parquet given'
    digests = HERE / f'data/{dataset_of(a)}/published_digests.json'
    if not digests.exists():
        return SKIP, f'{digests.relative_to(HERE)} does not exist'
    return run([PY, HERE / 'verify/check_reproducible.py', *a.parquet, '--expect', digests])


def check_sandbox(a):
    return run([PY, HERE / 'verify/check_reward_harbor.py', a.tasks, a.out / 'sandbox',
                *(['--limit', a.limit] if a.limit else [])])


def check_isolation(a):
    task = sorted((a.tasks / 'tasks').iterdir())[0]
    return run([PY, HERE / 'verify/check_isolation.py', task, a.out / 'isolation'])


def check_model(a):
    cluster = detect_cluster() if a.cluster is None else next(
        (c for c in CLUSTERS if c.name == a.cluster), None)
    name = a.cluster or (cluster.name if cluster else None)
    if not name:
        return SKIP, 'no cluster matched this host; pass --cluster'
    sbatch = HERE / f'hpc/{name}/run_pilot.sbatch'
    if not sbatch.exists():
        return SKIP, f'no launcher for cluster {name}'
    # Resources are submit-time arguments, not header lines: hpc/clusters.py holds
    # them per cluster (Helma rejects a GPU-partition job without --gres).
    gpus = resolve(a.model)[1].gpus
    extra = [a.gres] if a.gres else (cluster.submit_args(gpus) if cluster else [])
    rc = run(['sbatch', *extra, sbatch, a.model, a.stage],
             env={**os.environ, 'PILOT_ATTEMPTS': str(a.attempts)})
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
    ap.add_argument('--out', type=Path, default=Path('verify-out'), help='where check outputs are written')
    ap.add_argument('--limit', type=int, help='grade only the first N tasks')
    ap.add_argument('--max-images', type=int, help='fail if the dataset needs more distinct images than this')
    ap.add_argument('--cluster', help='which hpc/<cluster>/ to submit to (default: detected from the hostname)')
    ap.add_argument('--dataset', help='defaults to the part of the task name before the first "-"')
    ap.add_argument('--model', default='coder-30b', help='model key, see teacher_traces/models.py')
    ap.add_argument('--attempts', type=int, default=8, help='attempts per task (default 8)')
    ap.add_argument('--stage', default='diag', help='smoke, diag, sweep or full tasks per language')
    ap.add_argument('--gres', help="override the cluster's GPU request, e.g. '--gres=gpu:h200:2'")
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
            print(f'{name}: SKIPPED ({rc[1]})')
            skipped.append(name)
        elif rc != 0:
            sys.exit(f'{name} FAILED (exit {rc})')
    done = [n for n in names if n not in skipped]
    print(f'\nPassed: {", ".join(done) or "nothing"}'
          + (f'. Skipped: {", ".join(skipped)}' if skipped else ''))


if __name__ == '__main__':
    main()
