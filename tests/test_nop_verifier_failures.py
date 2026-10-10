"""NOP verification distinguishes genuine zero rewards from broken test runners."""
import json
from pathlib import Path

import pytest

from validation.stages.harbor import assess_trials


def execution_evidence(path, output, executed=1, setup_errors=0):
    """A current-run record from the runner, independent of captured log text."""
    token = 'a' * 32
    (path / 'verifier').mkdir(parents=True, exist_ok=True)
    (path / 'verifier' / 'execution-context.json').write_text(
        json.dumps({'version': 1, 'token': token}))
    begin = {'version': 1, 'id': 'outer'}
    end = dict(begin, finished=True, executed=executed, skipped=0,
               setup_errors=setup_errors, collection_errors=0, exitstatus=1)
    return ('OT_VERIFIER_EXECUTION:' + token + ':BEGIN:' + json.dumps(begin) + '\n' + output
            + '\nOT_VERIFIER_EXECUTION:' + token + ':END:' + json.dumps(end) + '\n')


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
        (tmp_path / 'verifier').mkdir(exist_ok=True)
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
        'rewards': {'reward': 0}, 'stdout': execution_evidence(tmp_path, output),
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
        'rewards': {'reward': 0}, 'stdout': execution_evidence(tmp_path, output, executed=0, setup_errors=1)}})], 1, 0, task_path=task)
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
    output = execution_evidence(tmp_path, output, executed=0, setup_errors=1)
    verifier = {'rewards': {'reward': reward}}
    if source == 'file':
        (tmp_path / 'verifier').mkdir(exist_ok=True)
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


def collection_error_evidence(path, output, errors=4):
    token = 'a' * 32
    (path / 'verifier').mkdir(parents=True, exist_ok=True)
    (path / 'verifier' / 'execution-context.json').write_text(
        json.dumps({'version': 1, 'token': token}))
    begin = {'version': 1, 'id': 'outer'}
    end = dict(begin, finished=True, executed=0, skipped=0, setup_errors=0,
               collection_errors=errors, exitstatus=2)
    return ('OT_VERIFIER_EXECUTION:' + token + ':BEGIN:' + json.dumps(begin) + '\n' + output
            + '\nOT_VERIFIER_EXECUTION:' + token + ':END:' + json.dumps(end) + '\n')


def reference_task(tmp_path, patch):
    task = tmp_path / 'task'
    (task / 'solution').mkdir(parents=True)
    (task / 'solution' / 'gold.patch').write_text(patch)
    (task / 'solve.sh').write_text('#!/bin/bash\ngit apply /solution/gold.patch\n')
    return task


RENAMED_MODULE = ("diff --git a/src/pkg/auxiliary.py b/src/pkg/auxiliary.py\n"
                  "--- a/src/pkg/auxiliary.py\n+++ b/src/pkg/auxiliary.py\n"
                  "-from .types import GUARDIAN_ID\n+from .type import GUARDIAN_ID\n")
MISSING_TYPES = ("==================================== ERRORS ====================================\n"
                 "________ ERROR collecting tests/unit/test_decryption.py ________\n"
                 "tests/unit/test_decryption.py:4: in <module>\n"
                 "    from pkg.auxiliary import GUARDIAN_ID\n"
                 "src/pkg/auxiliary.py:3: in <module>\n"
                 "    from .types import GUARDIAN_ID\n"
                 "E   ModuleNotFoundError: No module named 'pkg.types'\n"
                 "=========================== short test summary info ============================\n"
                 "!!!!!!!!!!!!!!!!!!! Interrupted: 4 errors during collection !!!!!!!!!!!!!!!!!!!!\n")


def test_nop_collection_error_on_code_the_reference_changes_is_a_valid_zero(tmp_path):
    task = reference_task(tmp_path, RENAMED_MODULE)
    trial = tmp_path / 'trial'
    output = collection_error_evidence(trial, MISSING_TYPES)
    results = [(trial, {'verifier_result': {'rewards': {'reward': 0.0}, 'stdout': output}})]
    assert assess_trials(results, 1, 0, task_path=task)['status'] == 'completed'
    # The same import failure in a reference run is still a failed oracle.
    assert assess_trials(results, 1, 1, task_path=task)['status'] == 'failed'


