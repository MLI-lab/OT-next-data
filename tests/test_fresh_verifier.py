import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from harbor_patches import fresh_verifier


def task_fixture(tmp_path, custom=False):
    from harbor.models.task.task import Task
    for d in ('environment', 'tests', 'setup_files', 'solution'):
        (tmp_path/d).mkdir(parents=True)
    (tmp_path/'task.toml').write_text('[verifier]\nenvironment_mode="separate"\n')
    (tmp_path/'instruction.md').write_text('task')
    (tmp_path/'solution/solve.sh').write_text('bash /setup_files/setup.sh\n')
    (tmp_path/'setup_files/setup.sh').write_text('echo initialize')
    (tmp_path/'environment/Dockerfile').write_text('FROM task-image\n')
    if custom: (tmp_path/'tests/Dockerfile').write_text('FROM verifier-image\n')
    return Task(tmp_path)


@pytest.mark.parametrize('custom', [False, True])
@pytest.mark.parametrize('failure', [False, True])
def test_fresh_task_setup_precedes_submission_and_always_cleans(tmp_path, monkeypatch, custom, failure):
    from harbor.trial import trial as module
    task = task_fixture(tmp_path, custom)
    events = []
    class Environment:
        async def empty_dirs(self, paths, **kw): events.append('clear setup files')
        async def upload_dir(self, source_dir, target_dir): events.append('upload ' + target_dir)
        async def exec(self, command, **kw):
            events.append(command)
            return SimpleNamespace(return_code=1 if failure and command == 'bash /setup_files/setup.sh' else 0,
                                   stdout='', stderr='setup failed' if failure else '')
    class Trial:
        def _verifier_env_build_context(self, step): return task.paths.tests_dir
        @asynccontextmanager
        async def _separate_verifier_env(self, config, *, key, step_cfg=None):
            events.append('start fresh')
            try: yield Environment()
            finally: events.append('stop fresh')
    monkeypatch.setattr(module, 'Trial', Trial)
    fresh_verifier.install(); fresh_verifier.install()
    trial = Trial(); trial.task = task; trial._environment_build_timeout_sec = 10
    expected_context = task.paths.tests_dir if custom else task.paths.environment_dir
    assert trial._verifier_env_build_context(None) == expected_context
    async def run():
        async with trial._separate_verifier_env(None, key='test'):
            events.append('import submission')
            events.append('verifier setup and tests')
    if failure and not custom:
        with pytest.raises(RuntimeError, match='Fresh verifier task setup'): asyncio.run(run())
        assert 'import submission' not in events
    else:
        asyncio.run(run())
        if not custom:
            assert events.index('bash /setup_files/setup.sh') < events.index('import submission')
            assert events.index('upload /tests') < events.index('verifier setup and tests')
        else:
            assert 'bash /setup_files/setup.sh' not in events
    assert events[-1] == 'stop fresh'
