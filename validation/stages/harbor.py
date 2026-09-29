"""Local Harbor runtime adapter, using the same Apptainer bridge as teacher runs."""
from __future__ import annotations

import asyncio
import json
import math
import os
from pathlib import Path
import tomllib
from uuid import uuid4


def install_runtime_patches():
    # The pinned bridge keys images only by Dockerfile text. Include COPY
    # payloads as well, or distinct review tasks can reuse stale baked evidence.
    from harbor.environments.apptainer import apptainer as bridge
    from harbor.utils.container_cache import environment_dir_hash_truncated
    bridge.dockerfile_hash_truncated = lambda path: environment_dir_hash_truncated(path.parent)


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
    # API routing is relevant to host-driven Terminus, not the Claude reviewer.
    if args.api_base and agent == 'terminus-2':
        kwargs['api_base'] = args.api_base
    config = {
        'job_name': 'job', 'jobs_dir': str(output.resolve()),
        'tasks': [{'path': str(task.resolve())}],
        'agents': [{'name': agent, **({'model_name': model} if model else {}), 'kwargs': kwargs}],
        'environment': environment_config(args), 'verifier': {'disable': False},
        'n_attempts': args.attempts, 'n_concurrent_trials': args.concurrency,
        'retry': {'max_retries': 0},
    }
    if artifacts:
        config['artifacts'] = artifacts
    return config


async def execute_job(config):
    install_runtime_patches()
    from harbor.job import Job
    from harbor.models.job.config import JobConfig
    job = await Job.create(JobConfig.model_validate(config))
    await job.run()
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


def assess_trials(results, expected_count, expected_reward=None, reward_key='reward'):
    findings = []
    if len(results) != expected_count:
        findings.append(f'expected {expected_count} trials, found {len(results)}')
    scores = []
    for path, result in results:
        if result.get('exception_info'):
            findings.append(f'{path.name}: exception: {result["exception_info"]}')
            continue
        rewards = (result.get('verifier_result') or {}).get('rewards') or {}
        score = rewards.get(reward_key)
        if not isinstance(score, (int, float)) or isinstance(score, bool) or not math.isfinite(score):
            findings.append(f'{path.name}: missing numeric reward {reward_key!r}')
            continue
        scores.append(score)
        if expected_reward is not None and score != expected_reward:
            findings.append(f'{path.name}: expected reward {expected_reward}, got {score}')
    return {'status': 'failed' if findings else 'completed', 'findings': findings, 'rewards': scores}


async def build_task(task_path, out, args):
    install_runtime_patches()
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
                context = task.paths.step_tests_dir(step.name)
                specs.append((f'verifier-{step.name}', context if context.exists() else task.paths.tests_dir, env))
    else:
        env = resolve_effective_verifier_env_config(task.config, None)
        if env is not None:
            specs.append(('verifier', task.paths.tests_dir, env))
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
        entry = {'environment': label, 'context': str(context)}
        try:
            environment = EnvironmentFactory.create_environment(
                type=EnvironmentType(args.backend), environment_dir=context,
                environment_name=f'{task.short_name}-{label}', session_id=f'validate-{uuid4().hex[:12]}',
                trial_paths=paths, task_env_config=spec, **config['kwargs'])
            await asyncio.wait_for(environment.start(force_build=args.force_build), timeout=spec.build_timeout_sec)
            from validation.checks.environment import inspect_environment
            inspection = await inspect_environment(environment, task, context, label)
            entry.update(inspection)
        except Exception as exc:
            entry.update(status='error', error=str(exc))
        finally:
            if environment is not None:
                try:
                    await environment.stop(delete=True)
                except Exception as exc:
                    entry.update(status='error', cleanup_error=str(exc))
        results.append(entry)
    return {'status': 'passed' if all(e['status'] == 'passed' for e in results) else 'error', 'environments': results}
