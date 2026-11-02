import json

from validation import automatic_repairs as repairs, run as results


def test_export_revision_bound_repairs_survive_partial_reruns(tmp_path):
    tasks = [{'task_id': 't', 'sha256': 'a'}]
    metadata = {'automation_changes': {'t': ['anti-cheat-instruction', 'relative-path-fix',
                                            'repository-leak-repair', 'manual-repair']}}
    contract = {'sha256': 'contract', 'dataset': {'automatic_repairs': repairs.contract_records(tasks, metadata)}}
    contract_path = tmp_path / 'contract.json'
    contract_path.write_text(json.dumps(contract))
    report = {'stage': 4, 'contract_sha256': 'contract', 'items': [{'task': 't', 'checks': [
        {'check': 'check-task-absolute-path.sh', 'status': 'warning'}]}]}
    path = tmp_path / 'results.parquet'
    for _ in range(2):
        results.export_report(path, report, {'t': 'a'}, settings={'contract': contract_path})
    results.export_report(path, {'stage': 2, 'items': [{'task': 't', 'status': 'passed'}]}, {'t': 'a'})
    row, = results.read(path)
    recorded = json.loads(row['automatic_repairs'])
    assert recorded['instruction-suffix']['applied'] is True
    assert recorded['task-absolute-path']['applied'] is True
    assert recorded['pip-pinning']['applied'] is False
    assert len(recorded['task-absolute-path']['evidence']) == 1
    assert 'repository_timestamps' not in recorded and 'manual-repair' not in recorded
    assert row['task-absolute-path'] == 'warning'
    results.export_report(path, report, {'t': 'b'})
    old, new = results.read(path)
    assert not old['latest'] and new['latest']
    assert new.get('automatic_repairs') is None


def test_capture_does_not_use_another_contract(tmp_path):
    path = tmp_path / 'contract.json'
    path.write_text(json.dumps({'sha256': 'new', 'dataset': {'automatic_repairs': {'t': {}}}}))
    report = {'contract_sha256': 'old'}
    repairs.capture(report, {'contract': path})
    assert 'automatic_repairs' not in report


def test_legacy_contract_recovers_only_positive_repair_evidence(tmp_path, monkeypatch):
    from validation import contract
    legacy = {'sha256': 'legacy', 'dataset': {'automation_changes': {'t': ['relative-path-fix']}},
              'tasks': [{'task_id': 't', 'sha256': 'a'}]}
    path = tmp_path / 'legacy.json'
    path.write_text(json.dumps(legacy))
    monkeypatch.setattr(contract, 'read', lambda _: legacy)
    report = {'contract_sha256': 'legacy'}
    repairs.capture(report, {'contract': path})
    rules = report['automatic_repairs']['t']['rules']
    assert set(rules) == {'task-absolute-path'}
    assert rules['task-absolute-path']['applied'] is True


def test_runtime_and_file_repairs_combine_across_stage_reports():
    records = repairs.contract_records([{'task_id': 't', 'sha256': 'a'}], {
        'automation_changes': {'t': ['anti-cheat-instruction']}})
    reports = {
        1: {'automatic_repairs': records, 'items': [{'task': 't', 'status': 'passed',
            'environments': [{'leaks': {'repair': {'repositories': [
                {'head': 'abc', 'mtime_files': 2, 'status': 'done'}]}}}]}]},
        4: {'automatic_repairs': records, 'items': [{'task': 't', 'checks': [
            {'check': 'check-instruction-suffix.sh', 'status': 'passed'}]}]}}
    row, = results.observations(reports, {'t': 'a'})
    evidence = json.loads(row['automatic_repairs'])
    assert evidence['repository_timestamps']['applied'] is True
    assert evidence['instruction-suffix']['applied'] is True


def test_timestamp_repairs_require_runtime_evidence():
    def item(record):
        return {'environments': [{'environment': 'agent', 'leaks': {'repair': record}}]}
    assert repairs.for_item({}, item({'git': False, 'repositories': []}), 't', 'a')[
        'repository_timestamps']['applied'] is False
    assert repairs.for_item({}, item({'repositories': [{'status': 'done', 'head': '', 'mtime_files': 5}]}),
                            't', 'a')['repository_timestamps']['applied'] is False
    record = {'repositories': [{'status': 'done', 'head': 'abc', 'mtime_files': 5}]}
    assert repairs.for_item({}, item(record), 't', 'a')['repository_timestamps']['applied'] is True
    assert repairs.for_item({}, item({'discovery_status': 'incomplete', 'repositories': []}),
                            't', 'a')['repository_timestamps']['applied'] is None


def test_inherited_provenance_requires_matching_input_revision(monkeypatch):
    from types import SimpleNamespace
    from validation import contract
    monkeypatch.setattr(contract, 'read', lambda _: {'dataset': {'automation_changes': {
        't': ['anti-cheat-instruction'], 'changed': ['dependency-pinning']}}})
    monkeypatch.setattr(contract, 'task_records', lambda _: [
        {'task_id': 't', 'sha256': 'a'}, {'task_id': 'changed', 'sha256': 'old'}])
    meta = {}
    repairs.inherit_changes(SimpleNamespace(contract='parent'), [
        {'task_id': 't', 'sha256': 'a'}, {'task_id': 'changed', 'sha256': 'new'}], meta)
    assert meta['automation_changes'] == {'t': ['anti-cheat-instruction']}
