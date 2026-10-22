from types import SimpleNamespace

import pytest

from hpc import model_startup as startup


def scenario(monkeypatch, failures, cleanup_failure=False):
    processes, launches, stops, waits = [], [], [], []
    record = {}

    def start(command, name):
        process = SimpleNamespace(poll=lambda: None)
        processes.append(process)
        launches.append((command, name))
        return process

    def ready(url, current, timeout):
        waits.append(timeout)
        assert len(current) == 1  # Failed launchers cannot poison the next wait.
        if len(waits) <= failures:
            raise RuntimeError('service readiness timed out')

    def stop(current, names):
        stops.append((list(current), set(names)))
        if cleanup_failure:
            raise RuntimeError('Slurm step still alive')

    monkeypatch.setattr(startup, 'stop_attempt', stop)
    def run():
        startup.start_with_retries([('vllm', ['srun', 'python', '-m', 'vllm'])],
            start=start, processes=processes, wait_ready=ready, url='http://localhost/health',
            timeout=600, retries=3, record=record, persist=lambda: None)
    return run, launches, stops, waits, record


def test_retry_preserves_logs_and_removes_failed_launchers(monkeypatch):
    run, launches, stops, waits, record = scenario(monkeypatch, 2)
    run()
    assert waits == [600, 600, 600]
    assert [name for command, name in launches] == ['vllm', 'vllm-attempt-2', 'vllm-attempt-3']
    assert len(stops) == 2
    assert record['startup_attempts'][-1]['status'] == 'ready'
    assert all(item['cleanup_confirmed'] for item in record['startup_attempts'][:-1])


def test_three_retries_then_exit_after_cleanup(monkeypatch):
    run, launches, stops, waits, record = scenario(monkeypatch, 4)
    with pytest.raises(RuntimeError, match='after 4 attempts'):
        run()
    assert len(launches) == len(stops) == 4
    assert all(item['cleanup_confirmed'] for item in record['startup_attempts'])


def test_no_retry_when_cleanup_is_unconfirmed(monkeypatch):
    run, launches, stops, waits, record = scenario(monkeypatch, 4, cleanup_failure=True)
    with pytest.raises(RuntimeError, match='refusing retry'):
        run()
    assert len(launches) == 1
    assert 'cleanup_error' in record['startup_attempts'][0]


def test_cleanup_targets_only_named_steps_in_this_allocation(monkeypatch):
    monkeypatch.setenv('SLURM_JOB_ID', '123')
    listings = iter(['123.1|ot-model-test\n123.2|other-service\n', '123.2|other-service\n'])
    def listing(command, **kwargs):
        assert command[command.index('--jobs') + 1] == '123'
        return next(listings)
    calls = []
    monkeypatch.setattr(startup.subprocess, 'check_output', listing)
    monkeypatch.setattr(startup.subprocess, 'run', lambda cmd, **kw: calls.append(cmd))
    monkeypatch.setattr(startup.time, 'sleep', lambda seconds: None)
    startup.stop_attempt([], {'ot-model-test'})
    assert calls == [['scancel', '--signal=KILL', '123.1']]
