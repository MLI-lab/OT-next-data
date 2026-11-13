"""Unresolved outcomes share categories without inventing a cause."""
import json
import pytest

from validation.outcomes import category, normalize
from validation.run import observations
from validation.publishing.publish import judge
from validation.publishing.readme import outcome


@pytest.mark.parametrize('record,expected', [
    ({'status': 'skipped', 'blocked_by_stage': 1}, 'skipped'),
    ({'status': 'not_applicable'}, 'skipped'),
    ({'status': 'error', 'error': 'Disk quota exceeded'}, 'infrastructure_error'),
    ({'status': 'failed', 'exception_type': 'EnvironmentStartTimeoutError'}, 'timeout'),
    ({'status': 'error', 'oom_killed': True}, 'oom'),
    ({'status': 'error', 'error': 'ModuleNotFoundError: package'}, 'execution_error'),
    ({'status': 'failed', 'rewards': []}, 'execution_error'),
    ({}, 'unknown'),
    ({'returncode': 137}, 'unknown'),
])
def test_categories(record, expected):
    assert category(record)['category'] == expected


def test_skip_reasons():
    assert category({'status': 'not_applicable'})['reason_code'] == 'not_applicable'
    assert category({'status': 'skipped', 'blocked_by_stage': 1})['reason_code'] == 'blocked_by_previous_check'
    assert category({'status': 'skipped'})['reason_code'] == 'not_requested'


@pytest.mark.parametrize('verdict', ['pass', 'warning'])
def test_completed_checks_do_not_inherit_diagnostic_errors(verdict):
    assert normalize('reference', verdict, {'status': 'passed', 'findings': ['earlier TimeoutError']})[0] == verdict


def test_old_attempts_do_not_supply_current_cause():
    record = {'status': 'failed', 'rewards': [], 'outcome_attempts': [{'error': 'Disk quota exceeded'}]}
    assert category(record)['category'] == 'execution_error'


def test_static_finding_is_not_checker_crash():
    assert normalize('python_missing_imports', 'fail', {'status': 'failed', 'findings': ['SyntaxError in solve.py']})[0] == 'fail'


@pytest.mark.parametrize('error,expected', [('Disk quota exceeded', 'infrastructure_error'),
                                           ('VerifierTimeoutError', 'timeout'),
                                           ('OOMKilled', 'oom'),
                                           ('ModuleNotFoundError', 'execution_error')])
def test_export_publisher_and_readme_agree(error, expected):
    item = {'task': 'task', 'status': 'failed', 'rewards': [], 'error': error}
    row, = observations({2: {'items': [item]}}, {'task': 'digest'})
    assert row['reference'] == expected
    assert judge(2, item)[0] == 'not_run'
    assert outcome(row, 'reference') in ('incomplete', 'infra error')


def test_wrong_reward_remains_failure_everywhere():
    item = {'task': 'task', 'status': 'failed', 'rewards': [0.0], 'findings': ['expected reward 1, got 0']}
    row, = observations({2: {'items': [item]}}, {'task': 'digest'})
    assert row['reference'] == 'fail'
    assert judge(2, item)[0] == 'archive'
    assert outcome(row, 'reference') == 'fail'


def test_not_applicable_is_not_unresolved():
    item = {'task': 'task', 'status': 'not_applicable'}
    row, = observations({2: {'items': [item]}}, {'task': 'digest'})
    assert row['reference'] == 'not_applicable'
    assert outcome(row, 'reference') == 'not applicable'


def test_nested_setup_error_preserves_specific_cause():
    issue = category({'status': 'error', 'environments': [
        {'status': 'error', 'error': 'container start timed out'}]})
    assert issue['category'] == 'timeout'
    assert issue['phase'] == 'setup'


def test_not_run_is_distinct_from_execution_failure():
    assert normalize('nop', 'not_requested', {}) == ('not_run', {'reason': 'Not requested'})
    assert normalize('nop', 'blocked', {}) == ('not_run', {'reason': 'Blocked by an earlier check'})
    for state in ('timeout', 'oom', 'execution_error', 'infrastructure_error'):
        assert normalize('nop', state, {})[0] == state
    assert normalize('nop', 'not_run', {'status': 'error', 'error': 'TimeoutError'})[0] == 'timeout'
    assert normalize('nop', 'not_run', {'details': 'Awaiting evaluation after repair'})[0] == 'not_run'
