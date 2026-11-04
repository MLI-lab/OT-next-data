import json
from pathlib import Path

from validation import run as results


def test_partial_update_preserves_other_checks_and_changed_content_resets(tmp_path):
    path = tmp_path / 'results.parquet'
    first = {'dataset': 'd', 'task_id': 't', 'content_sha256': 'a', 'nproc': 'pass', 'nop': 'fail'}
    results.update(path, [first])
    results.update(path, [dict(first, nproc='warning')])
    results.update(path, [{'dataset': 'd', 'task_id': 't', 'content_sha256': 'a', 'reference': 'pass'}])
    row, = results.read(path)
    assert row['nop'] == 'fail' and row['nproc'] == 'warning'
    results.update(path, [{'dataset': 'd', 'task_id': 't', 'content_sha256': 'b', 'nproc': 'pass'}])
    row, = results.read(path)
    # New task files replace the row: earlier results do not carry over.
    assert row['content_sha256'] == 'b' and row['nproc'] == 'pass'
    assert row['reference'] == 'not_requested' and row['nop'] == 'not_requested'
    assert 'revision' not in row and 'latest' not in row


def test_parquet_cells_hold_status_repair_and_one_details_text(tmp_path):
    import pyarrow.parquet as pq
    path = tmp_path / 'results.parquet'
    repairs = json.dumps({'task-absolute-path': {'applied': True, 'evidence': [], 'details': 'data.csv -> /app/data.csv'},
                          'instruction-suffix': {'applied': True, 'evidence': []},
                          'pip-pinning': {'applied': False, 'evidence': []}})
    results.update(path, [{'dataset': 'd', 'task_id': 't', 'content_sha256': 'a',
                          'task-absolute-path': 'warning', 'instruction-suffix': 'fail', 'pip-pinning': 'pass',
                          'nproc': 'pass', 'nop': 'fail', 'dockerfile-sanity': 'fail',
                          'automatic_repairs': repairs,
                          'details': json.dumps({'dockerfile-sanity': {'status': 'failed', 'check': 'x', 'log': '/tmp/l',
                                                                       'reason': 'FAIL Dockerfile line 3'},
                                                 'nop': {'rewards': [1]}})}])
    table = pq.read_table(path)
    assert {'details', 'automatic_repairs', 'revision', 'latest', 'task_file'}.isdisjoint(table.schema.names)
    row, = table.to_pylist()
    compact = lambda value: {k: v for k, v in value.items() if v is not None}
    assert compact(row['instruction-suffix']) == {'status': 'fail', 'repaired': True}
    assert compact(row['task-absolute-path']) == {'status': 'warning', 'repaired': True,
                                                 'details': 'data.csv -> /app/data.csv'}
    assert compact(row['pip-pinning']) == {'status': 'pass', 'repaired': False}
    assert compact(row['nproc']) == {'status': 'pass'} and 'repaired' not in row['nproc']
    assert compact(row['nop']) == {'status': 'fail'}
    assert compact(row['dockerfile-sanity']) == {'status': 'fail', 'details': 'FAIL Dockerfile line 3'}
    assert compact(row['build']) == {'status': 'not_requested'}
    # Reading and writing again keeps the same cells.
    results.write(path, results.read(path))
    assert pq.read_table(path).to_pylist() == [row]


def test_same_reports_have_same_cells_for_both_entrypoints(tmp_path):
    item = {'task': '/tmp/t', 'status': 'failed', 'checks': [
        {'check': 'check-agent-editable-test.py', 'status': 'failed'},
        {'check': 'check-nproc.sh', 'status': 'error', 'reason': 'missing tool'}]}
    static = {'tasks': [item]}
    pipeline = {'stage': 4, 'items': [item]}
    for name, report in [('static', static), ('pipeline', pipeline)]:
        results.export_report(tmp_path / (name + '.parquet'), report, {'t': 'hash'})
    a, = results.read(tmp_path / 'static.parquet')
    b, = results.read(tmp_path / 'pipeline.parquet')
    assert a == b
    assert a['agent_editable_test'] == 'fail'
    assert a['nproc'] == 'execution_error'
    assert results.details(a)['nproc']['details'] == 'missing tool'


