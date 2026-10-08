"""Local Harbor runtime adapter, using the same Apptainer bridge as teacher runs."""
from __future__ import annotations

import asyncio
import json
import math
import os
import re
from pathlib import Path
import tomllib
import time
from contextvars import ContextVar
from uuid import uuid4

_save_dependencies = ContextVar('validation_save_dependencies', default=False)
_review_preparation = ContextVar('validation_review_preparation', default=False)
BRIDGE_EXEC_LIMIT = 7 * 86400     # a command's own limit on the bridge: in practice none


# A lost session alone does not establish an infrastructure failure: agent exec,
# exit, kill-server and malformed heredocs can all close it. Preserve the attempt
# for diagnosis instead of granting a new sample based only on its exception type.
INFRASTRUCTURE_RETRY = {'max_retries': 0}


def install_runtime_patches():
    from harbor_patches.fresh_verifier import install as install_fresh_verifier
    install_fresh_verifier()
    from harbor_patches.verifier_setup import install as install_verifier_setup
    install_verifier_setup()
    from validation.stages.task_setup import install as install_task_setup
    install_task_setup()
    from harbor_patches.bridge_client import install as install_transport
    install_transport()
    from harbor_patches.reasoning_field import install as install_reasoning_field
    install_reasoning_field()
    from harbor_patches.tmux_capture import install as install_tmux_capture
    install_tmux_capture()
    from harbor_patches.tmux_diagnostics import install as install_tmux_diagnostics
    install_tmux_diagnostics()
    from harbor_patches.oom_recovery import install as install_oom_recovery
    install_oom_recovery()
    from validation.stages.container_reuse import install
    install()
    # The pinned bridge keys images only by Dockerfile text. Include COPY
    # payloads as well, or distinct review tasks can reuse stale baked evidence.
    from harbor.environments.apptainer import apptainer as bridge
    from harbor.utils.container_cache import environment_dir_hash_truncated
    bridge.dockerfile_hash_truncated = lambda path: environment_dir_hash_truncated(path.parent)
    if not getattr(bridge, '_bridge_exec_without_cap', False):
        # The bridge client runs a command without its own limit, such as the verifier's test.sh,
        # for at most 600 s. Harbor's own agent and verifier limits are the ones that count here.
        exec_with_cap = bridge.ApptainerEnvironment.exec

        async def exec_without_cap(self, command, *args, timeout_sec=None, **kwargs):
            return await exec_with_cap(self, command, *args, timeout_sec=timeout_sec or BRIDGE_EXEC_LIMIT, **kwargs)
        bridge.ApptainerEnvironment.exec = exec_without_cap
        bridge._bridge_exec_without_cap = True
    if not getattr(bridge, '_validation_dependency_request', False):
        post = bridge._async_http_post

        async def post_marking_oracle(url, data, timeout=60):
            # The bridge server passes task_env_config to the worker unchanged. With dependency
            # archives configured, the worker saves the archive of a marked trial that passes.
            if url.endswith('/env/create') and _review_preparation.get():
                data = dict(data, task_env_config=dict(data.get('task_env_config') or {}, disable_dependency_archive=True))
            if url.endswith('/env/create') and _save_dependencies.get():
                data = dict(data, task_env_config=dict(data.get('task_env_config') or {}, save_dependencies=True))
            return await post(url, data, timeout)
        bridge._async_http_post = post_marking_oracle
        bridge._validation_dependency_request = True


def environment_config(args):
    kwargs = dict(args.environment_kwargs)
    if args.backend == 'apptainer':
        kwargs.setdefault('bridge_url', os.environ.get('APPTAINER_BRIDGE_URL', ''))
        kwargs.setdefault('sif_cache', os.environ.get('HARBOR_SIF_CACHE', ''))
        if not args.dry_run and not kwargs['bridge_url']:
            raise RuntimeError('Apptainer needs APPTAINER_BRIDGE_URL and a running bridge worker inside your allocation')
    config = {'type': args.backend, 'kwargs': kwargs, 'force_build': args.force_build, 'delete': True}
    if args.trial_cpus:
        config['override_cpus'] = args.trial_cpus
    if args.trial_memory_mb:
        config['override_memory_mb'] = args.trial_memory_mb
    return config


