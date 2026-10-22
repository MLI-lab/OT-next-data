"""Compare repeated stage-5 reviews: pairwise/unanimous agreement and per-criterion variance."""
import argparse
from collections import Counter, defaultdict
from itertools import combinations
import json
from pathlib import Path


def ratio(numerator, denominator):
    return {'count': numerator, 'total': denominator,
            'percent': 100 * numerator / denominator if denominator else None}


def summarize(tasks, total_tasks=None, expected=None):
    """Compare independent run pairs on the same task and count unanimous tasks."""
    eligible = {task: labels for task, labels in tasks.items() if len(labels) >= 2}
    pairs = [(a, b) for labels in eligible.values() for a, b in combinations(labels, 2)]
    expected = expected or {}
    complete = {task: labels for task, labels in eligible.items() if len(labels) >= expected.get(task, 2)}
    unanimous = sum(len(set(labels)) == 1 for labels in complete.values())
    per_task = {}
    for task, labels in sorted(tasks.items()):
        counts = Counter(labels)
        count = len(labels)
        per_task[task] = {'reviews': count, 'labels': dict(counts),
            'requested_reviews': expected.get(task, count),
            'all_requested_completed': count >= expected.get(task, count),
            'unanimous': len(counts) == 1 if count >= max(2, expected.get(task, 2)) else None,
            'disagreement_percent': 100 * (1 - max(counts.values()) / count)}
    return {'tasks_observed': len(tasks), 'tasks_with_multiple_reviews': len(eligible),
            'tasks_with_only_one_review': len(tasks) - len(eligible),
            'mean_disagreement_percent': sum(value['disagreement_percent'] for value in per_task.values()) / len(per_task) if per_task else None,
            'pairwise_agreement': ratio(sum(a == b for a, b in pairs), len(pairs)),
            'tasks_with_all_requested_reviews': len(complete),
            'unanimous_tasks': ratio(unanimous, total_tasks if total_tasks is not None else len(tasks)),
            'unanimous_among_complete_tasks': ratio(unanimous, len(complete)), 'per_task': per_task}


def pairwise_runs(tasks, total_tasks):
    runs = sorted({run for samples in tasks.values() for run in samples})
    result = []
    for first, second in combinations(runs, 2):
        shared = [samples for samples in tasks.values() if first in samples and second in samples]
        agreeing = sum(samples[first] == samples[second] for samples in shared)
        result.append({'run_a': first, 'run_b': second, 'tasks_compared': len(shared),
                       'agreement': ratio(agreeing, total_tasks),
                       'agreement_among_compared_tasks': ratio(agreeing, len(shared))})
    return result


