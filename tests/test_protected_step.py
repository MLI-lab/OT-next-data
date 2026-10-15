"""Exercise controller recovery decisions and the real executor socket boundary."""
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace

import pytest

from harbor_patches import protected_step as p


def test_start_acquires_shared_gate_only_in_inner_instance(monkeypatch):
    import threading

    class Gate:
        def __init__(self):
            self.semaphore = threading.Semaphore(1)
            self.acquisitions = 0

        def __enter__(self):
            assert self.semaphore.acquire(timeout=.1), 'startup gate acquired twice'
            self.acquisitions += 1

        def __exit__(self, *args):
            self.semaphore.release()

    gate = Gate()

    class Inner:
        def start(self, payload):
            with gate:
                obj.start_command = ['apptainer', 'instance', 'start']
                return {'started': True}

    obj = p.ProtectedInstance('test', '', '', 1000,
                              instance_factory=lambda *args: Inner(), step_prefix=lambda *args: [])
    # The previous controller retained and acquired this same gate outside inner.start.
    obj.start_gate = gate
    monkeypatch.setattr(obj, '_new_step', lambda: setattr(obj, 'info', {}))
    monkeypatch.setattr(obj, '_oom', lambda: False)
    monkeypatch.setattr(obj, 'stop', lambda payload: None)
    assert obj.start({})['started']
    assert gate.acquisitions == 1


def controller(tmp_path, monkeypatch, *, oom, error=None):
    calls = []
    class Inner:
        def exec(self, payload):
            calls.append(payload)
            if error:
                raise error
            return {'return_code': 7, 'stdout': 'ordinary command failure', 'stderr': ''}
    obj = p.ProtectedInstance('test', '', str(tmp_path), 500000,
                              instance_factory=lambda *a: Inner(), step_prefix=lambda *a: [])
    monkeypatch.setattr(obj, '_oom', lambda: oom)
    recovered = []
    monkeypatch.setattr(obj, '_recover', lambda deadline: recovered.append(deadline))
    return obj, calls, recovered


def test_confirmed_oom_returns_feedback_without_replaying_command(tmp_path, monkeypatch):
    obj, calls, recovered = controller(tmp_path, monkeypatch, oom=True, error=ConnectionResetError())
    result = obj.exec({'command': p.AGENT_TAG + 'nonce\nmutate-files', 'timeout_sec': 20})
    assert len(calls) == len(recovered) == 1
    assert result['stdout'].startswith(p.OOM_PREFIX + 'nonce\n')
    assert 'NOT replayed' in result['stdout']