def check_runtime_task(task, backend):
    if backend != 'apptainer':
        return
    data = tomllib.loads((task / 'task.toml').read_text())
    if any((task / 'environment').glob('*compose*.y*ml')):
        raise ValueError('Apptainer bridge does not implement Compose sidecars; use a supported backend')
    environments = [data.get('environment', {}), data.get('verifier', {}).get('environment', {})]
    if any(env.get('gpus', 0) or env.get('tpu') for env in environments):
        raise ValueError('This pinned Apptainer bridge advertises CPU-only support')


def job_config(task, output, args, agent, model=None, artifacts=None):
    kwargs = dict(args.agent_kwargs) if agent == args.agent else {}
    agent_spec = {'name': agent, **({'model_name': model} if model else {}), 'kwargs': kwargs}
    # API routing is relevant to host-driven Terminus, not the Claude reviewer.
    if args.api_base and agent == 'terminus-2':
        kwargs['api_base'] = args.api_base
    config = {
        'job_name': 'job', 'jobs_dir': str(output.resolve()),
        'tasks': [{'path': str(task.resolve())}],
        'agents': [agent_spec],
        'environment': environment_config(args), 'verifier': {'disable': False},
        'n_attempts': args.attempts, 'n_concurrent_trials': args.concurrency,
        'retry': INFRASTRUCTURE_RETRY,
    }
    if artifacts:
        config['artifacts'] = artifacts
    return config


async def execute_job(config, *, path_search_roots=(), pip_pin_capture=None):
    install_runtime_patches()
    from validation.stages.path_diagnostics import install as install_path_diagnostics, observations
    install_path_diagnostics()
    from harbor.job import Job
    from harbor.models.job.config import JobConfig
    job = await Job.create(JobConfig.model_validate(config))
    token = _save_dependencies.set(all(agent.get('name') == 'oracle' for agent in config['agents']))
    diagnostic_agents = {agent.get('name') for agent in config['agents']}
    diagnostic_token = observations.set(
        {'roots': list(path_search_roots)}
        if path_search_roots and diagnostic_agents == {'oracle'} else None)
    from data.utils.resolve_pip_pins import install_capture, _capture
    if pip_pin_capture and diagnostic_agents == {'oracle'}:
        install_capture()
    pip_token = _capture.set(pip_pin_capture if diagnostic_agents == {'oracle'} else None)
    try:
        await job.run()
    finally:
        _save_dependencies.reset(token)
        observations.reset(diagnostic_token)
        _capture.reset(pip_token)
    return Path(str(job.job_dir))


def trial_results(job_dir):
    # Pinned Harbor stores one result directly beneath each trial directory.
    # Avoid recursively counting mirrored attempts or the job's aggregate result.
    results = []
    for path in sorted(job_dir.glob('*/result.json')):
        data = json.loads(path.read_text())
        # The top-level result mirrors the final attempt, but artifacts and
        # agent trajectories stay in attempts/NNN in this Harbor revision.
        relative = data.get('trial_relpath')
        evidence = job_dir / relative if relative else path.parent
        if not evidence.resolve().is_relative_to(path.parent.resolve()) or not evidence.is_dir():
            evidence = path.parent
        results.append((evidence, data))
    return results


def group_trial_results(results, tasks):
    """Match trials to selected tasks, retaining unmatched evidence as a finding."""
    groups = {task.name: [] for task in tasks}
    unmatched = []
    for trial, result in results:
        name = str(result.get('task_name') or '').split('/')[-1]
        path = ((result.get('config') or {}).get('task') or {}).get('path')
        if name not in groups and path:
            name = Path(path).name
        if name in groups:
            groups[name].append((trial, result))
        else:
            unmatched.append(str(trial))
    return groups, unmatched


