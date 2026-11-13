"""Deterministic release cards and selections from revision-aware results."""
import argparse
from collections import Counter
import json
import math
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
    """README group of a check status."""
    from validation.outcomes import normalize
    value, _ = normalize(rule, row.get(rule) or 'not_requested', results.details(row).get(rule, {}))
    if value in ('pass', 'fail', 'warning'):
        return value
    if value == 'not_applicable':
        return 'not applicable'
    if value == 'infrastructure_error':
        return 'infra error'
    return 'incomplete' if value in ('timeout', 'oom', 'execution_error') else 'not run'


def latest(rows, dataset):
    selected = [r for r in rows if r['dataset'] == dataset]
    if len({r['task_id'] for r in selected}) != len(selected):
        raise ValueError('Results contain duplicate task IDs')
    return selected


def baseline(rows, rules):
    if not rules or len(set(rules)) != len(rules):
        raise ValueError('required_rules must list the distinct checks required for v1')
    kept, failed, pending = [], [], []
    for row in rows:
        states = {outcome(row, rule) for rule in rules}
        (failed if 'fail' in states else kept if states <= {'pass', 'warning', 'not applicable'} else pending).append(row)
    return kept, failed, pending


def rule_statistics(rows, required_rules):
    """One row per task; repair actions are independent of check verdicts."""
    from validation.automatic_repairs import RULES as repair_rules
    observed = {key for row in rows for key in results.details(row)
                if isinstance(row.get(key), str) and row[key] in results.STATUSES
                and not key.startswith('passk_')}
    rules = list(dict.fromkeys([*required_rules, *sorted(observed - set(required_rules))]))
    stats = []
    for rule in rules:
        counts = Counter(outcome(row, rule) for row in rows)
        repairs = [document(row.get('automatic_repairs')).get(rule, {}).get('applied') for row in rows]
        stats.append({'rule': rule, 'total': len(rows), 'passed': counts['pass'],
                      'failed': counts['fail'], 'warning': counts['warning'],
                      'automatically_repaired': sum(value is True for value in repairs),
                      'unresolved': sum(counts[key] for key in ('infra error', 'incomplete', 'not run')),
                      'not_applicable': counts['not applicable'],
                      'repair_not_recorded': sum(value is None for value in repairs) if rule in repair_rules else 0})
    return stats


def rule_table(rows, required_rules):
    fields = ['passed', 'failed', 'warning', 'automatically_repaired', 'unresolved',
              'not_applicable']
    def share(count, total):
        return f'{count} ({count / total:.1%})' if total else '0 (0.0%)'
    return table(['Rule', 'Total tasks', 'Passed', 'Failed', 'Warning', 'Automatically repaired',
                  'Unresolved', 'Not applicable'],
                 [[stat['rule'], stat['total'], *[share(stat[field], stat['total']) for field in fields]]
                  for stat in rule_statistics(rows, required_rules)])


def evaluation(row, key, k):
    solved, attempts = row.get(key + '_solved'), row.get(key + '_attempts')
    if solved is None or attempts is None:
        return None, False
    if not (isinstance(solved, int) and isinstance(attempts, int) and 0 <= solved <= attempts <= k):
        raise ValueError(f'Invalid attempt counts for {row["task_id"]}: {key}')
    detail = results.details(row).get(key, {}).get('result', {})
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


def task_resources(rows):
    """What the tasks ask for: internet, CPUs and memory, as task counts."""
    internet = {'true': 'allowed', 'false': 'not allowed', 'unspecified': 'not specified'}
    result = [('Internet', internet.get(value, 'not recorded'), count)
              for value, count in sorted(Counter(r.get('allow_internet') for r in rows).items(), key=str)]
    result += [('CPUs', 'not specified' if value is None else value, count)
               for value, count in sorted(Counter(r.get('cpus') for r in rows).items(), key=str)]
    result += [('Memory', 'not specified' if value is None else f'{value / 1024:g} GiB', count)
               for value, count in sorted(Counter(r.get('memory_mb') for r in rows).items(), key=str)]
    return result


