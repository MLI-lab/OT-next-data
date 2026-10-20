"""Environment-only static checks before image builds and runtime validation."""
import asyncio
import copy
import json
from pathlib import Path


def preflight(sources, out, args):
    from validation.checks.check_terminal_bench import run_checks
    from validation.stages.runner import static_concurrency

    run_checks(sources, out, profile=args.static_profile, exclude=args.exclude,
               concurrency=static_concurrency(args), group='environment')
    summary = json.loads((out / 'summary.json').read_text())
    paths = {p.name: str(p) for p in sources}
    return [{**item, 'task': paths[Path(item['task']).name]} for item in summary['tasks']]


def passing_sources(sources, items, allow_pin_repair=False):
    def permitted(item):
        if item['status'] == 'passed':
            return True
        failures = [c for c in item.get('checks', []) if c['status'] in ('failed', 'error')]
        return (allow_pin_repair and bool(failures) and
                all(c['check'] == 'check-pip-pinning.sh' and c['status'] == 'failed' for c in failures))
    passed = {Path(i['task']).name for i in items if permitted(i)}
    return [p for p in sources if p.name in passed]


def run_build_stage(sources, out, args, checkpoint=None):
    """Keep static evidence with each build outcome; never build a rejected task."""
    from validation.stages.build_retries import run_builds
    items = preflight(sources, out / 'environment-static', args)
    selected = passing_sources(sources, items)
    if checkpoint:
        from validation.checkpoints.stage3 import restore
        subset = {**checkpoint, 'items': [i for i in checkpoint['items']
                                        if Path(i['task']).name in {p.name for p in selected}]}
        builds = restore(subset, selected)
    elif selected:
        args = copy.copy(args)
        args._collect_stage1_paths = getattr(args, 'fix_instruction_paths', False)
        with (out / 'outcomes.jsonl').open('a') as outcomes:
            builds = asyncio.run(run_builds(selected, out, args, outcomes))
    else:
        builds = []
    by_name = {Path(i['task']).name: i for i in builds}
    results = []
    for item in items:
        static = {'status': item['status'], 'checks': item.get('checks', []),
                  **({'error': item['error']} if 'error' in item else {})}
        if item['status'] == 'passed':
            result = dict(by_name[Path(item['task']).name])
            from validation.checks.environment import leak_verdict
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
        else:
            result = {'task': item['task'], 'status': item['status'],
                      'reason': 'environment static preflight did not pass'}
        result['environment_static'] = static
        results.append(result)
    return results
