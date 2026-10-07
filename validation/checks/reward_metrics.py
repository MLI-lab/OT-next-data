"""Reward statistics over repeated agent trials: measurements, not acceptance gates."""
from collections import Counter
import math
from pathlib import Path

from math import comb

GROUPS = ('all_solved', 'all_zero', 'constant_partial', 'varying')


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


def reward_group(rewards):
    """Classify observed rewards, leaving ungraded tasks unclassified."""
    if not rewards:
        return None
    if len(set(rewards)) > 1:
        return 'varying'
    return 'all_solved' if rewards[0] >= 1 else 'all_zero' if rewards[0] == 0 else 'constant_partial'


def task_metrics(results, attempts, key='reward'):
    """One task's attempts. An attempt without a reward counts as unsolved."""
    rewards = [r for r in (trial_reward(data, key) for _, data in results) if r is not None]
    n = max(attempts, len(results))
    solved = sum(r >= 1 for r in rewards)
    return {'attempts': n, 'rewards': rewards,
            'reward_counts': {str(value): count for value, count in sorted(Counter(rewards).items())},
            'mean_reward': sum(rewards) / len(rewards) if rewards else None,
            'solved': solved, 'partial': sum(0 < r < 1 for r in rewards),
            'zero': sum(r == 0 for r in rewards), 'no_reward': n - len(rewards),
            'exceptions': sum(bool(data.get('exception_info')) for _, data in results),
            'pass@1': solved / n, 'pass@k': pass_at_k(n, solved, n), 'k': n, 'variance_group': reward_group(rewards)}


def aggregate(rows):
    attempts = sum(m['attempts'] for m in rows)
    means = [m['mean_reward'] for m in rows if m['mean_reward'] is not None]
    return {'tasks': len(rows), 'attempts': attempts, 'k': sorted({m['k'] for m in rows}),
            'mean_reward': sum(means) / len(means) if means else None,
            'pass@1': sum(m['pass@1'] for m in rows) / len(rows),
            'pass@k': sum(m['pass@k'] for m in rows) / len(rows),
            'solved_rate': sum(m['solved'] for m in rows) / attempts,
            'partial_credit_rate': sum(m['partial'] for m in rows) / attempts,
            'zero_reward_rate': sum(m['zero'] for m in rows) / attempts,
            'no_reward_rate': sum(m['no_reward'] for m in rows) / attempts}


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