def resource_use(rows):
    """Measured use of passing reference runs, against the limits; empty when not recorded."""
    def summary(name, values, shown):
        values = sorted(values)
        p95 = values[max(0, math.ceil(0.95 * len(values)) - 1)]
        return (name, shown(sum(values) / len(values)), shown(p95), shown(values[-1]), len(values))
    measured = [(r, results.details(r).get('reference', {})) for r in rows if r.get('reference') == 'pass']
    memory = [d['memory_peak_mb'] for _, d in measured if d.get('memory_peak_mb') is not None]
    cpu = [d['cpu_seconds'] for _, d in measured if d.get('cpu_seconds') is not None]
    shares = [d['memory_peak_mb'] / float(r['memory_mb']) for r, d in measured
              if d.get('memory_peak_mb') is not None and r.get('memory_mb')]
    result = []
    if memory:
        result.append(summary('Peak memory', memory, lambda v: f'{v / 1024:.2f} GiB'))
    if cpu:
        result.append(summary('CPU time', cpu, lambda v: f'{v:.0f} s'))
    if shares:
        result.append(summary('Peak memory / limit', shares, lambda v: f'{v:.0%}'))
    return result


def complete_batch_distribution(rows, key, k=4):
    """Use one selected complete calibration batch per task, never pool retries."""
    from validation.publishing.filter_tasks import baseline_issues
    counts = Counter()
    for row in rows:
        value = row.get(key) or {}
        state = value.get('status') if isinstance(value, dict) else value
        if state in (None, 'not_run', 'not_requested', 'not_applicable'):
            counts['not_run'] += 1
            counts['excluded' if baseline_issues(row) else 'awaiting'] += 1
            continue
        try:
            record = document(value.get('details')) if isinstance(value, dict) else {}
        except (ValueError, TypeError):
            record = {}
        batch = record.get('selected_batch') or {}
        wins, attempts = batch.get('successes'), batch.get('valid_attempts')
        if (state in ('pass', 'fail') and record.get('k') == k
                and type(wins) is int and type(attempts) is int and attempts == k and 0 <= wins <= k):
            counts[wins] += 1
            if state == 'pass' and wins == 0:
                counts['success_elsewhere'] += 1
        else:
            counts['incomplete'] += 1
    return counts


