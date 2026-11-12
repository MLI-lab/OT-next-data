from validation.leak_repair import launch, tool


def test_exclude_other_failures_allow_warnings_and_retain_incomplete():
    row = {'task_id': 'a', 'agent_editable_test': 'fail', 'repository_history': 'warning',
           'reference_leaks': 'execution_error', 'build': 'pass'}
    assert tool.eligible_repairs(row) == ['agent_editable_test']
    assert tool.eligible_repairs(dict(row, reference='fail')) == []
    assert tool.eligible_repairs(dict(row, reference={'status': 'fail'})) == []
    assert tool.eligible_repairs(dict(row, agent_editable_test='pass')) == ['repository_history']
    assert row['reference_leaks'] == 'execution_error'


def test_open_tasks_never_sends_other_failed_tasks_to_repair(monkeypatch):
    monkeypatch.setattr(tool, 'leak_plans', lambda *a, **kw: {})
    rows = [{'task_id': 'keep', 'agent_editable_test': 'fail', 'reference': 'pass'},
            {'task_id': 'exclude', 'agent_editable_test': 'fail', 'reference': 'fail'}]
    assert set(launch.open_tasks(rows, None, {}, 'd', {}, 'agent_editable_test')) == {'keep'}
