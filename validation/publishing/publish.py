#!/usr/bin/env python3
"""Publish validation reports and kept/archived tasks as a Hugging Face pull request."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from validation.contract import read, task_records
from validation.data.materialize import parquet_files
from validation.data.selection import discover_tasks
from validation.publishing.outcome_labels import execution_label, labels as outcome_labels
from validation.publishing import patch_provenance
from validation.publishing.pr_tables import cell, primary_label, label_table

GATES = (1, 3, 4, 5)
# Start failures that come from the node or the bridge, not from the task's image.
NODE_FAILURE = re.compile(r'timed out after|No space left on device|no LD_PRELOAD in fakeroot|BridgeOutage|workers? (?:are )?dead', re.I)
COLUMNS = ('path', 'task_binary', 'content_sha256', 'stages_passed', 'archive_stage', 'archive_reason', 'run',
           'validation_labels', 'archive_labels', 'patch_labels', 'patch_changed', 'source_task_id')


def stage_reports(source):
    """Newest report of each stage: `stage-N-ID.json` files or `stage_N_name/ID/summary.json`."""
    source, found = Path(source), {}
    paths = [*source.glob('stage-*.json'), *source.glob('stage_*/*/summary.json')]
    for path in sorted(paths, key=lambda p: p.stat().st_mtime):
        report = json.loads(path.read_text())
        found[int(report['stage'])] = report
    if not found:
        raise ValueError(f'no stage reports in {source}')
    return found


def failed_checks(item, not_required=(), status='failed'):
    return sorted(c['check'] for c in item.get('checks', [])
                  if c['status'] == status and c['check'] not in not_required)


def require_complete(reports_dir, contract_path, skipped_stages=None):
    """Prevent an interrupted automatic run from opening a partial-data PR."""
    frozen = read(contract_path)
    skipped_stages = skipped_stages or {}
    if skipped_stages and (set(skipped_stages) != {'4'} or frozen['stages'] != [1, 3, 5] or
            not isinstance(skipped_stages['4'], str) or not skipped_stages['4'].strip()):
        raise ValueError('Only an explicit no-reference-solution stage-4 exception is supported')
    expected = {t['task_id'] for t in task_records(frozen)}
    reports = stage_reports(reports_dir)
    for stage in GATES:
        if str(stage) in skipped_stages:
            if stage in reports:
                raise ValueError('A stage cannot be both reported and explicitly not run')
            continue
        report = reports.get(stage, {})
        items = report.get('items', [])
        if (not report.get('complete') or report.get('dry_run') or
                report.get('contract_sha256') != frozen['sha256'] or
                len(items) != len(expected) or {Path(i['task']).name for i in items} != expected or
                any(judge(stage, item)[0] == 'not_run' and not build_blocked(stage, item, reports) for item in items)):
            raise ValueError(f'automatic PR deferred: stage {stage} lacks complete task outcomes')


def build_blocked(stage, item, reports):
    """A deliberate downstream skip is complete only with matching build evidence."""
    if stage not in (4, 5) or item.get('status') != 'skipped' or item.get('blocked_by_stage') != 3:
        return False
    build = reports.get(3, {})
    return bool(build.get('complete') and not build.get('dry_run') and any(
        Path(previous['task']).name == Path(item['task']).name and judge(3, previous)[0] == 'archive'
        for previous in build.get('items', [])))


def judge(stage, item, not_required=()):
    """(outcome, reason): outcome is 'passed', 'archive' or 'not_run'."""
    status = item.get('status')
    if item.get('not_robust'):
        from validation.stages.outcome_retries import not_robust_text
        return 'archive', not_robust_text(item['not_robust']['expected'], item['not_robust']['observed'])
    if stage == 1:
        if status == 'error':          # the checks could not be run on this task at all
            return 'not_run', 'static checks could not run: ' + str(item.get('error', 'unknown'))[:300]
        failed = failed_checks(item, not_required)
        if failed:
            return 'archive', 'static checks failed: ' + ', '.join(failed)
        # A check that timed out or crashed has not judged the task.
        errors = failed_checks(item, not_required, 'error')
        return ('not_run', 'static checks did not finish: ' + ', '.join(errors)) if errors else ('passed', '')
    if stage in (4, 5) and execution_label(item):
        return 'not_run', '; '.join(map(str, item.get('findings', [])))[:300] or str(item.get('reason') or status)
    if status == 'passed':
        return 'passed', ''
    if stage == 3:
        if item.get('build_stability') == 'unstable_build' and len(item.get('build_attempts', [])) == 4:
            return 'archive', item['reason']
        reason = item.get('reason') or '; '.join(
            str(e.get('error') or [c['check'] for c in e.get('checks', []) if c.get('status') in ('failed', 'error')])
            for e in item.get('environments', []) if e.get('status') != 'passed')
        if (status == 'error' and not item.get('environments')) or NODE_FAILURE.search(reason or ''):
            return 'not_run', 'node or bridge failure: ' + reason[:300]
        return 'archive', 'container did not build or start: ' + (reason or status or 'unknown')[:300]
    findings = [str(f) for f in item.get('findings', [])]
    # A crashed or skipped trial says nothing about the task's reward.
    if status in ('skipped', 'previewed', 'error') or any('exception' in f or 'missing numeric reward' in f
                                                          or 'trials, found' in f for f in findings):
        return 'not_run', '; '.join(findings)[:300] or str(item.get('reason') or status)
    wanted = 1 if stage == 4 else 0
    return 'archive', f"{'oracle' if stage == 4 else 'no-answer'} reward {item.get('rewards')} instead of {wanted}"


def decide(reports, task_ids, not_required=()):
    """Per task: stages passed in order, and the first stage that archives it."""
    results = {}
    for stage in GATES:
        report = reports.get(stage)
        if report is None or not report.get('complete') or report.get('dry_run'):
            continue
        for item in report['items']:
            name = Path(item.get('task', '')).name
            if name in task_ids:
                results.setdefault(name, {})[stage] = judge(stage, item, not_required)
    decisions = {}
    for task in task_ids:
        passed, archive, not_run = [], None, {}
        for stage in GATES:
            outcome, reason = results.get(task, {}).get(stage, ('not_run', 'stage was not run'))
            if outcome == 'passed':
                passed.append(stage)
            elif outcome == 'archive':
                archive = (stage, reason)
                break
            elif stage in reports:
                not_run[stage] = reason
        decisions[task] = {'passed': passed, 'archive': archive, 'not_run': not_run}
    return decisions


def task_seconds(stage, item):
    """Measured time of one task in a stage: its checks, its container start, inspection and stop,
    or its trials. None when the report recorded no times."""
    if stage == 1:
        values = [c.get('duration_seconds') for c in item.get('checks', [])]
    elif stage == 3:
        values = [v for e in item.get('environments', []) for v in (e.get('timings_seconds') or {}).values()]
    else:
        values = item.get('trial_seconds') or []
    values = [v for v in values if isinstance(v, (int, float))]
    return sum(values) if values else None


def stage_timings(reports):
    """Per stage: node, concurrency and wall time from the report, and the spread of per-task times."""
    timings = {}
    for stage, report in sorted(reports.items()):
        if not report.get('timing') or not report.get('complete') or report.get('dry_run'):
            continue
        seconds = sorted(s for s in (task_seconds(stage, item) for item in report['items']) if s is not None)
        timings[str(stage)] = {**report['timing'], 'tasks': len(report['items']), 'tasks_timed': len(seconds)}
        if seconds:
            timings[str(stage)]['task_seconds'] = {
                'median': round(seconds[len(seconds) // 2], 1), 'p90': round(seconds[int(len(seconds) * .9)], 1),
                'max': round(seconds[-1], 1)}
    return timings


def folder_of(task, mapping):
    """Data source folder of a task: its mapped prefix, the '*' catch-all, else the prefix itself."""
    prefix = re.sub(r'-\d+$', '', task)
    return mapping.get(prefix) or mapping.get('*') or prefix


def commit_title(run, what='validation run'):
    """Lead the commit and pull-request title with the data source, so PR lists scan by dataset."""
    match = re.fullmatch(r'\d{4}-\d{2}-\d{2}-(.+)-[0-9a-f]{12}', run or '')
    name = match.group(1) if match else ''
    return f'{name}: {what} {run}' if name else f'{what[0].upper()}{what[1:]} {run}'


def run_name(folders):
    """What a run covered, for its file name: the folder, or the words the folders share."""
    parts = [f.split('-') for f in sorted(set(folders))]
    if not parts:
        return ''
    shared = []
    for words in zip(*parts):
        if len(set(words)) > 1:
            break
        shared.append(words[0])
    return '-'.join(shared) if shared else 'mixed'


def previous_rows(repo, folder, token=None):
    """Rows already published for this data source, by task ID; empty for a new source."""
    from huggingface_hub import hf_hub_download
    from huggingface_hub.utils import EntryNotFoundError, RepositoryNotFoundError
    import pyarrow.parquet as pq
    rows = {}
    for name in ('tasks.parquet', 'archive.parquet'):
        try:
            path = hf_hub_download(repo, f'{folder}/{name}', repo_type='dataset', token=token)
        except (EntryNotFoundError, RepositoryNotFoundError):
            continue
        available = pq.read_schema(path).names
        for row in pq.read_table(path, columns=[c for c in COLUMNS if c != 'task_binary' and c in available]).to_pylist():
            rows[row['path']] = row
    return rows


def pack_task(task_dir):
    """A task directory as the gzip tar TaskTrove stores in task_binary (relative paths, no owner info)."""
    import io, tarfile
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w:gz') as tar:
        for path in sorted(p for p in Path(task_dir).rglob('*') if p.is_file()):
            info = tar.gettarinfo(path, arcname=path.relative_to(task_dir).as_posix())
            info.uid = info.gid = 0
            info.uname = info.gname = ''
            with path.open('rb') as stream:
                tar.addfile(info, stream)
    return buffer.getvalue()


def task_rows(source):
    """(task_id, task_binary) from Parquets, or from a directory of task directories."""
    import pyarrow.parquet as pq
    parquets = parquet_files(source)
    if parquets:
        for path in parquets:
            for batch in pq.ParquetFile(path).iter_batches(batch_size=64):
                for row in batch.to_pylist():
                    yield row['path'], row['task_binary']
    else:
        for task in discover_tasks(Path(source)):
            yield task.name, pack_task(task)


def build(contract, reports, mapping, not_required=(), previous=None, run_id=None):
    """Tables per data source and the run record. `previous(folder)` returns published rows."""
    hashes = {t['task_id']: t['sha256'] for t in task_records(contract)}
    decisions = decide(reports, hashes, not_required)
    task_labels = {}
    task_items = {}
    for stage, report in reports.items():
        if report.get('complete') and not report.get('dry_run'):
            for item in report.get('items', []):
                task_labels.setdefault(Path(item.get('task', '')).name, {})[stage] = outcome_labels(stage, item, not_required)
                task_items.setdefault(Path(item.get('task', '')).name, {})[stage] = item
    name = run_name(folder_of(task, mapping) for task in hashes)
    run_id = run_id or '-'.join(filter(None, [f'{datetime.now(timezone.utc):%Y-%m-%d}', name, contract['sha256'][:12]]))
    run_file = f'runs/{run_id}.json'
    tables, counts, archive_details = {}, {}, {}
    for task, task_binary in task_rows(contract['arguments']['tasks']):
        if task not in hashes:
            continue
        folder = folder_of(task, mapping)
        kept, archived = tables.setdefault(folder, ([], []))
        count = counts.setdefault(folder, {'tasks': 0, 'kept': 0, 'archived': 0,
            'archived_by_stage': Counter(), 'archive_reasons': Counter(), 'not_run_by_stage': Counter(),
            'not_run_reasons': Counter(), 'not_run_examples': {}})
        decision = decisions[task]
        before = (previous(folder) if previous else {}).get(task)
        passed, archive = list(decision['passed']), decision['archive']
        # Results carry over only for unchanged content; an earlier archive decision stands.
        if before and before.get('content_sha256') == hashes[task]:
            passed = sorted({*passed, *(int(s) for s in (before.get('stages_passed') or '').split(',') if s)})
            if before.get('archive_stage') is not None and archive is None:
                archive = (before['archive_stage'], before['archive_reason'])
                run_of_row = before['run']
            else:
                run_of_row = run_file
        else:
            run_of_row = run_file
        if archive:
            passed = [s for s in passed if s < archive[0]]
        record = {'path': task, 'task_binary': task_binary, 'content_sha256': hashes[task],
                  'stages_passed': ','.join(str(s) for s in passed),
                  'archive_stage': archive[0] if archive else None,
                  'archive_reason': archive[1] if archive else None, 'run': run_of_row,
                  'validation_labels': sorted({label for labels in task_labels.get(task, {}).values() for label in labels})}
        record['archive_labels'] = []
        if archive:
            carried = run_of_row != run_file
            item = {} if carried else task_items.get(task, {}).get(archive[0], {})
            labels = ((before or {}).get('archive_labels') or []) if carried else task_labels.get(task, {}).get(archive[0], [])
            label = primary_label(labels, item.get('failure_label'))
            record['archive_labels'] = [label]
            archive_details.setdefault(folder, []).append({
                'task': task, 'label': label,
                'explanation': item.get('failure_explanation') or archive[1]})
        (archived if archive else kept).append(record)
        count['tasks'] += 1
        count['archived' if archive else 'kept'] += 1
        if archive:
            count['archived_by_stage'][str(archive[0])] += 1
            count['archive_reasons'][re.sub(r'\[.*?\]', '[..]', archive[1])[:160]] += 1
            bucket = count.setdefault('archive_labels', Counter())
            bucket.update(record['archive_labels'])
        for stage, reason in decision['not_run'].items():
            count['not_run_by_stage'][str(stage)] += 1
            short = re.sub(r'[0-9a-f]{12,}|[0-9]{3,}', 'N', str(reason))[:200]
            key = f'{stage}: {short}'
            count['not_run_reasons'][key] += 1
            count['not_run_examples'].setdefault(key, [])
            if len(count['not_run_examples'][key]) < 3:
                count['not_run_examples'][key].append(task)
    profile = contract['execution_profile']
    record = {
        'run': run_id, 'name': name, 'created_at': datetime.now(timezone.utc).isoformat(),
        'contract_sha256': contract['sha256'], 'dataset': contract['dataset']['source'],
        'dataset_revision': contract['dataset']['revision'], 'stages_in_contract': contract['stages'],
        'stages_reported': sorted(s for s, r in reports.items() if r.get('complete') and not r.get('dry_run')),
        'execution_profile': {k: profile.get(k) for k in ('backend', 'architecture', 'network', 'network_guarantee', 'privileges', 'validation_container_reuse')},
        'static_checkpoint': contract.get('static_checkpoint'),
        'static_resume': reports.get(1, {}).get('resumed_from'),
        'static_checks': contract['success_criteria']['static_checks'],
        'static_exclusions': contract['success_criteria']['static_exclusions'],
        'not_required_checks': sorted(not_required),
        'rules': {'1': 'archive if any static check fails', '3': 'archive if the container does not build or start',
                  '4': 'archive if the oracle reward is not 1', '5': 'archive if the no-answer reward is not 0',
                  'other': 'never archive'},
        'validation_code': 'https://github.com/MLI-lab/OT-next-data/tree/main/validation',
        'stage_timings': stage_timings(reports),
        'data_sources': {folder: {k: (dict(v) if isinstance(v, Counter) else v) for k, v in count.items()}
                         for folder, count in sorted(counts.items())},
    }
    record['infrastructure_retries'] = {str(stage): report['retry_sources']
        for stage, report in reports.items() if report.get('retry_sources')}
    record['outcome_retries'] = {str(stage): report['outcome_retries']
        for stage, report in reports.items() if report.get('outcome_retries')}
    record['archive_details'] = archive_details
    record['task_findings'] = {}
    for stage, report in reports.items():
        if not report.get('complete') or report.get('dry_run'):
            continue
        for item in report.get('items', []):
            task = Path(item.get('task', '')).name
            labels = outcome_labels(stage, item, not_required)
            if task in hashes and labels:
                finding = {'stage': stage, 'labels': labels}
                # Preserve optional explanations and retry evidence without requiring
                # them from every stage or generating new judgments during publishing.
                finding.update({key: item[key] for key in (
                    'failure_label', 'failure_explanation', 'build_stability',
                    'build_attempts', 'recheck_evidence') if item.get(key) is not None})
                record['task_findings'].setdefault(task, []).append(finding)
    return tables, record, run_file


ANALYSIS_PROMPT = """You review the outcome of a validation run over a dataset of agent tasks.
The run summary at the end lists, for each data source, how many tasks were kept or archived,
the archive reasons with counts and example task IDs, and the reasons why some stages did not run.
Stage 1 = static checks, 3 = container build, 4 = oracle solution must get reward 1,
5 = doing nothing must get reward 0.

