"""Pass@k acceptance checks collection completeness, not model correctness.

Recorded terminal errors receive reward zero, retaining the original verifier
output and exceptions. Missing/unfinished results stay incomplete.
"""
from collections import Counter
from datetime import datetime
from pathlib import Path

from validation.checks.reward_metrics import trial_reward, passk_reward, REWARD_POLICY


POLICY = 'terminal-attempts-v1'


def assess(results, expected_count, reward_key='reward'):
    findings, outcomes, errors, rewards = [], [], [], []
    identities = []
    for path, result in results:
        identity = str(result.get('id') or Path(path).resolve())
        identities.append(identity)
        verifier_reward = trial_reward(result, reward_key)
        reward = passk_reward(result, reward_key)
        exceptions = [e for e in [result.get('exception_info'),
                      *(result.get('secondary_exception_info') or [])] if e]
        finished = False
        try:
            datetime.fromisoformat(result['finished_at'].replace('Z', '+00:00'))
            finished = True
        except (KeyError, ValueError, TypeError, AttributeError):
            pass
        terminal = finished and (reward is not None or any(e.get('exception_type') for e in exceptions))
        if not terminal:
            findings.append(f'{Path(path).name}: missing terminal outcome')
        if reward is not None:
            rewards.append(reward)
        if exceptions:
            errors.append({'trial': str(path), 'exceptions': exceptions})
        outcomes.append({'trial': str(path), 'id': identity, 'terminal': terminal,
                         'verifier_reward': verifier_reward,
                         'reward_origin': 'verifier' if verifier_reward is not None else 'terminal_error' if reward == 0 else 'unavailable',
                         'reward': reward, 'solved': terminal and reward is not None and reward >= 1,
                         'exceptions': exceptions})
    if len(results) != expected_count:
        findings.append(f'expected {expected_count} trials, found {len(results)}')
    if len(set(identities)) != len(identities):
        findings.append('duplicate trial results')
    return {'status': 'failed' if findings else 'completed', 'findings': findings,
            'rewards': rewards, 'attempt_outcomes': outcomes, 'recorded_errors': errors,
            'completion_policy': POLICY, 'reward_policy': REWARD_POLICY, 'expected_attempts': expected_count,
            'recorded_attempts': len(results), 'terminal_attempts': sum(o['terminal'] for o in outcomes)}


def check_reports(stage6, stage7, task_ids, attempts, contract_sha256=None):
    """One common gate for pilots and full shards; errors are informational."""
    expected = set(task_ids)
    problems = []
    for number, report in [(6, stage6), (7, stage7)]:
        if report.get('stage') != number or not report.get('complete') or report.get('dry_run'):
            problems.append(f'stage {number}: missing completed non-dry-run report')
        names = [Path(i.get('task', '')).name for i in report.get('items', [])]
        if set(names) != expected or len(names) != len(expected):
            problems.append(f'stage {number}: task coverage mismatch')
        if contract_sha256 is not None and report.get('contract_sha256') != contract_sha256:
            problems.append(f'stage {number}: wrong contract')
    for item in stage6.get('items', []):
        outcomes = item.get('attempt_outcomes', [])
        ids = [o.get('id') for o in outcomes]
        if (item.get('status') != 'completed' or item.get('completion_policy') != POLICY
                or len(outcomes) != attempts or len(set(ids)) != attempts or not all(ids)
                or not all(o.get('terminal') for o in outcomes)
                or item.get('metrics', {}).get('attempts') != attempts):
            problems.append(f'{item.get("task")}: incomplete attempts')
    traces = stage7.get('trace_metrics', {}).get('trajectories', [])
    counts = Counter(row.get('task') for row in traces)
    if counts != Counter({name: attempts for name in expected}):
        problems.append('stage 7: attempt coverage mismatch')
    identities = [row.get('trial_path') or row.get('trial') for row in traces]
    if len(set(identities)) != len(identities) or not all(identities):
        problems.append('stage 7: duplicate or unidentified attempts')
    errors = stage7.get('trace_metrics', {}).get('all_tasks', {}).get('errors', {})
    return {'passed': not problems, 'problems': problems, 'policy': POLICY,
            'recorded_attempts': sum(len(i.get('attempt_outcomes', [])) for i in stage6.get('items', [])),
            'graded_attempts': sum(len(i.get('rewards', [])) for i in stage6.get('items', [])),
            'errors': errors}
