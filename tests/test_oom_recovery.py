import asyncio
from types import SimpleNamespace

import pytest

from harbor_patches import oom_recovery as patch
from harbor_patches.protected_step import AGENT_TAG, OOM_PREFIX


@pytest.fixture
def terminal(monkeypatch):
    pytest.importorskip('harbor')
    from harbor.environments.apptainer.apptainer import ApptainerEnvironment
    from harbor.environments.base import ExecResult
    from harbor.agents.terminus_2.tmux_session import TmuxSession
    from harbor.agents.terminus_2.terminus_2 import Terminus2
    calls = []
    async def execute(self, command, *args, **kwargs):
        calls.append(command)
        token = next((line[len(AGENT_TAG):] for line in command.splitlines() if line.startswith(AGENT_TAG)), None)
        if token:
            return ExecResult(stdout=OOM_PREFIX + token + '\nfeedback', stderr='', return_code=0)
        return ExecResult(stdout='', stderr='', return_code=0)
    monkeypatch.setattr(ApptainerEnvironment, 'exec', execute)
    monkeypatch.setattr(ApptainerEnvironment, '_ot_oom_feedback', False, raising=False)
    monkeypatch.setattr(Terminus2, 'run', Terminus2.run)
    for name in ('send_keys_and_capture', 'capture_pane', 'send_keys', 'is_session_alive'):
        monkeypatch.setattr(TmuxSession, name, getattr(TmuxSession, name))
    patch.install()
    env = object.__new__(ApptainerEnvironment)
    env.session_id = 'test'
    session = TmuxSession('test', env)
    session._previous_buffer = 'old shell history'
    return session, calls


def test_recovered_batch_keeps_same_session_object_and_never_resends_keys(terminal):
    from harbor.agents.terminus_2.tmux_session import TmuxKeystrokeBatch
    session, calls = terminal
    output, alive = asyncio.run(session.send_keys_and_capture([TmuxKeystrokeBatch('mutate-file\n', .1)]))
    assert alive and output.startswith('TASK_MEMORY_LIMIT:')
    assert len(calls) == 2  # Original batch, then new terminal only.
    assert 'mutate-file' in calls[0] and 'mutate-file' not in calls[1]
    assert 'new-session' in calls[1] and session._previous_buffer is None


def test_cancellation_does_not_trigger_recovery(terminal, monkeypatch):
    session, calls = terminal
    async def cancelled(*a, **kw):
        raise asyncio.CancelledError()
    monkeypatch.setattr(session.environment, 'exec', cancelled)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(session.capture_pane())
    assert not calls


def test_terminal_restart_failure_keeps_confirmed_memory_limit_outcome(terminal, monkeypatch):
    session, calls = terminal
    execute = session.environment.exec
    async def failed_restart(command, *args, **kwargs):
        if 'new-session' in command:
            raise ConnectionResetError('replacement terminal unavailable')
        return await execute(command, *args, **kwargs)
    monkeypatch.setattr(session.environment, 'exec', failed_restart)
    with pytest.raises(patch.TaskMemoryLimitError, match='replacement terminal unavailable'):
        asyncio.run(session.capture_pane())
    assert len(calls) == 1


def test_original_timeout_cancels_terminal_recovery(terminal, monkeypatch):
    session, calls = terminal
    execute = session.environment.exec
    async def slow_restart(command, *args, **kwargs):
        if 'new-session' in command:
            await asyncio.sleep(30)
        return await execute(command, *args, **kwargs)
    monkeypatch.setattr(session.environment, 'exec', slow_restart)
    async def scenario():
        await asyncio.wait_for(session.capture_pane(), timeout=.03)
    with pytest.raises(TimeoutError):
        asyncio.run(scenario())
    assert len(calls) == 1


def test_report_distinguishes_recovered_events_from_terminal_memory_limit(tmp_path):
    from validation.checks.trace_metrics import trial_metrics, aggregate
    result = {'agent_result': {'metadata': {'oom_recoveries': 2, 'stop_reason': 'task_complete'}},
              'verifier_result': {'rewards': {'reward': 1}}}
    row = trial_metrics(tmp_path, result)
    assert row['termination'] == 'task_complete' and row['oom_recoveries'] == 2
    result['exception_info'] = {'exception_type': 'TaskMemoryLimitError'}
    failed = trial_metrics(tmp_path, result)
    assert failed['termination'] == 'task_memory_limit' and not any(failed['errors'].values())
    summary = aggregate([row, failed])
    assert summary['oom_recoveries'] == 4 and summary['trajectories_with_oom_recovery'] == 2
