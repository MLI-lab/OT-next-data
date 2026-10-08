import asyncio
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from validation.stages import task_setup


def test_start_and_upload_consume_the_setup_budget(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(task_setup.time, 'monotonic', lambda: clock[0])
    calls = []
    class Environment:
        async def start(self, **kwargs):
            calls.append(('start', kwargs))
            clock[0] += 400
        async def exec(self, command, **kwargs):
            calls.append(('setup', kwargs))
            clock[0] += 12
            return SimpleNamespace(return_code=0, stdout='', stderr='')
    async def upload():
        calls.append(('upload', {}))
        clock[0] += 50
    timings = {}
    asyncio.run(task_setup.prepare(Environment(), command='bash /setup_files/setup.sh',
        timeout_sec=600, force_build=True, upload=upload, timings=timings))
    assert calls == [('start', {'force_build': True}), ('upload', {}),
                     ('setup', {'timeout_sec': 150, 'user': 'root'})]
    assert timings == {'container_start': 400, 'setup_upload': 50, 'setup_execution': 12}


@pytest.mark.parametrize('failure', ['startup', 'upload', 'setup', 'nonzero'])
def test_shared_budget_stops_preparation(failure):
    calls = []
    class Environment:
        async def start(self, **kwargs):
            calls.append('start')
            if failure == 'startup':
                await asyncio.sleep(1)
        async def exec(self, command, **kwargs):
            calls.append('setup')
            if failure == 'setup':
                await asyncio.sleep(1)
            return SimpleNamespace(return_code=1, stdout='', stderr='bad package')
    async def upload():
        calls.append('upload')
        if failure == 'upload':
            await asyncio.sleep(1)
    async def run():
        await asyncio.wait_for(task_setup.prepare(Environment(), command='setup',
            timeout_sec=.02, force_build=False, upload=upload), timeout=.02)
    with pytest.raises(RuntimeError if failure == 'nonzero' else TimeoutError):
        asyncio.run(run())
    if failure in ('startup', 'upload'):
        assert 'setup' not in calls


@pytest.mark.parametrize('agent', ['nop', 'oracle', 'terminus-2'])
def test_trial_prepares_before_agent_and_preserves_setup_state(tmp_path, monkeypatch, agent):
    from harbor.trial import trial as trial_module
    calls = []
    (tmp_path / 'solution').mkdir()
    (tmp_path / 'setup_files').mkdir()
    (tmp_path / 'solution/solve.sh').write_text('bash /setup_files/setup.sh || exit $?\n')
    (tmp_path / 'setup_files/setup.sh').write_text('echo setup')
    class Environment:
        async def start(self, **kwargs): calls.append('start')
        async def exec(self, command, **kwargs):
            calls.append('setup')
            return SimpleNamespace(return_code=0, stdout='complete', stderr='')
    class Trial:
        async def _start_agent_environment(self): calls.append('original-start')
        async def _upload_setup_files(self): calls.append('upload')
        async def _await_phase(self, awaitable, *, timeout_sec, timeout_error):
            assert timeout_sec == 600
            await awaitable
    monkeypatch.setattr(trial_module, 'Trial', Trial)
    task_setup.install()
    task_setup.install()  # Installation must not nest the preparation phase.
    trial = Trial()
    trial.task = SimpleNamespace(paths=SimpleNamespace(task_dir=tmp_path))
    trial.config = SimpleNamespace(environment=SimpleNamespace(force_build=False), agent=SimpleNamespace(name=agent))
    trial.agent_environment = Environment()
    trial._environment_build_timeout_sec = 600
    trial.logger = logging.getLogger('test')
    async def run():
        await trial._start_agent_environment()
        await trial._upload_setup_files()
        calls.append(agent)
    asyncio.run(run())
    assert calls == ['start', 'upload', 'setup', agent]
    # A new attempt gets a fresh preparation, not the previous attempt's flag.
    asyncio.run(run())
    assert calls == ['start', 'upload', 'setup', agent] * 2


def test_tasks_without_setup_keep_original_start(tmp_path, monkeypatch):
    from harbor.trial import trial as trial_module
    calls = []
    class Trial:
        async def _start_agent_environment(self): calls.append('original-start')
        async def _upload_setup_files(self): calls.append('original-upload')
    monkeypatch.setattr(trial_module, 'Trial', Trial)
    task_setup.install()
    trial = Trial()
    trial.task = SimpleNamespace(paths=SimpleNamespace(task_dir=tmp_path))
    asyncio.run(trial._start_agent_environment())
    asyncio.run(trial._upload_setup_files())
    assert calls == ['original-start', 'original-upload']
