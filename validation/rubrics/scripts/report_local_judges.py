"""Report local judge accuracy and repeatability without inventing missing results."""
import argparse
from collections import Counter, defaultdict
import csv
import json
from pathlib import Path
import sys
import statistics

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from validation.rubrics.scripts.local_experiment import GROUPS, MODELS, read, save
EXAMPLES = ROOT / 'validation/rubrics/labeled_examples'


def csv_write(path, rows):
    if not rows:
        return
    with path.open('w') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(rows)


def section(path, body):
    begin, end = '<!-- BEGIN LOCAL_JUDGES -->', '<!-- END LOCAL_JUDGES -->'
    old = path.read_text()
    replacement = begin + '\n' + body + '\n' + end
    if begin in old:
        old = old[:old.index(begin)] + replacement + old[old.index(end)+len(end):]
    else:
        old = old.rstrip() + '\n\n' + replacement + '\n'
    if path == ROOT / 'validation/rubrics/README.md':
        start = old.index(begin)
        finish = old.index(end) + len(end)
        block = old[start:finish]
        old = old[:start].rstrip() + '\n\n' + old[finish:].lstrip()
        anchor = '## Harbor and Terminal-Bench rubric comparison'
        old = old.replace(anchor, block + '\n\n' + anchor, 1)
    path.write_text(old.rstrip() + '\n')


