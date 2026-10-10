"""Detect task initialization and run it within the environment build/start budget."""
import re
import time
from pathlib import Path, PurePosixPath


_CALL = re.compile(
    r"(?:(?:/bin/)?(?:bash|sh)\s+)?(?P<path>/setup_files/(?:[A-Za-z0-9_.-]+/)*setup\.sh)"
    r"(?:\s*\|\|\s*exit\s+\$\?)?\s*;?"
)


def detect(task: Path) -> str | None:
    """Return a verified setup command, or None when the oracle has no setup call.

    A mention that cannot be parsed safely is an error, never a silent omission.
    Only task-owned scripts under /setup_files may be executed automatically.
    """
    oracle = task / 'solution/solve.sh'
    if not oracle.is_file():
        return None
    calls = set()
    for number, raw in enumerate(oracle.read_text().splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith('#') or 'setup_files/' not in line or 'setup.sh' not in line:
            continue
        match = _CALL.fullmatch(line)
        if match is None:
            raise ValueError(f'{oracle}:{number}: setup.sh invocation needs review')
        path = PurePosixPath(match['path'])
        relative = path.relative_to('/setup_files')
        if '..' in relative.parts or not (task / 'setup_files' / relative).is_file():
            raise ValueError(f'{oracle}:{number}: referenced setup script is missing')
        calls.add(f'bash {path}')
    if len(calls) > 1:
        raise ValueError(f'{oracle}: multiple setup scripts need review')
    return next(iter(calls), None)


async def prepare(environment, *, command, timeout_sec, force_build, upload, logger=None, timings=None, start_environment=True):
    """Caller enforces timeout_sec around this entire operation, including uploads."""
    deadline = time.monotonic() + timeout_sec
    async def measured(name, operation):
        started = time.monotonic()
        try:
            return await operation()
        finally:
            if timings is not None:
                timings[name] = time.monotonic() - started
    if start_environment:
        await measured('container_start', lambda: environment.start(force_build=force_build))
    if not command:
        return
    await measured('setup_upload', upload)
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError('Environment preparation budget exhausted before task setup')
    if logger:
        logger.info('Task setup shares environment budget: %.3fs remaining; %s', remaining, command)
    result = await measured('setup_execution', lambda: environment.exec(command, timeout_sec=remaining, user='root'))
    if logger:
        logger.info('Task setup stdout:\n%s\nTask setup stderr:\n%s', result.stdout, result.stderr)
    if result.return_code != 0:
        raise RuntimeError(f'Task setup exited {result.return_code}: {result.stderr}')


async def upload_setup(environment, task_path):
    """Stage-3 equivalent of Harbor's setup-files upload, within the same budget."""
    await environment.empty_dirs(['/setup_files'], chmod=False)
    await environment.upload_dir(source_dir=task_path / 'setup_files', target_dir='/setup_files')
    await environment.exec('chmod -R a+rX /setup_files', user='root')


def install():
    from harbor.trial.trial import Trial
    from harbor.trial.errors import EnvironmentStartTimeoutError
    if getattr(Trial, '_shared_task_setup_budget', False):
        return
    original_start = Trial._start_agent_environment
    original_upload = Trial._upload_setup_files

    async def start(self):
        # Reset for every attempt, including retries on the same Trial object.
        self._task_setup_uploaded = False
        command = detect(self.task.paths.task_dir)
        if not command:
            return await original_start(self)
        budget = self._environment_build_timeout_sec
        error = EnvironmentStartTimeoutError(
            f'Environment build/start and task setup timed out after {budget} seconds')
        await self._await_phase(
            prepare(self.agent_environment, command=command, timeout_sec=budget,
                    force_build=self.config.environment.force_build,
                    upload=lambda: original_upload(self), logger=self.logger),
            timeout_sec=budget, timeout_error=error)
        self._task_setup_uploaded = True

    async def upload(self):
        # Re-uploading would erase SETA's completion marker. Its later setup
        # calls deliberately no-op, preserving the initialized baseline.
        if not getattr(self, '_task_setup_uploaded', False):
            await original_upload(self)

    Trial._start_agent_environment = start
    Trial._upload_setup_files = upload
    Trial._shared_task_setup_budget = True
