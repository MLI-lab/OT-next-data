"""Tamper comparisons use no-op accepted-zero policy, including its exclusions."""
import json
from types import SimpleNamespace
import pytest
from validation.stages import harbor
from validation.stages.runner import add_verifier_tamper
from validation.rules.dynamic.verifier_tamper.stage import EVIDENCE, HITS


def trial(root, name, reward, exception=None, wrapped=False, hits=True, key='reward'):
    path = root / name
    (path / 'verifier').mkdir(parents=True)
    if wrapped:
        (path / EVIDENCE).write_text(json.dumps({'status': 'applied', 'wrapped': ['/usr/bin/python3']}))
        if hits:
            (path / 'verifier' / HITS).write_text('/usr/bin/python3\n')
    return path, {'verifier_result': {'rewards': {key: reward}},
                  'exception_info': {'exception_type': exception} if exception else None}


def assess(root, rows, key='reward'):
    prepared = {'findings': []}
    add_verifier_tamper(prepared, rows, root / 'task', SimpleNamespace(reward_key=key))
    return prepared['verifier_tamper']


@pytest.mark.parametrize('exception', [None, 'RewardFileNotFoundError', 'RewardFileEmptyError',
                                      'VerifierRuntimeError', 'VerifierTimeoutError'])
@pytest.mark.parametrize('hits', [True, False])
def test_accepted_missing_reward_matches_noop_zero(tmp_path, exception, hits):
    wrapped = trial(tmp_path, 'wrapped', None, exception, wrapped=True, hits=hits)
    baseline = trial(tmp_path, 'baseline', 0)
    assert harbor.assess_trials([wrapped], 1, expected_reward=0)['status'] == 'completed'
    assert assess(tmp_path, [wrapped, baseline])['status'] == ('flagged' if hits else 'passed')


@pytest.mark.parametrize('reward', [None, 0])
@pytest.mark.parametrize('exception', ['BuildError', 'EnvironmentStartTimeoutError', 'PhaseCancellationError'])
def test_infrastructure_failure_is_not_zero(tmp_path, reward, exception):
    wrapped = trial(tmp_path, 'wrapped', reward, exception, wrapped=True)
    baseline = trial(tmp_path, 'baseline', 0)
    assert harbor.assess_trials([wrapped], 1, expected_reward=0)['status'] == 'failed'
    assert assess(tmp_path, [wrapped, baseline])['status'] == 'incomplete'


def test_baseline_missing_reward_and_custom_reward_key(tmp_path):
    wrapped = trial(tmp_path, 'wrapped', 0, wrapped=True, key='score')
    baseline = trial(tmp_path, 'baseline', None, 'RewardFileNotFoundError', key='score')
    assert assess(tmp_path, [wrapped, baseline], 'score')['status'] == 'flagged'


def test_nonzero_reward_mismatch_stays_incomplete(tmp_path):
    wrapped = trial(tmp_path, 'wrapped', 1, wrapped=True)
    baseline = trial(tmp_path, 'baseline', 0)
    result = assess(tmp_path, [wrapped, baseline])
    assert result['status'] == 'incomplete'
    assert 'reward 1 differs' in result['reason']


def test_missing_reward_with_resource_failure_is_not_zero(tmp_path):
    wrapped = trial(tmp_path, 'wrapped', None, wrapped=True)
    (wrapped[0] / 'verifier' / 'test-stdout.txt').write_text('No space left on device\n')
    baseline = trial(tmp_path, 'baseline', 0)
    assert assess(tmp_path, [wrapped, baseline])['status'] == 'incomplete'
