"""Stages 6 and 7: pass@k with resource calibration (validation.stages.calibration).

Stage 6 runs the target RL model on every task without a failed stage 0-3 check; stage 7 runs the strong
teacher on the tasks the target model left at 0/k, starting from the target model's calibrated allocation.
Each round gives a task a copy whose task.toml [environment] holds the round's allocation (files are hard
links, the environment directory is unchanged, so the cached image is reused) and runs k attempts. After a
round the calibration rules decide per task: final, rerun the batch with more of a resource, rerun single
attempts after an infrastructure failure, or inconclusive. The state is saved after every Harbor job, so a
rerun of the stage resumes without repeating finished batches.
"""
from datetime import datetime
import asyncio
import json
import os
from pathlib import Path
import shutil
import tomllib

from validation.stages import calibration as cal

STATE = 'calibration-state-stage{number}.json'


def upstream_resources(task):
    """The task's own [environment] cpus / memory / storage, in Harbor's units (MB)."""
    from harbor_config.models.task.config import EnvironmentConfig
    data = tomllib.loads((Path(task) / 'task.toml').read_text()).get('environment', {})
    env = EnvironmentConfig.model_validate(data)
    return {'cpus': env.cpus, 'memory_mb': env.memory_mb, 'storage_mb': env.storage_mb}


def with_allocation(task, destination, allocation):
    """A copy of `task` (hard links) whose task.toml requests `allocation`."""
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(task, destination, copy_function=os.link, symlinks=True)
    toml = destination / 'task.toml'
    text = toml.read_text()
    toml.unlink()  # break the hard link before rewriting
    toml.write_text(set_environment(text, allocation))
    return destination


def set_environment(text, allocation):
    """Replace cpus / memory / storage in the [environment] table, keeping everything else verbatim."""
    values = {'cpus': str(int(allocation['cpus'])), 'memory_mb': str(int(allocation['memory_mb'])),
              'storage_mb': str(int(allocation['storage_mb']))}
    lines, section, seen, out = text.splitlines(), None, set(), []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith('['):
            if section == 'environment':
                out += [f'{k} = {v}' for k, v in values.items() if k not in seen]
            section = stripped.strip('[]').strip()
        key = stripped.split('=', 1)[0].strip() if '=' in stripped else None
        if section == 'environment' and key in ('cpus', 'memory', 'memory_mb', 'storage', 'storage_mb'):
            name = {'memory': 'memory_mb', 'storage': 'storage_mb'}.get(key, key)
            if name not in seen:
                out.append(f'{name} = {values[name]}')
                seen.add(name)
            continue
        out.append(line)
    if section == 'environment':
        out += [f'{k} = {v}' for k, v in values.items() if k not in seen]
    elif 'environment' not in {l.strip().strip('[]').strip() for l in lines if l.strip().startswith('[')}:
        out += ['', '[environment]'] + [f'{k} = {v}' for k, v in values.items()]
    result = '\n'.join(out) + '\n'
    tomllib.loads(result)
    return result


def _seconds(phase):
    try:
        start = datetime.fromisoformat(phase['started_at'].replace('Z', '+00:00'))
        end = datetime.fromisoformat(phase['finished_at'].replace('Z', '+00:00'))
        return round((end - start).total_seconds(), 1)
    except (KeyError, TypeError, ValueError, AttributeError):
        return None


def tool_time(trial):
    """MiMo's tool time from mini-swe-agent message timestamps: tool message - its assistant message."""
    path = Path(trial) / 'agent' / 'mini.trajectory.json'
    try:
        messages = json.loads(path.read_text())['messages']
    except (OSError, ValueError, KeyError):
        return None, 0
    total, count, last = 0.0, 0, None
    for message in messages:
        stamp = (message.get('extra') or {}).get('timestamp')
        if stamp is None:
            continue
        if message.get('role') == 'assistant':
            last = stamp
        elif message.get('role') == 'tool' and last is not None:
            total += stamp - last
            count += 1
    return (round(total / count, 2) if count else None), count


def trial_result(trial):
    """The result.json of a trial's evidence directory (the trial directory or one of its parents)."""
    path = Path(trial)
    for directory in (path, *path.parents[:3]):
        if (directory / 'result.json').is_file():
            try:
                return json.loads((directory / 'result.json').read_text())
            except ValueError:
                return None
    return None


def attempt_record(trial, result, allocation, reward_key='reward'):
    from validation.metrics.passk_completion import passk_reward
    from validation.outcomes import category
    exception = result.get('exception_info') or {}
    message = exception.get('exception_message') or ''
    infra = category({'status': 'error', 'error': message, 'exception_type': exception.get('exception_type')})
    tool_mean, tool_calls = tool_time(trial)
    return {'trial': str(trial), 'reward': passk_reward(result, reward_key),
            'exception_type': exception.get('exception_type'), 'exception_message': message[:2000],
            'category': (infra or {}).get('category'),
            'phases': {name: _seconds(result.get(name)) for name in
                       ('environment_setup', 'agent_setup', 'agent_execution', 'verifier')},
            'tool_mean_sec': tool_mean, 'tool_calls': tool_calls,
            'resources': result.get('resources') or {}, 'allocation': dict(allocation)}


