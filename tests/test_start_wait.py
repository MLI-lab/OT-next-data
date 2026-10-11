"""Waiting for a worker or a Slurm step is not part of a container's start budget."""
import asyncio
import importlib.util
from pathlib import Path
import types

import pytest

from harbor_patches import start_wait
from harbor_patches.start_wait import StartWaitTimeout, record_phase, run_with_budget, waited_seconds
from validation.publishing.publish import judge


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def drive(clock, script, **kwargs):
    """Run a start that takes ``script['total']`` fake seconds, of which ``wait`` are waiting."""
    async def waited():
        return min(clock.now, script['wait'])

    async def start():
        while clock.now < script['total']:
            await asyncio.sleep(0)
        return 'started'

    async def main():
        real_wait = asyncio.wait

        async def fake_wait(tasks, timeout):
            clock.now += timeout          # the poll interval passes
            return await real_wait(tasks, timeout=0.01)
        start_wait.asyncio.wait, saved = fake_wait, start_wait.asyncio.wait
        try:
            return await run_with_budget(start(), kwargs.pop('budget', 600), waited, clock=clock, poll=50, **kwargs)
        finally:
            start_wait.asyncio.wait = saved
    return asyncio.run(main())


def test_waiting_time_extends_the_budget():
    # 900 s of waiting plus 300 s of work passes a 600 s budget.
    recorded = []
    assert drive(Clock(), {'wait': 900, 'total': 1200}, record=recorded.append) == 'started'
    assert recorded == [900]


def test_working_time_beyond_the_budget_still_times_out():
    error = TimeoutError('Environment build/start and task setup timed out after 600 seconds')
    with pytest.raises(TimeoutError) as caught:
        drive(Clock(), {'wait': 100, 'total': 5000}, timeout_error=error)
    assert caught.value is error


def test_waiting_beyond_its_own_limit_is_a_distinct_failure():
    with pytest.raises(StartWaitTimeout) as caught:
        drive(Clock(), {'wait': 10_000, 'total': 20_000}, wait_limit=3600)
    assert 'for a worker or Slurm step' in str(caught.value) and 'never started' in str(caught.value)


def test_server_accumulates_queue_and_step_waits():
    env = {'state': 'pending', 'created': 100.0}
    assert waited_seconds(env, 130.0) == 30.0                     # queued for a worker
    env['state'] = 'starting'
    assert waited_seconds(env, 140.0, queued_seconds=35.0) == 35.0  # worker took it after 35 s
    record_phase(env, True, 140.0)
    record_phase(env, True, 150.0)                                # repeated report keeps the first start
    assert waited_seconds(env, 200.0) == 35.0 + 60.0              # ongoing step wait
    record_phase(env, False, 260.0)
    assert waited_seconds(env, 900.0) == 35.0 + 120.0
    record_phase(env, False, 999.0)                               # stray end report changes nothing
    assert env['waited'] == 120.0 and 'wait_since' not in env


def test_worker_reports_and_logs_a_slow_wait(capsys):
    clock, posts = Clock(), []
    start_wait.configure('http://bridge:1/')
    try:
        with start_wait.waiting('env-1', 'its Slurm step', clock=clock, post=lambda url, data: posts.append((url, data)),
                                detail=lambda: 'srun: step creation temporarily disabled'):
            clock.now = 42.0
        with start_wait.waiting(None, 'the container start gate', clock=clock, post=lambda *a: posts.append(a)):
            pass                                                   # no environment: nothing reported
    finally:
        start_wait.configure(None)
    assert posts == [('http://bridge:1/worker/phase', {'env_id': 'env-1', 'waiting': True}),
                     ('http://bridge:1/worker/phase', {'env_id': 'env-1', 'waiting': False})]
    assert '[env-1] waited 42.0s for its Slurm step: srun: step creation temporarily disabled' in capsys.readouterr().out


def test_current_environment_is_scoped_to_the_start():
    assert start_wait.current_env() is None
    with start_wait.starting('env-9'):
        assert start_wait.current_env() == 'env-9'
    assert start_wait.current_env() is None


def test_srun_messages_are_no_longer_suppressed(monkeypatch):
    pytest.importorskip('harbor')
    from harbor_patches import bridge_worker
    monkeypatch.setenv('SLURM_JOB_ID', '1')
    monkeypatch.delenv('SLURM_STEP_ID', raising=False)
    monkeypatch.delenv('OT_TRIAL_SRUN', raising=False)
    command = bridge_worker.trial_step_prefix({'task_env_config': {'cpus': 4, 'memory_mb': 4096}}, 'env-1')
    assert command[0] == 'srun' and '--quiet' not in command and '--exact' in command


@pytest.mark.parametrize('error,expected', [
    ('', 'not_run'),                                                                   # reports before the cause was recorded
    ('Environment start and task setup timed out after 600 seconds (plus 0 s waiting for a worker or Slurm step)', 'not_run'),
    ('Container start waited 3600 s for a worker or Slurm step (limit 3600 s); the container itself never started', 'not_run'),
    ('TimeoutError during start', 'not_run'),
    ('Task setup exited 1: ERROR: No matching distribution found for vega==0.4.4', 'archive'),
])
def test_start_time_limits_are_not_a_verdict_on_the_task(error, expected):
    item = {'status': 'error', 'environments': [{'environment': 'agent', 'status': 'error', 'error': error,
                                                 'timings_seconds': {'start': 600.0}}]}
    assert judge(3, item)[0] == expected


def test_bridge_server_reports_waiting(monkeypatch):
    harbor = pytest.importorskip('harbor')
    from harbor_patches import bridge_server
    path = Path(harbor.__file__).parent / 'environments/apptainer/server.py'
    spec = importlib.util.spec_from_file_location('ot_test_bridge_server', path)
    server = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(server)
    bridge_server.install_transport(server)
    handler = server.BridgeHandler.__new__(server.BridgeHandler)
    sent = []
    handler._json = lambda data, status=200: sent.append((data, status))
    now = bridge_server.time.time()
    server._envs['env-a'] = {'state': 'pending', 'created': now - 50, 'task_name': 't', 'worker_id': None}
    server._jobs['j-a'] = {'job_id': 'j-a', 'env_id': 'env-a', 'type': server.JOB_START, 'state': 'pending',
                           'submitted': now - 50, 'payload': {}}
    handler._handle_job_result_poll('j-a')
    assert sent[-1][0] == {'state': 'pending', 'waiting': True}
    handler._handle_env_status('env-a')
    assert 49 <= sent[-1][0]['waited_seconds'] <= 52
    # A worker takes the job, then waits for its step.
    server._jobs['j-a'].update(state='running', started=now - 10)
    server._envs['env-a'].update(state='starting', worker_id='w')
    handler._handle_job_result_poll('j-a')
    assert sent[-1][0] == {'state': 'running'}
    start_wait.record_phase(server._envs['env-a'], True, now - 8)
    handler._handle_job_result_poll('j-a')
    assert sent[-1][0] == {'state': 'running', 'waiting': True}
    handler._handle_env_status('env-a')
    assert 47 <= sent[-1][0]['waited_seconds'] <= 50          # 40 s queued + 8 s for the step
    # Exec jobs never carry the flag.
    server._jobs['j-b'] = {'job_id': 'j-b', 'env_id': 'env-a', 'type': 'exec', 'state': 'pending', 'submitted': now, 'payload': {}}
    handler._handle_job_result_poll('j-b')
    assert sent[-1][0] == {'state': 'pending'}
