"""Group structured implementation reviews without counting errors as judgments."""
from pathlib import Path
from collections import Counter, defaultdict


def group_reviews(items):
    grouped = {key: [] for key in ('all_pass', 'pass_with_not_applicable', 'all_not_applicable', 'has_fail', 'review_error', 'not_run')}
    by_task = {}
    severities = defaultdict(Counter)
    subcategories = defaultdict(Counter)
    for item in items:
        by_task.setdefault(Path(item.get('task', '')).name, []).append(item)
        for trial in item.get('verdicts', []):
            for name, value in trial.get('results', {}).items():
                severities[name][value['severity']] += 1
                if value.get('subcategory') is not None:
                    subcategories[name][value['subcategory']] += 1
    if severities:
        grouped['has_incomplete'] = []
    for task, records in sorted(by_task.items()):
        outcomes = [v.get('outcome') for item in records for trial in item.get('verdicts', []) for v in trial.get('checks', {}).values()]
        if any(i.get('status') in ('error', 'failed') for i in records):
            key = 'review_error'
        elif any(i.get('status') in ('skipped', 'previewed') for i in records):
            key = 'not_run'
        elif not outcomes or any(v not in ('pass', 'fail', 'not_applicable', 'incomplete') for v in outcomes):
            key = 'review_error'
        elif 'fail' in outcomes:
            key = 'has_fail'
        elif 'incomplete' in outcomes:
            key = 'has_incomplete' if severities else 'review_error'
        elif all(v == 'not_applicable' for v in outcomes):
            key = 'all_not_applicable'
        elif 'not_applicable' in outcomes:
            key = 'pass_with_not_applicable'
        else:
            key = 'all_pass'
        grouped[key].append(task)
    result = {'groups': grouped, 'counts': {key: len(tasks) for key, tasks in grouped.items()}}
    if severities:
        result['severity_counts'] = {name: dict(counts) for name, counts in sorted(severities.items())}
        result['subcategory_counts'] = {name: dict(counts) for name, counts in sorted(subcategories.items())}
    return result
