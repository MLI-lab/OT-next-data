#!/usr/bin/env python3
"""The same reward check, but through Harbor: image build, sandbox, real test.sh.

Dataset-agnostic, because it only uses Harbor's own task contract:

  reference   run the task's solution/solve.sh (the oracle), then tests/test.sh -> 1
  no answer   run tests/test.sh with nothing written                            -> 0

A dataset may add more variants through `data/<name>/rewards.py` (`ANSWER_PATH`
and `extra_variants`), which are written to that path and graded the same way.

This is the check that fails when the environment/Dockerfile breaks, when the
image has no python, or when the reward never reaches /logs/verifier/reward.json
- none of which the local check can see. It needs a running bridge server and
harbor_patches/bridge_worker.py, so run it inside a Slurm job.

Usage: python verify/check_reward_harbor.py <dir with tasks/> <out dir> [--limit N] [--extras]
Exit code is 0 only if every task scores as expected.
"""
from __future__ import annotations
import argparse
import asyncio
import importlib
import json
import os
import sys
import tomllib
from pathlib import Path

from harbor.environments.apptainer.apptainer import ApptainerEnvironment
from harbor.models.task.config import EnvironmentConfig
from harbor.models.trial.paths import TrialPaths

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


async def reward_after(env, setup=None):
    """Optionally run `setup`, then the task's own tests/test.sh, and read the reward."""
    if setup:
        r = await env.exec(setup, timeout_sec=300)
        if r.return_code != 0:
            raise RuntimeError(f'setup failed ({r.return_code}): {(r.stderr or "")[:300]}')
    r = await env.exec('bash /tests/test.sh', timeout_sec=300)
    out = await env.exec('cat /logs/verifier/reward.json', timeout_sec=60)
    if out.return_code != 0:
        raise RuntimeError(f'no reward.json (test.sh exit {r.return_code}): {(r.stderr or "")[:300]}')
    return json.loads(out.stdout)['reward']


async def check_task(task, out_dir, extras_mod):
    cfg = tomllib.loads((task / 'task.toml').read_text())
    paths = TrialPaths(out_dir / task.name)
    paths.mkdir()
    env = ApptainerEnvironment(
        environment_dir=task / 'environment', environment_name=task.name,
        session_id=f'reward-{task.name}', trial_paths=paths,
        task_env_config=EnvironmentConfig.model_validate(cfg.get('environment', {})),
        bridge_url=os.environ['APPTAINER_BRIDGE_URL'], sif_cache=os.environ['HARBOR_SIF_CACHE'])
    await env.start(force_build=False)          # the build is part of the check
    try:
        await env.upload_dir(task / 'tests', '/tests')
        results = {'no answer': (await reward_after(env), 0)}
        if (task / 'solution').is_dir():
            await env.upload_dir(task / 'solution', '/solution')
            results['reference (oracle)'] = (await reward_after(env, 'bash /solution/solve.sh'), 1)
        if extras_mod:
            gold = extras_mod.reference(task)
            for label, (answer, want) in extras_mod.extra_variants(task, gold).items():
                setup = (f"mkdir -p $(dirname {extras_mod.ANSWER_PATH}) && "
                         f"cat > {extras_mod.ANSWER_PATH} <<'__EOF__'\n{answer}\n__EOF__")
                results[label] = (await reward_after(env, setup), want)
        return results
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
    ap.add_argument('--extras', action='store_true', help="also run the dataset's own variants")
    ap.add_argument('--dataset', help='defaults to the part of the task name before the first "-"')
    a = ap.parse_args()

    tasks = sorted((a.root / 'tasks').iterdir())[:a.limit]
    if not tasks:
        sys.exit(f'no tasks under {a.root / "tasks"}')
    extras_mod = None
    if a.extras:
        name = a.dataset or tasks[0].name.split('-')[0]
        try:
            extras_mod = importlib.import_module(f'data.{name}.rewards')
        except ModuleNotFoundError:
            sys.exit(f'--extras: no data/{name}/rewards.py')
    a.out.mkdir(parents=True, exist_ok=True)

    failures = []
    for task in tasks:
        try:
            results = await check_task(task, a.out, extras_mod)
            bad = {k: v for k, v in results.items() if v[0] != v[1]}
        except Exception as e:  # noqa: BLE001 - a failed build is a result, not a crash
            results, bad = {'error': str(e)[:200]}, {'error': True}
        print(f'{task.name:34} {"ok  " if not bad else "FAIL"} '
              f'{ {k: v[0] if isinstance(v, tuple) else v for k, v in results.items()} }', flush=True)
        if bad:
            failures.append((task.name, str(bad)[:300]))

    print(f'\n{len(tasks)} tasks built and graded through Harbor')
    (a.out / 'reward-harbor.json').write_text(json.dumps({'tasks': len(tasks), 'failures': failures}, indent=1))
    if failures:
        print(f'FAILED: {len(failures)} tasks')
        sys.exit(1)
    print('PASSED: image builds, sandbox runs, reference scores 1 and a wrong answer 0')


if __name__ == '__main__':
    asyncio.run(main())
