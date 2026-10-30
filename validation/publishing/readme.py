"""Deterministic release cards and selections from revision-aware results."""
import argparse
from collections import Counter
import json
from pathlib import Path

from validation import run as results

UNKNOWN = 'not recorded'


def cell(value):
    return str(UNKNOWN if value is None else value).replace('|', '\\|').replace('\n', ' ')


def table(headers, rows):
    return '\n'.join(['| ' + ' | '.join(map(cell, headers)) + ' |',
                      '| ' + ' | '.join('---' for _ in headers) + ' |'] +
                     ['| ' + ' | '.join(map(cell, row)) + ' |' for row in rows])


def document(value):
    if isinstance(value, str):
        return json.loads(value)
    return value or {}


def outcome(row, rule):
    value = row.get(rule)
    detail = document(row.get(rule + '_details'))
    original = detail.get('status')
    if detail.get('infrastructure_error'):
        return 'infra error'
    if value in ('pass', 'fail', 'warning'):
        return value
    if original in ('not_applicable', 'not-applicable'):
        return 'not applicable'
    if original in ('infrastructure_error', 'infra_error') or detail.get('failure_kind') == 'infrastructure':
        return 'infra error'
    if original in ('error', 'incomplete', 'failed', 'timeout'):
        return 'incomplete'
    return 'not run'


def latest(rows, dataset):
    selected = [r for r in rows if r.get('latest') and r['dataset'] == dataset]
    if len({r['task_id'] for r in selected}) != len(selected):
        raise ValueError('Results contain duplicate latest task IDs')
    return selected


def baseline(rows, rules):
    if not rules or len(set(rules)) != len(rules):
        raise ValueError('required_rules must list the distinct checks required for v1')
    kept, failed, pending = [], [], []
    for row in rows:
        states = {outcome(row, rule) for rule in rules}
        (failed if 'fail' in states else kept if states <= {'pass', 'warning', 'not applicable'} else pending).append(row)
    return kept, failed, pending


def evaluation(row, key, k):
    solved, attempts = row.get(key + '_solved'), row.get(key + '_attempts')
    if solved is None or attempts is None:
        return None, False
    if not (isinstance(solved, int) and isinstance(attempts, int) and 0 <= solved <= attempts <= k):
        raise ValueError(f'Invalid attempt counts for {row["task_id"]}: {key}')
    detail = document(row.get(key + '_details')).get('result', {})
    rewards = detail.get('rewards')
    complete = attempts == k and row.get(key) in ('pass', 'fail')
    if rewards is not None:
        complete = complete and len(rewards) == k and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in rewards)
    return solved, complete


def characteristics(annotation):
    if not annotation:
        return '## Dataset characteristics\n\nClassification not recorded.'
    from validation.publishing.annotation import render
    answer = annotation.get('annotation', annotation)
    # Reuse the established renderer, excluding its legacy payload-file list.
    text = render(answer, answer['dataset_name']).split('\n', 1)[1].split('\n## Files', 1)[0].strip()
    return text.replace(' — ', ': ').replace(' – ', ', ')


