#!/usr/bin/env python3
"""Check 2 of the pipeline: the same reward check, but through Harbor and the sandbox.

For each task this builds the task image, starts the container, writes an answer
to /app/solution.txt and runs the task's own tests/test.sh, exactly as a trial
does. It therefore proves what check_reward.py cannot: the environment/Dockerfile
builds, the image has a working python, the verifier runs inside the sandbox, and
the reward reaches /logs/verifier/reward.json.

Graded per task: the reference solution (must score 1) and garbage (must score 0).
The cheap variants stay in check_reward.py - there is no point paying a container
build per nonsense string.

Needs a running bridge server and harbor_patches/bridge_worker.py, i.e. run it
inside the Slurm job (hpc/<cluster>/run_pilot.sbatch does this as its oracle
stage for a whole selection).

Usage:
  python verify/check_reward_harbor.py <dir with tasks/> <out dir> [--limit N]
Exit code is 0 only if every task scores 1 on the reference and 0 on garbage.
"""
from __future__ import annotations
import argparse
import asyncio
import json
import os
import re
import sys
import tomllib
from pathlib import Path

from harbor.environments.apptainer.apptainer import ApptainerEnvironment
from harbor.models.task.config import EnvironmentConfig
from harbor.models.trial.paths import TrialPaths

GARBAGE = '__PILOT_WRONG_ANSWER__();'


async def reward_for(env, answer):
    """Write an answer where the agent would, run the task's test.sh, read the reward."""
    await env.exec(f"mkdir -p /app && cat > /app/solution.txt <<'__EOF__'\n{answer}\n__EOF__", timeout_sec=60)
    r = await env.exec('bash /tests/test.sh', timeout_sec=300)
    out = await env.exec('cat /logs/verifier/reward.json', timeout_sec=60)
    if out.return_code != 0:
        raise RuntimeError(f'no reward.json (test.sh exit {r.return_code}): {(r.stderr or "")[:400]}')
    return json.loads(out.stdout)['reward']


async def check_task(task, out_dir):
    cfg = tomllib.loads((task / 'task.toml').read_text())
    lang = re.search(r'-(\w+)-\d+$', task.name).group(1)
    gold = (task / 'tests' / ('expected.txt' if lang == 'java' else 'solution.txt')).read_text().strip()
    paths = TrialPaths(out_dir / task.name)
    paths.mkdir()
    env = ApptainerEnvironment(
        environment_dir=task / 'environment', environment_name=task.name,
        session_id=f'reward-{task.name}', trial_paths=paths,
        task_env_config=EnvironmentConfig.model_validate(cfg.get('environment', {})),
        bridge_url=os.environ['APPTAINER_BRIDGE_URL'], sif_cache=os.environ['HARBOR_SIF_CACHE'])
    await env.start(force_build=False)          # the build itself is part of the check
    try:
        await env.upload_dir(task / 'tests', '/tests')
        return {'gold': await reward_for(env, gold), 'garbage': await reward_for(env, GARBAGE)}
    finally:
        try:
            await env.stop(delete=True)
        except Exception as e:  # noqa: BLE001
            print(f'{task.name}: stop failed: {e}', file=sys.stderr)


async def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('root', type=Path, help='directory containing tasks/')
    ap.add_argument('out', type=Path, help='where trial directories are written')
    ap.add_argument('--limit', type=int, help='check only the first N tasks')
    a = ap.parse_args()

    tasks = sorted((a.root / 'tasks').iterdir())[:a.limit]
    if not tasks:
        sys.exit(f'no tasks under {a.root / "tasks"}')
    a.out.mkdir(parents=True, exist_ok=True)
    failures = []
    for task in tasks:
        try:
            r = await check_task(task, a.out)
            ok = r['gold'] == 1 and r['garbage'] == 0
        except Exception as e:  # noqa: BLE001 - a failed build is a result, not a crash
            r, ok = {'error': str(e)[:200]}, False
        print(f'{task.name:34} {"ok " if ok else "FAIL"} {r}', flush=True)
        if not ok:
            failures.append((task.name, r))

    print(f'\n{len(tasks)} tasks built and graded through Harbor')
    (a.out / 'reward-harbor.json').write_text(json.dumps(
        {'tasks': len(tasks), 'failures': failures}, indent=1))
    if failures:
        print(f'FAILED: {len(failures)} tasks')
        sys.exit(1)
    print('PASSED: image builds, sandbox runs, reference scores 1 and garbage 0')


if __name__ == '__main__':
    asyncio.run(main())