def report(base):
    manifest = read(base / 'manifest.json')
    labels = {r['task_id']: r for r in read(EXAMPLES / 'tb_human_labeled/metadata/resolved-examples.json')['tasks']}
    samples = read(EXAMPLES / 'tb3_tb4_assumed_positive/sample-manifest.json')['tasks']
    releases = {r['release']+'/'+r['task']: r['release'] for r in samples}
    table = ['## Local GLM judges', '',
        'The two local judges use the same example groups and evidence settings as the earlier runs. '
        'They use an OpenAI-compatible tool loop instead of the provider CLIs, so harness differences remain. '
        'Stability uses the same 20 tasks, five fresh reviews each, with previous verdicts and labels hidden.', '',
        '| Model | Calibration reviews | Stability reviews | Status |', '| --- | ---: | ---: | --- |']
    for group in GROUPS:
        lines = ['## Local GLM judges', '']
        for slug, (name, _) in MODELS.items():
            exp = [x for x in manifest['experiments'] if x['group'] == group]
            records = []
            for e in exp:
                p = base / slug / group / 'repeat-0' / e['task']['directory'] / 'summary.json'
                if p.exists(): records += read(p)['results']
            valid = {r['task_id']: r for r in records if r['status'] == 'completed'}
            errors = sum(r['status'] != 'completed' for r in records)
            lines += [f'### {name}', '', f'{len(valid)}/{len(exp)} completed; {errors} errors.', '']
            rows = []
            for e in exp:
                task = e['task']['task_id']
                checks = valid.get(task, {}).get('verdict', {}).get('checks', {})
                if group == 'tb_human_labeled':
                    label = labels[task]
                    category = ('excluded' if not label['score'] else label['role'] if label['label_status'] == 'upstream' else 'later_PR_assumed_positive')
                    expected = label['labels']
                elif group == 'charlie_hackable':
                    category, expected = 'reported_hackable', {'anti_cheat_robustness': 'fail'}
                else:
                    category = releases[task]
                    expected = {c: 'pass' for c in checks}  # Acceptance is not a verified label for each criterion.
                for criterion, label in expected.items():
                    observed = checks.get(criterion, {}).get('outcome')
                    rows.append({'task_id': task, 'category': category, 'criterion': criterion,
                        'expected': label, 'observed': observed, 'match': observed == label if observed else None,
                        'explanation': checks.get(criterion, {}).get('explanation', '')})
            dest = EXAMPLES / group / 'results'
            save(dest / f'{slug}.json', {'model': name, 'revision': MODELS[slug][1],
                'completed': len(valid), 'planned': len(exp), 'errors': errors,
                'results': records, 'comparisons': rows, 'compute': read(base / slug / 'compute.json') if (base / slug / 'compute.json').exists() else None})
            csv_write(dest / f'{slug}-labels.csv', rows)
            counts = []
            for category, criterion, expected in sorted({(r['category'],r['criterion'],r['expected']) for r in rows}):
                selected = [r for r in rows if (r['category'],r['criterion'],r['expected']) == (category,criterion,expected)]
                outcomes = Counter(r['observed'] or 'pending' for r in selected)
                counts.append({'category':category,'criterion':criterion,'expected':expected,'total':len(selected),
                    **{k:outcomes[k] for k in ('pass','fail','not_applicable','pending')}})
            csv_write(dest / f'{slug}-criterion-counts.csv', counts)
            lines += ['', f'[{name}: individual verdicts and explanations](results/{slug}.json). [Criterion counts](results/{slug}-criterion-counts.csv).' if rows else f'[{name}: pending results](results/{slug}.json)', '']
            if rows:
                lines += ['| Category | Expected | Match | Mismatch | Pending |', '| --- | --- | ---: | ---: | ---: |']
                for category, expected in sorted({(r['category'],r['expected']) for r in rows}):
                    selected=[r for r in rows if r['category']==category and r['expected']==expected]
                    c=Counter(r['match'] for r in selected)
                    lines.append(f'| {category} | {expected} | {c[True]} | {c[False]} | {c[None]} |')
                if group == 'tb3_tb4_assumed_positive':
                    lines += ['', 'Acceptance is only an assumed positive. Criterion mismatches here are findings, not established judge errors.']
        lines += ['', 'Missing or invalid reviews are reported separately. Local inference has no API charge; GPU time is recorded with each model.']
        section(EXAMPLES / group / 'README.md', '\n'.join(lines))
    stability = []
    for slug, (name, _) in MODELS.items():
        calibration_count = sum(read(EXAMPLES / group / 'results' / f'{slug}.json')['completed'] for group in GROUPS)
        valid_count = 0
        by_task = defaultdict(dict)
        for e in manifest['experiments']:
            if e['group'] != 'stability': continue
            p = base / slug / 'stability' / f"repeat-{e['repeat']}" / e['task']['directory'] / 'summary.json'
            if p.exists():
                records = [r for r in read(p)['results'] if r['status']=='completed']
                for r in records:
                    valid_count += 1
                    by_task[r['task_id']][e['repeat']] = r['verdict']['checks']
        for task, repeats in by_task.items():
            for criterion in sorted({c for checks in repeats.values() for c in checks}):
                verdicts = {i:checks[criterion]['outcome'] for i,checks in repeats.items()}
                c = Counter(verdicts.values()); n=sum(c.values()); pairs=n*(n-1)//2
                stability.append({'model':slug,'task_id':task,'category':labels[task]['role'],'criterion':criterion,
                    **{f'repeat_{i}':verdicts.get(i,'') for i in range(1,6)},'complete':n==5,
                    'unanimous':len(c)==1 if n==5 else None,
                    'pairwise_agreement':sum(v*(v-1)//2 for v in c.values())/pairs if n==5 else None})
        state = read(base / slug / 'status.json')['status'] if (base / slug / 'status.json').exists() else 'preparing'
        table.append(f'| {name} | {calibration_count}/104 | {valid_count}/100 | {state} |')
    prior = read(EXAMPLES / 'tb_human_labeled/results/stability-20261014/results.json')
    for slug, model in prior['models'].items():
        mixed = {r['task_id'] for r in model['mixed_model_reviews']}
        for row in model['rows']:
            if row['task_id'] in mixed:
                continue
            stability.append({'model':slug,'task_id':row['task_id'],'category':labels[row['task_id']]['role'],
                'criterion':row['criterion'], **{f'repeat_{i}':row['outcomes'].get(str(i),'') for i in range(1,6)},
                'complete':row['complete'], 'unanimous':not row['changed'] if row['complete'] else None,
                'pairwise_agreement':1-row['pairwise_disagreements']/row['pairs'] if row['complete'] else None})
    dest = EXAMPLES / 'tb_human_labeled/results/local-stability-20261015'
    dest.mkdir(exist_ok=True)
    save(dest / 'results.json', {'rows':stability, 'note':'Only five-valid-review groups contribute to agreement. No repetition is a reference answer.'})
    csv_write(dest / 'individual-verdicts.csv', stability)
    summary=[]
    complete_tasks = {slug:{r['task_id'] for r in stability if r['model']==slug and r['complete']} for slug in list(MODELS)+list(prior['models'])}
    matched = set.intersection(*complete_tasks.values())
    for slug in complete_tasks:
        for cohort in ('all_complete', 'matched_all_four_models'):
            selected = [r for r in stability if r['model']==slug and r['complete'] and (cohort=='all_complete' or r['task_id'] in matched)]
            for category in ['all'] + sorted({r['category'] for r in selected}):
                rows = [r for r in selected if category=='all' or r['category']==category]
                for criterion in ['all'] + sorted({r['criterion'] for r in rows}):
                    subset = [r for r in rows if criterion=='all' or r['criterion']==criterion]
                    summary.append({'model':slug,'cohort':cohort,'category':category,'criterion':criterion,
                        'classifications':len(subset),'unanimous':sum(r['unanimous'] for r in subset),
                        'pairwise_agreement':sum(r['pairwise_agreement'] for r in subset)/len(subset) if subset else None})
    csv_write(dest / 'agreement.csv',summary)
    accuracy = []
    for slug in complete_tasks:
        for cohort in ('all_complete', 'matched_all_four_models'):
            selected = [r for r in stability if r['model']==slug and r['complete']
                        and (cohort=='all_complete' or r['task_id'] in matched)
                        and r['criterion'] in labels[r['task_id']]['labels']]
            for category in ['all'] + sorted({r['category'] for r in selected}):
                rows = [r for r in selected if category=='all' or r['category']==category]
                values = [sum(r[f'repeat_{i}']==labels[r['task_id']]['labels'][r['criterion']]
                              for r in rows)/len(rows) for i in range(1,6)] if rows else []
                accuracy.append({'model':slug,'cohort':cohort,'category':category,'labels_per_repeat':len(rows),
                    **{f'repeat_{i}':values[i-1] if values else None for i in range(1,6)},
                    'mean_accuracy':statistics.mean(values) if values else None,
                    'sample_stddev':statistics.stdev(values) if values else None})
    csv_write(dest / 'accuracy-variance.csv',accuracy)
    table += ['', '[Per-category calibration results](labeled_examples/) and '
        '[stability results by category and criterion](labeled_examples/tb_human_labeled/results/local-stability-20261015/agreement.csv). '
        '[Accuracy variation across repeats](labeled_examples/tb_human_labeled/results/local-stability-20261015/accuracy-variance.csv) includes the mean and sample standard deviation. Only complete sets of five reviews enter these statistics. '
        'The matched comparison uses tasks completed by both GLM models, Opus and Sonnet, excluding mixed-model reviews.']
    table += ['', '| Local model | Human labels correct | TB3 all criteria pass | TB4 all criteria pass | Hackable tasks detected |',
              '| --- | ---: | ---: | ---: | ---: |']
    for slug, (name, _) in MODELS.items():
        human = read(EXAMPLES / 'tb_human_labeled/results' / f'{slug}.json')
        eligible = [r for r in human['comparisons'] if r['category'] in ('synthetic','reviewed','candidate')]
        returned = [r for r in eligible if r['observed'] is not None]
        benchmark = read(EXAMPLES / 'tb3_tb4_assumed_positive/results' / f'{slug}.json')
        counts = []
        for release in ('terminal-bench-3','terminal-bench-4'):
            records = [r for r in benchmark['results'] if r['status']=='completed' and releases[r['task_id']]==release]
            passed = sum(all(c['outcome']!='fail' for c in r['verdict']['checks'].values()) for r in records)
            counts.append(f'{passed}/{len(records)}')
        hack = read(EXAMPLES / 'charlie_hackable/results' / f'{slug}.json')
        detected = sum(r['match'] is True for r in hack['comparisons'])
        correct = sum(r['match'] is True for r in returned)
        table.append(f'| {name} | {correct}/{len(returned)} | {counts[0]} | {counts[1]} | {detected}/{hack["completed"]} |')
    table += ['', 'Denominators above count completed valid reviews or returned labels. Full targets are 137 human labels, 10 TB3 tasks, 10 TB4 tasks and 27 hackable tasks. TB3/TB4 acceptance is an assumed positive, not criterion-level ground truth.', '',
              '| Matched stability model | Classifications | Unanimous | Pairwise agreement |',
              '| --- | ---: | ---: | ---: |']
    for r in summary:
        if r['cohort']=='matched_all_four_models' and r['category']=='all' and r['criterion']=='all':
            pair = f'{100*r["pairwise_agreement"]:.1f}%' if r['pairwise_agreement'] is not None else 'pending'
            table.append(f'| {r["model"]} | {r["classifications"]} | {r["unanimous"]} | {pair} |')
    section(ROOT / 'validation/rubrics/README.md','\n'.join(table))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--base',type=Path,required=True)
    report(p.parse_args().base)
