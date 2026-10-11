"""SSH grading must require a handshake and clean up only its own processes."""
import subprocess

import pytest

from data.seta.patch import SSH_PROTOCOL_CHECK


@pytest.mark.parametrize('output,passes', [
    ('Connection refused', False),
    ('Remote protocol version 2.0', False),
    ('SSH2_MSG_SERVICE_ACCEPT received\nPermission denied', True),
])
def test_requires_authentication_handshake(monkeypatch, output, passes):
    monkeypatch.setenv("FAKEROOTKEY", "test-session")
    calls = []
    class Process:
        pid = 123456
        def communicate(self, timeout):
            return '', output
        def wait(self):
            calls.append('wait')
    def popen(command, **kwargs):
        assert 'ProxyCommand=env -u LD_PRELOAD -u FAKEROOTKEY unshare --user --map-user=1000 --map-group=1000 /usr/sbin/sshd -i -e' in command
        assert kwargs['start_new_session']
        return Process()
    import os
    monkeypatch.setattr(subprocess, 'Popen', popen)
    monkeypatch.setattr(os, 'makedirs', lambda *a, **k: None)
    monkeypatch.setattr(os, 'killpg', lambda pid, sig: calls.append(pid))
    namespace = {'os': os, 'subprocess': subprocess}
    exec(SSH_PROTOCOL_CHECK, namespace)
    if passes:
        namespace['ssh_handshake']('/usr/sbin/sshd')
    else:
        with pytest.raises(AssertionError, match='did not reach authentication'):
            namespace['ssh_handshake']('/usr/sbin/sshd')
    assert calls == [123456, 'wait']
