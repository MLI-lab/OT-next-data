"""Reuse compatible full-run build successes; execute only unresolved tasks.

The aggregate is derived evidence, not a claim that one job ran every task.
Task bytes, execution settings, validation code and gates must all match.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path


def compatible(previous, current):
    normalize = lambda value: json.loads(json.dumps(value))
    for key in ('stage_layout', 'stages', 'execution_profile', 'runtime_dependencies',
                'upstream', 'implementation', 'expected_outcomes_sha256'):
        if normalize(previous.get(key)) != normalize(current.get(key)):
            return False
    for key in ('source', 'revision'):
        if previous['dataset'].get(key) != current['dataset'].get(key):
            return False
    gates = lambda c: {k: v for k, v in c['success_criteria'].items() if k != 'minimum_tasks'}
    if gates(previous) != gates(current):
        return False
    if current.get('arguments', {}).get('force_build'):
        return False
    return True


def partition(previous, current, old_tasks, tasks, report, restored=()):
    """Return a passing subset only when both provenance and content match."""
    if not compatible(previous, current) or report.get('contract_findings'):
        return [], sorted(t['task_id'] for t in tasks)
    old = {t['task_id']: t for t in old_tasks}
    outcomes = {Path(i['task']).name: i for i in report['items']}
    restored = set(restored)
    reused = sorted(t['task_id'] for t in tasks if t['task_id'] not in restored
                    and old.get(t['task_id']) == t
                    and outcomes.get(t['task_id'], {}).get('status') == 'passed')
    return reused, sorted({t['task_id'] for t in tasks} - set(reused))


def plan(config, state, source, available, generation):
    from validation.contract import read, task_records
    from data.patch_repair_loop import controller as core
    from data.patch_repair_loop.reports import load_reports

    directory = generation / 'incremental-build'
    saved = directory / 'plan.json'
    if saved.exists():
        result = json.loads(saved.read_text())
        return result if result.get('reused_ids') else None
    # Never change the selection or contract of an already submitted stage.
    if list(generation.glob('stage3-*/job.json')):
        return None
    previous_entry = next((e for e in reversed(state['history'])
                           if e.get('phase') == 'build' and e.get('size') is None
                           and e.get('generation', state['generation']) < state['generation']), None)
    if previous_entry is None:
        return None
    directory.mkdir(exist_ok=True)
    try:
        old_job = previous_entry.get('job') or json.loads(Path(previous_entry['evidence']).read_text())['job']
        previous = read(old_job['contract'])
        old_tasks = list(task_records(previous))
        old_report_dir = Path(old_job['submission']) / 'report'
        report_path, report = load_reports(old_report_dir, [t['task_id'] for t in old_tasks], [1])[1]
        if report['contract_sha256'] != previous['sha256']:
            raise ValueError('Previous report does not match its contract')
    except (OSError, KeyError, ValueError) as exc:
        core.save(saved, {'reused_ids': [], 'reason': f'No compatible complete source evidence: {exc}'})
        return None
    carrier = core.submit(config, source, available, directory / 'full-contract',
                          stages=[1], review_setup=state['review_setup'], prepare_only=True)
    current = read(carrier['contract'])
    tasks = list(task_records(current))
    restored_path = generation / 'task-id-reconciliation.json'
    restored = json.loads(restored_path.read_text())['restored_task_ids'] if restored_path.exists() else []
    reused, execute = partition(previous, current, old_tasks, tasks, report, restored)
    result = {'reused_ids': reused, 'execute_ids': execute, 'carrier': carrier,
              'previous_job': old_job, 'previous_report': str(report_path),
              'previous_report_sha256': hashlib.sha256(report_path.read_bytes()).hexdigest(),
              'previous_contract_sha256': previous['sha256'],
              'reason': 'Matching task content and execution contract' if reused else 'No compatible passing tasks'}
    core.save(saved, result)
    return result if reused else None


def execute(config, state, source, available, generation, planned):
    from validation.contract import read, task_records
    from data.patch_repair_loop import controller as core
    from data.patch_repair_loop.reports import load_reports, summarize

    directory = generation / 'stage3-full'
    directory.mkdir(exist_ok=True)
    report_dir = directory / 'aggregate' / 'report'
    current = read(planned['carrier']['contract'])
    old_path = Path(planned['previous_report'])
    if hashlib.sha256(old_path.read_bytes()).hexdigest() != planned['previous_report_sha256']:
        raise ValueError('Incremental source evidence changed after planning')
    previous = json.loads(old_path.read_text())
    items = {}
    reused_ids = set(planned['reused_ids'])
    for item in previous['items']:
        task = Path(item['task']).name
        if task in reused_ids:
            item = deepcopy(item)
            item['reuse_provenance'] = {
                'source_contract_sha256': planned['previous_contract_sha256'],
                'source_report': str(old_path), 'source_report_sha256': planned['previous_report_sha256'],
                'previous_provenance': item.get('reuse_provenance'),
            }
            items[task] = item
    fresh_job = None
    if planned['execute_ids']:
        fresh_dir = directory / 'incremental-execution'
        fresh_dir.mkdir(exist_ok=True)
        job_path = fresh_dir / 'job.json'
        fresh_job = json.loads(job_path.read_text()) if job_path.exists() else core.submit(
            config, source, planned['execute_ids'], fresh_dir, stages=[1],
            review_setup=state['review_setup'])
        core.save(job_path, fresh_job)
        fresh_contract = read(fresh_job['contract'])
        if not compatible(fresh_contract, current):
            raise ValueError('Incremental execution differs from planned runtime or gates')
        full_tasks = {t['task_id']: t for t in task_records(current)}
        if {t['task_id'] for t in task_records(fresh_contract)} != set(planned['execute_ids']) or any(
                full_tasks.get(t['task_id']) != t for t in task_records(fresh_contract)):
            raise ValueError('Incremental execution task content differs from plan')
        path = core.wait_for_job(fresh_job, config['poll_seconds'], config.get('max_wait_hours', 24))
        fresh_path, fresh = load_reports(path, planned['execute_ids'], [1])[1]
        if fresh['contract_sha256'] != fresh_contract['sha256'] or fresh.get('contract_findings'):
            raise ValueError('Incremental execution has contract findings')
        for item in fresh['items']:
            item = deepcopy(item)
            item['execution_provenance'] = {'job': fresh_job, 'report': str(fresh_path),
                                            'contract_sha256': fresh_contract['sha256']}
            items[Path(item['task']).name] = item
    if set(items) != set(available):
        raise ValueError('Incremental aggregate does not cover every retained task')
    report = {k: deepcopy(v) for k, v in previous.items()
              if k not in ('items', 'timing', 'reused_from', 'shard')}
    report.update(items=[items[t] for t in sorted(items)], contract_sha256=current['sha256'],
                  report_kind='incremental_build_aggregate', complete=True, dry_run=False,
                  contract_findings=[], has_findings=any(i['status'] != 'passed' for i in items.values()),
                  incremental={'reused': len(planned['reused_ids']), 'executed': len(planned['execute_ids']),
                               'plan': str(generation / 'incremental-build/plan.json')})
    report_dir.mkdir(parents=True, exist_ok=True)
    core.save(report_dir / 'stage-1-incremental.json', report)
    outcome = summarize(report_dir, available, [1])
    core.save(report_dir / 'summary.json', {'complete': True, 'contract_sha256': current['sha256'],
                                           'report_kind': report['report_kind'], 'outcome': outcome})
    job = {**planned['carrier'], 'job_id': (fresh_job or planned['previous_job'])['job_id'],
           'submission': str(report_dir.parent), 'derived_evidence': True,
           'executed_job': fresh_job, 'reused_job': planned['previous_job']}
    return job, report_dir, outcome
