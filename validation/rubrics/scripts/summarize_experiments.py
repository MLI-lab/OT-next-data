"""Publish compact judge results, confusion counts, and reported per-task costs."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import importlib.util
import json
import shutil
from pathlib import Path
import time

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / 'labeled_examples'


def read(path):
    return json.loads(Path(path).read_text())


def save(path, value):
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, indent=2) + '\n')
    temp.replace(path)


def module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    obj = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj


def costs(rows, planned, status):
    known = [r['reported_cost_usd'] for r in rows if r.get('reported_cost_usd') is not None]
    return {'status': status, 'planned_tasks': planned, 'attempted_tasks': len(rows),
            'tasks_with_reported_cost': len(known), 'reported_cost_usd': sum(known),
            'average_reported_cost_per_task_usd': sum(known) / len(known) if known else None,
            'tasks': rows}


def stage_costs(report, names, planned):
    rows = []
    for item in report['items']:
        directory = Path(item['task']).name
        metric = item.get('usage', {}).get('summary', {}).get('metrics', {}).get('reported_cost_usd', {})
        rows.append({'task_id': names.get(directory, directory), 'directory': directory,
                     'status': item['status'], 'reported_cost_usd': metric.get('total'),
                     'attempts': item.get('usage', {}).get('expected_trials'),
                     'models': sorted({t['model'] for t in item.get('usage', {}).get('trials', []) if t.get('model')})})
    return costs(rows, planned, 'complete' if report.get('complete') else 'running')


def compact_reviews(report, names):
    return {'complete': report.get('complete'), 'task_count': len(report['items']), 'tasks': [
        {'task_id': names.get(Path(x['task']).name, Path(x['task']).name),
         'directory': Path(x['task']).name, 'status': x['status'],
         'reviews': [{'checks': v['checks']} for v in x.get('verdicts', [])],
         'reported_cost_usd': x.get('usage', {}).get('summary', {}).get('metrics', {}).get('reported_cost_usd', {}).get('total')}
        for x in report['items']]}


def cost_text(value):
    total = value['reported_cost_usd']
    avg = value['average_reported_cost_per_task_usd']
    if avg is None:
        return 'No reported cost yet.'
    noun = 'task' if value['tasks_with_reported_cost'] == 1 else 'tasks'
    return f"Reported cost: **${total:.4f} total**, **${avg:.4f} per task** across {value['tasks_with_reported_cost']} {noun} with costs."


def replace_block(text, name, content):
    start, end = f'<!-- BEGIN {name} -->', f'<!-- END {name} -->'
    before, rest = text.split(start, 1)
    _, after = rest.split(end, 1)
    return before + start + '\n' + content.rstrip() + '\n' + end + after


def prune_completed_working_files(entry, summary):
    """Delete only task workspaces whose results exist in both published summaries."""
    run = Path(entry['run'])
    published = EXAMPLES / entry['folder'] / 'results' / (entry['slug'] + '.json')
    if not published.exists():
        return
    saved = {r['task_id']: r for r in read(published).get('results', [])}
    plan = read(run / 'plan.json')
    directories = {t['task_id']: t['directory'] for t in plan['tasks']}
    for row in summary.get('results', []):
        if saved.get(row['task_id']) != row:
            continue
        working = run / 'results' / directories[row['task_id']]
        if working.is_dir():
            shutil.rmtree(working)
        staging = run / 'inputs' / directories[row['task_id']]
        if staging.is_dir() and not staging.is_symlink() and (staging / 'files').is_symlink():
            shutil.rmtree(staging)
    if summary['status'] in ('complete', 'complete_with_errors') and len(saved) == len(plan['tasks']):
        # Completed runs no longer need their per-task staging links.
        if (run / 'inputs').is_dir() and not (run / 'inputs').is_symlink():
            shutil.rmtree(run / 'inputs')
        result_dir = run / 'results'
        if result_dir.is_dir() and not any(result_dir.iterdir()):
            result_dir.rmdir()
        source = run / 'summary.json'
        if not source.is_symlink() and read(published) == summary:
            source.unlink()
            source.symlink_to(published.resolve())


def model_comparisons(src):
    """Publish live comparisons without replacing missing or invalid labels with passes."""
    by_folder = {}
    done = True
    human = EXAMPLES / 'tb_human_labeled'
    examples = read(human / 'metadata/resolved-examples.json')
    manifest = read(human / 'metadata/task-manifest.json')
    comparer = module(human / 'scripts/compare_reviews.py', 'model_compare_labels')
    samples = read(EXAMPLES / 'tb3_tb4_assumed_positive/sample-manifest.json')
    release_by_id = {x['release'] + '/' + x['task']: x['release'] for x in samples['tasks']}
    for entry in src.get('model_runs', []):
        path = Path(entry['run']) / 'summary.json'
        run = read(path) if path.exists() else {'status': 'queued', 'results': [], 'completed': 0, 'errors': 0}
        if path.exists():
            prune_completed_working_files(entry, run)
        terminal = run['status'] in ('complete', 'complete_with_errors', 'blocked', 'error')
        done = done and terminal
        rows = run['results']
        valid = [r for r in rows if r['status'] == 'completed']
        cost = costs(rows, entry['planned'], run['status'])
        cost['basis'] = (run.get('cost') or {}).get('basis')
        record = {**entry, 'status': run['status'], 'completed': len(valid), 'errors': run['errors'], 'cost': cost}
        if entry['folder'] == 'tb_human_labeled':
            directory = {x['task_id']: x['directory'] for x in manifest['tasks']}
            report = {'items': [{'task': directory[r['task_id']],
                                'status': 'findings' if r['status'] == 'completed' else 'error',
                                'verdicts': [r['verdict']] if r['status'] == 'completed' else []}
                               for r in rows]}
            comparison = comparer.compare(report, examples, manifest)
            record['label_groups'] = []
            for group, expected, label in [('upstream', 'pass', 'Original positive'),
                                            ('upstream', 'fail', 'Original negative'),
                                            ('assumed_pass', 'pass', 'Later PR positive')]:
                subset = [r for r in comparison['comparisons'] if r['group'] == group and r['expected'] == expected]
                counts = Counter(r['observed'] for r in subset)
                observed = sum(counts[k] for k in ('pass', 'fail', 'not_applicable'))
                record['label_groups'].append({'label': label, 'expected': expected, 'total': len(subset),
                                              'pass': counts['pass'], 'fail': counts['fail'],
                                              'not_applicable': counts['not_applicable'], 'pending_or_invalid': len(subset) - observed,
                                              'agreement_on_reviewed': counts[expected] / observed if observed else None})
        elif entry['folder'] == 'tb3_tb4_assumed_positive':
            record['releases'] = []
            for release in samples['releases']:
                reviewed = [r for r in valid if release_by_id[r['task_id']] == release]
                failures = sum(any(v['outcome'] == 'fail' for v in r['verdict']['checks'].values()) for r in reviewed)
                record['releases'].append({'release': release, 'reviewed': len(reviewed),
                                          'all_applicable_pass': len(reviewed) - failures,
                                          'at_least_one_fail': failures, 'pending_or_invalid': 10 - len(reviewed)})
        else:
            counts = Counter(r['verdict']['checks']['anti_cheat_robustness']['outcome'] for r in valid)
            record['outcomes'] = {k: counts[k] for k in ('pass', 'fail', 'not_applicable')}
        by_folder.setdefault(entry['folder'], []).append(record)
    for folder, records in by_folder.items():
        save(EXAMPLES / folder / 'results/model-comparison.json', {'models': records})
        lines = ['**Additional judges**', '']
        if folder == 'tb_human_labeled':
            lines += ['| Model | Expected labels | Judge: pass | Judge: fail | Not applicable | Pending or invalid |',
                      '| --- | --- | ---: | ---: | ---: | ---: |']
            for r in records:
                for group in r['label_groups']:
                    lines.append(f"| {r['label']} | {group['label']} ({group['total']}) | {group['pass']} | {group['fail']} | {group['not_applicable']} | {group['pending_or_invalid']} |")
        elif folder == 'tb3_tb4_assumed_positive':
            lines += ['| Model | Release | Reviewed | All applicable criteria pass | At least one fail | Pending or invalid |',
                      '| --- | --- | ---: | ---: | ---: | ---: |']
            for r in records:
                for release in r['releases']:
                    lines.append(f"| {r['label']} | {release['release']} | {release['reviewed']}/10 | {release['all_applicable_pass']} | {release['at_least_one_fail']} | {release['pending_or_invalid']} |")
        else:
            lines += ['| Model, task files + image | Reviewed | Fail (detected) | Pass (missed) | Not applicable | Errors |',
                      '| --- | ---: | ---: | ---: | ---: | ---: |']
            for r in records:
                counts = r['outcomes']
                lines.append(f"| {r['label']} | {r['completed']}/{r['planned']} | {counts['fail']} | {counts['pass']} | {counts['not_applicable']} | {r['errors']} |")
        lines += ['', '| Model | Status | Cost so far | Mean per task with cost | Labels and explanations |',
                  '| --- | --- | ---: | ---: | --- |']
        for r in records:
            value = r['cost']
            total = f"${value['reported_cost_usd']:.4f}" if value['tasks_with_reported_cost'] else 'pending'
            avg = value['average_reported_cost_per_task_usd']
            avg = f'${avg:.4f}' if avg is not None else 'pending'
            lines.append(f"| {r['label']} | {r['status']} | {total} | {avg} | [{r['label']}-generated labels and explanations](results/{r['slug']}.json) |")
        lines += ['', 'Claude costs are CLI-reported token costs; Astra costs are [API-equivalent estimates](https://developers.openai.com/api/docs/models/gpt-6-astra) from CLI token usage at standard-context rates. Both exclude container compute and subscription charges.']
        doc = EXAMPLES / folder / 'README.md'
        text = replace_block(doc.read_text(), 'MODEL_RESULTS', '\n'.join(lines))
        if text != doc.read_text():
            doc.write_text(text)
    return by_folder, done


def refresh():
    src = read(ROOT / 'scripts/sources.json')
    human = EXAMPLES / 'tb_human_labeled'
    report = read(src['terminal_bench_report'])
    manifest = read(human / 'metadata/task-manifest.json')
    names = {x['directory']: x['task_id'] for x in manifest['tasks']}
    comparison = module(human / 'scripts/compare_reviews.py', 'compare_labels').compare(report, read(human / 'metadata/resolved-examples.json'), manifest)
    save(EXAMPLES / 'tb_human_labeled/results/terminal-bench-comparison.json', comparison)
    save(EXAMPLES / 'tb_human_labeled/results/terminal-bench-reviews.json', compact_reviews(report, names))
    all_costs = {'terminal_bench': stage_costs(report, names, 57)}
    lines = ['| Expected label | Labels | Judge: pass | Judge: fail | Judge: not applicable |',
             '| --- | ---: | ---: | ---: | ---: |']
    for group, expected, title in [('upstream', 'pass', 'Original positive'), ('upstream', 'fail', 'Original negative'), ('assumed_pass', 'pass', 'Later PR assumed positive')]:
        rows = [r for r in comparison['comparisons'] if r['group'] == group and r['expected'] == expected]
        counts = Counter(r['observed'] for r in rows)
        cells = [f"{counts[outcome]} ({counts[outcome] / len(rows):.1%})" for outcome in ('pass', 'fail', 'not_applicable')]
        lines.append(f"| {title} | {len(rows)} | " + ' | '.join(cells) + ' |')
    lines += ['', 'A fail on a positive label is a false rejection; a pass on a negative label is a missed defect. Not-applicable judgments are counted as mismatches here.', '', cost_text(all_costs['terminal_bench']), '', '[Sonnet-generated labels and explanations](results/terminal-bench-reviews.json).']
    blocks = {'TB_RESULTS': '\n'.join(lines)}

    samples = EXAMPLES / 'tb3_tb4_assumed_positive'
    manifest = read(samples / 'sample-manifest.json')
    final_reports = list((Path(src['tb3_tb4_run']) / 'report').glob('stage-2-*.json'))
    report_path = final_reports[0] if len(final_reports) == 1 else Path(src['tb3_tb4_report'])
    report = read(report_path) if report_path.exists() else {'items': [], 'complete': False}
    summary = module(samples / 'scripts/summarize_reviews.py', 'summarize_releases').summarize(report, manifest)
    summary['complete'] = report.get('complete', False)
    save(EXAMPLES / 'tb3_tb4_assumed_positive/results/tb3-tb4-summary.json', summary)
    names = {x['directory']: x['release'] + '/' + x['task'] for x in manifest['tasks']}
    all_costs['tb3_tb4'] = stage_costs(report, names, 20)
    lines = ['| Release | Reviewed | All applicable criteria pass | At least one fail | Pending or invalid | Reported cost | Mean per task |', '| --- | ---: | ---: | ---: | ---: | ---: | ---: |']
    for name, group in summary['groups'].items():
        release_rows = [r for r in all_costs['tb3_tb4']['tasks'] if r['task_id'].startswith(name + '/')]
        value = costs(release_rows, 10, 'complete' if len(release_rows) == 10 else 'running')
        all_costs['tb3_tb4'].setdefault('releases', {})[name] = value
        total = f"${value['reported_cost_usd']:.4f}" if release_rows else 'pending'
        average = f"${value['average_reported_cost_per_task_usd']:.4f}" if value['average_reported_cost_per_task_usd'] is not None else 'pending'
        lines.append(f"| {name} | {group['valid_reviews']}/10 | {group['all_criteria_pass_tasks']} | {group['tasks_with_failures']} | {group['missing_or_invalid']} | {total} | {average} |")
    lines += ['', cost_text(all_costs['tb3_tb4']), '', '[Sonnet-generated labels and explanations](results/tb3-tb4-summary.json).']
    blocks['TB34_RESULTS'] = '\n'.join(lines)

    lines = ['| Evidence, both with shell tools | Reviewed | Judge: fail (detected) | Judge: pass (missed) | Not applicable | Errors |', '| --- | ---: | ---: | ---: | ---: | ---: |']
    cost_lines = []
    for key, label, filename in [('charlie_files', 'Task files only', 'charlie-files-tools-reviews.json'), ('charlie_image', 'Task files + task image', 'charlie-image-reviews.json')]:
        run = Path(src[key + '_run'])
        summary = read(run / 'summary.json') if (run / 'summary.json').exists() else {'status': 'pending', 'results': [], 'planned': 27, 'completed': 0, 'errors': 0}
        rows = []
        for r in summary['results']:
            rows.append({'task_id': r['task_id'], 'status': r['status'], 'reported_cost_usd': r.get('reported_cost_usd'), 'model_usage': r.get('model_usage')})
        value = costs(rows, 27, summary['status'])
        all_costs[key] = value
        summary['cost'] = {k: v for k, v in value.items() if k != 'tasks'}
        # The worker publishes its own live file; keep derived cost metadata separately.
        counts = Counter(r['verdict']['checks']['anti_cheat_robustness']['outcome'] for r in summary['results'] if r['status'] == 'completed')
        lines.append(f"| {label} | {summary['completed']}/27 | {counts['fail']} | {counts['pass']} | {counts['not_applicable']} | {summary['errors']} |")
        cost_lines.append(f"**{label}:** {cost_text(value)} [Sonnet-generated labels and explanations](results/{filename}).")
    blocks['CHARLIE_RESULTS'] = '\n'.join(lines) + '\n\n' + '\n\n'.join(cost_lines)
    model_groups, models_done = model_comparisons(src)
    cost_groups = {'tb_human_labeled': ['terminal_bench'],
                   'tb3_tb4_assumed_positive': ['tb3_tb4'],
                   'charlie_hackable': ['charlie_files', 'charlie_image']}
    for folder, keys in cost_groups.items():
        save(EXAMPLES / folder / 'results/experiment-costs.json', {
            'updated_at': datetime.now(timezone.utc).isoformat(),
            'basis': 'Claude-reported token costs, not subscription charges; excludes image builds and container compute. Averages use tasks with known costs and include recorded retries.',
            'experiments': {key: all_costs[key] for key in keys},
            'additional_models': {r['slug']: r['cost'] for r in model_groups.get(folder, [])}})
    destinations = {'TB_RESULTS': 'tb_human_labeled',
                    'TB34_RESULTS': 'tb3_tb4_assumed_positive',
                    'CHARLIE_RESULTS': 'charlie_hackable'}
    for name, content in blocks.items():
        doc = EXAMPLES / destinations[name] / 'README.md'
        original = doc.read_text()
        text = replace_block(original, name, content)
        if text != original:
            doc.write_text(text)
    return models_done and all(v['status'] in ('complete', 'completed', 'blocked', 'error') for v in all_costs.values())


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--watch', action='store_true')
    args = parser.parse_args()
    while True:
        done = refresh()
        if not args.watch or done:
            break
        time.sleep(30)