def render_compact(rows, metadata, dataset):
    """Consumer card generated from the same check cells as the detailed views."""
    from validation.publishing.filter_tasks import (
        REQUIRED_CHECKS, JUDGES, JUDGE_CRITERIA, EXPERIMENT, select, judge_failures,
    )
    rows = latest(rows, dataset)
    rules = metadata.get('required_rules', list(REQUIRED_CHECKS))
    kept, failed, pending = baseline(rows, rules)
    title = metadata.get('title', dataset)
    payloads = metadata.get('payload_tasks')
    payload_count = f'{payloads:,}' if isinstance(payloads, int) else UNKNOWN
    warnings = sum(any(outcome(r, rule) == 'warning' for rule in rules) for r in kept)
    intro = metadata.get('task_description') or (f"`tasks.parquet` and `results.parquet` contain all **{len(rows):,} tasks**."
             if payloads == len(rows) else
             f"There are **{payload_count} tasks** in `tasks.parquet` and results for all **{len(rows):,} tasks** in `results.parquet`.")
    if metadata.get('included_build_failures'):
        intro += (f" {metadata['included_build_failures']:,} tasks failed Docker-to-Apptainer conversion "
                  'and have no cached image.')
    if metadata.get('cache_description'):
        intro += '\n\n' + metadata['cache_description']
    sections = [f'# {title}', intro
                + (f" The other {len(rows) - payloads:,} tasks failed during the Docker-to-Apptainer image build. "
                   'They are left out of the task file, but their results explain why they failed.'
                   if isinstance(payloads, int) and len(rows) > payloads
                   and metadata.get('excluded_build_failures') == len(rows) - payloads else ''),
                '## Rule-based checks\n\n'
                f'All **{len(rows):,} tasks** are checked for setup problems, verifier behaviour and leakage in stages 0 to 3. '
                'Only tasks that pass all required checks or receive only warnings qualify for subsequent stages. '
                'Tasks that fail a check or have incomplete checks are not selected for subsequent stages.\n\n'
                + table(['Rule-check outcome', 'Tasks'], [
                    ['All applicable rules pass, no warnings', f'{len(kept) - warnings:,}'],
                    ['Passes with warnings, no failures or incomplete checks', f'{warnings:,}'],
                    ['Total retained by the rule filter', f'{len(kept):,}'],
                    ['At least one failed rule', f'{len(failed):,}'],
                    *([['Incomplete required checks, no recorded failure', f'{len(pending):,}']]
                      if pending else [])])]
    if metadata.get('incomplete_note'):
        sections.append(metadata['incomplete_note'])
    sections.append('<details>\n<summary>Show results for each rule</summary>\n\n'
                    + rule_table(rows, rules) + '\n\n</details>')
    judge_rows = []
    for alias in JUDGES:
        counts = Counter()
        for row in kept:
            failures = judge_failures(row, alias)
            category = ('incomplete' if failures is None else 'clean' if not failures
                        else next(iter(failures)) if len(failures) == 1 else 'multiple')
            counts[category] += 1
        judge_rows.append(['Qwen3.8-27B' if alias == 'qwen' else 'GLM-5.3-Flash',
                           *[f'{counts[k]:,}' for k in ('clean', *JUDGE_CRITERIA, 'multiple', 'incomplete')]])
    sections.append('## LLM-as-a-judge results\n\n'
                    f'This table covers the **{len(kept):,} tasks kept after the rule-based checks**. '
                    'A task is clean when it passes all eight criteria for that judge. '
                    'An “only” column means that one criterion fails and the other seven pass. '
                    'Tasks that fail several criteria, or still need a verdict, have their own columns, so each task is counted once per judge.\n\n'
                    + table(['Judge', 'Clean', *[f'Only {c} fails' for c in JUDGE_CRITERIA],
                             'Multiple criteria fail', 'Missing / incomplete verdicts'], judge_rows))
    models = [(key, label) for key, label in [('passk_target', 'Qwen3.5-35B-A3B'), ('passk_teacher', 'GLM-5.3-Flash')]
              if any(key in r for r in rows)]
    if models:
        distributions = [complete_batch_distribution(rows, key) for key, _ in models]
        sections.append('## Pass@4 results\n\n'
                        + table(['Successful attempts', *[label for _, label in models]],
                                [[f'{i}/4', *[f'{c[i]:,}' for c in distributions]] for i in range(5)]
                                + [['Solved (at least 1/4)', *[f'{sum(c[i] for i in range(1, 5)):,}' for c in distributions]],
                                   ['Incomplete batch', *[f'{c["incomplete"]:,}' for c in distributions]],
                                   ['Not run, excluded by rule checks', *[f'{c["excluded"]:,}' for c in distributions]],
                                   ['Awaiting evaluation or rerun', *[f'{c["awaiting"]:,}' for c in distributions]]]))
        for (_, label), counts in zip(models, distributions):
            if counts['success_elsewhere']:
                sections.append(f"{counts['success_elsewhere']:,} {label} tasks succeeded in another recorded batch "
                                'but scored 0/4 in the selected batch. They appear under 0/4 here.')
    if metadata.get('asset_note'):
        sections.append(metadata['asset_note'])
    sections.append('## Filtering\n\n'
                    'Install `pyarrow`, then put `filter_tasks.py`, `tasks.parquet` and `results.parquet` in the same directory. '
                    'The script first applies the rule-based checks above: warnings are kept, while failed or unfinished checks exclude a task. '
                    'It also checks that the results describe the same version of each task. It does not filter on pass@k.\n\n'
                    '```bash\n'
                    '# Deterministic validation and leakage checks only\n'
                    'python filter_tasks.py --out validated\n\n'
                    '# Also require every adjusted-rubric criterion to pass for one judge\n'
                    'python filter_tasks.py --judge qwen --out clean-qwen\n\n'
                    '# Allow one named criterion to fail, retain all other judge requirements\n'
                    'python filter_tasks.py --judge glm \\\n'
                    '  --allow-fail VERIFIER-ACCEPTS-INCOMPLETE --out glm-with-incomplete\n'
                    '```\n\n'
                    'Use `--criteria` to choose which judge criteria to check, otherwise, all eight are used. '
                    'Qwen and GLM are treated separately. A missing verdict excludes the task. If repeated reviews disagree, a failure takes precedence.')
    if metadata.get('experiment2'):
        cohorts = []
        for alias, model in JUDGES.items():
            sizes = [len(select(rows, alias, allowed)[0]) for allowed in EXPERIMENT.values()]
            cohorts.append(['Qwen' if alias == 'qwen' else 'GLM', *[f'{n:,}' for n in sizes]])
        sections.append('## Proposed RL experiment\n\n'
                        '**Experiment 2.1:** compare RL training on three datasets, using each judge separately: '
                        '**clean**, **clean + VERIFIER-ACCEPTS-INCOMPLETE-only**, and '
                        '**clean + VERIFIER-ACCEPTS-SHORTCUTS-only**. Clean tasks pass every selected judge criterion. '
                        'The added tasks fail only the named criterion and pass the other seven. Tasks failing both are excluded. '
                        'Both larger datasets include all the clean tasks. The judges are Qwen3.8-27B and GLM-5.3-Flash.\n\n'
                        + table(['Judge', 'Clean', 'Clean + incomplete-only', 'Clean + shortcuts-only'], cohorts)
                        + '\n\n**Hypothesis:** adding incomplete-only tasks improves performance on the held-out evaluation set compared with clean-only training, '
                        'while adding shortcuts-only tasks makes it worse. This is a proposed hypothesis, not a measured result.\n\n'
                        'Use the same model, evaluation set, seeds and training budget for all three runs. '
                        'The added groups differ in size, so also compare equally sized additions to check whether a change comes from task quality or simply more data.\n\n'
                        '```bash\n'
                        'python filter_tasks.py --judge qwen --experiment-2 --out experiment2/qwen\n'
                        'python filter_tasks.py --judge glm  --experiment-2 --out experiment2/glm\n'
                        '```\n\n'
                        'Each command writes `clean/`, `clean-plus-incomplete/` and `clean-plus-shortcuts/`, each with '
                        '`tasks.parquet` and `results.parquet`. A `selection.json` file records which tasks were chosen. '
                        'The counts above use the current task versions. Repaired tasks are left out until their new judge verdicts are available.')
    if metadata.get('resource_note'):
        sections.append('## Task resources\n\n' + metadata['resource_note'])
    return '\n\n'.join(sections) + '\n', kept