def test_hash_at_execution_is_preserved_and_passk_settings_are_distinct():
    reports = {2: {'task_content_sha256': {'t': 'old'},
                   'items': [{'task': 't', 'status': 'passed'}]}}
    row, = results.observations(reports, {'t': 'new'})
    assert row['content_sha256'] == 'old'
    trials = {6: {'items': [{'task': 't', 'status': 'completed', 'rewards': [0, 1]}]}}
    a, = results.observations(trials, {'t': 'hash'}, settings={'model': 'm', 'agent_kwargs': {'thinking': True}})
    b, = results.observations(trials, {'t': 'hash'}, settings={'model': 'm', 'agent_kwargs': {'thinking': False}})
    names_a = {k for k in a if k.startswith('passk_')}
    assert names_a.isdisjoint(k for k in b if k.startswith('passk_'))
    assert any(a[k] == 1 for k in names_a if k.endswith('_solved'))


def test_dynamic_warning_and_baseline_are_independent():
    report = {3: {'items': [{
        'task': 't', 'status': 'failed', 'normal_baseline_status': 'completed',
        'listing_order': {'status': 'flagged'},
        'verifier_tamper': {'status': 'flagged', 'tools': ['/bin/python']}}]}}
    row, = results.observations(report, {'t': 'hash'})
    assert row['nop'] == 'pass'
    assert row['nop_determinism'] == 'fail'
    assert row['verifier_tamper'] == 'warning'


def test_environment_failures_are_not_hidden_by_another_environment_pass():
    reports = {1: {'items': [{'task': 't', 'status': 'failed', 'environments': [
        {'environment': 'agent', 'checks': [{'check': 'declared-workdir', 'status': 'failed'}]},
        {'environment': 'verifier', 'checks': [{'check': 'declared-workdir', 'status': 'passed'}]}]}]}}
    row, = results.observations(reports, {'t': 'hash'})
    assert row['declared_workdir'] == 'fail'


def test_missing_selected_check_overwrites_old_pass(tmp_path):
    path = tmp_path / 'results.parquet'
    results.export_report(path, {'checks': ['check-nproc.sh'], 'tasks': [
        {'task': 't', 'checks': [{'check': 'check-nproc.sh', 'status': 'passed'}]}]}, {'t': 'hash'})
    results.export_report(path, {'checks': ['check-nproc.sh'], 'tasks': [
        {'task': 't', 'status': 'error', 'error': 'cannot read task', 'checks': []}]}, {'t': 'hash'})
    row, = results.read(path)
    assert row['nproc'] == 'unknown'


def test_details_are_kept_per_check_and_other_checks_are_preserved(tmp_path):
    import pyarrow.parquet as pq
    path = tmp_path / 'results.parquet'
    first = {'dataset': 'd', 'task_id': 't', 'content_sha256': 'a', 'reference': 'pass', 'nproc': 'fail',
             'details': json.dumps({'reference': {'rewards': [1]}, 'nproc': {'reason': 'FAIL make -j'}}),
             'publication_details': json.dumps({'source': 'original'})}
    results.update(path, [first])
    results.update(path, [{'dataset': 'd', 'task_id': 't', 'content_sha256': 'a',
                          'nop': 'fail', 'details': json.dumps({'nop': {'rewards': [1]}})}])
    row, = results.read(path)
    # Passes and failed no-op results (rewards are 0 or 1) need no text.
    recorded = results.details(row)
    assert recorded.pop('nproc') == {'details': 'FAIL make -j'}
    assert 'reference' not in recorded and 'nop' not in recorded
    assert recorded == {}  # checks that did not run have no text
    assert row['build'] == 'not_requested'
    assert row['reference'] == 'pass' and row['nop'] == 'fail'
    schema = pq.read_schema(path)
    assert 'details' not in schema.names and 'reference_details' not in schema.names
    assert 'publication_details' in schema.names
    assert schema.metadata[b'ot.results.version'] == b'3'
    results.update(path, [{'dataset': 'd', 'task_id': 't', 'content_sha256': 'b', 'reference': 'not_run'}])
    changed, = results.read(path)
    assert changed['nproc'] == 'not_requested' and changed['reference'] == 'unknown'
