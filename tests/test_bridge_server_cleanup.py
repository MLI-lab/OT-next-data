import importlib.util
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

spec = importlib.util.spec_from_file_location('bridge_server_patch', Path(__file__).resolve().parents[1] / 'harbor_patches/bridge_server.py')
patch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patch)


def server(job=None):
    stops = []
    return SimpleNamespace(
        _lock=threading.Lock(), _last_worker_poll=9999,
        _jobs={'job': job} if job else {},
        _envs={'env': {'state': 'ready', 'created': 0, 'last_used': 0}},
        _stats={'jobs_errors': 0}, ENV_READY='ready', ENV_PENDING='pending',
        ENV_STARTING='starting', ENV_STOPPED='stopped', ENV_STOPPING='stopping',
        JOB_STOP='stop', _submit_job=lambda *args: stops.append(args), stops=stops)


@pytest.mark.parametrize('state', ['pending', 'running'])
def test_long_active_command_is_not_reaped(state):
    s = server({'env_id': 'env', 'state': state, 'started': 0, 'payload': {'timeout_sec': 20000}})
    patch.cleanup_once(s, 10000, 3600, 50)
    assert not s.stops
    assert s._envs['env']['state'] == 'ready'


def test_completed_command_gets_idle_grace():
    s = server({'env_id': 'env', 'state': 'done', 'finished': 9990})
    patch.cleanup_once(s, 10000, 3600, 50)
    assert not s.stops
    patch.cleanup_once(s, 14000, 3600, 50)
    assert len(s.stops) == 1


def test_idle_orphan_is_reaped():
    s = server()
    patch.cleanup_once(s, 10000, 3600, 50)
    assert s.stops == [('env', 'stop', {'delete': True, 'reaped': True})]


def test_overdue_command_does_not_protect_orphan_forever():
    s = server({'env_id': 'env', 'state': 'running', 'started': 0, 'payload': {'timeout_sec': 600}})
    patch.cleanup_once(s, 10000, 3600, 50)
    assert s._stats['jobs_errors'] == 1
    assert len(s.stops) == 1
