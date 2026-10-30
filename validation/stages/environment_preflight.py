"""Stage 1: build and start every task. (Until 2026-10-29 environment static checks ran here first.)"""
import asyncio
import copy
from pathlib import Path


def run_build_stage(sources, out, args, checkpoint=None):
    """Build and start every task, then decide its environment leak checks. All static checks,
    including those about the environment, run together in stage 4."""
    from validation.stages.build_retries import run_builds
    if checkpoint:
        from validation.checkpoints.build import restore
        names = {p.name for p in sources}
        builds = restore({**checkpoint, 'items': [i for i in checkpoint['items'] if Path(i['task']).name in names]},
                         sources)
    elif sources:
        args = copy.copy(args)
        args._collect_stage1_paths = getattr(args, 'fix_instruction_paths', False)
        with (out / 'outcomes.jsonl').open('a') as outcomes:
            builds = asyncio.run(run_builds(sources, out, args, outcomes))
    else:
        builds = []
    by_name = {Path(i['task']).name: i for i in builds}
    results = []
    for source in sources:
        result = dict(by_name[source.name])
        from validation.rules.dynamic.environment.check import leak_verdict
        verdict = next((leak_verdict(e) for e in result.get('environments', [])
                        if e.get('environment') == 'agent'), None)
        if (checkpoint and result.get('status') == 'passed' and not any(
                'leaks' in e for e in result.get('environments', []) if e.get('environment') == 'agent')):
            # Restoring would need the container started again, which is stage 1 itself.
            result.update(status='error', environment_leak='not_run',
                          reason='leak checks missing from reused stage-1 result; rerun stage 1')
        elif verdict and result.get('status') == 'passed':
            # Decided after the build retries: a leak is the same on every rebuild.
            result.update(status=verdict[0], reason=verdict[1], environment_leak=verdict[0])
        results.append(result)
    return results
