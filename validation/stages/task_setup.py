"""Detect task initialization and run it within the environment build/start budget."""
import re
import json
import time
import shlex
from pathlib import Path, PurePosixPath


_CALL = re.compile(
    r"(?:(?:/bin/)?(?:bash|sh)\s+)?(?P<path>/setup_files/(?:[A-Za-z0-9_.-]+/)*setup\.sh)"
    r"(?:\s*\|\|\s*exit\s+\$\?)?\s*;?"
)


def detect(task: Path) -> str | None:
    """Return a verified setup command, or None when the task has no setup.

    The training harness runs setup_files/setup.sh before every agent start,
    with or without a solution, so its presence alone means setup. An oracle
    that calls a setup script must name that same script. A mention that cannot
    be parsed safely is an error, never a silent omission. Only task-owned
    scripts under /setup_files may be executed automatically.
    """
    from harbor_patches.task_runtime import declared
    default = 'bash /setup_files/setup.sh' if (task / 'setup_files/setup.sh').is_file() else None
    runtime_only = 'true' if declared(task) else None
    oracle = task / 'solution/solve.sh'
    if not oracle.is_file():
        return default or runtime_only
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
    if calls and default and calls != {default}:
        raise ValueError(f'{oracle}: calls a setup script other than the harness default')
    return next(iter(calls), default or runtime_only)


async def prepare(environment, *, command, timeout_sec, force_build, upload, logger=None, timings=None,
                  start_environment=True, waited=None):
    """Caller enforces timeout_sec around this entire operation, including uploads.

    ``waited`` reports the seconds the start spent waiting for a worker or a Slurm
    step; that time is not part of the budget (harbor_patches/start_wait.py).
    """
    deadline = time.monotonic() + timeout_sec
    async def measured(name, operation):
        started = time.monotonic()
        try:
            return await operation()
        finally:
            if timings is not None:
                timings[name] = time.monotonic() - started
    if start_environment:
        from harbor_patches.task_runtime import reset
        reset(environment)
        await measured('container_start', lambda: environment.start(force_build=force_build))
    if waited is not None:
        wait = await waited()
        deadline += wait
        if timings is not None:
            timings['start_wait'] = wait
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
        diagnostics = await setup_diagnostics(environment)
        # A Python whose own standard library no longer imports was not broken by the
        # task's requirements: the container's view of its image is damaged (2026-10-11).
        damaged = 'OT_PYTHON_STDLIB_IMPORT_FAILED:' in diagnostics
        prefix = DAMAGED + ': ' if damaged else ''
        error = RuntimeError(f'{prefix}Task setup exited {result.return_code}: {result.stderr}'
                             f'\nTask setup stdout:\n{result.stdout}')
        error.diagnostics = diagnostics
        if logger and error.diagnostics:
            logger.info('Task setup failure diagnostics:\n%s', error.diagnostics)
        raise error


def capture_setup_command(command, directory):
    """Review-only capture survives cancellation while retaining exit status."""
    if not re.fullmatch(r'/setup_files/\.ot-setup-output-[a-f0-9]{32}', directory):
        raise ValueError('Invalid setup output directory')
    quoted = shlex.quote(directory)
    return (f'mkdir -m 700 -- {quoted} || exit $?; '
            f'if ( {command} ) >{quoted}/stdout 2>{quoted}/stderr; '
            'then ot_setup_status=0; else ot_setup_status=$?; fi; '
            f'cat -- {quoted}/stdout; cat -- {quoted}/stderr >&2; '
            'exit "$ot_setup_status"')


