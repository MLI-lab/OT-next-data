"""Apply explicit zero rewards to terminal-error reports from older collectors."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from validation.checks.reward_metrics import REWARD_POLICY, task_metrics, summarize, group_of


def normalize(stage6, stage7):
    six, seven = deepcopy(stage6), deepcopy(stage7)
    outcomes_by_path = {}
    for item in six.get('items', []):
        outcomes = item.get('attempt_outcomes', [])
        if not outcomes:
            continue  # No terminal evidence means no assigned reward.
        results = []
        for outcome in outcomes:
            outcome.setdefault('verifier_reward', outcome.get('reward'))
            if (outcome.get('terminal') and outcome.get('reward') is None
                    and any(e.get('exception_type') for e in outcome.get('exceptions', []))):
                outcome['reward'] = 0
                outcome['reward_origin'] = 'terminal_error'
            outcome.setdefault('reward_origin', 'verifier' if outcome.get('reward') is not None else 'unavailable')
            errors = outcome.get('exceptions', [])
            results.append((Path(outcome['trial']), {
                'verifier_result': {'rewards': {'reward': outcome.get('reward')}},
                'exception_info': errors[0] if errors else None,
                'secondary_exception_info': errors[1:]}))
            outcomes_by_path[outcome['trial']] = outcome
        item['rewards'] = [o['reward'] for o in outcomes if o.get('reward') is not None]
        item['metrics'] = task_metrics(results, item['expected_attempts'])
        item['reward_policy'] = REWARD_POLICY
    six['reward_metrics'] = summarize(six.get('items', []))
    six['reward_policy'] = REWARD_POLICY
    metrics = seven.get('trace_metrics') or {}
    rows = metrics.get('trajectories', [])
    for row in rows:
        outcome = outcomes_by_path.get(row.get('trial_path'))
        if outcome is not None:
            row.setdefault('verifier_reward', row.get('reward'))
            row['reward'] = outcome['reward']
            row['reward_policy'] = REWARD_POLICY
    def update(summary, selected):
        summary['graded'] = sum(r.get('reward') is not None for r in selected)
        summary['solved'] = sum(r.get('reward') is not None and r['reward'] >= 1 for r in selected)
        throughput = summary.get('throughput', {})
        hours = throughput.get('wall_clock_hours')
        if hours:
            throughput['trajectories_per_hour'] = summary['graded'] / hours
            throughput['solved_trajectories_per_hour'] = summary['solved'] / hours
    if 'all_tasks' in metrics:
        update(metrics['all_tasks'], rows)
    for task, summary in metrics.get('tasks', {}).items():
        update(summary, [r for r in rows if r['task'] == task])
    for family, summary in metrics.get('families', {}).items():
        update(summary, [r for r in rows if group_of(r['task']) == family])
    seven['reward_policy'] = REWARD_POLICY
    return six, seven


def normalize_files(stage6_path, stage7_path, destination):
    paths = [Path(stage6_path), Path(stage7_path)]
    source = [p.read_bytes() for p in paths]
    reports = normalize(*(json.loads(data) for data in source))
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    for path, data, report in zip(paths, source, reports):
        report['reward_assignment'] = {'policy': REWARD_POLICY, 'source_report': str(path),
                                       'source_sha256': hashlib.sha256(data).hexdigest()}
        target = destination / path.name
        temporary = target.with_suffix('.tmp')
        temporary.write_text(json.dumps(report, indent=2) + '\n')
        temporary.replace(target)
    return reports
