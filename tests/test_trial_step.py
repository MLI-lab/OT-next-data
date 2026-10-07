"""Exercise the real lifecycle RPC/process boundary without requiring Slurm."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import threading

import pytest


@pytest.fixture
def proxy(tmp_path, monkeypatch):
    path = Path(__file__).resolve().parents[1] / 'harbor_patches/trial_step.py'
    spec = importlib.util.spec_from_file_location('trial_step_test', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    # Substitute only Slurm and the container backend. Socket transport, child
    # lifecycle, shutdown and failure propagation are the production code.
    launcher = tmp_path / 'step.py'
    launcher.write_text('''
import os,sys,json,types,runpy
class Instance:
    def __init__(self, *args):
        self.started=False; self.staging_dir=None
    def start(self, p):
        if p.get('fail'): raise ValueError('build failed')
        self.started=True
        return {'pid':os.getpid(), 'resources':p.get('task_env_config')}
    def exec(self, p):
        if p.get('die'): os._exit(7)
        return {'pid':os.getpid(), 'echo':p}
    upload=exec
    download=exec
    def stop(self,p):
        self.started=False
        if p.get('fail'): raise ValueError('stop failed')
        return {'stopped':True,'pid':os.getpid()}
fake=types.SimpleNamespace(configure_worker=lambda *a,**k:None,
                           worker=types.SimpleNamespace(ApptainerInstance=Instance))
sys.modules['bridge_worker']=fake
script,config=sys.argv[-2:]
from pathlib import Path
sys.path.insert(0,str(Path(script).parent))  # Match Python's direct script execution.
sys.argv=[script,config]
runpy.run_path(script,run_name='__main__')
''')
    # pytest paths may exceed Unix socket path length; use a short control root.
    import tempfile
    with tempfile.TemporaryDirectory(prefix='rpc-') as scratch:
        monkeypatch.setenv('TMPDIR', scratch)
        monkeypatch.setenv('OT_TRIAL_STEP_WAIT', '5')
        # One step per environment, as before steps were reused; `pool` below tests reuse.
        monkeypatch.setenv('OT_STEP_REUSE', '0')
        instance = mod.SlurmInstance(
            'test-env', str(tmp_path), str(tmp_path), 10000,
            step_prefix=lambda payload, env_id: [sys.executable, str(launcher)],
            start_gate=threading.Semaphore(2))
        instance.module, instance.launcher = mod, launcher
        yield instance
        instance._finish()
        mod.POOL.close_all()


@pytest.fixture
def pool(proxy, tmp_path, monkeypatch):
    """Environments that share steps; the 'cpus' of a payload stands for the task's limits."""
    monkeypatch.setenv('OT_STEP_REUSE', '1')
    mod, made = proxy.module, []
    def environment(name):
        instance = mod.SlurmInstance(
            name, str(tmp_path), str(tmp_path), 10000,
            step_prefix=lambda payload, env_id: [sys.executable, str(proxy.launcher), f"-c{payload.get('cpus', 1)}"],
            start_gate=threading.Semaphore(2))
        made.append(instance)
        return instance
    environment.pool = mod.POOL
    yield environment
    for instance in made:
        instance._finish()
    mod.POOL.close_all()


def test_all_operations_share_one_lifecycle_process(proxy):
    config = {'cpus': 2, 'memory_mb': 4096}
    started = proxy.start({'task_env_config': config})
    assert started['pid'] != os.getpid()
    assert started['resources'] == config
    for operation in ('exec', 'upload', 'download'):
        result = getattr(proxy, operation)({'value': 'preserved\ntext'})
        assert result == {'pid': started['pid'], 'echo': {'value': 'preserved\ntext'}}
    assert proxy.stop({}) == {'stopped': True, 'pid': started['pid']}
    assert proxy.process.poll() == 0
    assert not proxy.control.exists()


def test_build_failure_releases_the_step_without_unrestricted_fallback(proxy):
    with pytest.raises(RuntimeError, match='build failed'):
        proxy.start({'fail': True})
    assert proxy.process.poll() is not None
    assert not proxy.started
    assert not proxy.control.exists()


def test_stop_failure_still_reaps_the_step(proxy):
    proxy.start({})
    with pytest.raises(RuntimeError, match='stop failed'):
        proxy.stop({'fail': True})
    assert proxy.process.poll() is not None
    assert not proxy.control.exists()


