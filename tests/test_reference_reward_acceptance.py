"""Reference rewards do not depend on optional execution-recorder coverage."""
import json

import pytest

from validation.stages.harbor import assess_trials


@pytest.mark.parametrize('evidence', ['absent', 'empty', 'malformed', 'zero-tests'])
def test_reference_reward_one_without_complete_recorder(tmp_path, evidence):
    verifier = tmp_path / 'verifier'
    verifier.mkdir()
    if evidence != 'absent':
        token = 'a' * 32
        (verifier / 'execution-context.json').write_text(
            '{invalid' if evidence == 'malformed' else json.dumps({'version': 1, 'token': token}))
        output = ''
        if evidence == 'zero-tests':
            # TerminalLego: auxiliary Maven command recorded; uvx pytest missed.
            begin = {'version': 1, 'id': 'maven'}
            end = dict(begin, finished=True, executed=0, skipped=0,
                       setup_errors=0, collection_errors=0, exitstatus=5)
            output = (f'OT_VERIFIER_EXECUTION:{token}:BEGIN:{json.dumps(begin)}\n'
                      f'OT_VERIFIER_EXECUTION:{token}:END:{json.dumps(end)}\n')
        (verifier / 'ot-execution.txt').write_text(output)
    trial = {'verifier_result': {'rewards': {'reward': 1.0}}}
    result = assess_trials([(tmp_path, trial)], 1, expected_reward=1)
    assert result['status'] == 'completed'
    assert result['rewards'] == [1.0]
    assert result['findings'] == []
    # An unconditional reward of one still fails the independent no-op gate.
    assert assess_trials([(tmp_path, trial)], 1, expected_reward=0)['status'] == 'failed'


@pytest.mark.parametrize('reward', [None, 0, 0.5, 2, '1', True, float('nan'), float('inf')])
def test_reference_requires_actual_numeric_one(tmp_path, reward):
    trial = {'verifier_result': {'rewards': {'reward': reward}, 'stdout': 'reward 1\n23 passed'}}
    assert assess_trials([(tmp_path, trial)], 1, 1)['status'] == 'failed'


@pytest.mark.parametrize('exception', ['AgentTimeoutError', 'VerifierTimeoutError',
                                      'VerifierRuntimeError', 'BuildError'])
def test_reference_reward_does_not_override_trial_exception(tmp_path, exception):
    trial = {'exception_info': {'exception_type': exception},
             'verifier_result': {'rewards': {'reward': 1}}}
    assert assess_trials([(tmp_path, trial)], 1, 1)['status'] == 'failed'


def test_reference_requires_all_attempts_and_configured_reward_key(tmp_path):
    trial = {'verifier_result': {'rewards': {'correctness': 1}}}
    assert assess_trials([(tmp_path, trial)], 1, 1)['status'] == 'failed'
    assert assess_trials([(tmp_path, trial)], 1, 1, 'correctness')['status'] == 'completed'
    assert assess_trials([(tmp_path, trial)], 2, 1, 'correctness')['status'] == 'failed'
    assert assess_trials([], 1, 1)['status'] == 'failed'
