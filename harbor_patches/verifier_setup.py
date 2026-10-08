"""Run the conventional verifier setup script before checks, without upstream edits."""
import functools

SETUP_COMMAND = 'if [ -f /tests/setup.sh ]; then bash /tests/setup.sh; fi'


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
            # when this task/step has no setup.sh. Image-owned tests skip upload.
            result = await self.environment.exec('rm -f /tests/setup.sh', user='root')
            if result.return_code:
                raise RuntimeError('Could not clear stale verifier setup.sh')
            self._tests_started = True
        return await self.environment.upload_dir(source_dir=source_dir, target_dir=target_dir)

    async def exec(self, command, *args, **kwargs):
        if command == self.test_command:
            from harbor.verifier.verifier import VerifierRuntimeError
            result = await self.environment.exec(SETUP_COMMAND, *args, **kwargs)
            directory = self.verifier.trial_paths.verifier_dir
            directory.mkdir(parents=True, exist_ok=True)
            (directory / 'setup-stdout.txt').write_text(result.stdout or '')
            (directory / 'setup-stderr.txt').write_text(result.stderr or '')
            if result.return_code != 0:
                raise VerifierRuntimeError(
                    f'Verifier setup.sh exited {result.return_code}; checks were not run. '
                    + (result.stderr or result.stdout or '')[-1000:])
        return await self.environment.exec(command, *args, **kwargs)


def install():
    from harbor.verifier import verifier as module
    cls = module.Verifier
    if getattr(cls, '_shared_setup_script', False):
        return
    original = cls.verify

    @functools.wraps(original)
    async def verify(self):
        environment = self.environment
        _, context, host_test = self._resolve_tests()
        # This convention is for shell tasks; preserve the Windows .bat path.
        if host_test.suffix != '.sh':
            return await original(self)
        paths = module.EnvironmentPaths.for_os(environment.os)
        script = str(paths.tests_dir / host_test.relative_to(context).as_posix())
        stdout = str(paths.verifier_dir / self.trial_paths.test_stdout_path.relative_to(
            self.trial_paths.verifier_dir).as_posix())
        command = module.build_execution_command(script, stdout_path=stdout, task_os=environment.os)
        self.environment = SetupEnvironment(environment, command, self)
        try:
            # Harbor's enclosing verifier timeout covers setup and checks together.
            return await original(self)
        finally:
            self.environment = environment

    cls.verify = verify
    cls._shared_setup_script = True