@pytest.mark.parametrize('error', [None, ConnectionResetError('local connection')])
def test_no_recovery_without_oom_evidence(tmp_path, monkeypatch, error):
    obj, calls, recovered = controller(tmp_path, monkeypatch, oom=False, error=error)
    clock = [0.0]
    monkeypatch.setattr(p.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(p.time, 'sleep', lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    payload = {'command': p.AGENT_TAG + 'nonce\nexit 137'}
    if error:
        with pytest.raises(ConnectionResetError):
            obj.exec(payload)
    else:
        assert obj.exec(payload)['return_code'] == 7
    assert len(calls) == 1 and not recovered


@pytest.mark.parametrize('evidence', ['counter', 'log'])
def test_delayed_oom_evidence_prevents_terminal_protocol_error(tmp_path, monkeypatch, evidence):
    obj, calls, recovered = controller(tmp_path, monkeypatch, oom=False)
    monkeypatch.delattr(obj, '_oom')
    events = tmp_path / 'memory.events'
    events.write_text('oom_kill 0\n')
    obj.info = {'memory_events': str(events)}
    obj.oom_baseline = 0
    log = ['']
    obj.slot = SimpleNamespace(log_tail=lambda: log[0])
    clock = [0.0]
    monkeypatch.setattr(p.time, 'monotonic', lambda: clock[0])
    def advance(seconds):
        clock[0] += seconds
        if clock[0] >= .3:
            if evidence == 'counter':
                events.write_text('oom_kill 1\n')
            else:
                events.unlink(missing_ok=True)
                log[0] = 'Detected 1 oom_kill event in this step'
    monkeypatch.setattr(p.time, 'sleep', advance)
    result = obj.exec({'command': p.AGENT_TAG + 'nonce\nmutate-files', 'timeout_sec': 20})
    assert result['stdout'].startswith(p.OOM_PREFIX + 'nonce\n')
    assert len(calls) == len(recovered) == 1
    assert .3 <= clock[0] < 2


@pytest.mark.parametrize('version', [1, 2])
def test_memory_counter_never_uses_parent_job_limit(tmp_path, monkeypatch, version):
    monkeypatch.setenv('SLURM_STEP_ID', '3')
    membership = tmp_path / 'membership'
    root = tmp_path / 'cgroup'
    group = '/slurm/job_7/step_3/task_0'
    if version == 1:
        membership.write_text('0::/system.slice/slurmd.service\n4:memory:' + group + '\n')
        path = root / 'memory' / group.lstrip('/')
        limit, events, unlimited = 'memory.limit_in_bytes', 'memory.oom_control', str((1 << 63) - 4096)
    else:
        membership.write_text('0::' + group + '\n')
        path = root / group.lstrip('/')
        limit, events, unlimited = 'memory.max', 'memory.events', 'max'
    path.mkdir(parents=True)
    (path / limit).write_text(unlimited)
    (path / events).write_text('oom_kill 0\n')
    (path.parent / limit).write_text('4294967296')
    (path.parent / events).write_text('oom_kill 2\n')
    assert p.memory_events_path(membership, root) == str(path.parent / events)
    (path.parent / limit).write_text(unlimited)
    (path.parent.parent / limit).write_text('128849018880')
    (path.parent.parent / events).write_text('oom_kill 10\n')
    with pytest.raises(RuntimeError, match='enforcing task-step'):
        p.memory_events_path(membership, root)


def test_setup_oom_is_not_replayed_or_converted_to_agent_feedback(tmp_path, monkeypatch):
    obj, calls, recovered = controller(tmp_path, monkeypatch, oom=True)
    with pytest.raises(RuntimeError, match='outside a recoverable'):
        obj.exec({'command': 'setup.sh'})
    assert len(calls) == 1 and not recovered


def test_cancel_interrupts_busy_command_preserves_files_and_fences_queue(tmp_path, monkeypatch):
    import threading
    entered, interrupted = threading.Event(), threading.Event()
    written = tmp_path / 'answer'
    written.write_text('agent answer')
    class Inner:
        def exec(self, payload):
            entered.set()
            assert interrupted.wait(3)
            return {'return_code': 1}
    obj = p.ProtectedInstance('test', '', '', 1000,
        instance_factory=lambda *a: Inner(), step_prefix=lambda *a: [])
    obj.slot = SimpleNamespace(interrupt=lambda tag: interrupted.set(),
                               process=SimpleNamespace(poll=lambda: 0))
    monkeypatch.setattr(obj, '_confirmed_exec_oom', lambda *a: False)
    monkeypatch.setattr(obj, '_close_step', lambda: None)
    reopened = []
    monkeypatch.setattr(obj, '_recover', lambda deadline: reopened.append(written.read_text()))
    thread = threading.Thread(target=lambda: obj.exec({'command': 'busy'}))
    thread.start()
    assert entered.wait(2)
    obj.cancel_commands(1)
    thread.join(2)
    assert not thread.is_alive() and written.read_text() == 'agent answer'
    with pytest.raises(RuntimeError, match='before session cancellation'):
        obj.exec({'command': 'queued earlier', 'command_generation': 0})
    assert not reopened
    obj.exec({'command': 'grade', 'command_generation': 1})
    assert reopened == ['agent answer']


def test_command_timeout_kills_child_group_and_keeps_partial_output(tmp_path):
    marker = tmp_path / 'leaked-child'
    command = [sys.executable, '-c',
               'import subprocess,sys,time; '
               'subprocess.Popen([sys.executable,"-c",'
               + repr('import time,pathlib; time.sleep(.5); pathlib.Path(' + repr(str(marker)) + ').touch()')
               + ']); print("before timeout",flush=True); time.sleep(30)']
    result = p.retained_run(command, {'capture_output': True, 'text': True, 'timeout': .2}, tmp_path)
    assert result['timeout'] and result['stdout'] == 'before timeout\n'
    time.sleep(.6)
    assert not marker.exists()


@pytest.fixture
def executor(tmp_path, monkeypatch):
    with tempfile.TemporaryDirectory(prefix='ot-exec-') as control:
        control = Path(control)
        events = control / 'events'
        events.write_text('oom_kill 0\n')
        launcher = control / 'launcher.py'
        launcher.write_text('import sys\nfrom harbor_patches import protected_step as p\n'
                            f'p.memory_events_path=lambda: {str(events)!r}\n'
                            'p.serve_executor(sys.argv[-1])\n')
        monkeypatch.setenv('TMPDIR', str(control))
        monkeypatch.setenv('PYTHONPATH', str(Path(__file__).resolve().parents[1]))
        command = [sys.executable, str(launcher)]
        slot = p.StepSlot(command)
        try:
            until = time.monotonic() + 5
            while not slot.ready():
                assert slot.process.poll() is None, slot.log_tail()
                assert time.monotonic() < until
                time.sleep(.02)
            yield slot
        finally:
            slot.close('test')


def test_executor_preserves_binary_results_timeouts_and_background_processes(executor):
    response = executor.request('test', 'run', {'cmd': [sys.executable, '-c', 'import os;os.write(1,b"\\xff")'],
                                'kwargs': p.options({'capture_output': True})}, 5)
    assert p.unpack(response)['stdout'] == b'\xff'
    response = executor.request('test', 'run', {'cmd': [sys.executable, '-c', 'import time;time.sleep(5)'],
                                'kwargs': {'capture_output': True, 'timeout': .05}}, 5)
    assert response['timeout'] is True
    proc = p.RemoteProcess(executor, [sys.executable, '-c', 'import time;time.sleep(20)'], {})
    assert proc.poll() is None
    proc.terminate()
    assert proc.wait(5) != 0


def test_interrupt_stops_real_executor_without_removing_control_files(executor):
    executor.interrupt('test')
    executor.process.wait(timeout=5)
    assert executor.control.exists()
    executor.interrupt('test')  # Safe when a concurrent exit has already won.


def test_cgroup_counter_is_not_inferred_from_exit_code(tmp_path):
    events = tmp_path / 'memory.events'
    events.write_text('oom 2\noom_kill 1\n')
    assert p.oom_count(events) == 1
    assert p.oom_count(tmp_path / 'gone') is None


@pytest.mark.parametrize('kind', ['missing-overlay', 'volatile-overlay', 'missing-bind'])
def test_recovery_refuses_a_fresh_or_incomplete_filesystem(tmp_path, monkeypatch, kind):
    pytest.importorskip('harbor')
    obj, calls, recovered = controller(tmp_path, monkeypatch, oom=True)
    # Use the real recovery method, with only the old step's teardown substituted.
    monkeypatch.delattr(obj, '_recover')
    monkeypatch.setattr(obj, '_close_step', lambda: None)
    monkeypatch.setattr(obj, 'start_anchor', lambda inner: None)
    monkeypatch.setattr(obj, 'stop_anchor', lambda inner: None)
    obj.slot = SimpleNamespace(process=SimpleNamespace(poll=lambda: 0))
    overlay = tmp_path / 'overlay'
    if kind != 'missing-overlay':
        overlay.mkdir()
    obj.start_command = ['apptainer', 'instance', 'start', '--overlay', str(overlay)]
    if kind == 'volatile-overlay':
        obj.start_command += ['--writable-tmpfs']
    if kind == 'missing-bind':
        obj.start_command += ['--bind', str(tmp_path / 'lost-workspace') + ':/workspace:rw']
    obj.start_command += ['image.sif', 'test']
    monkeypatch.setattr(obj, '_new_step', lambda **kw: pytest.fail('must not start a replacement'))
    with pytest.raises(RuntimeError, match='Cannot recover OOM'):
        obj._recover(time.monotonic() + 30)


def test_dead_anchor_exposes_stable_returncode():
    from types import SimpleNamespace
    slot = SimpleNamespace(alive=lambda: False, request=lambda *a: {'pid': 42})
    proc = p.RemoteProcess(slot, ['anchor'], {})
    assert proc.poll() == -9 and proc.returncode == -9
    assert proc.wait(0) == -9


@pytest.mark.parametrize('ready', [False, True])
def test_abandoned_start_does_not_keep_waiting_for_slurm(tmp_path, monkeypatch, ready):
    obj, _, _ = controller(tmp_path, monkeypatch, oom=False)
    obj.step_prefix = lambda *a: ['srun']
    obj.is_abandoned = lambda env_id: True
    monkeypatch.setattr(p, 'StepSlot', lambda *a: SimpleNamespace(ready=lambda: ready))
    with pytest.raises(RuntimeError, match='stopped'):
        obj._new_step()


def test_recovery_subprocesses_share_remaining_budget(tmp_path, monkeypatch):
    obj, _, _ = controller(tmp_path, monkeypatch, oom=False)
    calls = []
    obj.slot = SimpleNamespace(request=lambda *args: calls.append(args) or {'returncode': 0})
    obj.recovery_deadline = time.monotonic() + 2
    obj.remote_run(['apptainer', 'instance', 'start'], {'timeout': 120})
    assert 0 < calls[0][2]['kwargs']['timeout'] <= 2
    obj.recovery_deadline = time.monotonic() - 1
    with pytest.raises(TimeoutError, match='budget exhausted'):
        obj.remote_run(['apptainer', 'exec'], {'timeout': 120})
    assert len(calls) == 1


def test_stop_interrupts_command_before_waiting_for_controller_lock(tmp_path, monkeypatch):
    import threading
    entered, interrupted, released = threading.Event(), threading.Event(), threading.Event()
    calls = []
    class Inner:
        staging_dir = tmp_path / 'staging'
        def exec(self, payload):
            entered.set()
            assert interrupted.wait(3), 'stop is blocked behind the running command'
            released.set()
            return {'return_code': 1, 'stdout': '', 'stderr': 'interrupted'}
    Inner.staging_dir.mkdir()
    obj = p.ProtectedInstance('test', '', '', 1000,
        instance_factory=lambda *a: Inner(), step_prefix=lambda *a: [])
    obj.slot = SimpleNamespace(interrupt=lambda tag: (calls.append(tag), interrupted.set()), alive=lambda: False)
    monkeypatch.setattr(obj, '_oom', lambda: False)
    def close():
        assert released.is_set(), 'cannot clean files while command still owns the lock'
        calls.append('close')
    monkeypatch.setattr(obj, '_close_step', close)
    command = threading.Thread(target=lambda: obj.exec({'command': 'blocked verifier'}))
    command.start()
    assert entered.wait(1)
    try:
        assert obj.stop({}) == {'stopped': True}
    finally:
        interrupted.set()
        command.join(3)
    assert not command.is_alive()
    assert calls == ['test', 'close']
    assert not Inner.staging_dir.exists()
    assert obj.slot is None
