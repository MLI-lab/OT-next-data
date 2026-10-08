"""A separate verifier can reuse the task image and reconstruct task setup."""
import asyncio
from contextlib import asynccontextmanager


def tests_context(task, step=None):
    if step is not None:
        context = task.paths.step_tests_dir(step.name)
        if context.exists():
            return context
    return task.paths.tests_dir


def uses_task_image(task, step=None):
    if task.config.verifier.environment is not None or (
            step is not None and step.verifier.environment is not None):
        return False
    context = tests_context(task, step)
    return not (context / 'Dockerfile').exists() and not any(context.glob('*compose*.y*ml'))


def build_context(task, step=None):
    return task.paths.environment_dir if uses_task_image(task, step) else tests_context(task, step)


async def prepare_task(environment, task, *, timeout_sec, timings=None):
    """Recreate initialization before importing agent submissions; never run the agent."""
    from validation.stages.task_setup import detect, upload_setup
    import time
    command = detect(task.paths.task_dir)
    if not command:
        return
    async def operation():
        await upload_setup(environment, task.paths.task_dir)
        result = await environment.exec(command, user='root', timeout_sec=timeout_sec)
        if result.return_code:
            raise RuntimeError(f'Fresh verifier task setup exited {result.return_code}: {result.stderr}')
    started = time.monotonic()
    try:
        await asyncio.wait_for(operation(), timeout=timeout_sec)
    finally:
        if timings is not None:
            timings['task_setup'] = time.monotonic() - started


async def upload_tests(environment, task, step=None):
    # Fresh task images need verifier files staged explicitly. Harbor's separate
    # mode normally assumes the verifier image already contains /tests.
    result = await environment.exec('rm -f /tests/setup.sh', user='root')
    if result.return_code:
        raise RuntimeError('Could not clear stale verifier setup.sh')
    await environment.upload_dir(source_dir=task.paths.tests_dir, target_dir='/tests')
    context = tests_context(task, step)
    if context != task.paths.tests_dir:
        await environment.upload_dir(source_dir=context, target_dir='/tests')


def install():
    from harbor.trial.trial import Trial
    if getattr(Trial, '_fresh_task_image_verifier', False):
        return
    original_context = Trial._verifier_env_build_context
    original_environment = Trial._separate_verifier_env

    def context(self, step_cfg):
        if uses_task_image(self.task, step_cfg):
            return self.task.paths.environment_dir
        return original_context(self, step_cfg)

    @asynccontextmanager
    async def environment(self, env_config, *, key, step_cfg=None):
        async with original_environment(self, env_config, key=key, step_cfg=step_cfg) as target:
            if uses_task_image(self.task, step_cfg):
                # _run_separate_verifier imports artifacts only after this yield.
                await prepare_task(target, self.task, timeout_sec=self._environment_build_timeout_sec)
                await asyncio.wait_for(upload_tests(target, self.task, step_cfg),
                                       timeout=self._environment_build_timeout_sec)
            yield target

    Trial._verifier_env_build_context = context
    Trial._separate_verifier_env = environment
    Trial._fresh_task_image_verifier = True
