import copy
import json
import pytest
from data.patch_repair_loop import controller as c


def setup_case(monkeypatch):
    config = {'source': '/pinned/tasks.parquet', 'source_revision': 'pinned-revision'}
    record = {'task_id': 'restored', 'category': 'build', 'reason': 'old build error', 'evidence': '/old/log'}
    state = {'known_ids': ['kept', 'removed'], 'exclusions': [record,
             dict(record, task_id='removed')], 'pilot': 3, 'review_setup': False,
             'stage3_passed_ids': ['kept']}
    monkeypatch.setattr(c, 'task_ids', lambda p: ['kept', 'removed', 'restored', 'unreviewed'])
    return config, state


def test_restoration_preserves_evidence_and_restarts_validation(tmp_path, monkeypatch):
    config, state = setup_case(monkeypatch)
    c.reconcile_task_ids(config, state, ['kept', 'restored'], tmp_path)
    assert state['known_ids'] == ['kept', 'restored']
    assert [x['task_id'] for x in state['exclusions']] == ['removed']
    assert state['pilot'] == 0 and state['review_setup'] is True
    assert state['phase'] == 'build'
    assert 'stage3_passed_ids' not in state
    audit = json.loads((tmp_path / 'task-id-reconciliation.json').read_text())
    assert audit['restored_task_ids'] == ['restored']
    assert audit['newly_excluded_task_ids'] == ['removed']
    assert audit['prior_exclusions'][0]['evidence'] == '/old/log'
    c.reconcile_task_ids(config, state, ['kept', 'restored'], tmp_path)
    assert len(state['restorations']) == 1


@pytest.mark.parametrize('available,match', [
    (['kept','removed','invented'], 'outside the pinned source'),
    (['kept','removed','unreviewed'], 'lack prior exclusion evidence'),
    (['restored'], 'Undeclared task exclusions'),
])
def test_invalid_transitions_leave_state_unchanged(tmp_path, monkeypatch, available, match):
    config, state = setup_case(monkeypatch)
    before = copy.deepcopy(state)
    with pytest.raises(ValueError, match=match):
        c.reconcile_task_ids(config, state, available, tmp_path)
    assert state == before
    assert not (tmp_path / 'task-id-reconciliation.json').exists()


def test_bad_exclusion_evidence_does_not_authorize_restoration(tmp_path, monkeypatch):
    config, state = setup_case(monkeypatch)
    state['exclusions'][0]['evidence'] = ''
    with pytest.raises(ValueError, match='exclusions require'):
        c.reconcile_task_ids(config, state, ['kept','removed','restored'], tmp_path)


def test_regular_exclusion_and_initial_generation(tmp_path, monkeypatch):
    config, state = setup_case(monkeypatch)
    monkeypatch.setattr(c, 'task_ids', lambda p: pytest.fail('no source lookup needed'))
    c.reconcile_task_ids(config, state, ['kept'], tmp_path)
    assert state['known_ids'] == ['kept']
    assert 'restorations' not in state
    state['known_ids'] = None
    c.reconcile_task_ids(config, state, ['initial'], tmp_path)
    assert state['known_ids'] == ['initial']