def render(rows, metadata, view, dataset, annotation=None, evaluation_key=None, student_key=None):
    if view not in ('v1', 'v2', 'v3'):
        raise ValueError('Unknown release view')
    rows = latest(rows, dataset)
    rules = metadata.get('required_rules', [])
    kept, failed, pending = baseline(rows, rules)
    stats = []
    selected = kept
    distribution = None
    if view == 'v1':
        stats = [('Upstream tasks', metadata.get('upstream_tasks')),
                 ('Tasks with results', len(rows)), ('Kept tasks', len(kept)),
                 ('Flagged tasks', len(failed)), ('Unresolved tasks', len(pending)),
                 ('Original images', metadata.get('original_image_count')),
                 ('Current shared images', metadata.get('current_image_count'))]
        network = Counter(r.get('allow_internet') for r in kept)
        stats += [('Kept tasks with allow_internet=true', network['true']),
                  ('Kept tasks with allow_internet=false', network['false']),
                  ('Kept tasks with allow_internet unspecified', network['unspecified']),
                  ('Kept tasks with internet setting not recorded',
                   len(kept) - sum(network[value] for value in ('true', 'false', 'unspecified')))]
    else:
        evaluations = metadata.get('evaluations', {})
        if evaluation_key not in evaluations:
            raise ValueError('Select an evaluation_key listed in run metadata evaluations')
        config = evaluations[evaluation_key]
        k = config.get('k')
        if not isinstance(k, int) or isinstance(k, bool) or k < 1:
            raise ValueError('Evaluation k must be a positive integer')
        cohort = kept
        if view == 'v3':
            if student_key not in evaluations or student_key == evaluation_key:
                raise ValueError('v3 requires a distinct student_key in evaluations')
            student_k = evaluations[student_key]['k']
            prior = [r for r in kept if (evaluation(r, student_key, student_k)[0] or 0) > 0]
            # Missing student attempts do not establish an unsolved student task.
            cohort = [r for r in kept if evaluation(r, student_key, student_k) == (0, True)]
            stats.append(('Existing v2 tasks', len(prior)))
        selected = [r for r in cohort if (evaluation(r, evaluation_key, k)[0] or 0) > 0]
        states = [evaluation(r, evaluation_key, k) for r in cohort]
        counts = Counter(solved for solved, complete in states if complete)
        stats += [('Validated v1 tasks', len(kept)), ('Evaluation cohort', len(cohort)),
                  ('Tasks with recorded attempts', sum((r.get(evaluation_key + '_attempts') or 0) > 0 for r in cohort)),
                  ('Newly solved tasks' if view == 'v3' else 'Kept student-solved tasks', len(selected))]
        if view == 'v3':
            stats.append(('Combined v2 and v3 tasks', len(prior) + len(selected)))
        stats += [('Model', config.get('model')), ('Attempts per task (k)', k),
                  ('Network access', config.get('network', 'unverified'))]
        distribution = table(['Successful attempts', 'Tasks'],
                             [(f'{i}/{k}', counts[i]) for i in range(k + 1)] +
                             [('Incomplete or missing attempts', sum(not complete for _, complete in states))])
    sections = [f'# {dataset} {view}', '## Dataset stats\n\n' + table(['Measure', 'Value'], stats),
                characteristics(annotation)]
    if view == 'v1':
        labels = ['pass', 'fail', 'warning', 'infra error', 'incomplete', 'not run', 'not applicable']
        breakdown = []
        for rule in rules:
            counts = Counter(outcome(r, rule) for r in rows)
            breakdown.append([rule, *[counts[label] for label in labels]])
        sections += ['## Validation rules\n\n' + table(['Rule', 'Passed', 'Flagged', 'Warning', 'Infra error', 'Incomplete', 'Not run', 'Not applicable'], breakdown) +
                     '\n\nA task can be flagged by several rules. Warnings do not remove tasks. Unresolved tasks are excluded from v1 until their required checks finish.']
    else:
        sections += ['## Evaluation settings\n\n' + table(['Setting', 'Value'],
                     [(key, json.dumps(value) if isinstance(value, (dict, list)) else value)
                      for key, value in config.items() if key not in ('model', 'k', 'network')]) ,
                     '## Results\n\n' + distribution + '\n\nTasks with at least one recorded success are kept. Incomplete attempts stay outside the fixed-k distribution.']
    sections += ['## Timing and resources\n\n' + table(['Measurement', 'Value'], [
        ('Overall wall time (seconds)', metadata.get('wall_seconds')),
        ('Image preparation/build time (seconds)', metadata.get('image_build_seconds')),
        ('Cluster', metadata.get('resources', {}).get('cluster')),
        ('Allocated CPUs', metadata.get('resources', {}).get('cpus')),
        ('Allocated GPUs', metadata.get('resources', {}).get('gpus')),
        ('Allocated memory (GiB)', metadata.get('resources', {}).get('memory_gib')),
        ('Concurrency', metadata.get('resources', {}).get('concurrency'))])]
    timing = metadata.get('rule_timings', {})
    sections.append(table(['Rule', 'Measured tasks', 'Total task seconds', 'Mean task seconds', 'Median task seconds'],
                          [[name, *[timing.get(name, {}).get(field) for field in ('tasks', 'total_seconds', 'mean_seconds', 'median_seconds')]]
                           for name in (rules if view == 'v1' else [evaluation_key])]))
    disclosures = (annotation or {}).get('validation_disclosures', [])
    if disclosures:
        sections.append('\n\n'.join(disclosures))
    sections.append('## Files\n\n`results.parquet` stores checks and evaluations by task revision. '
                    f'`{view}/selection.parquet` lists the selected task revisions and their payload files. '
                    'Payloads are shared across versions; only changed tasks need a new payload.')
    return '\n\n'.join(sections) + '\n', selected


def write(results_path, metadata, out, view, dataset, annotation=None, evaluation_key=None, student_key=None):
    import pyarrow as pa
    import pyarrow.parquet as pq
    text, selected = render(results.read(results_path), metadata, view, dataset, annotation, evaluation_key, student_key)
    if any(not row.get('task_file') for row in selected):
        raise ValueError('Selected tasks need published task_file pointers; prepare revision payloads first')
    out = Path(out) / view
    out.mkdir(parents=True, exist_ok=True)
    schema = pa.schema([(name, pa.int64() if name == 'revision' else pa.string())
                        for name in ('dataset', 'task_id', 'revision', 'content_sha256', 'task_file')])
    pq.write_table(pa.Table.from_pylist(selected, schema=schema), out / 'selection.parquet')
    (out / 'README.md').write_text(text)
    (out / 'run.json').write_text(json.dumps(metadata, indent=2) + '\n')
    if annotation:
        (out / 'annotation.json').write_text(json.dumps(annotation, indent=2) + '\n')
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('results', type=Path)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--annotation', type=Path)
    parser.add_argument('--view', choices=['v1', 'v2', 'v3'], required=True)
    parser.add_argument('--dataset', required=True)
    parser.add_argument('--evaluation-key')
    parser.add_argument('--student-key')
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    write(args.results, json.loads(args.run.read_text()), args.out, args.view, args.dataset,
          json.loads(args.annotation.read_text()) if args.annotation else None,
          args.evaluation_key, args.student_key)


if __name__ == '__main__':
    main()
