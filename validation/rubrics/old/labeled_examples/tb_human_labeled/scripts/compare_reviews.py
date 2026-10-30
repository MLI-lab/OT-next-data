#!/usr/bin/env python3
"""Compare stage-2 verdicts with version-specific calibration labels."""
import argparse
from collections import Counter
import csv
import json
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]


def compare(report, examples, manifest):
    directories = {x['task_id']: x['directory'] for x in manifest['tasks']}
    items = {Path(x['task']).name: x for x in report['items']}
    rows, tasks = [], []
    for example in examples['tasks']:
        name = example['task_id']
        item = items.get(directories[name], {})
        verdicts = item.get('verdicts', [])
        valid = item.get('status') not in ('error', 'failed', 'skipped', 'previewed')
        group = example['label_status'] if example['score'] else 'excluded'
        tasks.append({'example': name, 'directory': directories[name], 'group': group,
                      'status': item.get('status', 'missing'),
                      'verdict_count': len(verdicts),
                      'reason': example.get('exclusion_reason', item.get('reason'))})
        for attempt, verdict in enumerate(verdicts or [{}], 1):
            for criterion, expected in example['labels'].items():
                actual = verdict.get('checks', {}).get(criterion, {})
                observed = actual.get('outcome') if valid else None
                result = ('excluded' if not example['score'] else 'missing' if observed is None
                          else 'match' if observed == expected else 'mismatch')
                rows.append({'example': name, 'directory': directories[name], 'attempt': attempt,
                             'group': group, 'criterion': criterion, 'expected': expected,
                             'observed': observed, 'result': result,
                             'explanation': actual.get('explanation', '')})
    summaries = {}
    for group in ('upstream', 'assumed_pass', 'excluded'):
        selected = [r for r in rows if r['group'] == group]
        counts = Counter(r['result'] for r in selected)
        judged = counts['match'] + counts['mismatch']
        summaries[group] = {**dict(counts), 'labeled_comparisons': len(selected),
                            'accuracy_on_returned_labels': counts['match'] / judged if judged else None,
                            'confusion': dict(Counter(f'{r["expected"]}->{r["observed"]}' for r in selected
                                                     if r['result'] in ('match', 'mismatch')))}
    return {'groups': summaries, 'tasks': tasks, 'comparisons': rows, 'usage': report.get('usage'),
            'note': 'Only labeled criteria are scored. Missing outputs are reported separately. '
                    'Assumed passes and excluded examples are not mixed into upstream-label accuracy.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('report', type=Path, help='Stage-2 JSON report containing items and verdicts')
    parser.add_argument('--examples', type=Path, default=HERE / 'metadata/resolved-examples.json')
    parser.add_argument('--manifest', type=Path, default=HERE / 'metadata/task-manifest.json')
    parser.add_argument('--out', type=Path, required=True, help='Output directory')
    args = parser.parse_args()
    read = lambda path: json.loads(path.read_text())
    result = compare(read(args.report), read(args.examples), read(args.manifest))
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / 'comparison.json').write_text(json.dumps(result, indent=2) + '\n')
    with (args.out / 'comparison.csv').open('w') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(result['comparisons'][0]))
        writer.writeheader()
        writer.writerows(result['comparisons'])
    print(json.dumps(result['groups'], indent=2))


if __name__ == '__main__':
    main()
