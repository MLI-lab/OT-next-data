"""Reward statistics over repeated agent trials: measurements, not acceptance gates."""
from collections import Counter
import math
from pathlib import Path

from math import comb

GROUPS = ('all_solved', 'all_zero', 'constant_partial', 'varying')
REWARD_POLICY = 'terminal-errors-zero-v2'


def pass_at_k(n, c, k):
    """Unbiased pass@k estimate; unavailable when fewer than k attempts exist."""
    if n < k:
        return None
    return 1.0 if n - c < k else 1.0 - comb(n - c, k) / comb(n, k)


def group_of(task):
    parts = task.split('-')
    return parts[1] if len(parts) >= 3 else 'all'


def trial_reward(result, key='reward'):
    value = ((result.get('verifier_result') or {}).get('rewards') or {}).get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return value


def passk_reward(result, key='reward'):
    """Assign zero to recorded terminal errors, without changing raw evidence."""
    reward = trial_reward(result, key)
    if reward is not None:
        return reward
    from datetime import datetime
    try:
        datetime.fromisoformat(result['finished_at'].replace('Z', '+00:00'))
    except (KeyError, ValueError, TypeError, AttributeError):
        return None
    errors = [result.get('exception_info') or {}, *(result.get('secondary_exception_info') or [])]
    return 0 if any(e.get('exception_type') for e in errors) else None


def reward_group(rewards):
    """Classify observed rewards, leaving ungraded tasks unclassified."""
    if not rewards:
        return None
    if len(set(rewards)) > 1:
        return 'varying'
    return 'all_solved' if rewards[0] >= 1 else 'all_zero' if rewards[0] == 0 else 'constant_partial'


def task_metrics(results, attempts, key='reward'):
    """One task's attempts. An attempt without a reward counts as unsolved."""
    from validation.checks.trace_metrics import ERRORS
    verifier_errors = sum(any(e.get('exception_type') in ERRORS['verifier'] for e in
        [data.get('exception_info') or {}, *(data.get('secondary_exception_info') or [])]) for _, data in results)
    rewards = [r for r in (passk_reward(data, key) for _, data in results) if r is not None]
    n = len(results)
    solved = sum(r >= 1 for r in rewards)
    return {'reward_policy': REWARD_POLICY, 'attempts': n, 'expected_attempts': attempts, 'missing_attempts': max(0, attempts - n), 'rewards': rewards,
            'reward_counts': {str(value): count for value, count in sorted(Counter(rewards).items())},
            'mean_reward': sum(rewards) / len(rewards) if rewards else None,
            'solved': solved, 'partial': sum(0 < r < 1 for r in rewards),
            'zero': sum(r == 0 for r in rewards), 'no_reward': n - len(rewards),
            'exceptions': sum(bool(data.get('exception_info')) for _, data in results),
            'verifier_errors': verifier_errors,
            'pass@1': solved / n if n else 0, 'pass@k': pass_at_k(n, solved, n) if n else 0,
            'k': n, 'variance_group': reward_group(rewards)}


def aggregate(rows):
    attempts = sum(m['attempts'] for m in rows)
    means = [m['mean_reward'] for m in rows if m['mean_reward'] is not None]
    return {'tasks': len(rows), 'attempts': attempts, 'k': sorted({m['k'] for m in rows}),
            'mean_reward': sum(means) / len(means) if means else None,
            'pass@1': sum(m['pass@1'] for m in rows) / len(rows),
            'pass@k': sum(m['pass@k'] for m in rows) / len(rows),
            'solved_rate': sum(m['solved'] for m in rows) / attempts if attempts else 0,
            'partial_credit_rate': sum(m['partial'] for m in rows) / attempts if attempts else 0,
            'zero_reward_rate': sum(m['zero'] for m in rows) / attempts if attempts else 0,
            'no_reward_rate': sum(m['no_reward'] for m in rows) / attempts if attempts else 0,
            'verifier_error_rate': sum(m.get('verifier_errors', 0) for m in rows) / attempts if attempts else 0}


def summarize(items):
    """All tasks, each family (middle part of the task ID) and variance groups."""
    measured = {Path(i['task']).name: i['metrics'] for i in items if i.get('metrics')}
    if not measured:
        return None
    families, groups = {}, {key: [] for key in GROUPS}
    for task, metrics in sorted(measured.items()):
        families.setdefault(group_of(task), []).append(metrics)
        if metrics['variance_group']:
            groups[metrics['variance_group']].append(task)
    return {'all_tasks': aggregate(list(measured.values())),
            'families': {name: aggregate(rows) for name, rows in sorted(families.items())},
            'variance_groups': groups, 'variance_group_counts': {key: len(tasks) for key, tasks in groups.items()},
            'tasks_without_rewards': sorted(task for task, m in measured.items() if not m['variance_group']),
            'tasks_without_trials': sorted(Path(i['task']).name for i in items if not i.get('metrics'))}
