"""Retain every independent attempt; incomplete sets do not receive pass@k."""
import json
from pathlib import Path


def summarize_attempts(jobs_dir: Path, task_ids: list[str], attempts: int) -> dict:
    grouped = {task: [] for task in task_ids}
    for path in sorted(jobs_dir.rglob('result.json')):
        # Retry artifacts mirror the canonical trial result; do not count twice.
        if path.parent.parent.name == 'attempts' and path.parent.name.isdigit():
            continue
        result = json.loads(path.read_text())
        task = result.get('task_name')
        if task not in grouped:
            continue
        reward = ((result.get('verifier_result') or {}).get('rewards') or {}).get('reward')
        exception = result.get('exception_info')
        exc_type = (exception or {}).get('exception_type') or ''
        # Agent/verifier timeouts are legitimate terminations of a valid attempt
        # (the task's own budget ran out), scored 0 unless the verifier produced
        # a reward; other exceptions are infrastructure errors.
        timeout = bool(exception) and ('AgentTimeout' in exc_type or 'VerifierTimeout' in exc_type)
        if timeout and reward is None:
            reward = 0
        grouped[task].append({
            'trial_dir': str(path.parent), 'reward': reward,
            'complete': (not exception or timeout) and reward is not None,
            'timeout': timeout,
            'exception': exception,
        })
    tasks = {}
    for task, trials in grouped.items():
        valid = [t for t in trials if t['complete']]
        complete = len(trials) == attempts and len(valid) == attempts
        successes = sum(t['reward'] == 1 for t in valid)
        tasks[task] = {'trials': trials, 'n_valid': len(valid),
                       'n_errors': len(trials) - len(valid),
                       'n_timeouts': sum(t['timeout'] for t in trials),
                       'n_missing': max(0, attempts - len(trials)),
                       'n_success': successes, 'complete': complete,
                       'pass_at_1': successes / attempts if complete else None,
                       'pass_at_k': int(successes > 0) if complete else None}

    def aggregate(names):
        rows = [tasks[t] for t in names]
        complete = bool(rows) and all(t['complete'] for t in rows)
        return {'tasks': len(rows), 'valid_trials': sum(t['n_valid'] for t in rows),
                'infrastructure_errors': sum(t['n_errors'] for t in rows),
                'timeouts': sum(t['n_timeouts'] for t in rows),
                'missing_trials': sum(t['n_missing'] for t in rows),
                'complete': complete,
                'pass_at_1': sum(t['n_success'] for t in rows) / (len(rows) * attempts) if complete else None,
                f'pass_at_{attempts}': sum(t['pass_at_k'] for t in rows) / len(rows) if complete else None}

    languages = sorted({t.split('-')[1] for t in task_ids if t.startswith('crosscodeeval-')})
    return {'attempts_per_task': attempts, 'tasks': tasks,
            'overall': aggregate(task_ids),
            'languages': {lang: aggregate([t for t in task_ids if t.startswith(f'crosscodeeval-{lang}-')]) for lang in languages}}
