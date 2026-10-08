"""Update final PR results after infrastructure retries.

The controller retries tasks marked 'retry' by the final review agent.
This module updates their results after each attempt: tasks that pass on retry
change from failed to passed in the final report used for the PR. Results for
other tasks stay unchanged, and earlier attempts remain saved for reference.
"""

from copy import deepcopy
import json
from pathlib import Path

from validation.contract import read, task_records
from validation.patch_repair_loop import controller as core
from validation.patch_repair_loop.reports import load_reports, summarize


def decisions(result, outcome):
    failed = {item['task_id'] for item in outcome['failures']}
    findings = result.get('findings')
    if not isinstance(findings, list):
        raise ValueError('Final reviewer must return findings')
    ids = [item.get('task_id') for item in findings if isinstance(item, dict)]
    if len(ids) != len(findings) or len(ids) != len(set(ids)) or set(ids) != failed:
        raise ValueError('Final reviewer must classify every failed task exactly once')
    groups = {key: [] for key in ('retry', 'repair', 'archive')}
    for item in findings:
        if item.get('action') not in groups or not isinstance(item.get('reason'), str) or not item['reason'].strip():
            raise ValueError('Final reviewer requires a supported action and reason')
        paths = item.get('evidence')
        if not isinstance(paths, list) or not paths or any(not isinstance(p, str) or not p.strip() for p in paths):
            raise ValueError('Final reviewer requires evidence paths for every finding')
        groups[item['action']].append(item['task_id'])
    return groups


def merge(original_job, previous_report, retry_job, selected, destination, retry_report=None):
    """Rebind verified per-task outcomes, recording their real execution contracts.

    Original reports and contracts are immutable. The aggregate represents the
    original task selection with replacement observations from compatible subset
    contracts. It is explicitly marked as derived evidence, never a new execution.
    """
    original, retry = read(original_job['contract']), read(retry_job['contract'])
    for key in ('execution_profile', 'runtime_dependencies', 'implementation', 'upstream', 'stages'):
        if original[key] != retry[key]:
            raise ValueError(f'Retry differs from the final contract: {key}')
    criteria = lambda value: {key: item for key, item in value['success_criteria'].items()
                              if key != 'minimum_tasks'}
    if criteria(original) != criteria(retry):
        raise ValueError('Retry changes validation success criteria')
    if any(original['dataset'].get(key) != retry['dataset'].get(key) for key in ('source', 'revision')):
        raise ValueError('Retry changes the pinned datasource')
    hashes = {item['task_id']: item['sha256'] for item in task_records(original)}
    retry_hashes = {item['task_id']: item['sha256'] for item in task_records(retry)}
    if set(retry_hashes) != set(selected) or not set(selected) <= set(hashes) or any(
            hashes[task] != retry_hashes[task] for task in selected):
        raise ValueError('Retry task content differs; restart the stage-3 repair workflow')
    prior = load_reports(previous_report, sorted(hashes), original['stages'])
    current = load_reports(retry_report or Path(retry_job['submission']) / 'report', selected, original['stages'])
    if any(report['contract_sha256'] != original['sha256'] for _, report in prior.values()):
        raise ValueError('Previous aggregate belongs to another final contract')
    if any(report['contract_sha256'] != retry['sha256'] for _, report in current.values()):
        raise ValueError('Retry reports do not match their execution contract')
    destination.mkdir(parents=True, exist_ok=True)
    provenance = {'job': retry_job, 'task_ids': selected, 'contract_sha256': retry['sha256'],
                  'evidence_archive': str(Path(retry_job['submission']) / 'evidence.tar.gz')}
    for stage in original['stages']:
        previous_path, report = prior[stage]
        retry_path, replacement = current[stage]
        aggregate = deepcopy(report)
        by_id = {Path(item['task']).name: item for item in replacement['items']}
        for index, item in enumerate(aggregate['items']):
            task = Path(item['task']).name
            if task in by_id:
                updated = deepcopy(by_id[task])
                updated['retry_provenance'] = {**provenance, 'report': str(retry_path),
                                               'previous_report': str(previous_path)}
                aggregate['items'][index] = updated
        aggregate.update(report_kind='retry_aggregate', contract_findings=[],
                         has_findings=any(item.get('status') != 'passed' for item in aggregate['items']))
        aggregate.setdefault('retry_sources', []).append(provenance)
        # One execution's wall time cannot describe a combined report.
        aggregate.pop('timing', None)
        core.save(destination / f'stage-{stage}-effective.json', aggregate)
    outcome = summarize(destination, sorted(hashes), original['stages'])
    core.save(destination / 'summary.json', {'complete': True, 'missing_stages': [],
              'report_kind': 'retry_aggregate', 'contract_sha256': original['sha256'],
              'outcome': outcome, 'original_submission': original_job['submission'],
              'latest_retry': provenance})
    core.save(destination / 'outcome.json', outcome)
    return outcome


def collect(config, job, selected, stages, directory):
    """Turn a terminal incomplete job into explicit unexecuted-task evidence.

    Timeouts while a job may still be alive are not retried automatically.
    """
    error = None
    try:
        report = core.wait_for_job(job, config['poll_seconds'], config.get('max_wait_hours', 24))
    except RuntimeError as exc:
        report = Path(job['submission']) / 'report'
        error = str(exc)
    if error is None:
        try:
            return report, core.summarize(report, selected, required_stages=stages)
        except ValueError as exc:
            error = str(exc)
    contract = read(job['contract'])
    if set(selected) != {task['task_id'] for task in task_records(contract)}:
        raise ValueError('Incomplete job does not match its frozen selection')
    recovered = directory / 'incomplete-report'
    recovered.mkdir(exist_ok=True)
    for stage in stages:
        paths = list(report.glob(f'stage-{stage}-*.json'))
        if len(paths) > 1:
            raise ValueError('Ambiguous incomplete stage reports')
        raw = json.loads(paths[0].read_text()) if paths else {}
        if raw and (raw.get('contract_sha256') != contract['sha256'] or raw.get('dry_run')):
            raise ValueError('Incomplete report has invalid execution provenance')
        observed = {Path(item['task']).name: item for item in raw.get('items', [])}
        if len(observed) != len(raw.get('items', [])) or not set(observed) <= set(selected):
            raise ValueError('Incomplete report has unexpected or duplicate tasks')
        items = []
        for task in selected:
            item = observed.get(task)
            if not item or item.get('status') == 'previewed':
                item = {'task': task, 'status': 'error', 'reason': 'No completed execution: ' + error,
                        'execution_missing': True, 'submission': job['submission']}
                if stage == 1:
                    item['checks'] = [{'check': 'execution', 'status': 'error', 'error': item['reason']}]
            items.append(item)
        core.save(recovered / f'stage-{stage}-incomplete.json', {
            'stage': stage, 'complete': True, 'dry_run': False, 'has_findings': True,
            'contract_sha256': contract['sha256'], 'report_kind': 'incomplete_execution_recovery',
            'original_report': str(paths[0]) if paths else None, 'execution_error': error,
            'original_complete': raw.get('complete', False), 'items': items})
    outcome = core.summarize(recovered, selected, required_stages=stages)
    core.save(recovered / 'summary.json', {'complete': True, 'missing_stages': [],
              'report_kind': 'incomplete_execution_recovery', 'outcome': outcome,
              'execution_error': error, 'submission': job['submission']})
    return recovered, outcome
