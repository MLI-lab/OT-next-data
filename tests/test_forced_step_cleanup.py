"""Forced cancellation must bypass locks and stay inside its owning step."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from harbor_patches import protected_step as protected
from harbor_patches import trial_step as steps


def fake_slot(job='123', step='7'):
    slot = object.__new__(steps.StepSlot)
    slot.slurm_job, slot.slurm_step = job, step
    slot.process = SimpleNamespace(poll=lambda: 0)
    return slot


def test_forced_stop_targets_only_verified_step_and_waits_for_absence(monkeypatch):
    slot = fake_slot()
    calls = []
    listings = iter(['100_2.7\n100_2.8\n', '100_2.8\n'])
    def cancel(cmd, **kwargs):
        calls.append(cmd)
        return SimpleNamespace(stderr='', returncode=0)
    def listing(cmd, **kwargs):
        assert cmd[cmd.index('--jobs') + 1] == '123'
        return next(listings)
    monkeypatch.setattr(steps.subprocess, 'run', cancel)
    monkeypatch.setattr(steps.subprocess, 'check_output', listing)
    monkeypatch.setattr(steps.time, 'sleep', lambda seconds: None)
    slot.force_stop('test')
    assert calls == [['scancel', '--signal=KILL', '123.7']]


@pytest.mark.parametrize('job,step', [('123', None), ('123', 'batch'), ('123', '7,8'), ('123,999', '7')])
def test_unverified_step_cannot_trigger_cancel(monkeypatch, job, step):
    monkeypatch.setattr(steps.subprocess, 'run', lambda *a, **kw: pytest.fail('must not signal anything'))
    with pytest.raises(RuntimeError, match='unverified'):
        fake_slot(job, step).force_stop()


def test_step_remaining_alive_is_not_reported_as_cleaned_up(monkeypatch):
    monkeypatch.setattr(steps.subprocess, 'run', lambda *a, **kw: SimpleNamespace(stderr='still alive'))
    monkeypatch.setattr(steps.subprocess, 'check_output', lambda *a, **kw: '123.7\n')
    with pytest.raises(RuntimeError, match='remains alive after KILL'):
        fake_slot().force_stop(timeout=0)


def test_concurrent_normal_exit_is_safe(monkeypatch):
    monkeypatch.setattr(steps.subprocess, 'run', lambda *a, **kw: SimpleNamespace(stderr='Invalid job id', returncode=1))
    monkeypatch.setattr(steps.subprocess, 'check_output', lambda *a, **kw: '')
    fake_slot().force_stop()


@pytest.mark.parametrize('operation', ['cancel', 'stop'])
def test_lock_timeout_forces_real_stubborn_process_without_touching_sibling(tmp_path, monkeypatch, operation):
    ready = tmp_path / 'ready'
    code = ('import signal,time,pathlib; signal.signal(signal.SIGTERM,signal.SIG_IGN); '
            f'pathlib.Path({str(ready)!r}).touch(); time.sleep(120)')
    process = subprocess.Popen([sys.executable, '-c', code], start_new_session=True)
    sibling = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(120)'], start_new_session=True)
    slot = fake_slot(job=None, step=None)
    slot.process = process
    slot.control = tmp_path / 'control'
    slot.control.mkdir()
    slot.ready = lambda: False
    written = tmp_path / 'answer'
    written.write_text('keep the submitted solution')
    actual_lock = threading.RLock()
    entered = threading.Event()
    class Lock:
        def acquire(self, timeout):
            # Exercise the expired grace period without spending15s per test.
            if timeout in (15, .1, 10):
                return False
            return actual_lock.acquire(timeout=timeout)
        def release(self):
            actual_lock.release()
    obj = protected.ProtectedInstance('test', '', '', 100,
        instance_factory=lambda *a: SimpleNamespace(), step_prefix=lambda *a: [])
    obj.slot, obj.lock = slot, Lock()
    closed = []
    monkeypatch.setattr(obj, '_close_step', lambda: closed.append(True))
    def blocked_operation():
        with actual_lock:
            entered.set()
            process.wait(timeout=10)
    thread = threading.Thread(target=blocked_operation)
    try:
        # Shared-cluster interpreter/filesystem startup can take several
        # seconds; this is setup, not the cancellation deadline under test.
        until = time.monotonic() + 30
        while not ready.exists():
            assert time.monotonic() < until
            time.sleep(.01)
        thread.start()
        assert entered.wait(2)
        if operation == 'cancel':
            obj.cancel_commands(1)
        else:
            obj.stop({})
        thread.join(2)
        assert not thread.is_alive()
        assert process.returncode == -signal.SIGKILL
        assert sibling.poll() is None
        assert closed == [True]
        assert written.read_text() == 'keep the submitted solution'
        assert slot.control.exists()  # Forced stop never deletes evidence.
    finally:
        for proc in (process, sibling):
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGKILL)
            proc.wait(timeout=5)
        if thread.ident:
            thread.join(2)
