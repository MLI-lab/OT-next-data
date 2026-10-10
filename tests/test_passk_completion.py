from copy import deepcopy
from pathlib import Path
import pytest
from validation.checks.passk_completion import assess, check_reports
from validation.checks.reward_metrics import task_metrics, summarize


def trial(index, reward=None, error=None, finished=True):
    data = {'id': str(index), 'finished_at': '2026-10-08T12:00:00Z' if finished else None,
            'verifier_result': {'rewards': {'reward': reward}}}
    if error:
        data['exception_info'] = {'exception_type': error, 'exception_message': 'original failure'}
    return Path('/trials') / str(index), data


@pytest.mark.parametrize('error', ['VerifierRuntimeError', 'VerifierTimeoutError', 'RewardFileNotFoundError',
                                  'VerifierOutputParseError', 'APIError', 'UnknownError'])
def test_terminal_error_receives_zero_preserving_error_evidence(error):
    results = [trial(1, 1), trial(2, error=error)]
    original = deepcopy(results)
    report = assess(results, 2)
    assert report['status'] == 'completed'
    assert report['rewards'] == [1, 0]
    assert report['attempt_outcomes'][1]['reward'] == 0
    assert report['attempt_outcomes'][1]['verifier_reward'] is None
    assert report['recorded_errors'][0]['exceptions'][0]['exception_type'] == error
    metrics = task_metrics(results, 2)
    assert metrics['pass@1'] == .5 and metrics['pass@k'] == 1
    assert metrics['no_reward'] == 0
    assert metrics['mean_reward'] == .5
    assert results == original


@pytest.mark.parametrize('results', [[], [trial(1, 1)], [trial(1, 1), trial(1, 1)],
    [trial(1, 1), trial(2, error='VerifierRuntimeError', finished=False)],
    [trial(1, 1), trial(2)]])
def test_missing_duplicate_unfinished_or_unknown_outcome_blocks_completion(results):
    assert assess(results, 2)['status'] == 'failed'


def test_missing_results_do_not_become_attempts():
    metrics = task_metrics([trial(1, 1)], 16)
    assert metrics['attempts'] == 1 and metrics['missing_attempts'] == 15
    assert metrics['no_reward'] == 0
    assert task_metrics([], 16)['attempts'] == 0


def reports():
    results = [trial(1, 1), trial(2, error='VerifierRuntimeError')]
    item = dict(task='/tasks/example', **assess(results, 2), metrics=task_metrics(results, 2))
    six = {'stage': 6, 'complete': True, 'contract_sha256': 'hash', 'items': [item]}
    seven = {'stage': 7, 'complete': True, 'contract_sha256': 'hash',
             'items': [{'task': '/tasks/example', 'trajectories': 2}],
             'trace_metrics': {'trajectories': [{'task': 'example', 'trial_path': str(p)} for p, _ in results],
                               'all_tasks': {'errors': {'verifier': {'trajectories': 1}}}}}
    return six, seven


def test_report_gate_accepts_errors_and_reports_rates():
    six, seven = reports()
    assert check_reports(six, seven, ['example'], 2, 'hash')['passed']
    summary = summarize(six['items'])['all_tasks']
    assert summary['no_reward_rate'] == 0 and summary['verifier_error_rate'] == .5


@pytest.mark.parametrize('mutation', ['missing_trace', 'duplicate_trace', 'missing_task', 'wrong_contract',
                                     'incomplete', 'dry_run', 'missing_outcome'])
def test_report_gate_rejects_incomplete_evidence(mutation):
    six, seven = reports()
    if mutation == 'missing_trace': seven['trace_metrics']['trajectories'].pop()
    if mutation == 'duplicate_trace': seven['trace_metrics']['trajectories'][1] = seven['trace_metrics']['trajectories'][0]
    if mutation == 'missing_task': six['items'] = []
    if mutation == 'wrong_contract': seven['contract_sha256'] = 'wrong'
    if mutation == 'incomplete': six['complete'] = False
    if mutation == 'dry_run': six['dry_run'] = True
    if mutation == 'missing_outcome': six['items'][0]['attempt_outcomes'].pop()
    assert not check_reports(six, seven, ['example'], 2, 'hash')['passed']


def test_older_reports_receive_zero_with_original_errors_and_provenance():
    from validation.checks.passk_rewards import normalize
    six, seven = reports()
    item = six['items'][0]
    item['attempt_outcomes'][1]['reward'] = None
    item['attempt_outcomes'][1].pop('reward_origin')
    item['rewards'] = [1]
    rows = seven['trace_metrics']['trajectories']
    rows[0]['reward'] = 1
    rows[1]['reward'] = None
    original = deepcopy((six, seven))
    six, seven = normalize(six, seven)
    assert six['items'][0]['rewards'] == [1, 0]
    assert six['items'][0]['metrics']['no_reward'] == 0
    assert six['items'][0]['metrics']['mean_reward'] == .5
    assert seven['trace_metrics']['trajectories'][1]['reward'] == 0
    assert seven['trace_metrics']['all_tasks']['graded'] == 2
    assert six['items'][0]['recorded_errors'] == original[0]['items'][0]['recorded_errors']
    assert normalize(six, seven) == (six, seven)
    assert check_reports(six, seven, ['example'], 2, 'hash')['passed']


def test_unfinished_error_does_not_receive_zero():
    from validation.checks.reward_metrics import passk_reward
    result = trial(1, error='VerifierRuntimeError', finished=False)[1]
    assert passk_reward(result) is None
    assert assess([(Path('/trials/1'), result)], 1)['status'] == 'failed'
