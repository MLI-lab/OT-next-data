"""Summarize release-task reviews without treating acceptance as a human label."""
import argparse
from collections import Counter
import json
from pathlib import Path


def summarize(report, manifest):
    items = {Path(i['task']).name: i for i in report['items']}
    tasks = []
    for task in manifest['tasks']:
        item = items.get(task['directory'], {})
        valid = item.get('status') in ('passed', 'findings') and len(item.get('verdicts', [])) == 1
        checks = item['verdicts'][0]['checks'] if valid else {}
        failures = {k: v['explanation'] for k, v in checks.items() if v['outcome'] == 'fail'}
        tasks.append({**task, 'status': item.get('status', 'pending'),
                      'all_applicable_pass': not failures if valid and checks else None,
                      'failures': failures, 'criteria': checks,
                      'cost': item.get('usage', {}).get('summary', {}).get('metrics', {}).get('reported_cost_usd'),
                      'errors': item.get('findings', []) if not valid else []})
    groups = {}
    for release in manifest['releases']:
        selected = [t for t in tasks if t['release'] == release]
        valid = [t for t in selected if t['all_applicable_pass'] is not None]
        costs = [t['cost']['total'] for t in selected if t['cost'] and t['cost'].get('total') is not None]
        passed = sum(t['all_applicable_pass'] for t in valid)
        groups[release] = {'selected': len(selected), 'valid_reviews': len(valid),
                           'all_criteria_pass_tasks': passed, 'tasks_with_failures': len(valid) - passed,
                           'missing_or_invalid': len(selected) - len(valid),
                           'pass_fraction_on_valid_reviews': passed / len(valid) if valid else None,
                           'failure_counts_by_criterion': dict(Counter(c for t in valid for c in t['failures'])),
                           'reported_cost_known_subtotal_usd': sum(costs) if costs else None,
                           'tasks_with_cost': len(costs),
                           'average_reported_cost_per_attempted_task_usd': sum(costs) / len(costs) if costs else None}
    return {'seed': manifest['seed'], 'groups': groups, 'tasks': tasks,
            'usage': report.get('usage'),
            'interpretation': 'Released tasks are expected-positive candidates, not human-labeled ground truth. '
                              'Failures need inspection; not_applicable criteria are not failures.'}


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('report', type=Path)
    ap.add_argument('--manifest', type=Path, required=True)
    ap.add_argument('--out', type=Path, required=True)
    args = ap.parse_args()
    result = summarize(json.loads(args.report.read_text()), json.loads(args.manifest.read_text()))
    args.out.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result['groups'], indent=2))
