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
    assert recorded['repository_timestamps']['applied'] is None and 'manual-repair' not in recorded
    assert row['task-absolute-path'] == 'warning'
    results.export_report(path, report, {'t': 'b'})
    new, = results.read(path)
    # Repairs recorded for other task files do not apply to the new files.
    assert {r['applied'] for r in json.loads(new['automatic_repairs']).values()} == {None}


def test_capture_does_not_use_another_contract(tmp_path):
    path = tmp_path / 'contract.json'
    path.write_text(json.dumps({'sha256': 'new', 'dataset': {'automatic_repairs': {'t': {}}}}))
    report = {'contract_sha256': 'old'}
    repairs.capture(report, {'contract': path})
    assert 'automatic_repairs' not in report


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


def test_task_specific_repair_text_reaches_the_cell(tmp_path):
    import pyarrow.parquet as pq
    meta = {'automation_changes': {'t': ['relative-path-fix', 'dependency-pinning', 'anti-cheat-instruction']}}
    repairs.record_details(meta, 'task-absolute-path', repairs.path_details(
        {'tasks': [{'task': 't', 'edits': [{'old': 'data.csv', 'new': '/app/data.csv'}]}]}))
    repairs.record_details(meta, 'pip-pinning', repairs.pip_details(
        [{'task': 't', 'edits': [{'original': 'numpy', 'replacement': "'numpy==1.26.4'"}]}]))
    contract = {'sha256': 'c', 'dataset': {'automatic_repairs': repairs.contract_records([{'task_id': 't', 'sha256': 'a'}], meta)}}
    (tmp_path / 'contract.json').write_text(json.dumps(contract))
    path = tmp_path / 'results.parquet'
    report = {'stage': 4, 'contract_sha256': 'c', 'items': [{'task': 't', 'checks': [
        {'check': 'check-task-absolute-path.sh', 'status': 'passed'},
        {'check': 'check-pip-pinning.sh', 'status': 'passed'},
        {'check': 'check-instruction-suffix.sh', 'status': 'passed'}]}]}
    results.export_report(path, report, {'t': 'a'}, 'd', settings={'contract': tmp_path / 'contract.json'})
    row, = pq.read_table(path).to_pylist()
    assert row['task-absolute-path']['repaired'] and row['task-absolute-path']['details'] == 'data.csv -> /app/data.csv'
    assert row['pip-pinning']['details'] == 'numpy -> numpy==1.26.4'
    assert row['instruction-suffix']['repaired'] and row['instruction-suffix']['details'] is None


def _task(tmp_path):
    root = tmp_path / 'tasks' / 'sample'
    (root / 'tests').mkdir(parents=True)
    (root / 'instruction.md').write_text('Read data.csv and write the summary.\n')
    (root / 'tests/test.sh').write_text('python3 -m pip install duckdb\npytest\n')
    (root / 'tests/test.sh').chmod(0o755)
    return root


def _evidence(root):
    from validation.rules.static.pip_pinning.repair import commands
    build = {'task': str(root), 'status': 'passed', 'environments': [{'environment': 'agent', 'instruction_paths': {
        'status': 'completed', 'candidates': ['data.csv'], 'instruction_path_hints': [],
        'instruction_sha256': __import__('hashlib').sha256((root / 'instruction.md').read_bytes()).hexdigest(),
        'findings': [{'relative_path': 'data.csv', 'status': 'unique', 'matches': ['/app/data.csv']}]}}]}
    reference = {'task': str(root), 'status': 'passed', 'pip_snapshots': [
        {**finding, 'exit_code': 0, 'stdout': 'duckdb==1.1.3\nother==2.0\n'} for finding in commands(root)]}
    return build, reference


def test_end_of_run_repairs_edit_files_and_rows(tmp_path):
    from validation.contract import task_digest
    root = _task(tmp_path)
    build, reference = _evidence(root)
    reports = {1: {'items': [build]}, 2: {'items': [reference]}}
    plans = repairs.plan(reports, lambda tasks: repairs.files_from(root.parent, tasks))
    plan = plans['sample']
    assert plan['from'] == task_digest(root)
    assert plan['files']['instruction.md'] == b'Read /app/data.csv and write the summary.\n'
    assert b'duckdb==1.1.3' in plan['files']['tests/test.sh']
    assert plan['rules'] == {'task-absolute-path': {'status': 'pass', 'details': 'data.csv -> /app/data.csv'},
                             'pip-pinning': {'status': 'pass', 'details': 'duckdb -> duckdb==1.1.3'}}
    # Original inputs never change.
    assert (root / 'instruction.md').read_text() == 'Read data.csv and write the summary.\n'
    rows = [{'dataset': 'd', 'task_id': 'sample', 'content_sha256': plan['from'],
             'task-absolute-path': 'warning', 'pip-pinning': 'warning'}]
    results.repair_rows(rows, plans)
    assert rows[0]['content_sha256'] == plan['to']
    assert rows[0]['task-absolute-path'] == rows[0]['pip-pinning'] == 'pass'
    recorded = json.loads(rows[0]['automatic_repairs'])
    assert recorded['task-absolute-path']['details'] == 'data.csv -> /app/data.csv'


def test_repaired_archive_keeps_modes_and_matches_the_planned_hash(tmp_path):
    import io
    import tarfile
    from validation.contract import files_digest
    from validation.publishing.publish import pack_task
    root = _task(tmp_path)
    build, reference = _evidence(root)
    binary = pack_task(root)
    plan = repairs.plan({1: {'items': [build]}, 2: {'items': [reference]}},
                        lambda tasks: {'sample': repairs.archive_files(binary)})['sample']
    repaired = repairs.replace_in_archive(binary, plan['files'])
    assert files_digest(repairs.archive_files(repaired).items()) == plan['to']
    with tarfile.open(fileobj=io.BytesIO(repaired)) as archive:
        assert archive.getmember('tests/test.sh').mode & 0o111


def test_no_evidence_means_no_repair(tmp_path):
    root = _task(tmp_path)
    build, _ = _evidence(root)
    build['environments'][0]['instruction_paths']['findings'][0].update(status='ambiguous', matches=['/a/data.csv', '/b/data.csv'])
    assert repairs.plan({1: {'items': [build]}}, lambda tasks: repairs.files_from(root.parent, tasks)) == {}
