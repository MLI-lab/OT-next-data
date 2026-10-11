"""Explicit preparation-only boundary for stage-3 setup review.

Never infer a preparation prefix from test.sh: that can execute grading, and a
tests/setup.sh is the preparation boundary; test.sh is never run here.
"""
import asyncio
import json
import math
import logging
import shutil
import time
from uuid import uuid4


async def review_task(task_path, out, args):
    from harbor.environments.factory import EnvironmentFactory
    from harbor.models.environment_type import EnvironmentType
    from harbor.models.task.task import Task
    from harbor.models.task.verifier_mode import resolve_effective_verifier_env_config
    from harbor.models.trial.paths import TrialPaths
    from harbor.trial.trial import ArtifactHandler
    from harbor.verifier.verifier import EnvironmentPaths, resolve_env_vars
    from harbor_patches.verifier_setup import SETUP_COMMAND, build_context
    from validation.stages import harbor as runtime
    from validation.stages.task_setup import detect, prepare, upload_setup
    from validation.checks.environment import inspect_environment

    task = Task(task_path)
    config = runtime.environment_config(args)
    environments, entries, verifiers = [], [], []
    agent = None
    plans = []
    for step in task.config.steps or [None]:
        name = f'verifier-{step.name}' if step else 'verifier'
        context = task.paths.step_tests_dir(step.name) if step else task.paths.tests_dir
        if not context.exists():
            context = task.paths.tests_dir
        verifier = step.verifier if step else task.config.verifier
        sources = [task.paths.tests_dir]
        if context != task.paths.tests_dir:
            sources.append(context)
        artifacts = list(task.config.artifacts) + (list(step.artifacts) if step else [])
        plans.append((name, context, verifier, resolve_effective_verifier_env_config(task.config, step), sources, artifacts, step))

    def create(label, context, spec):
        overrides = {}
        if args.trial_cpus:
            overrides['cpus'] = args.trial_cpus
        if args.trial_memory_mb:
            overrides['memory_mb'] = args.trial_memory_mb
        spec = spec.model_copy(update=overrides)
        paths = TrialPaths(out / label)
        paths.mkdir()
        env = EnvironmentFactory.create_environment(
            type=EnvironmentType(args.backend), environment_dir=context,
            environment_name=f'{task.short_name}-{label}', session_id=f'validate-{uuid4().hex[:12]}',
            trial_paths=paths, task_env_config=spec, **config['kwargs'])
        environments.append(env)
        return env

    async def measured(record, phase, operation):
        started = time.monotonic()
        try:
            return await operation()
        finally:
            record['timings_seconds'][phase] = time.monotonic() - started

    async def execute(env, command, record, phase, timeout, *, user='root', variables=None):
        result = await measured(record, phase, lambda: env.exec(
            command, timeout_sec=timeout, user=user, env=variables or {}))
        directory = out / record['environment']
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f'{phase}.stdout').write_text(result.stdout or '')
        (directory / f'{phase}.stderr').write_text(result.stderr or '')
        if result.return_code:
            raise RuntimeError(f'{phase} exited {result.return_code}: {result.stderr}')

    async def start_container(env, spec, record):
        record['startup_timeout_seconds'] = spec.build_timeout_sec
        record['failure_phase'] = 'container_start'
        await asyncio.wait_for(measured(record, 'container_start',
            lambda: env.start(force_build=False)), timeout=spec.build_timeout_sec)
        record.pop('failure_phase', None)

    agent_entry = {'environment': 'agent', 'timings_seconds': {}, 'status': 'error'}
    entries.append(agent_entry)
    try:
        started = time.monotonic()
        preparation_started = None
        try:
            agent = create('agent', task.paths.environment_dir, task.config.environment)
            await start_container(agent, task.config.environment, agent_entry)
            preparation_started = time.monotonic()
            agent_entry['failure_phase'] = 'preparation'
            command = detect(task_path)
            task_budget = min(task.config.environment.build_timeout_sec,
                              getattr(args, 'preparation_max_seconds', None) or 60.0)
            await asyncio.wait_for(prepare(agent, command=command,
                timeout_sec=task_budget, force_build=False, start_environment=False,
                upload=lambda: upload_setup(agent, task_path), timings=agent_entry['timings_seconds']),
                timeout=task_budget)
            agent_entry['status'] = 'passed'
            agent_entry.pop('failure_phase', None)
        except Exception as exc:
            agent_entry['error'] = f'{type(exc).__name__}: {exc}'
        finally:
            agent_entry['timings_seconds']['start'] = time.monotonic() - started
            if preparation_started is not None:
                agent_entry['timings_seconds']['preparation'] = time.monotonic() - preparation_started

        # Preparation is measured before inspection: inspection must not warm or
        # otherwise change the state the verifier preparation is intended to test.
        for name, context, verifier, spec, sources, artifacts, step in plans:
            record = {'environment': name, 'status': 'error', 'timings_seconds': {},
                      'timeout_seconds': verifier.timeout_sec,
                      'mean_target_seconds': verifier.timeout_sec * .05}
            verifiers.append(record)
            if agent_entry['status'] != 'passed':
                record.update(status='blocked', error='task preparation failed')
                continue
            preparation_started = None
            try:
                timeout = float(verifier.timeout_sec)
                if not math.isfinite(timeout) or timeout <= 0:
                    raise ValueError('Verifier timeout must be positive and finite')
                record['setup_command'] = SETUP_COMMAND
                target = agent
                if spec is not None:
                    target = create(name, build_context(task, step), spec)
                    await start_container(target, spec, record)
                preparation_started = time.monotonic()
                record['failure_phase'] = 'preparation'
                async def preparation():
                    hooks = list(task.config.verifier.collect)
                    if step is not None:
                        hooks += list(step.verifier.collect)
                    for index, hook in enumerate(hooks):
                        await execute(agent, hook.command, record, f'submission_collect_{index}',
                                      min(timeout, hook.timeout_sec), user=hook.user)
                    if spec is not None:
                        if task.paths.setup_files_dir.is_dir():
                            await measured(record, 'setup_files_upload', lambda: upload_setup(target, task_path))
                        # Use Harbor's existing artifact rules rather than inventing
                        # another transfer declaration for setup review.
                        handler = ArtifactHandler(artifacts=artifacts, logger=logging.getLogger(__name__))
                        source_paths = EnvironmentPaths.for_os(agent.os)
                        target_paths = EnvironmentPaths.for_os(target.os)
                        saved = out / name / 'artifacts'
                        await measured(record, 'submission_download', lambda: handler.download_artifacts(
                            agent, saved, source_artifacts_dir=source_paths.artifacts_dir))
                        await measured(record, 'submission_upload', lambda: handler.upload_artifacts(
                            target, saved, source_artifacts_dir=source_paths.artifacts_dir,
                            target_artifacts_dir=target_paths.artifacts_dir))
                    await execute(target, 'rm -f /tests/setup.sh', record, 'clear_stale_setup', timeout)
                    for index, source in enumerate(sources):
                        await measured(record, f'tests_upload_{index}', lambda: target.upload_dir(
                            source_dir=source, target_dir='/tests'))
                    # The same optional script is invoked by normal verification.
                    # All work in verifier setup belongs to this timer.
                    variables = resolve_env_vars(dict(task.config.verifier.env, **verifier.env))
                    await execute(target, SETUP_COMMAND, record, 'setup_execution', timeout,
                                  user=verifier.user if verifier.user is not None else task.config.verifier.user, variables=variables)
                    record['status'] = 'passed'

                # A slow run is rejected at the individual-run limit, even if
                # task.toml allows hours for actual compilation/analysis.
                await asyncio.wait_for(preparation(), timeout=min(timeout, 60.0))
                record['timings_seconds']['preparation'] = time.monotonic() - preparation_started
                record.pop('failure_phase', None)
                if spec is not None:
                    inspection = await inspect_environment(target, task, context, name)
                    record['inspection'] = inspection
                    if inspection.get('status') != 'passed':
                        record.update(status='error', error='separate verifier runtime checks failed')
            except Exception as exc:
                record.update(status='error', error=f'{type(exc).__name__}: {exc}')
            finally:
                # Per-run submission transfers are temporary, not five persistent copies.
                cleanup_started = time.monotonic()
                shutil.rmtree(out / name / 'artifacts', ignore_errors=True)
                cleanup_seconds = time.monotonic() - cleanup_started
                record['timings_seconds']['transfer_cleanup'] = cleanup_seconds
                if 'preparation' in record['timings_seconds']:
                    record['timings_seconds']['preparation'] += cleanup_seconds
                elif preparation_started is not None:
                    record['timings_seconds']['preparation'] = time.monotonic() - preparation_started

        if agent_entry['status'] == 'passed':
            try:
                inspection = await inspect_environment(agent, task, task.paths.environment_dir, 'agent')
                agent_entry.update(inspection)
            except Exception as exc:
                agent_entry.update(status='error', error=f'{type(exc).__name__}: {exc}')
    finally:
        for env in reversed(environments):
            try:
                await env.stop(delete=True)
            except Exception as exc:
                agent_entry.update(status='error', cleanup_error=str(exc))
    result = {'status': 'passed' if agent_entry['status'] == 'passed' and all(
            v['status'] == 'passed' for v in verifiers) else 'error',
            'environments': entries, 'verifier_preparation': verifiers}
    # Keep failed preparation timings available while the full stage is running.
    # Interrupted exec calls may never return stdout/stderr to the runner.
    out.mkdir(parents=True, exist_ok=True)
    (out / 'preparation-review.json').write_text(json.dumps(result, indent=2) + '\n')
    return result
