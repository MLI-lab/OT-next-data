"""No-reward infrastructure outcomes remain distinguishable from wrong rewards."""
import json
import pytest
from validation.run import observations


@pytest.mark.parametrize('stage,name,wrong', [(2, 'reference', 0), (3, 'nop', 1)])
def test_no_reward_is_not_run_but_observed_wrong_reward_fails(stage, name, wrong):
    item = {'task': 'task', 'status': 'failed', 'normal_baseline_status': 'failed',
            'rewards': [], 'findings': ['exception: runtime installation failed']}
    row, = observations({stage: {'items': [item]}}, {'task': 'digest'})
    assert row[name] == 'not_run'
    assert json.loads(row[name + '_details']) == item
    item.update(rewards=[wrong], findings=['wrong reward'])
    row, = observations({stage: {'items': [item]}}, {'task': 'digest'})
    assert row[name] == 'fail'


def test_historical_status_only_evidence_is_preserved():
    item = {'task': 'task', 'status': 'failed', 'findings': ['archived reference zero']}
    row, = observations({2: {'items': [item]}}, {'task': 'digest'})
    assert row['reference'] == 'fail'