def declared_output_paths(task_path):
    """Conservatively recognize paths named in output section headings."""
    if task_path is None:
        return set()
    instruction = Path(task_path) / 'instruction.md'
    if not instruction.is_file():
        return set()
    outputs, output_level = set(), None
    for line in instruction.read_text().splitlines():
        heading = re.match(r'^(#{1,6})\s+(.+)', line)
        if not heading:
            continue
        level, title = len(heading[1]), heading[2]
        if output_level is not None and level <= output_level:
            output_level = None
        if re.search(r'\b(?:outputs?|deliverables?)\b', title, re.I):
            output_level = level
        if output_level is not None:
            outputs.update(re.findall(r'`(/[^`\s]+)`', title))
    return outputs


def expected_missing_output_setup(output, task_path):
    """Accept fixture assertions only for explicitly declared output paths.

    Every setup error must be the same kind of existence assertion. Import,
    parsing and dependency failures remain execution failures.
    """
    allowed = declared_output_paths(task_path)
    if not allowed:
        return False
    blocks = re.split(r'(?m)^_+ ERROR at setup of [^\n]+ _+\s*$', output)[1:]
    if not blocks or not re.search(r'collected [1-9]\d* items?', output):
        return False
    error_counts = re.findall(r'(?m)^(?:=+ )?(\d+) errors? in [\d.]+s(?: .*?)?(?: =+)?\s*$', output)
    if sum(map(int, error_counts)) != len(blocks):
        return False
    for block in blocks:
        exceptions = re.findall(r'^E\s+([A-Za-z]\w*(?:Error|Exception)):\s*(.*)$', block, re.M)
        if len(exceptions) != 1 or exceptions[0][0] != 'AssertionError':
            return False
        match = re.fullmatch(r'(/\S+) must exist', exceptions[0][1].strip())
        if not match or match[1] not in allowed:
            return False
        if not re.search(r'^>\s+assert [^\n]+\.(?:is_file|exists)\(\)', block, re.M):
            return False
    return True


def verifier_output(path, verifier):
    outputs = [verifier.get('stdout') or '', verifier.get('stderr') or '']
    for name in ('test-stdout.txt', 'test-stderr.txt'):
        log = path / 'verifier' / name
        if log.is_file():
            outputs.append(log.read_text(errors='replace'))
    return re.sub(r'\x1b\[[0-9;]*m', '', '\n'.join(outputs))


def nop_missing_instruction_files(path, result, task_path, reward_key):
    """Recognize missing files mentioned in instructions, using verifier logs only."""
    if task_path is None or not (Path(task_path) / 'instruction.md').is_file():
        return []
    exception = result.get('exception_info') or {}
    if exception.get('exception_type') not in (None, 'VerifierRuntimeError', 'RewardFileNotFoundError'):
        return []
    verifier = result.get('verifier_result') or {}
    score = (verifier.get('rewards') or {}).get(reward_key)
    if score is not None and (isinstance(score, bool) or score != 0):
        return []
    problem = nop_execution_problem(path, verifier, task_path)
    if problem and problem.startswith('verifier did not run:'):
        return []
    output = verifier_output(path, verifier)
    errors = re.findall(r'^(?:E\s+)?([\w.]+(?:Error|Exception)):\s*(.*)$', output, re.M)
    if not errors or any(kind != 'FileNotFoundError' for kind, _ in errors):
        return []
    instruction = (Path(task_path) / 'instruction.md').read_text()
    paths = set()
    for _, message in errors:
        match = re.fullmatch(r"\[Errno 2\] No such file or directory: (['\"])(.+)\1", message.strip())
        if not match:
            return []
        missing = match[2]
        if not re.search(r'(?<![\w./-])' + re.escape(missing) + r'(?![\w./-])', instruction):
            return []
        paths.add(missing)
    return sorted(paths)


