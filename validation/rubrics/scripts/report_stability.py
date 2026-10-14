"""Present a frozen stability export without launching or rescoring reviews."""
import argparse
from collections import Counter
import csv
import json
from pathlib import Path


def agreement(rows):
    complete = [row for row in rows if row['complete']]
    unanimous = sum(not row['changed'] for row in complete)
    pairs = sum(row['pairs'] for row in complete)
    agreeing = pairs - sum(row['pairwise_disagreements'] for row in complete)
    return {'task_criterion_count': len(complete), 'unanimous_count': unanimous,
            'unanimous_agreement': unanimous / len(complete) if complete else None,
            'review_pair_count': pairs, 'agreeing_review_pairs': agreeing,
            'pairwise_agreement': agreeing / pairs if pairs else None}


def write_csv(path, rows):
    with path.open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def report(directory, readme):
    data = json.loads((directory / 'results.json').read_text())
    matched = set(data['matched_pure_model_comparison']['task_ids'])
    summaries, criteria, tasks, verdicts = [], [], [], []
    for model, values in data['models'].items():
        mixed = {r['task_id'] for r in values['mixed_model_reviews']}
        for row in values['rows']:
            counts = Counter(row['outcomes'].values())
            n = sum(counts.values())
            pairs = n * (n - 1) // 2
            disagreements = pairs - sum(c * (c - 1) // 2 for c in counts.values())
            assert row['complete'] == (n == 5)
            assert row['changed'] == (len(counts) > 1)
            assert row['pairs'] == pairs and row['pairwise_disagreements'] == disagreements
            verdicts.append({'model': model, 'task_id': row['task_id'], 'criterion': row['criterion'],
                **{f'repeat_{i}': row['outcomes'].get(str(i), '') for i in range(1, 6)},
                'valid_reviews': n, 'complete': row['complete'], 'matched_comparison': row['task_id'] in matched,
                'task_has_mixed_model_usage': row['task_id'] in mixed,
                'unanimous': not row['changed'] if row['complete'] else '',
                'pairwise_agreement': 1 - disagreements / pairs if row['complete'] else '',
                'human_label': row['expected']})
        for cohort in ('matched', 'all_complete'):
            selected = [r for r in values['rows'] if r['complete'] and (cohort == 'all_complete' or r['task_id'] in matched)]
            summaries.append({'cohort': cohort, 'model': model, **agreement(selected)})
            for criterion in sorted({r['criterion'] for r in selected}):
                rows = [r for r in selected if r['criterion'] == criterion]
                criteria.append({'cohort': cohort, 'model': model, 'criterion': criterion, **agreement(rows),
                    'pass_fail_flips': sum('pass' in r['counts'] and 'fail' in r['counts'] for r in rows),
                    'not_applicable_flips': sum(r['changed'] and 'not_applicable' in r['counts'] for r in rows)})
        for task in sorted({r['task_id'] for r in values['rows']}):
            rows = [r for r in values['rows'] if r['task_id'] == task]
            tasks.append({'model': model, 'task_id': task, 'matched_comparison': task in matched,
                          'task_has_mixed_model_usage': task in mixed,
                          'valid_reviews': len(rows[0]['outcomes']), **agreement(rows)})
    for name, rows in [('agreement-summary.csv', summaries), ('criterion-agreement.csv', criteria),
                       ('task-agreement.csv', tasks), ('verdict-agreement.csv', verdicts)]:
        write_csv(directory / name, rows)
    pct = lambda value: f'{100 * value:.1f}%'
    names = {'opus-5.5': 'Opus 5.5', 'sonnet-5': 'Sonnet 5'}
    lines = ['<!-- BEGIN STABILITY_RESULTS -->', '## Classification stability (14 October 2026)', '',
        'We selected 20 fixed tasks (10 synthetic and 10 historical) from the usable',
        'human-label set, using seed 42 and prioritizing criterion and label coverage',
        'without consulting previous model verdicts. The sample covers all 22 criteria',
        'and contains 84 human labels (56 positive, 28 negative). We requested **five',
        'fresh reviews per task per model**, with all 22 criteria assessed each time.',
        'The earlier calibration reviews above are not counted as a sixth repetition.', '',
        'Task files, rubric, prompt, tool access, and requested model settings were held',
        'fixed. Each review used a fresh session and an isolated neutral container with',
        'task files only. Human labels and previous reviews were hidden from the judge.',
        'Task images were not inspected.', '',
        'The run stopped after reaching the provider session quota: **172 valid reviews**',
        '(99 Opus and 73 Sonnet). Sonnet has 27 quota-failed review slots; one Opus',
        'review was stopped without a valid result. Two earlier invalid-format Sonnet',
        'responses triggered fresh replacement attempts, with the failed attempts retained.',
        'Missing and invalid responses are not classification disagreements.', '',
        '### How agreement is measured', '',
        '- **Unanimous agreement:** the percentage of task/criterion combinations for',
        '  which all five reviews returned the same verdict.',
        '- **Pairwise agreement:** the percentage of unordered pairs of reviews that',
        '  returned the same verdict for the same task and criterion. Five reviews',
        '  provide 10 pairs. Every complete task/criterion combination has equal weight.', '',
        'The three verdicts (`pass`, `fail`, `not_applicable`) are distinct. For example,',
        '`pass, pass, pass, pass, fail` is not unanimous, but 6/10 pairs agree (60%).',
        'No review, including the first one, is treated as the reference answer.',
        'These measures describe consistency, not agreement with human labels or correctness.', '',
        '### Matched comparison', '',
        'For the **same seven tasks**, we evaluated **22 criteria per task**, giving',
        '**7 × 22 = 154 task/criterion classifications, each repeated five times per model**.',
        'Both models have all five valid reviews for these tasks. This is the primary',
        'comparison because both models are measured on the same tasks.', '',
        '| Result | Opus 5.5 | Sonnet |',
        '| --- | ---: | ---: |']
    matched_summary = {r['model']: r for r in summaries if r['cohort'] == 'matched'}
    for label, format_value in [
        ('Always gave the same label across all five reviews', lambda r: f"{r['unanimous_count']}/{r['task_criterion_count']} ({pct(r['unanimous_agreement'])})"),
        ('Changed the label at least once', lambda r: f"{r['task_criterion_count'] - r['unanimous_count']}/{r['task_criterion_count']}"),
        ('Pairwise agreement', lambda r: pct(r['pairwise_agreement'])),
    ]:
        lines.append('| ' + label + ' | ' + ' | '.join(format_value(matched_summary[m]) for m in names) + ' |')
    lines += ['', 'Five reviews provide 10 unordered pairs per classification, giving',
              '**154 × 10 = 1,540 pairwise comparisons per model**. Opus agrees in',
              '1,488/1,540 pairs; Sonnet agrees in 1,438/1,540 pairs.']
    lines += ['', 'Opus was more consistent on this completed subset. It contains **six synthetic',
        'tasks and one historical task**, so it is not representative of the full',
        '20-task sample or other datasources. Completion was limited by runtime and quota.',
        'Three Opus reviews of `cron-broken-network` reported both Opus 5.5 and Opus 4.8',
        'usage. That task is absent from the complete groups and the matched comparison.', '',
        '### Agreement by criterion on the same seven tasks', '',
        'Each unanimous percentage below has a denominator of seven tasks; each pairwise',
        'percentage has a denominator of 70 review pairs. Criteria are ordered by the',
        'combined number of non-unanimous tasks, with alphabetical ties.', '',
        '| Criterion | Opus unanimous | Opus pairwise | Sonnet unanimous | Sonnet pairwise |',
        '| --- | ---: | ---: | ---: | ---: |']
    lookup = {(r['model'], r['criterion']): r for r in criteria if r['cohort'] == 'matched'}
    order = sorted({c for m, c in lookup}, key=lambda c: (sum(lookup[m, c]['unanimous_count'] for m in names), c))
    for criterion in order:
        cells = []
        for model in names:
            row = lookup[model, criterion]
            cells += [f"{row['unanimous_count']}/7 ({pct(row['unanimous_agreement'])})", pct(row['pairwise_agreement'])]
        lines.append('| `' + criterion + '` | ' + ' | '.join(cells) + ' |')
    lines += ['', '### Additional completed groups', '',
        'For completeness, the separate model totals below use every task with five',
        'valid reviews for that model. **The task sets differ**, so these percentages',
        'should not be used as the primary comparison between models.', '',
        '| Model | Complete tasks | Unanimous agreement | Pairwise agreement |',
        '| --- | ---: | ---: | ---: | ---: |']
    for row in summaries:
        if row['cohort'] == 'all_complete':
            lines.append(f"| {names[row['model']]} | {row['task_criterion_count'] // 22} | {row['unanimous_count']}/{row['task_criterion_count']} ({pct(row['unanimous_agreement'])}) | {row['agreeing_review_pairs']}/{row['review_pair_count']:,} ({pct(row['pairwise_agreement'])}) |")
    lines += ['', '### Saved results', '',
        '- [Agreement summary CSV](results/stability-20261014/agreement-summary.csv).',
        '- [Per-criterion agreement CSV](results/stability-20261014/criterion-agreement.csv), with matched and all-complete groups, plus pass/fail and not-applicable changes.',
        '- [Per-task agreement CSV](results/stability-20261014/task-agreement.csv), including review counts and matched-subset membership.',
        '- [Individual verdicts CSV](results/stability-20261014/verdict-agreement.csv), with all five repetition columns and agreement measures. Incomplete groups have blank agreement values.',
        '- [Frozen results JSON](results/stability-20261014/results.json) and [sample manifest](results/stability-20261014/sample-manifest.json).', '',
        'CSV agreement values are fractions from 0 to 1; the tables display percentages.',
        'Raw logs, failed attempts, frozen inputs, and the execution snapshot are retained',
        'at `/hnvme/workspace/y500bb12-optiagent/rubric-stability-20261014`.',
        'Regenerate these presentation tables from the saved results without running judges:', '',
        '```bash', 'python validation/rubrics/scripts/report_stability.py \\',
        '  --results validation/rubrics/labeled_examples/tb_human_labeled/results/stability-20261014 \\',
        '  --readme validation/rubrics/labeled_examples/tb_human_labeled/README.md',
        '```', '<!-- END STABILITY_RESULTS -->']
    text = readme.read_text()
    block = '\n'.join(lines)
    if '<!-- BEGIN STABILITY_RESULTS -->' in text:
        before, rest = text.split('<!-- BEGIN STABILITY_RESULTS -->', 1)
        _, after = rest.split('<!-- END STABILITY_RESULTS -->', 1)
        text = before + block + after
    else:
        text = text.rstrip() + '\n\n' + block + '\n'
    readme.write_text(text)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--results', type=Path, required=True)
    parser.add_argument('--readme', type=Path, required=True)
    args = parser.parse_args()
    report(args.results, args.readme)