def block(kind, where, lines):
    return '_' * 8 + ' ERROR ' + kind + ' ' + where + ' ' + '_' * 8 + '\n' + lines


@pytest.mark.parametrize('output', [
    block('collecting', 'tests/test_a.py', "tests/test_a.py:1: in <module>\nE   ModuleNotFoundError: No module named 'requests'\n"),  # unrelated dependency, unpatched file
    block('collecting', 'tests/test_a.py', "tests/test_a.py:1: in <module>\nE   ImportError: cannot import name 'Other' from 'pkg.types'\n"),  # symbol the patch never touches
    block('collecting', 'tests/test_a.py', "tests/test_a.py:1: in <module>\nE   SyntaxError: invalid syntax\n"),  # raised in an unpatched file
    block('at setup of', 'test_x', "tests/conftest.py:9: in fixture\nE   RuntimeError: database down\n"),  # a setup error, not collection
    "E   ModuleNotFoundError: No module named 'pkg.types'\n",  # no pytest error block at all
])
def test_nop_collection_errors_outside_the_reference_change_stay_runner_problems(tmp_path, output):
    task = reference_task(tmp_path, RENAMED_MODULE)
    trial = tmp_path / 'trial'
    evidence = collection_error_evidence(trial, output)
    results = [(trial, {'verifier_result': {'rewards': {'reward': 0.0}, 'stdout': evidence}})]
    report = assess_trials(results, 1, 0, task_path=task)
    assert report['status'] == 'failed'
    assert any('collection' in finding for finding in report['findings'])


def test_nop_collection_error_without_a_reference_patch_is_not_excused(tmp_path):
    trial = tmp_path / 'trial'
    evidence = collection_error_evidence(trial, MISSING_TYPES)
    results = [(trial, {'verifier_result': {'rewards': {'reward': 0.0}, 'stdout': evidence}})]
    assert assess_trials(results, 1, 0, task_path=None)['status'] == 'failed'
    assert assess_trials(results, 1, 0, task_path=tmp_path)['status'] == 'failed'


def test_nop_errors_raised_inside_patched_files_are_valid_zeros(tmp_path):
    # itolapi-11: Python 2 print statements in the files the reference ports to Python 3.
    patch = ("diff --git a/itolapi/Itol.py b/itolapi/Itol.py\n--- a/itolapi/Itol.py\n+++ b/itolapi/Itol.py\n"
             "-        print variable_name\n+        print(variable_name)\n")
    task = reference_task(tmp_path, patch)
    output = block('collecting', 'tests/itol.py', "tests/itol.py:5: in <module>\n    from itolapi import Itol\n"
                   'E     File "/testbed/itolapi/Itol.py", line 88\nE       print variable_name\n'
                   "E   SyntaxError: Missing parentheses in call to 'print'\n")
    trial = tmp_path / 'trial'
    evidence = collection_error_evidence(trial, output, errors=1)
    results = [(trial, {'verifier_result': {'rewards': {'reward': 0.0}, 'stdout': evidence}})]
    assert assess_trials(results, 1, 0, task_path=task)['status'] == 'completed'


def test_nop_setup_errors_in_patched_code_are_valid_zeros(tmp_path):
    # django-countries-339: every fixture fails on the attribute the reference adds.
    patch = ("diff --git a/django_countries/fields.py b/django_countries/fields.py\n"
             "--- a/django_countries/fields.py\n+++ b/django_countries/fields.py\n+        self.db_collation = None\n")
    task = reference_task(tmp_path, patch)
    output = ''.join(block('at setup of', 'TestCountryField.test_' + n,
                           "E   AttributeError: 'CountryField' object has no attribute 'db_collation'\n") for n in ('a', 'b'))
    token = 'a' * 32
    trial = tmp_path / 'trial'
    (trial / 'verifier').mkdir(parents=True)
    (trial / 'verifier' / 'execution-context.json').write_text(json.dumps({'version': 1, 'token': token}))
    begin = {'version': 1, 'id': 'outer'}
    end = dict(begin, finished=True, executed=0, skipped=0, setup_errors=2, collection_errors=0, exitstatus=1)
    evidence = ('OT_VERIFIER_EXECUTION:' + token + ':BEGIN:' + json.dumps(begin) + '\n' + output
                + '\nOT_VERIFIER_EXECUTION:' + token + ':END:' + json.dumps(end) + '\n')
    results = [(trial, {'verifier_result': {'rewards': {'reward': 0.0}, 'stdout': evidence}})]
    assert assess_trials(results, 1, 0, task_path=task)['status'] == 'completed'
    unrelated = evidence.replace('db_collation', 'db_table')
    results = [(trial, {'verifier_result': {'rewards': {'reward': 0.0}, 'stdout': unrelated}})]
    assert assess_trials(results, 1, 0, task_path=task)['status'] == 'failed'


