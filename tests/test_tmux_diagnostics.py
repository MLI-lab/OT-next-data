import asyncio
import logging
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize('kind', ['TmuxSessionEndedError', 'TmuxCommandError'])
@pytest.mark.parametrize('probe_fails', [False, True])
def test_terminal_error_records_failed_commands_without_retry(monkeypatch, caplog, kind, probe_fails):
    pytest.importorskip('harbor')
    from harbor.agents.terminus_2 import tmux_session as module
    from harbor_patches import tmux_diagnostics

    failure = getattr(module, kind)('original failure')
    calls = []
    class Session:
        _session_name = 'terminus-2'
        _user = 'root'
        _logger = logging.getLogger('test-tmux-diagnostic')
        async def send_keys_and_capture(self, batches):
            calls.append('action')
            raise failure
    async def probe(**kwargs):
        calls.append('probe')
        if probe_fails:
            raise RuntimeError('probe failed too')
        return SimpleNamespace(stdout='_pilot_anchor: 1 windows', stderr='')
    monkeypatch.setattr(module, 'TmuxSession', Session)
    tmux_diagnostics.install()
    session = Session()
    session.environment = SimpleNamespace(exec=probe)
    with caplog.at_level(logging.ERROR), pytest.raises(type(failure)) as error:
        asyncio.run(session.send_keys_and_capture([SimpleNamespace(keystrokes='exit\n', duration_sec=.1)]))
    assert error.value is failure
    assert calls == ['action', 'probe']
    assert kind in caplog.text and 'exit' in caplog.text
