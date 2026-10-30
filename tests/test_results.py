import json
from pathlib import Path

from validation import run as results


def test_partial_update_preserves_other_checks_and_changed_content_resets(tmp_path):
    path = tmp_path / 'results.parquet'
    first = {'dataset': 'd', 'task_id': 't', 'content_sha256': 'a', 'nproc': 'pass', 'nop': 'fail'}
    results.update(path, [first])
    results.update(path, [dict(first, nproc='warning') | {'nop_details': '{}'}])
    results.update(path, [{'dataset': 'd', 'task_id': 't', 'content_sha256': 'a', 'reference': 'pass'}])
    row, = results.read(path)
    assert row['nop'] == 'fail' and row['nproc'] == 'warning'
    results.update(path, [{'dataset': 'd', 'task_id': 't', 'content_sha256': 'b', 'nproc': 'pass',
                          'change_reason': 'Fixed verifier'}])
    old, new = results.read(path)
    assert not old['latest'] and new['latest']
    assert old['reference'] == 'pass' and new['reference'] == 'not_run'
    assert new['revision'] == 2 and new['change_reason'] == 'Fixed verifier'


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
    a.pop('updated_at'); b.pop('updated_at')
    assert a == b
    assert a['agent_editable_test'] == 'fail'
    assert a['nproc'] == 'not_run'
    assert json.loads(a['nproc_details'])['status'] == 'error'


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
    assert row['nproc'] == 'not_run'
