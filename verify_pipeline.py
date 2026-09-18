#!/usr/bin/env python3
"""Verify a data pipeline end to end, in four checks of rising cost.

  python verify_pipeline.py tests            seconds   unit tests of the patcher and run layer
  python verify_pipeline.py reward <dir>     ~1 min    gold scores 1, nonsense scores 0 (this machine)
  python verify_pipeline.py sandbox <dir>    minutes   the same, but built and run through Harbor
  python verify_pipeline.py model            ~1 h      run a model and report pass@k
  python verify_pipeline.py all <dir>                  in order, stopping at the first failure

`tests` and `reward` need no GPU and no cluster; `sandbox` needs the bridge
server and bridge worker of a Slurm job; `model` submits one (default:
Qwen3-Coder-30B-A3B-Instruct, 8 attempts per task, 5 tasks per language, sampling
from the model card) and is the only check that exercises the whole chain
including the agent.
"""
from __future__ import annotations
import argparse
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PY = sys.executable


def run(cmd, **kw):
    print(f'\n$ {" ".join(str(c) for c in cmd)}', flush=True)
    return subprocess.run([str(c) for c in cmd], **kw).returncode


def check_tests(a):
    """Unit tests: the patcher's rules, and the run layer when OTAGENT_ROOT is set."""
    return run([PY, '-m', 'pytest', HERE / 'tests', '-q'], env={**os.environ, 'PYTHONPATH': str(HERE)})


def check_reward(a):
    """Every reference solution scores 1, every nonsense answer 0 (this machine)."""
    if not a.tasks:
        sys.exit('reward: pass the directory that contains tasks/')
    cmd = [PY, HERE / 'verify/check_reward.py', a.tasks]
    if a.limit:
        cmd += ['--limit', a.limit]
    return run(cmd)


def check_sandbox(a):
    """Image builds, sandbox runs the task's own test.sh, reference 1 and garbage 0."""
    if not a.tasks:
        sys.exit('sandbox: pass the directory that contains tasks/')
    if 'APPTAINER_BRIDGE_URL' not in os.environ:
        sys.exit('sandbox: needs a running bridge (submit through hpc/<cluster>/run_pilot.sbatch)')
    return run([PY, HERE / 'verify/check_reward_harbor.py', a.tasks, a.out,
                *(['--limit', str(a.limit)] if a.limit else [])])


def check_model(a):
    """Submit a short teacher run; its oracle stage also proves the sandbox works."""
    sbatch = HERE / f'hpc/{a.cluster}/run_pilot.sbatch'
    if not sbatch.exists():
        sys.exit(f'no launcher for cluster {a.cluster}: {sbatch}')
    env = {**os.environ, 'PILOT_ATTEMPTS': str(a.attempts)}
    rc = run(['sbatch', sbatch, a.model, a.stage], env=env)
    if rc == 0:
        print(f'\nSubmitted. When it finishes:\n'
              f'  python {HERE}/verify/pass_at_k.py $PILOT_ROOT/runs/<run-id> --k 1 {a.attempts}\n'
              f'  python {HERE}/verify/plot_pass_rates.py "<model>=<run dir>" -o pass_rates.png')
    return rc


CHECKS = {'tests': check_tests, 'reward': check_reward, 'sandbox': check_sandbox, 'model': check_model}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('check', choices=[*CHECKS, 'all'])
    ap.add_argument('tasks', nargs='?', type=Path, help='directory containing tasks/ (for reward)')
    ap.add_argument('--out', type=Path, default=Path('sandbox-check'), help='trial dir for the sandbox check')
    ap.add_argument('--limit', type=int, help='grade only the first N tasks')
    ap.add_argument('--cluster', default='helma', help='which hpc/<cluster>/run_pilot.sbatch to submit')
    ap.add_argument('--model', default='weak', choices=['weak', 'strong'],
                    help='weak = Qwen3-Coder-30B-A3B-Instruct (default), strong = Qwen3.5-122B-A10B')
    ap.add_argument('--attempts', type=int, default=8, help='attempts per task (default 8)')
    ap.add_argument('--stage', default='diag', help='smoke=1, diag=5, sweep=25, full=250 tasks per language')
    a = ap.parse_args()

    for name in ([*CHECKS] if a.check == 'all' else [a.check]):
        print(f'\n=== {name}: {CHECKS[name].__doc__}')
        rc = CHECKS[name](a)
        if rc != 0:
            sys.exit(f'{name} FAILED (exit {rc})')
    print('\nAll requested checks passed.')


if __name__ == '__main__':
    main()
