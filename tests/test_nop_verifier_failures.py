"""NOP verification distinguishes genuine zero rewards from broken test runners."""
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


@pytest.mark.parametrize('declared,mixed,expected_status', [
    (True, False, 'completed'), (False, False, 'failed'), (True, True, 'failed')])
def test_nop_missing_declared_output_fixture_is_valid_but_other_setup_errors_are_not(
        tmp_path, declared, mixed, expected_status):
    task = tmp_path / 'task'
    task.mkdir()
    (task / 'instruction.md').write_text(
        '# Required output artifacts\n## 1. `/task_file/answer.json`\n'
        if declared else '# Inputs\n## `/task_file/answer.json`\n')
    output = '''collected 1 item
________________ ERROR at setup of test_answer ________________
>       assert OUTPUT.is_file(), f"{OUTPUT} must exist"
E       AssertionError: /task_file/answer.json must exist
================ 1 error in 0.04s ================
'''
    if mixed:
        output = output.replace('================ 1 error',
            'E       ModuleNotFoundError: No module named dependency\n================ 1 error')
    result = assess_trials([(tmp_path, {'verifier_result': {
        'rewards': {'reward': 0}, 'stdout': output}})], 1, 0, task_path=task)
    assert result['status'] == expected_status


@pytest.mark.parametrize('source', ['stdout', 'stderr', 'file'])
@pytest.mark.parametrize('reward,exception', [
    (0, None), (None, 'VerifierRuntimeError'), (None, 'RewardFileNotFoundError'),
])
def test_nop_counts_missing_instruction_file_as_zero(tmp_path, source, reward, exception):
    task = tmp_path / 'task'
    task.mkdir()
    (task / 'instruction.md').write_text('Write your answer to `/project/answer.json`.')
    output = ("E   FileNotFoundError: [Errno 2] No such file or directory: '/project/answer.json'\n"
              '================ 1 error in 0.04s ================\n')
    verifier = {'rewards': {'reward': reward}}
    if source == 'file':
        (tmp_path / 'verifier').mkdir()
        (tmp_path / 'verifier' / 'test-stdout.txt').write_text(output)
    else:
        verifier[source] = output
    trial = {'verifier_result': verifier,
             'exception_info': {'exception_type': exception} if exception else None}
    result = assess_trials([(tmp_path, trial)], 1, 0, task_path=task)
    assert result['status'] == 'completed'
    assert result['rewards'] == [0]
    assert result['missing_file_zeros'][0]['paths'] == ['/project/answer.json']
    assert verifier['rewards']['reward'] == reward
    assert trial['exception_info'] == ({'exception_type': exception} if exception else None)
    # The exception remains an error for reference and teacher trials.
    for expected in (1, None):
        other = assess_trials([(tmp_path, trial)], 1, expected, task_path=task)
        assert 'missing_file_zeros' not in other
        if exception:
            assert other['status'] == 'failed'


@pytest.mark.parametrize('instruction,extra,exception,reward', [
    ('No output file specified.', '', None, None),
    ('Write /project/answer.json.bak', '', None, None),
    ('Write /project/answer.json', 'E   PermissionError: denied\n', None, None),
    ('Write /project/answer.json', '/usr/bin/python3: No module named pytest\n', None, None),
    ('Write /project/answer.json', '/tests/test.sh: line 9: pytest: command not found\n', None, None),
    ('Write /project/answer.json',
     "E   FileNotFoundError: [Errno 2] No such file or directory: '/tests/input.json'\n", None, None),
    ('Write /project/answer.json', '', 'BuildError', None),
    ('Write /project/answer.json', '', 'AgentTimeoutError', None),
    ('Write /project/answer.json', '', None, 1),
])
def test_nop_missing_file_rule_does_not_hide_other_failures(tmp_path, instruction, extra, exception, reward):
    (tmp_path / 'instruction.md').write_text(instruction)
    trial = {'verifier_result': {'rewards': {'reward': reward}, 'stdout':
        "E   FileNotFoundError: [Errno 2] No such file or directory: '/project/answer.json'\n"
        + extra + '================ 1 error in 0.04s ================\n'},
        'exception_info': {'exception_type': exception} if exception else None}
    result = assess_trials([(tmp_path, trial)], 1, 0, task_path=tmp_path)
    assert result['status'] == 'failed'
    assert 'missing_file_zeros' not in result
