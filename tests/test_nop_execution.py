from pathlib import Path

import pytest

from validation.stages.harbor import assess_trials


@pytest.mark.parametrize('output', [
    '/usr/bin/python3: No module named pytest\n',
    '/tests/test.sh: line 9: pytest: command not found\n',
    '/tests/test.sh: 9: pytest: not found\n',
    '================ Interrupted: 1 error during collection ================\n',
    '================ no tests ran in 0.01s ================\n',
    '3 skipped in 0.02s\n',
    '================ 2 errors in 0.04s ================\n',
    '\x1b[31m3 skipped\x1b[0m in 0.02s\n',
])
@pytest.mark.parametrize('source', ['stdout', 'stderr', 'file'])
def test_nop_rejects_runner_failure_despite_zero_reward(tmp_path, output, source):
    verifier = {'rewards': {'reward': 0}}
    if source == 'file':
        (tmp_path / 'verifier').mkdir()
        (tmp_path / 'verifier' / 'test-stdout.txt').write_text(output)
    else:
        verifier[source] = output
    result = assess_trials([(tmp_path, {'verifier_result': verifier})], 1, 0)
    assert result['status'] == 'failed'
    assert any('verifier' in finding for finding in result['findings'])


@pytest.mark.parametrize('output', [
    '================ 7 failed, 1 passed in 0.21s ================\n',
    '1 failed, 3 skipped in 0.01s\n',
    "E   ModuleNotFoundError: No module named 'task_package'\n1 failed in 0.01s\n",
    'E   FileNotFoundError: /project/answer.txt\n1 failed in 0.01s\n',
    "    print('no tests ran')\n1 failed in 0.01s\n",
    'Custom grader: answer incorrect\n',
    '',
])
def test_nop_accepts_executed_failures_and_non_pytest_graders(tmp_path, output):
    result = assess_trials([(tmp_path, {'verifier_result': {
        'rewards': {'reward': 0}, 'stdout': output,
    }})], 1, 0)
    assert result['status'] == 'completed'


def test_nop_guard_does_not_change_teacher_reward_assessment():
    result = assess_trials([(Path('trial'), {'verifier_result': {
        'rewards': {'reward': 0}, 'stdout': '/usr/bin/python3: No module named pytest\n',
    }})], 1)
    assert result['status'] == 'completed'
