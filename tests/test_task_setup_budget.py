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


def test_start_without_setup_does_not_charge_waiting_to_the_budget(tmp_path, monkeypatch):
    from harbor.trial import trial as trial_module
    from harbor_patches import start_wait
    started = []
    class Environment:
        _env_id = _bridge_url = None
        async def start(self, **kwargs):
            started.append(kwargs)
    class Trial:
        async def _start_agent_environment(self): raise AssertionError('upstream start charges waiting time')
        async def _upload_setup_files(self): pass
    monkeypatch.setattr(trial_module, 'Trial', Trial)
    task_setup.install()
    trial = Trial()
    trial.task = SimpleNamespace(paths=SimpleNamespace(task_dir=tmp_path))
    trial._environment_build_timeout_sec = 600
    trial.agent_environment = Environment()
    trial.config = SimpleNamespace(environment=SimpleNamespace(force_build=False))
    trial.logger = logging.getLogger('test')
    asyncio.run(trial._start_agent_environment())
    assert started == [{'force_build': False}]


def test_setup_budget_is_extended_by_the_reported_wait(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(task_setup.time, 'monotonic', lambda: clock[0])
    seen = {}
    class Environment:
        async def start(self, **kwargs):
            clock[0] += 500                      # 450 s of it waiting for a Slurm step
        async def exec(self, command, **kwargs):
            seen.update(kwargs)
            return SimpleNamespace(return_code=0, stdout='', stderr='')
    async def upload(): pass
    async def waited(): return 450.0
    timings = {}
    asyncio.run(task_setup.prepare(Environment(), command='setup', timeout_sec=600, force_build=False,
                                   upload=upload, timings=timings, waited=waited))
    assert seen['timeout_sec'] == 550 and timings['start_wait'] == 450.0


def test_failed_setup_records_container_diagnostics():
    calls = []
    class Environment:
        async def start(self, **kwargs): pass
        async def exec(self, command, **kwargs):
            calls.append(command)
            if command == 'setup':
                return SimpleNamespace(return_code=1, stdout='', stderr="No module named 'keyword'")
            return SimpleNamespace(return_code=0, stdout='== mounts\noverlay / overlay rw\n', stderr='')
    async def upload(): pass
    with pytest.raises(RuntimeError, match='Task setup exited 1') as caught:
        asyncio.run(task_setup.prepare(Environment(), command='setup', timeout_sec=60, force_build=False, upload=upload))
    assert caught.value.diagnostics.startswith('== mounts') and calls[1] == task_setup.SETUP_DIAGNOSTICS


@pytest.mark.parametrize('python_section,damaged', [
    ('3.9.18 stdlib ok\npython3.9\n212\n', False),
    ("ModuleNotFoundError: No module named 'keyword'\nOT_PYTHON_STDLIB_IMPORT_FAILED:/opt/conda/bin/python\n", True),
    ("", False),
    ("OT_PYTHON_PROBE:/usr/bin/python3\n3.12 stdlib ok\n", False),
])
def test_setup_failure_on_a_broken_interpreter_is_marked_as_environment_damage(python_section, damaged):
    from validation.publishing.publish import judge
    class Environment:
        async def start(self, **kwargs): pass
        async def exec(self, command, **kwargs):
            if command == 'setup':
                return SimpleNamespace(return_code=1, stdout='', stderr='ERROR: Could not install packages')
            return SimpleNamespace(return_code=0, stdout='== mounts\n== space\n== python\n' + python_section + '== processes\n', stderr='')
    async def upload(): pass
    with pytest.raises(RuntimeError) as caught:
        asyncio.run(task_setup.prepare(Environment(), command='setup', timeout_sec=60, force_build=False, upload=upload))
    assert str(caught.value).startswith(task_setup.DAMAGED) is damaged
    item = {'status': 'error', 'environments': [{'status': 'error', 'error': str(caught.value)}]}
    assert judge(3, item)[0] == ('not_run' if damaged else 'archive')
