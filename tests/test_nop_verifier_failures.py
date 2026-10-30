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


def test_oracle_passed_tasks_reads_this_submissions_stage_4_reports(tmp_path):
    from validation.stages.runner import oracle_passed_tasks
    report = tmp_path / 'stage_2_oracle_validation' / 'abc' / 'summary.json'
    report.parent.mkdir(parents=True)
    report.write_text(json.dumps({'items': [{'task': '/x/tasks/a__1', 'status': 'passed', 'rewards': [1.0]},
                                            {'task': '/x/tasks/b__2', 'status': 'failed'}]}))
    (tmp_path / 'stage_2_oracle_validation' / 'broken').mkdir()
    (tmp_path / 'stage_2_oracle_validation' / 'broken' / 'summary.json').write_text('{')
    assert oracle_passed_tasks(tmp_path) == {'a__1'}
    assert oracle_passed_tasks(tmp_path / 'missing') == set()


@pytest.mark.parametrize("error", ["MemoryLimitExceededError", "VerifierTimeoutError", "ContainerStartError"])
def test_paired_reference_cannot_override_resource_or_infrastructure_exception(tmp_path, error):
    task = reference_task(tmp_path, RENAMED_MODULE)
    trial = tmp_path / "trial"
    result = {"verifier_result": {"rewards": {"reward": 0},
              "stdout": collection_error_evidence(trial, MISSING_TYPES)},
              "exception_info": {"exception_type": error}}
    assert assess_trials([(trial, result)], 1, 0, task_path=task, oracle_passed=True)["status"] == "failed"


@pytest.mark.parametrize("extra", ["Killed (out of memory)", "No space left on device"])
def test_missing_score_rule_does_not_mask_mixed_infrastructure_failure(tmp_path, extra):
    task = reference_task(tmp_path, RENAMED_MODULE)
    trial = tmp_path / "trial"
    result = {"verifier_result": {"rewards": {},
              "stdout": collection_error_evidence(trial, MISSING_TYPES + extra)}}
    assert assess_trials([(trial, result)], 1, 0, task_path=task, oracle_passed=True)["status"] == "failed"


def test_paired_reference_requires_matching_contract_and_actual_reward_one(tmp_path):
    from validation.stages.runner import oracle_passed_tasks
    root = tmp_path / "stage_2_oracle_validation"
    for name,contract,rewards in [("a", "current", [1]), ("b", "old", [1]), ("c", "current", []), ("d", "current", [0])]:
        p = root / name / "summary.json"
        p.parent.mkdir(parents=True)
        p.write_text(json.dumps({"contract_sha256": contract, "items": [
            {"task": "/tasks/" + name, "status": "passed", "rewards": rewards}]}))
    assert oracle_passed_tasks(tmp_path, "current") == {"a"}


def test_infrastructure_zero_is_retried_not_archived_as_wrong_reward(tmp_path):
    from validation.publishing.publish import judge
    output = execution_evidence(tmp_path, 'Killed (out of memory)', executed=0, setup_errors=1)
    report = assess_trials([(tmp_path, {'verifier_result': {'stdout': output, 'rewards': {'reward': 0}}})],
                           1, 0, oracle_passed=True)
    assert report['status'] == 'failed'
    assert judge(3, report)[0] == 'not_run'


@pytest.mark.parametrize('exception_type, reward, oracle_passed, status', [
    ('RewardFileNotFoundError', None, True, 'completed'),   # needs a file only the solution creates
    ('VerifierRuntimeError', None, True, 'completed'),
    ('VerifierTimeoutError', None, True, 'failed'),         # hang or overloaded node: stays open
    ('EnvironmentStartTimeoutError', None, True, 'failed'),
    ('RewardFileNotFoundError', None, False, 'completed'),
    (None, 1, True, 'failed'),
])
def test_nop_result_with_and_without_a_passing_reference(tmp_path, exception_type, reward, oracle_passed, status):
    from validation.publishing.publish import judge
    (tmp_path / 'verifier').mkdir()
    result = {'verifier_result': {'rewards': {} if reward is None else {'reward': reward}}}
    if exception_type:
        result['exception_info'] = {'exception_type': exception_type}
    report = assess_trials([(tmp_path, result)], 1, 0, oracle_passed=oracle_passed)
    assert report['status'] == status
    if status == 'failed' and reward is None:
        assert judge(3, report)[0] == 'not_run'


def test_resource_failure_stays_open_unless_tests_ran(tmp_path):
    (tmp_path / 'verifier').mkdir()
    (tmp_path / 'verifier' / 'execution-context.json').write_text(json.dumps({'version': 1, 'token': 'a' * 32}))
    result = {'verifier_result': {'rewards': {'reward': 0}, 'stdout': 'Killed (out of memory)\n'}}
    assert assess_trials([(tmp_path, result)], 1, 0)['status'] == 'failed'
    (tmp_path / 'verifier' / 'ctrf.json').write_text(json.dumps({'results': {'tests': [{'name': 't', 'status': 'failed'}]}}))
    assert assess_trials([(tmp_path, result)], 1, 0)['status'] == 'completed'


def test_zero_without_evidence_passes_and_leaves_a_note(tmp_path):
    (tmp_path / 'verifier').mkdir()
    result = {'verifier_result': {'rewards': {'reward': 0}, 'stdout': 'E  connection refused\n'}}
    report = assess_trials([(tmp_path, result)], 1, 0)
    assert report['status'] == 'completed' and 'accepted zero' in report['notes'][0]
