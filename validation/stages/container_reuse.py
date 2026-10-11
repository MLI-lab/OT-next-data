"""Reuse Apptainer containers within one task, preserving files and clearing logs."""
from contextvars import ContextVar

_scope = ContextVar('validation_container_scope', default=None)


class ReuseScope:
    def __init__(self):
        self.cached = {}
        self.owners = []
        self.borrowers = []
        self.starts = 0
        self.reuses = 0

    async def __aenter__(self):
        self.token = _scope.set(self)
        return self

    async def clear_logs(self):
        for environment in self.cached.values():
            result = await environment.exec(
                'find /logs/verifier /logs/agent -mindepth 1 -maxdepth 1 '
                '-exec rm -rf -- {} +', timeout_sec=30)
            if result.return_code != 0:
                raise RuntimeError('could not clear previous validation logs before reuse')

    async def __aexit__(self, *exc):
        _scope.reset(self.token)
        errors = []
        try:
            for environment, stop in self.owners:
                try:
                    await stop(environment, delete=True)
                except Exception as error:
                    errors.append(error)
        finally:
            for environment in self.borrowers:
                environment._env_id = None
        if errors:
            raise ExceptionGroup('validation container cleanup failed', errors)


def install(environment_class=None):
    if environment_class is None:
        from harbor.environments.apptainer.apptainer import ApptainerEnvironment
        environment_class = ApptainerEnvironment
    if getattr(environment_class, '_validation_reuse_installed', False):
        return
    original_start, original_stop = environment_class.start, environment_class.stop

    async def start(environment, force_build=False):
        scope = _scope.get()
        if scope is None:
            return await original_start(environment, force_build=force_build)
        if force_build:
            raise ValueError('force-build is incompatible with live validation reuse')
        key = (str(environment.environment_dir.resolve()), environment._bridge_url,
               environment.task_env_config.model_dump_json())
        owner = scope.cached.get(key)
        if owner is not None and owner._env_id:
            environment._env_id = owner._env_id
            scope.borrowers.append(environment)
            scope.reuses += 1
            return
        scope.owners.append((environment, original_stop))
        await original_start(environment, force_build=False)
        scope.cached[key] = environment
        scope.starts += 1

    async def stop(environment, delete=True):
        scope = _scope.get()
        if scope is not None and any(owner._env_id and owner._env_id == environment._env_id
                                     for owner in scope.cached.values()):
            return
        return await original_stop(environment, delete=delete)

    environment_class.start = start
    environment_class.stop = stop
    environment_class._validation_reuse_installed = True
