"""Task disk: measured, reserved against node disk and limited by task.toml storage."""
import asyncio
import os
import time
from types import SimpleNamespace

import pytest

from harbor_patches import task_disk
from harbor_patches import protected_step as p


def write(path, mib):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'wb') as f:
        f.write(os.urandom(mib * 2**20))


def test_used_bytes_counts_allocated_files(tmp_path):
    write(tmp_path / 'a' / 'big', 3)
    assert 3 * 2**20 <= task_disk.used_bytes(tmp_path) < 4 * 2**20


def test_ledger_waits_until_storage_fits_and_refuses_impossible(tmp_path, monkeypatch):
    free = {'value': 30 * 2**30}
    monkeypatch.setattr(task_disk.shutil, 'disk_usage', lambda root: SimpleNamespace(free=free['value']))
    monkeypatch.setenv('OT_TASK_DISK_MARGIN_BYTES', str(5 * 2**30))
    ledger = task_disk.Ledger(tmp_path)
    assert ledger.try_reserve('a', 10 * 2**30)[0]
    assert ledger.try_reserve('b', 10 * 2**30)[0]
    assert not ledger.try_reserve('c', 10 * 2**30)[0]  # 30 + 0 written - 5 margin = 25 < 30 reserved
    # What running tasks already wrote is part of their reservation, not lost capacity.
    free['value'] -= 8 * 2**30
    ledger.update('a', 8 * 2**30)
    assert not ledger.try_reserve('c', 10 * 2**30)[0]
    ledger.release('b')
    assert ledger.try_reserve('c', 10 * 2**30)[0]
    with pytest.raises(RuntimeError, match='did not fit'):
        ledger.reserve('d', 10 * 2**30, wait_sec=0)
    for key in ('a', 'c'):
        ledger.release(key)
    with pytest.raises(RuntimeError, match='exceeds the free node disk'):
        ledger.try_reserve('huge', 40 * 2**30)


def test_reservations_of_dead_processes_are_dropped(tmp_path, monkeypatch):
    monkeypatch.setattr(task_disk.shutil, 'disk_usage', lambda root: SimpleNamespace(free=20 * 2**30))
    monkeypatch.setenv('OT_TASK_DISK_MARGIN_BYTES', '0')
    ledger = task_disk.Ledger(tmp_path)
    ledger.path.write_text('{"gone": {"bytes": 21474836480, "used": 0, "pid": 999999999}}')
    assert ledger.try_reserve('new', 15 * 2**30)[0]


def test_watchdog_reports_peak_and_stops_at_the_limit(tmp_path, monkeypatch):
    monkeypatch.setenv('OT_TASK_DISK_POLL_SEC', '0.05')
    hits = []
    dog = task_disk.Watchdog(tmp_path, 2 * 2**20, lambda used, limit: hits.append((used, limit)), key='env')
    dog.start()
    write(tmp_path / 'x', 1)
    time.sleep(.3)
    assert not hits
    write(tmp_path / 'y', 2)
    deadline = time.time() + 5
    while not hits and time.time() < deadline:
        time.sleep(.05)
    dog.stop()
    assert hits and hits[0][1] == 2 * 2**20 and dog.peak >= 3 * 2**20
    assert 'Task disk limit exceeded' in task_disk.exceeded_message(*hits[0])


def controller(tmp_path, cfg):
    staging = tmp_path / 'staging' / 'apt_env'
    staging.mkdir(parents=True)

    class Inner:
        staging_base = str(tmp_path / 'staging')
        staging_dir = str(staging)
        def exec(self, payload):
            write(staging / 'overlay' / 'out', 3)
            time.sleep(.5)
            return {'return_code': 0, 'stdout': '', 'stderr': ''}
        def stop(self, payload):
            return {'stopped': True}

    obj = p.ProtectedInstance('env', '', str(tmp_path), 1000, instance_factory=lambda *a: Inner(), step_prefix=lambda *a: [])
    interrupted = []
    obj.slot = SimpleNamespace(interrupt=lambda tag: interrupted.append(tag), alive=lambda: True)
    obj.payload = {'task_env_config': cfg}
    obj._oom = lambda: False
    obj._close_step = lambda: None
    return obj, interrupted


