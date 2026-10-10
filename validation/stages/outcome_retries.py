"""One retry rule for the gated container stages, applied after the whole run.

The pipeline runs its configured stages once. Then every task is classified the
way the publisher classifies it, passed, archive or not run, and the tasks
without a result are rerun in the stages that produced none, up to
``--outcome-retries`` more attempts (stage 3 first, then 4, then 5, so a build
that recovers lets its trials run in the same round). A wrong reward or a failed
build is a result and is not retried. A task still without a result after the
last attempt is archived as not robust under this infrastructure, so one run
always ends in a decision for every task.

With an expected-outcomes file (``--expected-outcomes``, frozen into the
contract) the classifier is not used for the tasks it names: the outcome of each
stage is compared with the stages the earlier audit passed for the same task
content, any difference is rerun the same way, and a task that never reproduces
the audit is archived as not robust with every observed outcome. Tasks absent
from the file, or with another content hash, follow the classifier rule.

Stage 1 is left out: its checks are deterministic and the pipeline rewrites some
task files itself before running them.
"""
from collections import Counter
import json
from pathlib import Path

FORMAT = 'ot-expected-outcomes-v1'
STAGES = (3, 4, 5)


def load_expectations_file(path):
    data = json.loads(Path(path).read_text())
    load_expectations(path)
    return data


def load_expectations(path):
    data = json.loads(Path(path).read_text())
    if data.get('format') != FORMAT or not isinstance(data.get('tasks'), dict):
        raise ValueError(f'{path} is not an expected-outcomes file ({FORMAT})')
    for task, record in data['tasks'].items():
        if not isinstance(record.get('sha256'), str) or not isinstance(record.get('stages_passed'), list):
            raise ValueError(f'expected outcome of {task} needs sha256 and stages_passed')
    return data['tasks']


def expectation(expected, hashes, task):
    """The earlier audit's record for this exact task content, else None."""
    if expected is None:
        return None
    record = expected.get(task)
    if record is None or hashes.get(task) != record['sha256']:
        return None
    return record


def outcome_of(stage, item):
    from validation.publishing.publish import judge
    return judge(stage, item)[0]


def mismatch(stage, outcome, record):
    if record is None:
        return outcome == 'not_run'
    return (outcome == 'passed') != (stage in record['stages_passed'])


def retry_round(stage_runs, args, contract, batch_for, save):
    """Rerun tasks without a settled outcome across the gated stages of one run.

    ``stage_runs`` maps a stage number to ``(report_path, report)``; the reports
    are updated in place and saved through ``save(path, report)`` after every
    rerun so later stages see earlier results. ``batch_for(stage)`` returns a
    callable ``(task_paths, out_dir) -> items`` that runs the stage again for
    those tasks. Returns the per-stage summaries, or None when retries are off.
    """
    retries = int(getattr(args, 'outcome_retries', 0) or 0)
    stages = [n for n in STAGES if n in stage_runs]
    if retries <= 0 or getattr(args, 'dry_run', False) or not stages:
        return None
    expected_path = getattr(args, 'expected_outcomes', None)
    expected_file = load_expectations_file(expected_path) if expected_path else None
    expected = expected_file['tasks'] if expected_file else None
    from validation.contract import task_records
    hashes = {t['task_id']: t['sha256'] for t in task_records(contract)} if contract else {}

    def by_task(number):
        return {Path(i['task']).name: i for i in stage_runs[number][1]['items'] if i.get('task')}

    history = {n: {} for n in stages}
    paths = {}
    for n in stages:
        for task, item in by_task(n).items():
            paths.setdefault(task, Path(item['task']))
            record = expectation(expected, hashes, task)
            history[n][task] = {'mode': 'expected' if record else 'classifier',
                                'expected': (('passed' if n in record['stages_passed'] else 'not passed')
                                             if record else 'result'),
                                'outcomes': [outcome_of(n, item)]}

    def pending(n):
        return sorted(task for task, entry in history[n].items()
                      if mismatch(n, entry['outcomes'][-1], expectation(expected, hashes, task)))

    for attempt in range(1, retries + 1):
        if not any(pending(n) for n in stages):
            break
        for n in stages:
            tasks = pending(n)
            if n in (4, 5) and 3 in stages:
                # A trial needs its container; wait for the build to come back first.
                tasks = [t for t in tasks if history[3].get(t, {}).get('outcomes', ['passed'])[-1] == 'passed']
            if not tasks:
                continue
            report_path, report = stage_runs[n]
            retry_out = report_path.parent / f'retry-{attempt}'
            retry_out.mkdir(parents=True, exist_ok=True)
            fresh = {Path(i['task']).name: i for i in batch_for(n)([paths[t] for t in tasks], retry_out)}
            current = by_task(n)
            for task in tasks:
                item, new = current[task], fresh[task]
                previous = [dict(a) for a in item.get('outcome_attempts', [])]
                previous.append({k: v for k, v in item.items() if k != 'outcome_attempts'})
                item.clear()
                item.update(new)
                item['outcome_attempts'] = previous
                history[n][task]['outcomes'].append(outcome_of(n, item))
            report['has_findings'] = any(i['status'] in ('failed', 'findings', 'error') for i in report['items'])
            save(report_path, report)

    summaries = {}
    for n in stages:
        current = by_task(n)
        for task in pending(n):
            entry = history[n][task]
            item = current[task]
            item['status'] = 'failed'
            item['not_robust'] = {'expected': entry['expected'], 'observed': entry['outcomes']}
            item.setdefault('findings', []).append(not_robust_text(entry['expected'], entry['outcomes']))
        summaries[n] = summarize(history[n], expected_path, retries, n)
        if expected_file:
            summaries[n]['expected_outcomes_note'] = expected_file.get('note')
        report_path, report = stage_runs[n]
        report['outcome_retries'] = summaries[n]
        report['has_findings'] = any(i['status'] in ('failed', 'findings', 'error') for i in report['items'])
        save(report_path, report)
    return summaries


def not_robust_text(expected, observed):
    seen = ', '.join(observed)
    if expected == 'result':
        return f'not robust: no result in {len(observed)} attempts under this infrastructure; observed {seen}'
    return f'not robust: the earlier audit {expected} this stage; observed {seen} over {len(observed)} attempts'


def summarize(history, expected_path, retries, number):
    matched_after = Counter()
    summary = {'mode': 'expected' if expected_path else 'classifier',
               'expected_outcomes': str(expected_path) if expected_path else None,
               'max_retries': retries, 'stage': number,
               'tasks_with_expectation': 0, 'matched_first_attempt': 0, 'matched_after_retries': {},
               'not_robust': [], 'retried_not_run': 0, 'recovered_not_run': 0, 'unresolved_not_run': [],
               'history': history}
    for task, entry in history.items():
        outcomes = entry['outcomes']
        if entry['mode'] == 'expected':
            summary['tasks_with_expectation'] += 1
            final_matches = (outcomes[-1] == 'passed') == (entry['expected'] == 'passed')
            if len(outcomes) == 1:
                summary['matched_first_attempt'] += 1
            elif final_matches:
                matched_after[len(outcomes) - 1] += 1
            else:
                summary['not_robust'].append(task)
        elif len(outcomes) > 1:
            summary['retried_not_run'] += 1
            if outcomes[-1] != 'not_run':
                summary['recovered_not_run'] += 1
            else:
                summary['unresolved_not_run'].append(task)
    summary['matched_after_retries'] = {str(k): v for k, v in sorted(matched_after.items())}
    return summary