def nop_execution_problem(path, verifier, task_path=None):
    """Recognize runner failures that wrappers sometimes turn into reward zero.

    This is deliberately conservative: arbitrary verifiers need not use pytest,
    and assertion failures (including missing task outputs) are valid NOP results.
    """
    output = verifier_output(path, verifier)
    if re.search(r'^\S*python[\w.]*: No module named [\'"]?pytest\b', output, re.M):
        return 'verifier did not run: pytest is not installed'
    if re.search(r'^(?:[^\n]*: )?(?:\S*/)?pytest: (?:command )?not found\s*$', output, re.M):
        return 'verifier did not run: pytest command not found'
    if re.search(r'^=+.*\b\d+ errors? during collection\b.*=+\s*$', output, re.M):
        return 'verifier could not collect tests'
    # Match pytest summaries, not traceback source lines or application messages.
    summaries = re.findall(r'^(?:=+ )?((?:\d+ (?:passed|failed|skipped|deselected|xfailed|xpassed|errors?)'
                           r'(?:, )?)+|no tests ran) in [\d.]+s(?: .*?)?(?: =+)?\s*$', output, re.M)
    for summary in summaries:
        if not re.search(r'\b[1-9]\d* (?:passed|failed|xfailed|xpassed)\b', summary):
            if re.fullmatch(r'\d+ errors?', summary) and expected_missing_output_setup(output, task_path):
                continue
            return 'verifier executed no tests (empty, skipped, or setup errors)'
    return None


def trial_seconds(result, phase=None):
    """Harbor's own start-to-finish time of one trial, or of one of its phases (agent_execution,
    verifier), or None when it recorded none."""
    from datetime import datetime
    try:
        record = result[phase] if phase else result
        return round((datetime.fromisoformat(record['finished_at'])
                      - datetime.fromisoformat(record['started_at'])).total_seconds(), 1)
    except (KeyError, TypeError, ValueError):
        return None


def assess_trials(results, expected_count, expected_reward=None, reward_key='reward', *, task_path=None):
    findings = []
    missing_file_zeros = []
    if len(results) != expected_count:
        findings.append(f'expected {expected_count} trials, found {len(results)}')
    scores = []
    for path, result in results:
        missing = (nop_missing_instruction_files(path, result, task_path, reward_key)
                   if expected_reward == 0 else [])
        if missing:
            scores.append(0)
            missing_file_zeros.append({'trial': str(path), 'paths': missing,
                                       'reason': 'verifier FileNotFoundError for a path mentioned in instruction.md'})
            continue
        if result.get('exception_info'):
            findings.append(f'{path.name}: exception: {result["exception_info"]}')
            # Harbor still verifies the final files after an agent time limit.
            # Retain that measured score for teacher statistics, with the error
            # visible. Oracle/NOP acceptance still requires an error-free trial.
            if expected_reward is not None or result['exception_info'].get('exception_type') != 'AgentTimeoutError':
                continue
        verifier = result.get('verifier_result') or {}
        if expected_reward in (0, 1):
            problem = nop_execution_problem(path, verifier, task_path)
            if problem:
                findings.append(f'{path.name}: {problem}')
        rewards = verifier.get('rewards') or {}
        score = rewards.get(reward_key)
        if not isinstance(score, (int, float)) or isinstance(score, bool) or not math.isfinite(score):
            findings.append(f'{path.name}: missing numeric reward {reward_key!r}')
            continue
        scores.append(score)
        if expected_reward is not None and score != expected_reward:
            findings.append(f'{path.name}: expected reward {expected_reward}, got {score}')
    return {'status': 'failed' if findings else 'completed', 'findings': findings, 'rewards': scores,
            **({'missing_file_zeros': missing_file_zeros} if missing_file_zeros else {}),
            'trial_seconds': [trial_seconds(result) for _, result in results],
            # How long the solution and the verifier themselves ran: the slow tasks can be found later.
            'agent_seconds': [trial_seconds(result, 'agent_execution') for _, result in results],
            'verifier_seconds': [trial_seconds(result, 'verifier') for _, result in results]}