def analyze(paths):
    groups = {}
    seen = set()
    duplicates = 0
    for path in paths:
        path = Path(path)
        report = json.loads(path.read_text())
        legacy_review = report.get('stage') == 2 and not report.get('stage_layout')
        if (report.get('stage') != 5 and not legacy_review) or not isinstance(report.get('items'), list):
            raise ValueError(f'{path}: expected a stage-5 JSON report with items')
        config = {key: report.get(key) for key in ('review_rubric', 'review_format', 'review_agent', 'review_model', 'review_criteria')}
        key = json.dumps(config, sort_keys=True)
        group = groups.setdefault(key, {'configuration': config, 'reports': [],
            'criteria': defaultdict(lambda: defaultdict(list)), 'categories': defaultdict(list),
            'task_vectors': defaultdict(list), 'tasks': set(), 'expected': defaultdict(set),
            'completed': Counter(), 'errors': set(), 'run_vectors': defaultdict(dict),
            'run_criteria': defaultdict(lambda: defaultdict(dict))})
        if str(path.resolve()) not in group['reports']:
            group['reports'].append(str(path.resolve()))
        for item in report['items']:
            task = Path(item.get('task', item.get('task_id', 'unknown'))).name
            group['tasks'].add(task)
            trials = item.get('verdicts', [])
            if item.get('status') in ('error', 'failed'):
                group['errors'].add(task)
            # Count configured runs once per report; completed samples are de-duplicated below.
            checkpoint = item.get('review_checkpoint')
            if checkpoint is None and trials and trials[0].get('sample_id'):
                checkpoint = str(Path(trials[0]['sample_id']).parent)
            for attempt in range(report.get('review_attempts', len(trials))):
                group['expected'][task].add(f'{checkpoint}/{attempt:03d}' if checkpoint else f'{path.resolve()}:{task}:{attempt}')
            for index, trial in enumerate(trials):
                identity = trial.get('sample_id') or trial.get('trial') or f'{path.resolve()}:{task}:{index}'
                sample = (key, task, identity)
                if sample in seen:
                    duplicates += 1
                    continue
                seen.add(sample)
                checks = trial.get('checks', {})
                if not checks or any(v.get('outcome') not in ('pass', 'fail', 'not_applicable', 'incomplete') for v in checks.values()):
                    group['errors'].add(task)
                    continue
                group['completed'][task] += 1
                if checkpoint and '/fresh-' not in checkpoint:
                    run_group = str(Path(checkpoint).parent)
                else:
                    run_group = str(path.resolve())
                run_id = run_group + '#' + (Path(identity).name if trial.get('sample_id') else str(index))
                if 'category' in trial:
                    group['categories'][task].append(trial['category'])
                    group['task_vectors'][task].append(trial['category'])
                    group['run_vectors'][task][run_id] = trial['category']
                else:
                    group['task_vectors'][task].append(tuple(sorted((name, value['outcome']) for name, value in checks.items())))
                    group['run_vectors'][task][run_id] = tuple(sorted((name, value['outcome']) for name, value in checks.items()))
                    for name, value in checks.items():
                        group['criteria'][name][task].append(value['outcome'])
                        group['run_criteria'][name][task][run_id] = value['outcome']
    results = []
    for group in groups.values():
        # Resume exports may repeat expected counts; only report observed complete samples,
        # errors, and eligibility rather than inventing independent runs from those exports.
        expected = {task: len(samples) for task, samples in group['expected'].items()}
        overall = summarize(group['task_vectors'], len(group['tasks']), expected)
        for detail in overall['per_task'].values():
            detail.pop('labels')  # Whole label vectors are unwieldy; per-criterion results retain distributions.
        overall['per_run_pair'] = pairwise_runs(group['run_vectors'], len(group['tasks']))
        metrics = group['categories'] or group['criteria']
        results.append({'configuration': group['configuration'], 'reports': group['reports'],
            'total_tasks': len(group['tasks']), 'completed_reviews': sum(group['completed'].values()),
            'tasks_with_review_errors': ratio(len(group['errors']), len(group['tasks'])),
            'tasks_with_no_completed_review': ratio(sum(group['completed'][t] == 0 for t in group['tasks']), len(group['tasks'])),
            'overall': overall,
            'classification': summarize(metrics, len(group['tasks']), expected) if group['categories'] else None,
            'per_criterion': {name: {**summarize(tasks, len(group['tasks']), expected),
                'per_run_pair': pairwise_runs(group['run_criteria'][name], len(group['tasks']))}
                for name, tasks in sorted(group['criteria'].items())}})
    return {'groups': results, 'duplicate_samples_ignored': duplicates,
        'definitions': {
            'pairwise_agreement': 'Agreeing run pairs / all run pairs for the same task; count, total and percent.',
            'unanimous_tasks': 'Tasks where all requested reviews completed and agree / all tasks. Also reports agreement among tasks with all requested reviews and at least two reviews.',
            'overall': 'Labeling requires the complete criterion-label vector to agree; classification compares the category.',
            'missing': 'Pairwise agreement uses available completed pairs. Unanimity requires all requested reviews and at least two reviews; its primary denominator is all tasks.',
            'grouping': 'Different rubric, format, selected criteria, agent or model settings are analyzed separately.',
            'repairs': 'Repair rounds are merged into one review; resumed copies of the same sample are not independent runs.'}}


def render(result):
    def percent(metric):
        value = metric['percent']
        return f"{metric['count']}/{metric['total']} ({value:.1f}%)" if value is not None else '0/0 (not available)'

    lines = []
    for group in result['groups']:
        lines += ['Configuration: ' + json.dumps(group['configuration'], sort_keys=True),
                  f"Tasks: {group['total_tasks']}; completed independent reviews: {group['completed_reviews']}",
                  'Tasks with review errors: ' + percent(group['tasks_with_review_errors']),
                  'Tasks without a completed review: ' + percent(group['tasks_with_no_completed_review'])]
        for name, metrics in [('Overall', group['overall']), *group['per_criterion'].items()]:
            lines += [name + ':',
                      '  Pairwise agreement: ' + percent(metrics['pairwise_agreement']),
                      '  All requested runs agree, out of all tasks: ' + percent(metrics['unanimous_tasks']),
                      '  All requested runs agree, among fully reviewed tasks: ' + percent(metrics['unanimous_among_complete_tasks'])]
            for pair in metrics.get('per_run_pair', []):
                lines.append('  ' + pair['run_a'] + ' vs ' + pair['run_b'] + ': ' + percent(pair['agreement']) + ' of all tasks; ' + str(pair['tasks_compared']) + ' tasks compared')
        lines.append('')
    lines += [f"Duplicate resumed samples ignored: {result['duplicate_samples_ignored']}",
              'Pairwise agreement compares every unique pair of completed runs on the same task.',
              'Overall labeling agreement requires the full label vector to match; classification compares categories.',
              'Repairs count as part of their original run. Unanimity requires all requested runs and at least two completed runs.']
    return '\n'.join(lines) + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('reports', nargs='+', type=Path, help='stage-5 summary.json or exported stage-5-*.json files')
    parser.add_argument('--out', type=Path, default=Path(__file__).with_name('results.log'), help='readable log; default: validation/rubrics/results.log')
    parser.add_argument('--json-out', type=Path, help='optional detailed analysis JSON with per-task label distributions')
    args = parser.parse_args()
    result = analyze(args.reports)
    text = render(result)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text)
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(result, indent=2) + '\n')
    print(text, end='')
    print(f'Analysis saved to {args.out}')


if __name__ == '__main__':
    main()
