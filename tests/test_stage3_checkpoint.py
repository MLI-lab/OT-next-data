from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from validation.checkpoints import stage3
from validation import contract as contracts


def checkpoint(tmp_path, monkeypatch):
    tasks = [{'task_id': 'a', 'sha256': 'aaa'}, {'task_id': 'b', 'sha256': 'bbb'}]
    old = {'tasks': tasks, 'sha256': 'old-contract', 'stages': [3],
           'dataset': {'source': 'dataset', 'revision': 'pinned'},
           **{k: {k: 'same'} for k in
              ('execution_profile', 'runtime_dependencies', 'implementation', 'upstream')}}
    monkeypatch.setattr(contracts, 'read', lambda _: old)
    report = {'stage': 3, 'complete': True, 'contract_sha256': old['sha256'],
              'items': [{'task': '/old/' + t['task_id'], 'status': 'passed',
                         'output': '/old/logs/' + t['task_id']} for t in tasks]}
    path = tmp_path / 'stage-3-build.json'
    path.write_text(json.dumps(report))
    record = tmp_path / 'checkpoint.json'
    record.write_text(json.dumps({'contract': str(tmp_path / 'old.json'), 'report': str(tmp_path)}))
    current = deepcopy(old)
    current['stages'] = [1, 3, 4, 5]
    return record, current, tasks, path


def test_freeze_subset_and_restore_paths(tmp_path, monkeypatch):
    record, current, tasks, _ = checkpoint(tmp_path, monkeypatch)
    frozen = stage3.freeze(record, current, tasks[:1])
    assert frozen['source_contract_sha256'] == 'old-contract'
    assert len(frozen['source_report_sha256']) == 64
    restored = stage3.restore(frozen, [Path('/new/a')])
    assert restored == [{'task': '/new/a', 'status': 'passed', 'output': '/old/logs/a'}]
    assert frozen['items'][0]['task'] == '/old/a'


@pytest.mark.parametrize('key', ['execution_profile', 'runtime_dependencies', 'implementation', 'upstream'])
def test_reject_changed_runtime(tmp_path, monkeypatch, key):
    record, current, tasks, _ = checkpoint(tmp_path, monkeypatch)
    current[key] = {'changed': True}
    with pytest.raises(ValueError, match='rerun stage 3'):
        stage3.freeze(record, current, tasks)


def test_reject_changed_tasks(tmp_path, monkeypatch):
    record, current, tasks, _ = checkpoint(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match='task content'):
        stage3.freeze(record, current, [{'task_id': 'a', 'sha256': 'changed'}])


@pytest.mark.parametrize('change', ['failed', 'wrong-contract', 'incomplete', 'duplicate'])
def test_reject_invalid_source_evidence(tmp_path, monkeypatch, change):
    record, current, tasks, path = checkpoint(tmp_path, monkeypatch)
    report = json.loads(path.read_text())
    if change == 'failed':
        report['items'][0]['status'] = 'failed'
    elif change == 'wrong-contract':
        report['contract_sha256'] = 'other'
    elif change == 'incomplete':
        report['complete'] = False
    else:
        report['items'].append(report['items'][0])
    path.write_text(json.dumps(report))
    with pytest.raises(ValueError):
        stage3.freeze(record, current, tasks)


def test_runner_imports_without_executing_builds(tmp_path, monkeypatch):
    from validation.stages import runner, build_retries
    record, current, tasks, _ = checkpoint(tmp_path, monkeypatch)
    current['stage3_checkpoint'] = stage3.freeze(record, current, tasks)
    current['sha256'] = 'final-contract'
    current['success_criteria'] = {'minimum_tasks': 2}
    monkeypatch.setattr(contracts, 'verify_materialized', lambda _: current)
    monkeypatch.setattr(runner, 'check_args', lambda _: None)
    monkeypatch.setattr(runner, 'checkout', lambda _: tmp_path)
    monkeypatch.setattr(runner, 'discover_tasks', lambda _: [tmp_path / 'tasks/a', tmp_path / 'tasks/b'])
    monkeypatch.setattr(runner, 'select_paths', lambda sources, _: sources)
    def forbidden(*args, **kwargs):
        pytest.fail('reused stage 3 must not launch builds')
    monkeypatch.setattr(build_retries, 'run_builds', forbidden)
    args = SimpleNamespace(tasks=tmp_path / 'tasks', out=tmp_path / 'out', backend='apptainer',
                           dry_run=False, concurrency=2)
    _, report = runner.run_stage(3, args)
    assert report['complete'] and not report['has_findings']
    assert report['contract_sha256'] == 'final-contract'
    assert report['reused_from']['source_contract_sha256'] == 'old-contract'
    assert len(report['items']) == 2