async def collect_setup_output(environment, directory, destination):
    """Read bounded diagnostics after the measured preparation, never change its result."""
    import asyncio
    if not re.fullmatch(r'/setup_files/\.ot-setup-output-[a-f0-9]{32}', directory):
        raise ValueError('Invalid setup output directory')
    quoted = shlex.quote(directory)
    script = (f'printf "%s\\n" "== setup stdout (last 16384 bytes)"; tail -c 16384 -- {quoted}/stdout; '
              f'printf "%s\\n" "== setup stderr (last 16384 bytes)"; tail -c 16384 -- {quoted}/stderr')
    try:
        result = await asyncio.wait_for(environment.exec(script, user='root', timeout_sec=10), timeout=12)
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text((result.stdout or '') + (result.stderr or ''))
        return {'path': str(destination), 'return_code': result.return_code}
    except Exception as error:
        return {'error': f'{type(error).__name__}: {error}'}


DAMAGED = 'Container environment damaged (Python standard library not importable after the failed setup)'

PIP_DIAGNOSTICS = '''
import importlib, sys
from pathlib import Path
print("executable:", sys.executable)
print("sys.path:", repr(sys.path))
for name in ("pip", "pip._vendor.requests.sessions", "pip._vendor.packaging.markers"):
    try:
        module = importlib.import_module(name)
        print(name, "import ok:", module.__file__)
    except Exception as exc:
        print(name, type(exc).__name__, str(exc)[:300])
root = Path(sys.prefix) / "lib" / ("python%d.%d" % sys.version_info[:2]) / "site-packages"
for name in ("pip/__init__.py", "pip/_vendor/requests/sessions.py", "pip/_vendor/packaging/markers.py"):
    path = root / name
    try:
        st = path.stat()
        print(str(path), "mode", oct(st.st_mode), "bytes", st.st_size)
        with path.open("rb") as stream:
            print("readable bytes:", len(stream.read(128)))
    except OSError as exc:
        print(str(path), type(exc).__name__, str(exc)[:300])
'''

SETUP_DIAGNOSTICS = (
    'echo "== mounts"; grep -E " / | /opt| /testbed|overlay|fuse" /proc/mounts | cut -c1-300; '
    'echo "== space"; df -h / /tmp 2>&1 | tail -n +2; df -i / 2>&1 | tail -1; '
    'echo "== python"; for p in "$(command -v python3 2>/dev/null)" "$(command -v python 2>/dev/null)" '
    '/opt/conda/envs/testbed/bin/python /opt/conda/bin/python; do '
    '[ -n "$p" ] && [ -x "$p" ] || continue; '
    'printf "OT_PYTHON_PROBE:%s\\n" "$p"; '
    'if "$p" -c "import sys, keyword, collections, encodings.utf_8; print(sys.version.split()[0], \'stdlib ok\')"; '
    'then :; else printf "OT_PYTHON_STDLIB_IMPORT_FAILED:%s\\n" "$p"; fi; done; '
    'echo "== processes"; ps -e -o comm= 2>/dev/null | sort | uniq -c | sort -rn | head -8; '
    'echo "== pip"; p=/opt/conda/envs/testbed/bin/python; '
    f'[ ! -x "$p" ] || "$p" -c {shlex.quote(PIP_DIAGNOSTICS)} 2>&1')


async def setup_diagnostics(environment):
    """State of the container right after a failed setup: is the image's Python still intact?

    Setup failures on 2026-10-11 showed standard-library modules missing and files
    reported as directories in bursts across unrelated images; this records what the
    container sees at that moment. Never raises.
    """
    try:
        result = await environment.exec(SETUP_DIAGNOSTICS, timeout_sec=60, user='root')
        return ((result.stdout or '') + (result.stderr or ''))[-8000:]
    except Exception as exc:
        return f'diagnostics unavailable: {type(exc).__name__}: {exc}'


async def upload_setup(environment, task_path, *, task_runtime=True):
    """Stage-1 equivalent of Harbor's setup-files upload, within the same budget."""
    await environment.empty_dirs(['/setup_files'], chmod=False)
    await environment.upload_dir(source_dir=task_path / 'setup_files', target_dir='/setup_files')
    await environment.exec('chmod -R a+rX /setup_files', user='root')
    if task_runtime:
        from harbor_patches.task_runtime import stage
        await stage(environment, task_path)


