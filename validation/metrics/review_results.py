"""Group structured implementation reviews without counting errors as judgments."""
from pathlib import Path


def group_reviews(items):
    grouped = {key: [] for key in ('all_pass', 'pass_with_not_applicable', 'all_not_applicable', 'has_fail', 'review_error', 'not_run')}
    by_task = {}
    for item in items:
        by_task.setdefault(Path(item.get('task', '')).name, []).append(item)
    for task, records in sorted(by_task.items()):
        outcomes = [v.get('outcome') for item in records for trial in item.get('verdicts', []) for v in trial.get('checks', {}).values()]
        if any(i.get('status') in ('error', 'failed') for i in records):
            key = 'review_error'
        elif any(i.get('status') in ('skipped', 'previewed') for i in records):
            key = 'not_run'
        elif not outcomes or any(v not in ('pass', 'fail', 'not_applicable') for v in outcomes):
            key = 'review_error'
        elif 'fail' in outcomes:
            key = 'has_fail'
        elif all(v == 'not_applicable' for v in outcomes):
            key = 'all_not_applicable'
        elif 'not_applicable' in outcomes:
            key = 'pass_with_not_applicable'
        else:
            key = 'all_pass'
        grouped[key].append(task)
    return {'groups': grouped, 'counts': {key: len(tasks) for key, tasks in grouped.items()}}