class Calibration:
    """Per-task calibration state of one stage, saved after every Harbor job."""

    def __init__(self, path, policy, k):
        self.path, self.policy, self.k = Path(path), policy, k
        self.tasks = json.loads(self.path.read_text())['tasks'] if self.path.is_file() else {}

    def save(self):
        temporary = self.path.with_name(self.path.name + '.tmp')
        temporary.write_text(json.dumps({'k': self.k, 'policy': cal.policy_record(self.policy), 'tasks': self.tasks},
                                        indent=1, default=str) + '\n')
        temporary.replace(self.path)

    def start(self, name, upstream, allocation, capped=False):
        state = self.tasks.get(name)
        if state is None or state.get('k') != self.k:
            state = {'k': self.k, 'upstream': upstream, 'allocation': allocation, 'status': 'pending',
                     'rounds': [], 'batch': [], 'retries': [], 'infra_retries': 0, 'capped': capped}
            self.tasks[name] = state
        return state

    def need(self, state):
        """How many attempts the task's next job runs (0 when finished)."""
        if state['status'] != 'pending':
            return 0
        if state['retries']:
            return len(state['retries'])
        return self.k

    def record(self, state, attempts):
        """Add a job's attempts and decide the next step."""
        for attempt in attempts:
            attempt['calibration'] = cal.classify(attempt, state['allocation'], self.policy)
        if state['retries']:
            for index, attempt in zip(state['retries'], attempts):
                state['history'] = state.get('history', []) + [dict(state['batch'][index], superseded=True)]
                state['batch'][index] = attempt
            state['infra_retries'] += 1
        else:
            state['batch'] = attempts
        round_index = len(state['rounds'])
        decision = cal.decide(state['batch'], state['allocation'], self.policy, round_index=round_index,
                              infra_retries_used=state['infra_retries'], capped=state.get('capped', False))
        state['retries'] = decision.retry if decision.action == 'retry_attempts' else []
        if decision.action == 'retry_attempts':
            return decision
        state['rounds'].append({'allocation': dict(state['allocation']), 'attempts': state['batch'],
                                'decision': decision.action, 'reason': decision.reason, 'raised': decision.raised})
        if decision.action == 'rerun_batch':
            state['allocation'], state['batch'], state['infra_retries'] = decision.allocation, [], 0
        else:
            state['status'] = decision.action  # final or inconclusive
            state['reason'] = decision.reason
        return decision

    def summary(self, state):
        rounds = state['rounds']
        final = rounds[-1]['attempts'] if rounds else []
        measured = [a for r in rounds for a in r['attempts']] + state.get('history', [])
        passk = cal.pass_at_k(final, self.k) if state['status'] == 'final' else None
        outcome = ('inconclusive' if state['status'] != 'final' else
                   'solved' if passk['solved'] else 'unsolved')
        return {'outcome': outcome, 'reason': state.get('reason'), 'passk': passk,
                'final_allocation': state['allocation'], 'upstream': state['upstream'], 'capped': state.get('capped', False),
                'requirements': cal.requirements(measured, state['allocation'], state['upstream'], self.policy),
                'rounds': [{k: v for k, v in r.items() if k != 'attempts'} | {'attempts': len(r['attempts'])}
                           for r in rounds],
                'final_attempts': [{k: a.get(k) for k in ('trial', 'reward', 'exception_type', 'phases', 'tool_mean_sec',
                                                          'resources', 'allocation', 'calibration')} for a in final],
                'earlier_attempts': sum(len(r['attempts']) for r in rounds[:-1]) + len(state.get('history', []))}


