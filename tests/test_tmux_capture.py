"""Run Harbor's real batch script, worker truncation and response parser together."""
import asyncio
import shlex
import subprocess

import pytest

from harbor_patches import tmux_capture as patch


@pytest.fixture
def bridge(tmp_path, monkeypatch):
    pytest.importorskip('harbor')
    from harbor.agents.terminus_2 import tmux_session as tmux
    from harbor.environments.apptainer import worker
    from harbor.environments.apptainer.apptainer import ApptainerEnvironment
    from harbor.environments.base import ExecResult

    # Record both methods and flags before installation so tests are isolated.
    monkeypatch.setattr(tmux.TmuxSession, '_build_batch_script', tmux.TmuxSession._build_batch_script)
    monkeypatch.setattr(tmux.TmuxSession, '_ot_bounded_batch_captures', False, raising=False)
    monkeypatch.setattr(worker.ApptainerInstance, 'exec', worker.ApptainerInstance.exec)
    monkeypatch.setattr(worker.ApptainerInstance, '_ot_bounded_batch_captures', False, raising=False)
    original_run = subprocess.run
    full, visible, sent = [tmp_path / name for name in ('full', 'visible', 'sent')]
    full.write_text('history\nrecent\n')
    visible.write_text('recent\n')
    state = {'capture_rc': 0, 'absent': False, 'calls': 0}

    def execute(cmd, **kwargs):
        state['calls'] += 1
        # Replace only the container boundary; run the actual generated shell
        # script and let ApptainerInstance.exec apply its actual truncation.
        fake_tmux = f'''
tmux() {{
  case "$1" in
    has-session)
      if {'true' if state['absent'] else 'false'}; then
        echo "can't find session: test"; return 1
      fi;;
    send-keys) printf '%s\\n' "$*" >> {shlex.quote(str(sent))};;
    capture-pane)
      if [ {state['capture_rc']} -ne 0 ]; then return {state['capture_rc']}; fi
      case " $* " in
        *" -S "*) cat {shlex.quote(str(full))};;
        *) cat {shlex.quote(str(visible))};;
      esac;;
  esac
}}
'''
        return original_run(['/bin/bash', '-c', fake_tmux + cmd[-1]], **kwargs)

    monkeypatch.setattr(worker.subprocess, 'run', execute)
    instance = object.__new__(worker.ApptainerInstance)
    instance.instance_name = 'test'
    instance.env_id = 'test'
    instance.max_exec_output_chars = worker.DEFAULT_MAX_EXEC_OUTPUT_CHARS
    instance._proxy_command_prefix = lambda: ''

    class LocalBridge(ApptainerEnvironment):
        def __init__(self):
            self.session_id = 'test'
            self.responses = []

        async def exec(self, command, **kwargs):
            response = instance.exec({'command': command, 'timeout_sec': 10})
            self.responses.append(response)
            return ExecResult(**response)

    env = LocalBridge()
    return tmux, worker, instance, env, state, full, visible, sent


@pytest.mark.parametrize('limit', [500_000, 32_000])
@pytest.mark.parametrize('line', ['terminal output\n', '測試🙂 terminal output\n'])
def test_oversized_captures_survive_actual_worker_and_parser(bridge, limit, line):
    tmux, worker, instance, env, state, full, visible, sent = bridge
    instance.max_exec_output_chars = limit
    text = line * 90_000 + 'LATEST OUTPUT\n'
    full.write_text(text)
    visible.write_text(text)
    session = tmux.TmuxSession('test', env)
    actions = [tmux.TmuxKeystrokeBatch('echo once\n', 0)]
    # Reproduce the failure before installing either patch.
    with pytest.raises(tmux.TmuxBatchProtocolError):
        asyncio.run(session.send_keys_and_capture(actions))
    assert env.responses[-1]['stdout_truncated']
    before = sent.read_text()
    patch.install()
    patch.install_worker(worker)
    builder, executor = tmux.TmuxSession._build_batch_script, worker.ApptainerInstance.exec
    patch.install()
    patch.install_worker(worker)
    assert (builder, executor) == (tmux.TmuxSession._build_batch_script, worker.ApptainerInstance.exec)
    output, alive = asyncio.run(session.send_keys_and_capture(actions))
    assert alive and output.endswith('LATEST OUTPUT\n')
    assert session._previous_buffer.endswith('LATEST OUTPUT\n')
    assert not env.responses[-1]['stdout_truncated']
    assert len(env.responses[-1]['stdout']) <= limit
    assert state['calls'] == 2  # No command replay or additional capture request.
    assert sent.read_text() == before * 2


def test_small_output_unchanged_and_other_backends_untouched(bridge):
    tmux, worker, instance, env, state, full, visible, sent = bridge
    baseline = asyncio.run(tmux.TmuxSession('test', env).send_keys_and_capture([]))
    other = tmux.TmuxSession('test', object())
    markers = tmux._BatchMarkers.create()
    original_script = other._build_batch_script([], markers)
    patch.install()
    patch.install_worker(worker)
    assert asyncio.run(tmux.TmuxSession('test', env).send_keys_and_capture([])) == baseline
    assert other._build_batch_script([], markers) == original_script
    instance.max_exec_output_chars = 100
    response = instance.exec({'command': "printf '%0200d' 0"})
    assert len(response['stdout']) == 100 and response['stdout_truncated']


@pytest.mark.parametrize('absent,capture_rc,error', [
    (True, 0, 'TmuxSessionEndedError'), (False, 7, 'TmuxCommandError')])
def test_real_terminal_failures_are_preserved(bridge, absent, capture_rc, error):
    tmux, worker, _, env, state, *_ = bridge
    patch.install()
    patch.install_worker(worker)
    state.update(absent=absent, capture_rc=capture_rc)
    with pytest.raises(getattr(tmux, error)):
        asyncio.run(tmux.TmuxSession('test', env).send_keys_and_capture([]))
    assert state['calls'] == 1