async def upload_review_files(environment, task_path):
    """Rubric-review evidence (validation/stages/env_review.py) goes under /app after
    task setup, so the reviewed environment and its cached image stay unchanged."""
    parts = sorted((task_path / 'review_files').glob('*'))
    if not parts:
        return
    await environment.exec('rm -f /app/verdicts.json /app/analysis.json', user='root')
    for part in parts:
        if part.is_dir():
            await environment.upload_dir(source_dir=part, target_dir=f'/app/{part.name}')
        else:
            await environment.upload_file(source_path=part, target_path=f'/app/{part.name}')
    targets = ' '.join(shlex.quote(f'/app/{part.name}') for part in parts)
    await environment.exec(f'chmod -R a+rX {targets}', user='root')


def save_timings(path, timings, logger=None):
    """Seconds of each preparation step of a trial (container start, upload, setup), next to its
    result: stages 2 and 3 then give further samples beside stage 1's. Never fails the trial."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({name: round(seconds, 3) for name, seconds in timings.items()}) + '\n')
    except Exception as exc:
        if logger:
            logger.warning('Could not save setup timings to %s: %s', path, exc)


def install():
    from harbor.trial.trial import Trial
    from harbor.trial.errors import EnvironmentStartTimeoutError
    if getattr(Trial, '_shared_task_setup_budget', False):
        return
    original_start = Trial._start_agent_environment
    original_upload = Trial._upload_setup_files

    async def start(self):
        await start_task(self)
        if (self.task.paths.task_dir / 'review_files').is_dir():
            await upload_review_files(self.agent_environment, self.task.paths.task_dir)

    async def start_task(self):
        # Reset for every attempt, including retries on the same Trial object.
        self._task_setup_uploaded = False
        from harbor_patches.start_wait import run_with_budget, status_reader
        command = detect(self.task.paths.task_dir)
        if not command and not hasattr(self, '_environment_build_timeout_sec'):
            return await original_start(self)      # a Trial without Harbor's budget: nothing to adjust
        budget = self._environment_build_timeout_sec
        # The budget covers container start and task setup; waiting for a worker
        # or a Slurm step comes on top and is logged, never charged to the task.
        waited = status_reader(self.agent_environment)
        error = EnvironmentStartTimeoutError(
            f'Environment build/start and task setup timed out after {budget} seconds')
        note = lambda wait: wait >= 5 and self.logger.info('Start waited %.0fs for a worker or Slurm step', wait)
        if not command:
            return await run_with_budget(
                self.agent_environment.start(force_build=self.config.environment.force_build),
                budget, waited, timeout_error=error, record=note)
        timings = {}
        async def upload_prepared():
            await original_upload(self)
            from harbor_patches.task_runtime import stage
            paths = getattr(self, 'paths', None)
            await stage(self.agent_environment, self.task.paths.task_dir,
                        paths.trial_dir if paths is not None else None)
        try:
            await run_with_budget(
                prepare(self.agent_environment, command=command, timeout_sec=budget,
                        force_build=self.config.environment.force_build, timings=timings,
                        upload=upload_prepared, logger=self.logger, waited=waited),
                budget, waited, timeout_error=error, record=note)
        finally:
            paths = getattr(self, 'paths', None)
            if paths is not None:
                save_timings(paths.trial_dir / 'task-setup-timing.json', timings, self.logger)
        self._task_setup_uploaded = True

    async def upload(self):
        # Re-uploading would erase SETA's completion marker. Its later setup
        # calls deliberately no-op, preserving the initialized baseline.
        if not getattr(self, '_task_setup_uploaded', False):
            await original_upload(self)

    Trial._start_agent_environment = start
    Trial._upload_setup_files = upload
    Trial._shared_task_setup_budget = True