def render(rows, metadata, view, dataset, annotation=None, evaluation_key=None, student_key=None):
    if metadata.get('compact'):
        return render_compact(rows, metadata, dataset)
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
        sections += ['## Validation rules\n\n' + rule_table(rows, rules) +
                     '\n\nCounts and percentages use all tasks with a latest result in this dataset, including excluded tasks. '
                     'The table includes required rules and other checks with recorded outcomes. '
                     'Failed and Warning show final check outcomes. Unresolved combines infrastructure errors, incomplete checks and checks not run. '
                     'Automatically repaired counts tasks with a recorded automatic pipeline repair, regardless of the final outcome; '
                     'it overlaps the other columns and does not include manual repairs. Installing a timestamp repair script alone does not count as executing a repair. '
                     'A task can be flagged by several rules. Warnings do not remove tasks. Unresolved tasks are excluded from v1 until their required checks finish.']
    else:
        sections += ['## Evaluation settings\n\n' + table(['Setting', 'Value'],
                     [(key, json.dumps(value) if isinstance(value, (dict, list)) else value)
                      for key, value in config.items() if key not in ('model', 'k', 'network')]) ,
                     '## Results\n\n' + distribution + '\n\nTasks with at least one recorded success are kept. Incomplete attempts stay outside the fixed-k distribution.']
    sections.append('## Resources\n\n' + table(['Resource', 'Per task', 'Tasks'], task_resources(selected)))
    used = resource_use(selected)
    if used:
        sections.append('Measured in the reference run, including task setup:\n\n'
                        + table(['Resource use', 'Mean', 'p95', 'Max', 'Tasks'], used))
    sections += ['## Timing\n\n' + table(['Measurement', 'Value'], [
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
    sections.append('## Files\n\n`results.parquet` has one row per task with its checks and evaluations. '
                    f'`{view}/selection.parquet` lists the selected tasks and their content hashes; '
                    '`tasks/` holds the payloads. Only new or changed tasks need a new payload file.')
    return '\n\n'.join(sections) + '\n', selected


def write(results_path, metadata, out, view, dataset, annotation=None, evaluation_key=None, student_key=None):
    import pyarrow as pa
    import pyarrow.parquet as pq
    text, selected = render(results.read(results_path), metadata, view, dataset, annotation, evaluation_key, student_key)
    out = Path(out) / view
    out.mkdir(parents=True, exist_ok=True)
    names = ('dataset', 'task_id', 'content_sha256')
    schema = pa.schema([(name, pa.string()) for name in names])
    pq.write_table(pa.Table.from_pylist([{n: row[n] for n in names} for row in selected], schema=schema),
                   out / 'selection.parquet')
    (out / 'README.md').write_text(text)
    (out / 'run.json').write_text(json.dumps(metadata, indent=2) + '\n')
    if view == 'v1':
        stats = rule_statistics(latest(results.read(results_path), dataset), metadata.get('required_rules', []))
        (out / 'rule-stats.json').write_text(json.dumps(stats, indent=2) + '\n')
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
    parser.add_argument('--compact', action='store_true', help='Generate a compact consumer card with filtering commands')
    args = parser.parse_args()
    metadata = json.loads(args.run.read_text())
    if args.compact:
        metadata['compact'] = True
    write(args.results, metadata, args.out, args.view, args.dataset,
          json.loads(args.annotation.read_text()) if args.annotation else None,
          args.evaluation_key, args.student_key)


if __name__ == '__main__':
    main()
