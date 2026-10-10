"""Run the conventional verifier setup script before checks, without upstream edits."""
import functools

SETUP_COMMAND = 'if [ -f /tests/setup.sh ]; then bash /tests/setup.sh; fi'
CLEAR_REWARDS_COMMAND = 'rm -f /logs/verifier/reward.txt /logs/verifier/reward.json'
FAILED_SETUP_REWARD_COMMAND = 'test ! -e /logs/verifier/reward.json && test -f /logs/verifier/reward.txt && test "$(cat /logs/verifier/reward.txt)" = 0'


def build_context(task, step=None):
    """Select the image independently of how verifier files are uploaded."""
    context = task.paths.step_tests_dir(step.name) if step else task.paths.tests_dir
    if not context.exists():
        context = task.paths.tests_dir
    explicit = task.config.verifier.environment is not None or (
        step is not None and step.verifier.environment is not None)
    if not explicit and not (context / 'Dockerfile').exists() and not any(context.glob('*compose*.y*ml')):
        return task.paths.environment_dir
    return context


class SetupEnvironment:
    """Intercept only the resolved grading command, after Harbor uploads tests."""
    def __init__(self, environment, test_command, verifier):
        self.environment = environment
        self.test_command = test_command
        self.verifier = verifier
        self._tests_started = False

    def __getattr__(self, name):
        return getattr(self.environment, name)

    async def upload_dir(self, source_dir, target_dir):
        if str(target_dir).rstrip('/') == '/tests' and not self._tests_started:
            # Do not execute an agent-created script or a previous step's setup
            # when this task/step has no setup.sh, including baked image scripts.
            result = await self.environment.exec('rm -f /tests/setup.sh', user='root')
            if result.return_code:
                raise RuntimeError('Could not clear stale verifier setup.sh')
            self._tests_started = True
        return await self.environment.upload_dir(source_dir=source_dir, target_dir=target_dir)

    async def exec(self, command, *args, **kwargs):
        if command == self.test_command:
            from harbor.verifier.verifier import VerifierRuntimeError
            cleared = await self.environment.exec(CLEAR_REWARDS_COMMAND, *args, **kwargs)
            if cleared.return_code:
                raise VerifierRuntimeError('Could not clear stale verifier rewards before setup')
            result = await self.environment.exec(SETUP_COMMAND, *args, **kwargs)
            directory = self.verifier.trial_paths.verifier_dir
            directory.mkdir(parents=True, exist_ok=True)
            (directory / 'setup-stdout.txt').write_text(result.stdout or '')
            (directory / 'setup-stderr.txt').write_text(result.stderr or '')
            if result.return_code != 0:
                scored = await self.environment.exec(FAILED_SETUP_REWARD_COMMAND, *args, **kwargs)
                if scored.return_code == 0:
                    # Explicit task-written rejection. Stop grading and let Harbor
                    # collect the new zero; missing captures still raise below.
                    return result
                raise VerifierRuntimeError(
                    f'Verifier setup.sh exited {result.return_code}; checks were not run. '
                    + (result.stderr or result.stdout or '')[-1000:])
            # The module is uploaded outside task code and loaded only by pytest.
            # Each verifier call has its own token; stale logs cannot certify it.
            import json
            from pathlib import Path
            import shlex
            from uuid import uuid4
            token = uuid4().hex
            target = '/tmp/ot-pytest-execution-' + token
            await self.environment.upload_file(
                source_path=Path(__file__).with_name('ot_pytest_execution.py'),
                target_path=target + '/ot_pytest_execution.py')
            # Twisted trial verifiers are recorded by a sitecustomize hook on
            # the same path; it is inert for every other interpreter.
            await self.environment.upload_file(
                source_path=Path(__file__).with_name('sitecustomize.py'),
                target_path=target + '/sitecustomize.py')
            (directory / 'execution-context.json').write_text(json.dumps({'version': 1, 'token': token}))
            prefix = (
                'export OT_VERIFIER_EXECUTION_TOKEN=' + shlex.quote(token) + '; '
                'unset OT_PYTEST_EXECUTION_ACTIVE; '
                'export PYTHONPATH=' + shlex.quote(target) + '${PYTHONPATH:+:$PYTHONPATH}; '
                'export PYTEST_PLUGINS=ot_pytest_execution${PYTEST_PLUGINS:+,$PYTEST_PLUGINS}; '
            )
            try:
                return await self.environment.exec(prefix + command, *args, **kwargs)
            finally:
                # The node-local job directory is the final fallback after kill.
                import asyncio
                try:
                    await asyncio.wait_for(self.environment.exec(
                        'rm -rf -- ' + shlex.quote(target), user='root'), timeout=10)
                except Exception:
                    # Cleanup must not replace the verifier's result or timeout.
                    pass
        return await self.environment.exec(command, *args, **kwargs)


def install():
    from harbor.verifier import verifier as module
    from harbor.trial.trial import Trial
    # Preserve task-image reuse without replaying task initialization. Verifier
    # initialization is owned exclusively by tests/setup.sh.
    if not getattr(Trial, '_verifier_image_context', False):
        Trial._verifier_env_build_context = lambda self, step_cfg: build_context(self.task, step_cfg)
        Trial._verifier_image_context = True
    cls = module.Verifier
    if getattr(cls, '_shared_setup_script', False):
        return
    original = cls.verify

    @functools.wraps(original)
    async def verify(self):
        environment = self.environment
        skip_tests_upload = self._skip_tests_upload
        # Harbor normally skips this upload for separate images. Our task files
        # are authoritative for every verifier, regardless of its image.
        self._skip_tests_upload = False
        try:
            _, context, host_test = self._resolve_tests()
            # Always upload on Windows too, but preserve its .bat execution path.
            if host_test.suffix != '.sh':
                return await original(self)
            paths = module.EnvironmentPaths.for_os(environment.os)
            script = str(paths.tests_dir / host_test.relative_to(context).as_posix())
            stdout = str(paths.verifier_dir / self.trial_paths.test_stdout_path.relative_to(
                self.trial_paths.verifier_dir).as_posix())
            command = module.build_execution_command(script, stdout_path=stdout, task_os=environment.os)
            self.environment = SetupEnvironment(environment, command, self)
            # Harbor's enclosing verifier timeout covers setup and checks together.
            return await original(self)
        finally:
            self.environment = environment
            self._skip_tests_upload = skip_tests_upload

    cls.verify = verify
    cls._shared_setup_script = True
