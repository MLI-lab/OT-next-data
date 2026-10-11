"""Carry successful stage-3 evidence into a compatible frozen validation run."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path


def freeze(record_path, current, tasks):
    from validation.contract import read, task_records
    from validation.patch_repair_loop.reports import load_reports

    record = json.loads(Path(record_path).read_text())
    previous = read(record['contract'])
    old_tasks = {t['task_id']: t for t in task_records(previous)}
    if 3 not in previous['stages'] or 3 not in current['stages']:
        raise ValueError('stage-3 reuse requires stage 3 in both contracts')
    for key in ('execution_profile', 'runtime_dependencies', 'implementation', 'upstream'):
        if previous[key] != current[key]:
            raise ValueError(f'stage-3 reuse: changed {key}; rerun stage 3')
    for key in ('source', 'revision'):
        if previous['dataset'][key] != current['dataset'][key]:
            raise ValueError(f'stage-3 reuse: changed dataset {key}')
    # Compare complete task records, including content hashes, not just IDs.
    if not tasks or any(old_tasks.get(t['task_id']) != t for t in tasks):
        raise ValueError('stage-3 reuse: changed or missing task content; rerun stage 3')
    path, report = load_reports(record['report'], old_tasks, [3])[3]
    if report['contract_sha256'] != previous['sha256']:
        raise ValueError('stage-3 reuse: report does not match its source contract')
    if report.get('has_findings') or report.get('contract_findings') or any(
            item.get('status') != 'passed' for item in report['items']):
        raise ValueError('stage-3 reuse requires a fully passing source report')
    selected = {t['task_id'] for t in tasks}
    # Embed evidence in the new immutable contract so execution does not depend
    # on the old run's directories being mounted on a worker.
    return {
        'source_contract': str(Path(record['contract']).resolve()),
        'source_contract_sha256': previous['sha256'],
        'source_report': str(path.resolve()),
        'source_report_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
        'source_timing': report.get('timing'),
        'items': [deepcopy(i) for i in report['items'] if Path(i['task']).name in selected],
    }


def restore(checkpoint, sources):
    """Use current task paths while retaining original logs and provenance."""
    paths = {p.name: str(p) for p in sources}
    items = deepcopy(checkpoint['items'])
    if len(items) != len(paths) or {Path(i['task']).name for i in items} != set(paths):
        raise ValueError('stage-3 checkpoint coverage differs from materialized tasks')
    for item in items:
        if item.get('status') != 'passed':
            raise ValueError('stage-3 checkpoint contains a non-passing task')
        item['task'] = paths[Path(item['task']).name]
    return items