def run(number, selected, out, args, upstream, report, contract, *, start_allocations=None, execute=None):
    """Calibrated pass@k for the selected (task, item) pairs; fills each item and returns the items."""
    from validation.stages import harbor as runtime
    from validation.stages.runner import save
    policy = cal.policy_from_args(args)
    k = int(getattr(args, 'pass_k', None) or args.attempts)
    state_root = Path(getattr(args, 'calibration_state', None) or args.out)
    state_root.mkdir(parents=True, exist_ok=True)
    book = Calibration(state_root / STATE.format(number=number), policy, k)
    model = args.model
    agent = args.agent
    tasks = {}
    for task, item in selected:
        resources = upstream_resources(task)
        # A start allocation from the target model: {'cpus', 'memory_mb', 'storage_mb'[, 'capped': True]}.
        given = dict((start_allocations or {}).get(task.name) or {})
        capped = bool(given.pop('capped', False))
        start = {key: given[key] for key in ('cpus', 'memory_mb', 'storage_mb') if key in given} or cal.initial_allocation(resources, policy)
        tasks[task.name] = (task, item, book.start(task.name, resources, start, capped))
    book.save()
    execute = execute or (lambda config: asyncio.run(runtime.execute_job(config)))
    job_index = 0
    while True:
        groups = {}
        for name, (task, item, state) in tasks.items():
            need = book.need(state)
            if need:
                key = (json.dumps(state['allocation'], sort_keys=True), need)
                groups.setdefault(key, []).append(name)
        if not groups:
            break
        for (allocation_key, need), names in sorted(groups.items()):
            allocation = json.loads(allocation_key)
            job_index += 1
            job_root = out / f'calibration-job-{job_index:03d}'
            inputs = job_root / 'inputs'
            copies = [with_allocation(tasks[n][0], inputs / n, allocation) for n in names]
            config = runtime.job_config(copies[0], job_root / 'jobs', args, agent, model)
            overrides = [key for key in ('override_cpus', 'override_memory_mb', 'override_storage_mb')
                         if config['environment'].get(key)]
            if overrides:
                # MiMo's warning: run-wide overrides replace every task's calibrated allocation.
                raise ValueError('calibrated pass@k sets each task\'s resources; do not combine it with '
                                 '--trial-cpus / --trial-memory-mb (' + ', '.join(overrides) + ')')
            config['tasks'] = [{'path': str(c.resolve())} for c in copies]
            config['n_attempts'] = need
            save(job_root / 'job.json', config)
            job_dir = execute(config)
            results = runtime.trial_results(job_dir)
            grouped, unmatched = runtime.group_trial_results(results, copies)
            if unmatched:
                report.setdefault('unmatched_trials', []).extend(unmatched)
            for name in names:
                trials = sorted(grouped.get(name, []), key=lambda pair: str(pair[0]))
                attempts = [attempt_record(trial, result, allocation, args.reward_key) for trial, result in trials]
                # Missing attempts (the trial never wrote a result) are infrastructure failures.
                attempts += [{'trial': None, 'reward': None, 'exception_type': 'MissingTrialResult',
                              'exception_message': 'no result.json', 'category': 'infrastructure_error',
                              'phases': {}, 'resources': {}, 'allocation': dict(allocation)}] * (need - len(attempts))
                book.record(tasks[name][2], attempts[:need])
                tasks[name][1].setdefault('job_dirs', []).append(str(job_dir))
            book.save()
    for name, (task, item, state) in tasks.items():
        summary = book.summary(state)
        final = [(Path(a['trial']), result) for a in summary['final_attempts']
                 if a.get('trial') and (result := trial_result(a['trial'])) is not None]
        if summary['outcome'] != 'inconclusive':
            # The same per-task records as before calibration, from the final batch only.
            from validation.metrics.passk_completion import assess
            from validation.metrics.reward_metrics import task_metrics
            item.update(runtime.assess_trials(final, k, None, args.reward_key, task_path=task))
            item.update(assess(final, k, args.reward_key))
            item['metrics'] = task_metrics(final, k, args.reward_key)
        item['calibration'] = summary
        passk = summary['passk'] or {}
        item['rewards'] = [a['reward'] for a in summary['final_attempts']] if summary['outcome'] != 'inconclusive' else []
        item['status'] = 'completed' if summary['outcome'] != 'inconclusive' else 'inconclusive'
        item['passk'] = {'model': model, 'k': k, 'outcome': summary['outcome'], **passk}
        if item.get('job_dirs'):
            item['job_dir'] = item['job_dirs'][-1]
    report['calibration_policy'] = cal.policy_record(policy)
    report['calibration_state'] = str(book.path)
    return [item for _, item in selected]


def resolve_models(args, numbers):
    """Stage 6 serves --target-model, stage 7 --teacher-model, unless --model/--api-base/--serve-model is given.
    A job serves one model, so with serving the two stages are separate submissions."""
    stages = [n for n in numbers if n in (6, 7)]
    if not stages or getattr(args, 'contract', None):
        return
    explicit = getattr(args, 'model', None) or getattr(args, 'api_base', None) or getattr(args, 'serve_model', None)
    if len(stages) == 2 and not explicit:
        raise ValueError('stages 6 and 7 serve different models (--target-model, --teacher-model): '
                         'run them as separate submissions, stage 7 after stage 6')
    if not explicit:
        from config.models import model_key, MODELS
        name = args.target_model if stages[0] == 6 else args.teacher_model
        key = model_key(name)
        if key not in MODELS:
            raise ValueError(f'{name} is not registered in config/models.py; add it or pass --model/--api-base')
        args.serve_model = key
