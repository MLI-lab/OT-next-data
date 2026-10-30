"""Run file-only checks before expensive stages, preserving evidence for blocked tasks."""
import json
from pathlib import Path
from uuid import uuid4


def prepare(args, numbers):
    if 4 not in numbers or not set(numbers) & {1, 2, 3, 5, 6, 9, 10} or args.dry_run:
        return
    from validation.data.selection import discover_tasks, select_paths
    from validation.contract import verify_materialized
    from validation.shards import parse_shard, slice_ids
    from validation.rules.static.terminal_bench import run_checks
    from validation.stages.runner import static_concurrency, checkout
    contract = verify_materialized(args)
    tasks = select_paths(discover_tasks(args.tasks), args)
    shard = parse_shard(getattr(args, 'shard', None))
    if shard:
        if not contract:
            raise ValueError('--shard requires a frozen contract')
        wanted = set(slice_ids(contract, shard))
        tasks = [t for t in tasks if t.name in wanted]
    out = args.out.resolve() / 'static-preflight' / uuid4().hex[:12]
    run_checks(tasks, out, profile=args.static_profile, upstream=checkout('terminal-bench'),
               exclude=args.exclude, concurrency=static_concurrency(args))
    report = json.loads((out / 'summary.json').read_text())
    args._static_blocked = {
        Path(item['task']).name: {'report': str(out / 'summary.json'),
                                 'status': item['status']}
        for item in report['tasks'] if item['status'] != 'passed'
    }


def partition(sources, args):
    blocked = getattr(args, '_static_blocked', {})
    selected, skipped = [], []
    for task in sources:
        if task.name in blocked:
            skipped.append({'task': str(task), 'status': 'skipped', 'blocked_by_stage': 4,
                            'static_report': blocked[task.name]['report'],
                            'reason': 'Static checks rejected the task or could not complete; no container trial run'})
        else:
            selected.append(task)
    return selected, skipped