async def build_task(task_path, out, args):
    install_runtime_patches()
    if getattr(args, 'review_setup', None) is not None:
        from validation.stages.verifier_setup import review_task
        token = _review_preparation.set(True)
        try:
            return await review_task(task_path, out, args)
        finally:
            _review_preparation.reset(token)
    from harbor.environments.factory import EnvironmentFactory
    from harbor.models.environment_type import EnvironmentType
    from harbor.models.task.task import Task
    from harbor.models.task.verifier_mode import resolve_effective_verifier_env_config
    from harbor.models.trial.paths import TrialPaths
    task = Task(task_path)
    config = environment_config(args)
    specs = [('agent', task.paths.environment_dir, task.config.environment)]
    if task.config.steps:
        for step in task.config.steps:
            env = resolve_effective_verifier_env_config(task.config, step)
            if env is not None:
                from harbor_patches.fresh_verifier import build_context
                specs.append((f'verifier-{step.name}', build_context(task, step), env))
    else:
        env = resolve_effective_verifier_env_config(task.config, None)
        if env is not None:
            from harbor_patches.fresh_verifier import build_context
            specs.append(('verifier', build_context(task), env))
    results = []
    for label, context, spec in specs:
        overrides = {}
        if args.trial_cpus:
            overrides['cpus'] = args.trial_cpus
        if args.trial_memory_mb:
            overrides['memory_mb'] = args.trial_memory_mb
        spec = spec.model_copy(update=overrides)
        paths = TrialPaths(out / label)
        paths.mkdir()
        environment = None
        entry = {'environment': label, 'context': str(context), 'timings_seconds': {}}
        phase = 'start'
        started = time.monotonic()
        try:
            environment = EnvironmentFactory.create_environment(
                type=EnvironmentType(args.backend), environment_dir=context,
                environment_name=f'{task.short_name}-{label}', session_id=f'validate-{uuid4().hex[:12]}',
                trial_paths=paths, task_env_config=spec, **config['kwargs'])
            from validation.stages.task_setup import detect, prepare, upload_setup
            command = detect(task_path) if label == 'agent' else None
            await asyncio.wait_for(
                prepare(environment, command=command, timeout_sec=spec.build_timeout_sec,
                        force_build=args.force_build,
                        timings=entry['timings_seconds'],
                        upload=lambda: upload_setup(environment, task_path)),
                timeout=spec.build_timeout_sec)
            if command:
                entry['setup_command'] = command
                entry['preparation_budget_seconds'] = spec.build_timeout_sec
            entry['timings_seconds']['start'] = time.monotonic() - started
            phase, started = 'inspect', time.monotonic()
            if command and getattr(args, 'preparation_runs', 1) > 1:
                timing = await environment.exec(
                    'if [ -f /setup_files/setup-timing.json ]; then cat /setup_files/setup-timing.json; fi',
                    user='root')
                if timing.return_code == 0 and timing.stdout.strip():
                    try:
                        entry['setup_timing'] = json.loads(timing.stdout)
                        (out / label / 'setup-timing.json').write_text(timing.stdout)
                    except (ValueError, OSError) as exc:
                        entry['timing_evidence_error'] = str(exc)
            from validation.checks.environment import inspect_environment
            inspection = await inspect_environment(environment, task, context, label)
            entry.update(inspection)
            if label == 'agent' and getattr(args, 'resolve_path_root', None):
                from data.utils.resolve_absolute_paths import resolve_in_environment
                entry['instruction_paths'] = await resolve_in_environment(environment, task_path, args.resolve_path_root)
        except Exception as exc:
            entry.update(status='error', error=str(exc))
        finally:
            entry['timings_seconds'][phase] = time.monotonic() - started
            if environment is not None:
                stopped = time.monotonic()
                try:
                    await environment.stop(delete=True)
                except Exception as exc:
                    entry.update(status='error', cleanup_error=str(exc))
                finally:
                    entry['timings_seconds']['stop'] = time.monotonic() - stopped
        results.append(entry)
    return {'status': 'passed' if all(e['status'] == 'passed' for e in results) else 'error', 'environments': results}