def test_step_death_is_reported_to_caller(proxy):
    proxy.start({})
    with pytest.raises(RuntimeError, match='resource step exited during exec'):
        proxy.exec({'die': True})
    proxy.stop({})
    assert proxy.process.poll() == 7
    assert not proxy.control.exists()


def test_cleanup_consolidates_bounded_logs(proxy, capsys):
    proxy.start({})
    (proxy.control / 'step.log').write_text('old output\n' + 'x' * 10000 + '\nlast diagnostic\n')
    proxy.stop({})
    output = capsys.readouterr().out
    assert 'last diagnostic' in output and 'old output' not in output
    assert len(output) < 9000
    assert not proxy.control.exists()
    proxy._finish()  # Repeated cleanup is harmless.


def test_a_stop_that_arrives_before_the_start_finishes_is_not_lost():
    """The worker forgets a stop for a container it does not know yet; the registry remembers it."""
    import importlib.util, threading, time
    spec = importlib.util.spec_from_file_location('trial_step_registry', Path(__file__).resolve().parents[1] / 'harbor_patches/trial_step.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    registry = module.InstanceRegistry()
    stopped = threading.Event()
    class Instance:
        def stop(self, payload):
            stopped.set()
    # Normal order: registered, then stopped by the worker itself.
    registry['env-a'] = Instance()
    assert registry.pop('env-a', None) is not None and not registry.abandoned
    # The trial gave up first: the stop finds nothing, the late start must not stay registered.
    assert registry.pop('env-b', None) is None and 'env-b' in registry.abandoned
    registry['env-b'] = Instance()
    assert stopped.wait(5) and 'env-b' not in registry and 'env-b' not in registry.abandoned
    for n in range(registry.KEEP + 5):
        registry.pop(f'gone-{n}', None)
    assert len(registry.abandoned) == registry.KEEP


def test_the_next_task_with_the_same_limits_uses_the_same_step(pool):
    first = pool('env-1')
    step = first.start({'cpus': 1})['pid']
    assert first.stop({}) == {'stopped': True, 'pid': step}
    assert first.process.poll() is None and len(pool.pool.idle) == 1       # the step waits for the next task
    second = pool('env-2')
    assert second.start({'cpus': 1})['pid'] == step                          # same step, new container
    assert second.exec({'x': 1})['pid'] == step
    second.stop({})
    assert (pool.pool.created, pool.pool.reused) == (1, 1)


def test_a_task_with_other_limits_gets_its_own_step_and_the_idle_one_is_closed(pool):
    first = pool('env-1')
    step = first.start({'cpus': 1})['pid']
    first.stop({})
    second = pool('env-2')
    other = second.start({'cpus': 2})['pid']
    assert other != step and first.process.poll() is not None               # old step closed, not reused
    assert not first.control.exists() and pool.pool.idle == []
    second.stop({})
    assert [slot.key[-1] for slot in pool.pool.idle] == ['-c2']


def test_a_step_in_use_is_never_taken_or_closed(pool):
    first, second = pool('env-1'), pool('env-2')
    one = first.start({'cpus': 1})['pid']
    two = second.start({'cpus': 1})['pid']                                   # first is still running
    assert one != two and first.process.poll() is None
    third = pool('env-3')
    assert third.start({'cpus': 2})['pid'] not in (one, two)                 # nothing idle to close
    assert first.exec({})['pid'] == one and second.exec({})['pid'] == two
    for instance in (first, second, third):
        instance.stop({})


def test_a_failed_stop_or_a_dead_step_is_not_reused(pool):
    first = pool('env-1')
    step = first.start({'cpus': 1})['pid']
    with pytest.raises(RuntimeError, match='stop failed'):
        first.stop({'fail': True})
    assert first.process.poll() is not None and pool.pool.idle == []
    second = pool('env-2')
    fresh = second.start({'cpus': 1})['pid']
    assert fresh != step
    second.stop({})
    os.kill(fresh, 9)                                                        # the idle step dies
    second.process.wait(timeout=5)
    third = pool('env-3')
    assert third.start({'cpus': 1})['pid'] not in (step, fresh)
    third.stop({})