def test_nop_usage_error_for_an_option_the_reference_removes_is_a_valid_zero(tmp_path):
    # poliastro-192: setup.cfg still asked for pytest-benchmark flags; the reference drops them.
    patch = ("diff --git a/setup.cfg b/setup.cfg\n--- a/setup.cfg\n+++ b/setup.cfg\n"
             "-addopts = --benchmark-autosave --benchmark-skip\n+addopts =\n")
    task = reference_task(tmp_path, patch)
    output = ("ERROR: usage: pytest [options] [file_or_dir] [file_or_dir] [...]\n"
              "pytest: error: unrecognized arguments: --benchmark-autosave\n  inifile: /testbed/setup.cfg\n  rootdir: /testbed\n")
    trial = tmp_path / 'trial'
    (trial / 'verifier').mkdir(parents=True)
    (trial / 'verifier' / 'execution-context.json').write_text(json.dumps({'version': 1, 'token': 'a' * 32}))
    results = [(trial, {'verifier_result': {'rewards': {'reward': 0.0}, 'stdout': output}})]
    assert assess_trials(results, 1, 0, task_path=task)['status'] == 'completed'
    assert assess_trials(results, 1, 1, task_path=task)['status'] == 'failed'
    other = output.replace('--benchmark-autosave', '--cov')
    results = [(trial, {'verifier_result': {'rewards': {'reward': 0.0}, 'stdout': other}})]
    assert assess_trials(results, 1, 0, task_path=task)['status'] == 'failed'


def test_nop_failure_before_any_test_is_a_valid_zero_when_the_oracle_ran_the_suite(tmp_path):
    # Seven SWE-Lego no-op runs fail in unpatched code without a usable traceback
    # (one-line tb mode); the oracle of the same job executed the suite.
    output = block('collecting', 'tests/test_api.py', "E   TypeError: __new__() got an unexpected keyword argument 'write_reference'\n")
    trial = tmp_path / 'trial'
    evidence = collection_error_evidence(trial, output, errors=1)
    results = [(trial, {'verifier_result': {'rewards': {'reward': 0.0}, 'stdout': evidence}})]
    strict = assess_trials(results, 1, 0, task_path=None)
    assert strict['status'] == 'failed'
    relaxed = assess_trials(results, 1, 0, task_path=None, oracle_passed=True)
    assert relaxed['status'] == 'completed'
    assert relaxed['notes'] == ['trial: verifier invocation failed during collection or execution; '
                                'accepted because the reference run of this job executed the suite']
    # It never excuses a no-op that executed tests and still failed the checks,
    # a reward of 1, or an oracle run.
    assert assess_trials(results, 1, 1, task_path=None, oracle_passed=True)['status'] == 'failed'
    one = [(trial, {'verifier_result': {'rewards': {'reward': 1.0}, 'stdout': evidence}})]
    assert assess_trials(one, 1, 0, task_path=None, oracle_passed=True)['status'] == 'failed'


def test_oracle_passed_tasks_reads_this_submissions_stage_4_reports(tmp_path):
    from validation.stages.runner import oracle_passed_tasks
    report = tmp_path / 'stage_4_oracle_validation' / 'abc' / 'summary.json'
    report.parent.mkdir(parents=True)
    report.write_text(json.dumps({'items': [{'task': '/x/tasks/a__1', 'status': 'passed'},
                                            {'task': '/x/tasks/b__2', 'status': 'failed'}]}))
    (tmp_path / 'stage_4_oracle_validation' / 'broken').mkdir()
    (tmp_path / 'stage_4_oracle_validation' / 'broken' / 'summary.json').write_text('{')
    assert oracle_passed_tasks(tmp_path) == {'a__1'}
    assert oracle_passed_tasks(tmp_path / 'missing') == set()