def test_protected_instance_limits_declared_storage(tmp_path, monkeypatch):
    monkeypatch.setenv('OT_TASK_DISK_POLL_SEC', '0.05')
    monkeypatch.setenv('OT_TASK_DISK_MARGIN_BYTES', '0')
    obj, interrupted = controller(tmp_path, {'storage_mb': 2})
    obj._reserve_disk(obj.payload)
    obj._watch_disk(obj.payload)
    with pytest.raises(RuntimeError, match='Task disk limit exceeded: the task wrote 3 MiB'):
        obj.exec({'command': 'pip install big', 'timeout_sec': 5})
    assert interrupted == ['env']
    with pytest.raises(RuntimeError, match='Task disk limit exceeded'):
        obj.exec({'command': 'next'})
    result = obj.stop({})
    assert result['resources']['disk_limit_exceeded'] is True
    assert result['resources']['disk_limit_mb'] == 2.0 and result['resources']['disk_peak_mb'] >= 3
    assert task_disk.Ledger(tmp_path / 'staging').try_reserve('after', 1)[0]  # reservation released


def test_judges_and_tasks_without_storage_are_measured_but_not_stopped(tmp_path, monkeypatch):
    monkeypatch.setenv('OT_TASK_DISK_POLL_SEC', '0.05')
    for cfg in ({'storage_mb': 2, 'storage_limit': False}, {}):
        obj, interrupted = controller(tmp_path / str(len(cfg)), cfg)
        obj._reserve_disk(obj.payload)
        obj._watch_disk(obj.payload)
        assert obj.exec({'command': 'build', 'timeout_sec': 5})['return_code'] == 0
        resources = obj.stop({})['resources']
        assert not interrupted and resources['disk_peak_mb'] >= 3 and 'disk_limit_mb' not in resources


def test_client_sends_storage_and_judge_switch(monkeypatch):
    sent = []
    class Env:
        task_env_config = SimpleNamespace(storage_mb=10240)
        async def start(self):
            return await bridge._async_http_post('http://b/env/create', {'task_env_config': {'cpus': 1}})
    async def post(url, data):
        sent.append(data)
    bridge = SimpleNamespace(ApptainerEnvironment=Env, _async_http_post=post)
    task_disk.install_client(bridge)
    asyncio.run(Env().start())
    monkeypatch.setenv('OT_TASK_STORAGE_LIMIT', '0')
    asyncio.run(Env().start())
    assert [s['task_env_config'] for s in sent] == [{'cpus': 1, 'storage_mb': 10240, 'storage_limit': True},
                                                    {'cpus': 1, 'storage_mb': 10240, 'storage_limit': False}]


def test_results_record_disk_peak():
    from validation.run import usage
    record = {'trial_seconds': [3, 4], 'resources': [{'agent': {'disk_peak_mb': 120.0}}, {'agent': {'disk_peak_mb': 80.5}}]}
    assert usage('nop', record)['disk_peak_mb'] == 120.0


def test_capacity_cap_limits_reservations(tmp_path, monkeypatch):
    monkeypatch.setattr(task_disk.shutil, 'disk_usage', lambda root: SimpleNamespace(free=500 * 2**30))
    monkeypatch.setenv('OT_TASK_DISK_CAPACITY_BYTES', str(12 * 2**30))
    ledger = task_disk.Ledger(tmp_path)
    assert ledger.try_reserve('a', 10 * 2**30)[0]
    assert not ledger.try_reserve('b', 5 * 2**30)[0]
    assert ledger.try_reserve('c', 1 * 2**30)[0]