Group the archived and not-run tasks by cause and say, for each group, whether it looks like
(a) a defect of the tasks, (b) an infrastructure or tooling failure, or (c) a check that does
not fit this dataset. Recommend one action per group: rerun, exclude-check, fix-tasks,
keep-archived or investigate, with one or two sentences of reasoning based only on the run summary.
Do not invent facts that are not in it.

Answer with JSON only, of this form:
{"groups": [{"name": "...", "tasks": <count>, "stages": [..], "cause": "task|infrastructure|check|unclear",
             "action": "rerun|exclude-check|fix-tasks|keep-archived|investigate", "reasoning": "..."}],
 "overview": "two or three sentences"}

Run summary:
"""


def run_summary(record, tables):
    """Input for the analysis: counts, reasons and a few example IDs, no task content or logs."""
    digest = {'data_sources': {}}
    for folder, (kept, archived) in sorted(tables.items()):
        examples = {}
        for row in archived:
            examples.setdefault((row['archive_stage'], re.sub(r'\[.*?\]', '[..]', row['archive_reason'] or '')[:160]), []).append(row['path'])
        counts = record['data_sources'][folder]
        digest['data_sources'][folder] = {
            'tasks': counts['tasks'], 'kept': counts['kept'],
            'archived': [{'stage': stage, 'reason': reason, 'tasks': len(ids), 'examples': ids[:3]}
                         for (stage, reason), ids in sorted(examples.items(), key=lambda kv: -len(kv[1]))],
            'not_run_by_stage': counts['not_run_by_stage'],
            'not_run': [{'stage_and_reason': key, 'tasks': n, 'examples': counts.get('not_run_examples', {}).get(key, [])}
                        for key, n in sorted(counts.get('not_run_reasons', {}).items(), key=lambda kv: -kv[1])[:12]]}
    digest['static_checks_run'] = record['static_checks']
    digest['static_checks_excluded'] = record['static_exclusions']
    digest['stages_reported'] = record['stages_reported']
    return digest


def run_llm(prompt, model='sonnet', timeout=600):
    """One non-interactive call through the Claude Code login on this machine."""
    import subprocess
    command = ['claude', '-p', '--model', model, '--output-format', 'json', '--tools', '']
    result = subprocess.run(command, input=prompt, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout).strip()[-500:] or f'exit {result.returncode}')
    output = json.loads(result.stdout)
    match = re.search(r'\{.*\}', output.get('result', ''), re.S)
    if not match:
        raise ValueError('the model did not answer with JSON')
    answer = json.loads(match.group(0))
    answer['model'] = ', '.join(sorted(output.get('modelUsage') or {})) or model
    return answer


def analyse(record, tables, model='sonnet', call=run_llm):
    """Optional: a model groups the archived and not-run tasks and suggests what to do.
    Any failure (no login, no quota, bad answer) skips the step and is recorded."""
    digest = run_summary(record, tables)
    if not any(s['archived'] or s['not_run_by_stage'] for s in digest['data_sources'].values()):
        return {'status': 'skipped', 'reason': 'nothing was archived or left unrun'}
    try:
        answer = call(ANALYSIS_PROMPT + json.dumps(digest, indent=1), model)
        groups = answer['groups']
        assert isinstance(groups, list) and all({'name', 'action', 'cause'} <= set(g) for g in groups)
        return {'status': 'done', 'model': str(answer.get('model', model)), 'prompt': ANALYSIS_PROMPT,
                'run_summary': digest, 'groups': groups, 'overview': str(answer.get('overview', answer.get('summary', '')))}
    except Exception as exc:
        return {'status': 'skipped', 'reason': f'{type(exc).__name__}: {str(exc)[:300]}', 'run_summary': digest}


def analysis_text(analysis):
    if not analysis or analysis.get('status') != 'done':
        return ''
    lines = ['', f"## Analysis (written by {analysis.get('model', 'a model')}, advisory)", '', analysis['overview'], '',
             '| Group | Tasks | Stages | Cause | Suggested action |', '| --- | --- | --- | --- | --- |']
    for g in analysis['groups']:
        stages = ', '.join(str(s) for s in g.get('stages', [])) or '-'
        lines.append(f"| {g['name']} | {g.get('tasks', '?')} | {stages} | {g['cause']} | {g['action']} |")
    lines += [''] + [f"- **{g['name']}:** {g.get('reasoning', '')}" for g in analysis['groups']]
    return '\n'.join(lines) + '\n'


def reproducibility_text(retries):
    """Stage 4 and 5 reruns: how often a single run reproduced the earlier audit, or recovered a crash."""
    if not retries:
        return []
    lines = ['### Reproducibility and retries', '',
             '| Stage | Mode | Tasks with expectation | Matched first attempt | Matched after 1 / 2 / 3+ retries | Archived as not robust | Not-run retried | Recovered | Unresolved |',
             '| --- | --- | --- | --- | --- | --- | --- | --- | --- |']
    not_robust = []
    for stage, summary in sorted(retries.items()):
        after = summary.get('matched_after_retries', {})
        later = sum(v for k, v in after.items() if int(k) >= 3)
        lines.append(f"| {stage} | {summary['mode']} | {summary['tasks_with_expectation']} | {summary['matched_first_attempt']} "
                     f"| {after.get('1', 0)} / {after.get('2', 0)} / {later} | {len(summary['not_robust'])} "
                     f"| {summary['retried_not_run']} | {summary['recovered_not_run']} | {len(summary['unresolved_not_run'])} |")
        not_robust += [f'stage {stage}: {cell(task)}' for task in summary['not_robust']]
    lines += ['', 'Expected outcomes come from an earlier audit of the same task content (matched by content hash). '
              'A task whose result differs from that audit is rerun up to the retry limit; a task that never matches is archived as not robust. '
              'Without an expectation only tasks with no result (crash, node failure, missing reward or missing runner evidence) are rerun, and a task still without a result after the last attempt is archived as not robust. Stages 3, 4 and 5 are covered; the retries run after the whole pipeline.', '']
    notes = {s.get('expected_outcomes_note') for s in retries.values() if s.get('expected_outcomes_note')}
    for note in sorted(notes):
        lines += ['Expected outcomes: ' + cell(note), '']
    if not_robust:
        lines += ['Archived as not robust: ' + '; '.join(not_robust), '']
    return lines


def description(record, repo=None, revision=None):
    lines = []
    lines += reproducibility_text(record.get('outcome_retries'))
    for stage, reason in record.get('skipped_stages', {}).items():
        lines += [f'> Stage {cell(stage)} not run: {cell(reason)}', '']
    provenance = record.get('review_provenance', {})
    if (provenance.get('workflow') == 'agent_patch_repair_loop' and
            provenance.get('human_audited') is False):
        lines += ['> These results were produced by an automated agent patch-and-repair loop '
                  'and have not been independently audited by a human.', '']
    if summary := record.get('patch_repair_summary'):
        seconds = summary.get('elapsed_seconds')
        elapsed = 'Not recorded' if seconds is None else f'{seconds / 3600:.2f} hours'
        estimate = summary.get('estimated_cost_usd')
        cost = ('Not available' if estimate is None else
                f"${estimate:.4f} estimated ({summary.get('cost_reported_attempts', 0)}/{summary['agent_attempts']} attempts reported)")
        lines += ['### Agent patch and repair loop', '', '| Metric | Result |', '| --- | --- |',
                  f"| Completed repair rounds | {summary['repair_rounds']} |",
                  f"| Completed infrastructure retry rounds | {summary['infrastructure_retry_rounds']} |",
                  f"| Recovery attempts | {summary.get('recovery_attempts', 0)} |",
                  f"| Supervisor reviews | {summary.get('supervisor_reviews', 0)} |",
                  f"| Recorded agent attempts | {summary['agent_attempts']} |",
                  f"| Usage limit events | {summary['usage_limit_events']} |",
                  f'| Elapsed time | {elapsed} |',
                  f'| Cost | {cost} |', '', cell(summary['elapsed_scope']), '',
                  cell(summary['cost_note']), '']
        sessions = summary.get('agent_sessions', [])
        if sessions:
            lines += ['| Agent role | Provider | Configured model | Completed sessions |',
                      '| --- | --- | --- | --- |']
            groups = {}
            for session in sessions:
                key = (session['role'], session['provider'], session.get('model') or 'CLI default (not recorded)')
                groups[key] = groups.get(key, 0) + 1
            for (role, provider, model), count in sorted(groups.items()):
                lines.append(f'| {cell(role)} | {cell(provider)} | {cell(model)} | {count} |')
            lines.append('')
        if not any(session['role'] == 'final_failure_reviewer' for session in sessions):
            lines += ['No completed final failure reviewer session was recorded.', '']
    for folder, counts in record['data_sources'].items():
        evidence = record.get('patch_provenance', {}).get(folder, {})
        source = evidence.get('source') or {'dataset': record['dataset'], 'revision': record.get('dataset_revision')}
        source_text = cell(source['dataset'])
        if source.get('url'):
            source_text = f"[{source_text}]({source['url']})"
        revision_text = f" at commit `{cell(source['revision'])}`" if source.get('revision') else ''
        patches = evidence.get('patches', [])
        versioned = [p for p in patches if p.get('patcher_sha256') or p.get('patcher')] or patches
        versions = list(dict.fromkeys(p['version'] for p in versioned if p.get('version')))
        patch_text = ' Patch version: ' + ', '.join(f'`{cell(v)}`' for v in versions) + '.' if versions else ''
        lines += [f'## {cell(folder)}', '',
                  f"Keeps **{counts['kept']:,} tasks** and archives **{counts['archived']:,}** "
                  f"from {source_text}{revision_text}.{patch_text}", '']
        details = record.get('archive_details', {}).get(folder, [])
        lines += label_table(((r['task'], r['label'], r.get('explanation')) for r in details), 'Archive label')
        if counts.get('not_run_by_stage'):
            lines += ['', 'Incomplete checks (tasks per stage): ' + ', '.join(
                f'{stage}: {count}' for stage, count in sorted(counts['not_run_by_stage'].items())) + '.']
        changes = patch_provenance.render(record, folder)
        if changes:
            lines += ['', changes.rstrip()]
        lines += ['']
    if record.get('infrastructure_retries'):
        retried = {task for attempts in record['infrastructure_retries'].values()
                   for attempt in attempts for task in attempt['task_ids']}
        lines += [f'Infrastructure retries covered **{len(retried):,} tasks**. '
                  'The latest compatible outcomes determine retention; earlier attempts and '
                  'execution contract references are preserved in the run record.', '']
    from validation.publishing.pr_runtime import render as render_runtime
    lines += [render_runtime(record).rstrip(), '']
    excluded = {k: v for k, v in record['static_exclusions'].items()}
    if excluded or record['not_required_checks']:
        lines += ['', f"Static checks excluded by the contract: {len(excluded)}. "
                      f"Recorded but not required: {', '.join(record['not_required_checks']) or 'none'}. "
                      'Reasons are in the run file.']
    if record.get('environment_images'):
        images = record['environment_images']
        lines += ['', f"Image manifest: `{images['manifest']}`. Cached images use Apptainer; Dockerfiles remain in the tasks."]
    if record.get('dataset_cards'):
        lines += ['', 'Automatically generated data card:', '']
        for folder, card in record['dataset_cards'].items():
            if repo and revision:
                url = (f'https://huggingface.co/datasets/{repo}/blob/{quote(revision, safe="")}/'
                       f'{quote(card["readme"], safe="/")}')
                lines.append(f'- [{folder}]({url})')
            else:
                # A discussion-relative path does not address a repository file.
                lines.append(f'- {folder}: `{card["readme"]}`')
    for folder, notes in record.get('validation_disclosures', {}).items():
        lines += ['', f'<details><summary>Validation details ({cell(folder)})</summary>', '']
        lines += [f'- {note}' for note in notes]
        lines += ['', '</details>']
    links = {record.get('run_file', f"runs/{record['run']}.json"): 'Validation results and upstream comparison'}
    for folder in record['data_sources']:
        links[f'{folder}/tasks.parquet'] = f'{folder}: dataset'
        links[f'{folder}/archive.parquet'] = f'{folder}: archived tasks'
    for folder, scripts in record.get('patcher_scripts', {}).items():
        for script in scripts:
            links[script['path']] = f'{folder}: patch script'
    links.update(record.get('evidence_links', {}))
    for path, info in (record.get('trajectories') or {}).get('files', {}).items():
        links[path] = f"{path.split('/')[-2]}: {info['rows']} attempts of {path.rsplit('/', 1)[-1][:-len('.parquet')]} (trajectory, verifier record, reward)"
    if record.get('patcher_upload_notes'):
        lines += ['', *[cell(note) for note in record['patcher_upload_notes']], '']
    lines += ['', '## Files and evidence', '']
    for path, label in links.items():
        if repo and revision:
            url = f'https://huggingface.co/datasets/{repo}/blob/{quote(revision, safe="")}/{quote(path, safe="/")}'
            lines.append(f'- [{cell(label)}]({url})')
        else:
            lines.append(f'- {cell(label)}: `{path}`')
    return '\n'.join(lines) + '\n' + analysis_text(record.get('analysis'))


def write(tables, record, run_file, out, cards=None, image_manifest=None):
    import pyarrow as pa
    import pyarrow.parquet as pq
    out = Path(out)
    schema = pa.schema([('path', pa.string()), ('task_binary', pa.binary()), ('content_sha256', pa.string()),
                        ('stages_passed', pa.string()), ('archive_stage', pa.int64()),
                        ('archive_reason', pa.string()), ('run', pa.string()),
                        ('validation_labels', pa.list_(pa.string())), ('patch_labels', pa.list_(pa.string())),
                        ('archive_labels', pa.list_(pa.string())),
                        ('patch_changed', pa.bool_()), ('source_task_id', pa.string())])
    files = []
    for folder, (kept, archived) in sorted(tables.items()):
        for name, rows in (('tasks.parquet', kept), ('archive.parquet', archived)):
            path = out / folder / name
            path.parent.mkdir(parents=True, exist_ok=True)
            rows = sorted(rows, key=lambda r: r['path'])
            table_schema = schema
            if image_manifest:
                table_schema = schema.with_metadata({b'ot.images.v1': json.dumps(image_manifest).encode()})
            pq.write_table(pa.table({c: [r.get(c, [] if c.endswith('_labels') else None) for r in rows]
                                    for c in COLUMNS}, schema=table_schema), path)
            files.append(f'{folder}/{name}')
    for folder, card in sorted((cards or {}).items()):
        directory = out / folder
        (directory / 'README.md').write_text(card['readme'])
        if card.get('validation_disclosures'):
            record.setdefault('validation_disclosures', {})[folder] = card['validation_disclosures']
        (directory / 'annotation.json').write_text(json.dumps(
            {k: v for k, v in card.items() if k != 'readme'}, indent=2) + '\n')
        files.append(f'{folder}/README.md')
    path = out / run_file
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2) + '\n')
    (out / 'pull-request.md').write_text(description(record))
    return [*files, run_file]


def add_conversion_archives(tables, record, run_file, paths, mapping):
    """Carry reviewed converter exclusions into archives, cards and PR counts.

    Stage 0 denotes a conversion review, not a failed runtime validation stage.
    Explicit inputs prevent unrelated archives from being picked up silently.
    """
    import pyarrow.parquet as pq
    from validation.contract import inventory
    seen = {row['path'] for kept, archived in tables.values() for row in [*kept, *archived]}
    for path in paths:
        explicit_folder = None
        if '=' in str(path):
            explicit_folder, path = str(path).split('=', 1)
        if pq.ParquetFile(path).metadata.num_rows == 0:
            continue
        hashes = {item['task_id']: item['sha256'] for item in inventory(path)[0]}
        for row in pq.read_table(path).to_pylist():
            name, category, reason = row['path'], row['archive_category'], row['archive_reason']
            if name in seen:
                raise ValueError(f'conversion archive overlaps selected tasks: {name}')
            folder = explicit_folder or folder_of(name, mapping)
            if folder not in tables:
                raise ValueError(f'conversion archive has no validated data source: {folder}')
            if not category or not reason.startswith(category + ': ') or not row['task_binary']:
                raise ValueError(f'incomplete conversion exclusion: {name}')
            seen.add(name)
            tables[folder][1].append({
                'path': name, 'task_binary': row['task_binary'],
                'content_sha256': hashes[name],
                'stages_passed': '', 'archive_stage': 0, 'archive_reason': reason, 'run': run_file,
                'validation_labels': [], 'patch_labels': row.get('patch_labels') or [],
                'archive_labels': ['patch-drop:' + category],
                'source_task_id': row.get('source_task_id') or name})
            record.setdefault('archive_details', {}).setdefault(folder, []).append({
                'task': name, 'label': 'patch-drop:' + category,
                'explanation': reason.removeprefix(category + ': ')})
            count = record['data_sources'][folder]
            count['tasks'] += 1
            count['archived'] += 1
            for field, key in [('archived_by_stage', '0'), ('archive_reasons', reason[:160]),
                               ('archive_categories', category), ('archive_labels', 'patch-drop:' + category)]:
                bucket = count.setdefault(field, {})
                bucket[key] = bucket.get(key, 0) + 1
            record.setdefault('conversion_exclusions', []).append({
                'task': name, 'category': category, 'reason': reason, 'source': str(path)})
    if paths:
        record['rules']['0'] = 'reviewed conversion exclusion; no runtime failure implied'


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('reports', type=Path, help="a submission's report/ directory, or a results directory")
    ap.add_argument('--contract', type=Path, required=True)
    ap.add_argument('--repo', default='FWeindel/validated-tasks')
    ap.add_argument('--folder', action='append', default=[], metavar='PREFIX=FOLDER',
                    help='data source folder for task IDs starting with PREFIX; default is the ID without its number')
    ap.add_argument('--not-required', action='append', default=[], metavar='CHECK',
                    help='static check whose failure is recorded but does not archive; repeat')
    ap.add_argument('--out', type=Path, help='where the files are written; default next to the reports')
    ap.add_argument('--run-id', help='default: date, name of the data sources and contract hash')
    ap.add_argument('--conversion-archive', action='append', type=Path, default=[],
                    help='converter archive.parquet containing reviewed exclusions; repeatable')
    ap.add_argument('--patch-manifest', action='append', default=[], metavar='FOLDER=JSON',
                    help='complete cumulative patch provenance compared with the original source; repeatable')
    ap.add_argument('--image-cache', type=Path, help='pristine bundles cache; defaults to $OT_WORKSPACE/images')
    ap.add_argument('--dry-run', action='store_true', help='write the files and the description, open no pull request')
    ap.add_argument('--analysis', action='store_true', help='let a model group the archived tasks and suggest actions (Claude Code login); skipped on any failure')
    ap.add_argument('--analysis-model', default='sonnet')
    ap.add_argument('--readme', action='store_true', help='generate dataset cards through Claude Code in Harbor/Apptainer before publishing')
    ap.add_argument('--readme-force', action='store_true', help='regenerate existing cards in the PR instead of preserving them')
    ap.add_argument('--readme-model', default='claude-fable-5-1')
    ap.add_argument('--readme-seed', type=int, default=0, help='seed for sampling up to ten kept tasks per dataset')
    ap.add_argument('--readme-work-dir', type=Path, help='cluster workspace for annotation inputs and Harbor jobs; defaults under --out')
    ap.add_argument('--readme-evidence', action='append', default=[], metavar='FOLDER=PATH',
                    help='generator script, source documentation, or patch excerpt to stage for an output folder; repeatable')
    ap.add_argument('--no-trajectories', action='store_true',
                    help='with stage 6: publish the results without the trajectories of the attempts (default: with them)')
    a = ap.parse_args()
    result = publish(a.reports, a.contract, a.repo, a.folder, a.not_required, a.out, a.run_id, a.dry_run,
                     analysis=a.analysis, analysis_model=a.analysis_model,
                     readme=a.readme, readme_model=a.readme_model, readme_evidence=a.readme_evidence,
                     readme_seed=a.readme_seed, readme_work_dir=a.readme_work_dir, readme_force=a.readme_force,
                     conversion_archives=a.conversion_archive, patch_manifests=a.patch_manifest, image_cache=a.image_cache,
                     trajectories=not a.no_trajectories)
    print(result['description'])
    print(f"Dry run: {len(result['files'])} files written to {result['out']}; no pull request opened."
          if a.dry_run else f"Pull request: {result['pull_request']}")


def readme_exists(repo, folder):
    """Preserve a dataset card already present in the destination repository."""
    if not repo:
        return False
    from huggingface_hub import HfApi
    return HfApi().file_exists(repo_id=repo, repo_type='dataset', filename=f'{folder}/README.md')


def publish(reports_dir, contract_path, repo, folders=(), not_required=(), out=None, run_id=None, dry_run=False,
            analysis=False, analysis_model='sonnet', call=run_llm,
            readme=False, readme_model='claude-fable-5-1', readme_evidence=(),
            readme_seed=0, readme_work_dir=None, annotation_runner=None, readme_force=False,
            conversion_archives=(), patch_manifests=(), image_cache=None, agent_patch_repair_loop=False,
            patch_repair_summary=None, skipped_stages=None, trajectories=True):
    """Build the files for one run and open the pull request; returns what was done."""
    contract = read(contract_path)
    if skipped_stages:
        require_complete(reports_dir, contract_path, skipped_stages=skipped_stages)
    reports = stage_reports(reports_dir)
    for stage, report in reports.items():
        if report.get('contract_sha256') not in (None, contract['sha256']):
            raise ValueError(f'stage {stage} report belongs to another contract')
    mapping = dict(item.split('=', 1) for item in folders)
    checks = [c if c.endswith('.sh') else f"check-{c.removeprefix('check-')}.sh" for c in not_required]
    cache = {}
    def previous(folder):
        if folder not in cache:
            cache[folder] = {} if dry_run and not repo else previous_rows(repo, folder)
        return cache[folder]
    tables, record, run_file = build(contract, reports, mapping, checks, previous, run_id)
    if agent_patch_repair_loop:
        record['review_provenance'] = {'workflow': 'agent_patch_repair_loop', 'human_audited': False}
        if patch_repair_summary is not None:
            record['patch_repair_summary'] = patch_repair_summary
    if skipped_stages:
        record['skipped_stages'] = skipped_stages
    add_conversion_archives(tables, record, run_file, conversion_archives, mapping)
    patch_provenance.attach(record, tables, patch_manifests)
    patch_provenance.attach_automations(record, tables, contract)
    from validation.publishing.pr_runtime import collect
    record['runtime_summary'] = collect(contract, reports_dir)
    if analysis:
        record['analysis'] = analyse(record, tables, analysis_model, call)
    out = Path(out) if out else Path(reports_dir) / 'publish'
    patcher_files = patch_provenance.stage_patchers(record, patch_manifests, out)
    cards = None
    if readme_evidence and not readme:
        raise ValueError('--readme-evidence requires --readme')
    if readme_force and not readme:
        raise ValueError('--readme-force requires --readme')
    if readme:
        from validation.publishing.annotation import generate
        existing = set() if readme_force else {folder for folder in tables if readme_exists(repo, folder)}
        missing = {folder: rows for folder, rows in tables.items() if folder not in existing}
        evidence = {}
        for item in readme_evidence:
            folder, path = item.split('=', 1)
            evidence.setdefault(folder, []).append(path)
        from uuid import uuid4
        work = Path(readme_work_dir) if readme_work_dir else out / 'annotation-runs'
        if work.resolve().is_relative_to('/home'):
            raise ValueError('annotation inputs and jobs must be outside /home; set --readme-work-dir to a cluster workspace')
        cards = generate(missing, readme_model, annotation_runner, work / uuid4().hex,
                         evidence, seed=readme_seed) if missing else {}
        record['dataset_cards'] = {folder: {'readme': f'{folder}/README.md',
            'model': card['annotation']['model'],
            'prompt_sha256': card['provenance']['prompt_sha256']} for folder, card in cards.items()}
        record['dataset_cards'].update({folder: {'readme': f'{folder}/README.md',
            'status': 'preserved_existing'} for folder in sorted(existing)})
    # Include operator notes in the first PR description, including image-first uploads.
    for folder, card in (cards or {}).items():
        if card.get('validation_disclosures'):
            record.setdefault('validation_disclosures', {})[folder] = card['validation_disclosures']
    from validation.publishing.image_release import prepare_release
    if image_cache is None and os.environ.get('OT_WORKSPACE'):
        image_cache = Path(os.environ['OT_WORKSPACE']) / 'images'
    manifest, artifacts = prepare_release(tables, image_cache, out, repo)
    record['environment_images'] = {'manifest': f"images/manifests/{record['run']}.json",
                                    'bundles': len(manifest['bundles']),
                                    'tasks': len(manifest['tasks']), 'missing': manifest['missing'],
                                    'cache_status': manifest.get('cache_status', 'configured')}
    artifact_commit = None
    if artifacts and not dry_run:
        from huggingface_hub import CommitOperationAdd, HfApi
        artifact_commit = HfApi().create_commit(
            repo_id=repo, repo_type='dataset', create_pr=True,
            operations=[CommitOperationAdd(path_in_repo=name, path_or_fileobj=str(path))
                        for name, path in artifacts.items()],
            commit_message=commit_title(record['run'], 'environment images for'),
            commit_description=description(record))
        manifest['revision'] = artifact_commit.oid
    manifest_path = record['environment_images']['manifest']
    (out / manifest_path).parent.mkdir(parents=True, exist_ok=True)
    (out / manifest_path).write_text(json.dumps(manifest, indent=2) + '\n')
    files = write(tables, record, run_file, out, cards, manifest if manifest['tasks'] else None)
    files.extend([manifest_path, *patcher_files])
    submission = Path(reports_dir).parent
    if trajectories and 6 in reports and (submission / 'request.json').is_file():
        # A run with agent trials publishes the attempts behind its numbers: every trial's
        # trajectory and verifier record from the job's evidence archive.
        from validation.publishing import trajectories as traces
        request = json.loads((submission / 'request.json').read_text())['args']
        extra, record['trajectories'] = traces.collect(
            [(submission, (contract, request, reports[6]))], record['run'], out,
            lambda task: folder_of(task, mapping))
        files.extend(extra)
        (out / run_file).write_text(json.dumps(record, indent=2) + '\n')
    result = {'run': record['run'], 'files': [*artifacts, *files], 'out': str(out), 'description': description(record),
              'analysis': (record.get('analysis') or {}).get('status')}
    if dry_run:
        return result
    from huggingface_hub import CommitOperationAdd, HfApi
    api = HfApi()
    commit = api.create_commit(
        repo_id=repo, repo_type='dataset', create_pr=artifact_commit is None,
        **({'revision': artifact_commit.pr_revision, 'parent_commit': artifact_commit.oid} if artifact_commit else {}),
        operations=[CommitOperationAdd(path_in_repo=name, path_or_fileobj=str(out / name)) for name in files],
        commit_message=commit_title(record['run']), commit_description=description(record))
    result['pull_request'] = artifact_commit.pr_url if artifact_commit else commit.pr_url
    # The PR revision becomes available only after the commit is created.
    linked_description = description(record, repo, artifact_commit.pr_revision if artifact_commit else commit.pr_revision)
    result['description'] = linked_description
    (out / 'pull-request.md').write_text(linked_description)
    try:
        number = int(result['pull_request'].rstrip('/').rsplit('/', 1)[-1])
        discussion = api.get_discussion_details(repo, number, repo_type='dataset')
        initial = next(event for event in discussion.events
                       if getattr(event, 'content', None) == description(record))
        api.edit_discussion_comment(repo, number, initial.id, linked_description, repo_type='dataset')
    except Exception as exc:
        # The upload succeeded; do not report the publication as failed.
        result['description_update_error'] = str(exc)
        print(f'PR created, but updating its artifact links failed: {exc}', file=sys.stderr)
    return result


if __name__ == '__main__':
    main()
