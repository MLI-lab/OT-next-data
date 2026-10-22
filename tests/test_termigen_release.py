import pytest

from data.termigen.patch import release_nop_failure


def test_only_completed_zero_reward_check_is_accepted():
    assert release_nop_failure({'status': 'passed', 'rewards': [0.0], 'findings': []}) is None


def test_zero_reward_without_executed_tests_is_excluded():
    result = release_nop_failure({'status': 'failed', 'rewards': [0.0],
                                  'findings': ['000: verifier executed no tests (empty, skipped, or setup errors)']})
    assert result[0] == 'no-op-no-tests'


def test_timeout_is_excluded_even_if_reward_zero_is_present():
    result = release_nop_failure({'status': 'failed', 'rewards': [0.0],
                                  'findings': ['VerifierTimeoutError: verifier timed out']})
    assert result[0] == 'no-op-timeout'


@pytest.mark.parametrize('item', [
    {'status': 'passed', 'rewards': [1.0]},
    {'status': 'passed', 'rewards': []},
    {'status': 'failed', 'rewards': [0.0]},
    {'status': 'skipped', 'rewards': []},
])
def test_unreviewed_outcomes_stop_preparation(item):
    with pytest.raises(ValueError, match='Unreviewed no-op outcome'):
        release_nop_failure(item)
