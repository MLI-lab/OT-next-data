"""Real tmux regression for task scratch cleanup and independent containers."""
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from harbor_patches import tmux_socket


def test_worker_prefix_preserves_proxy_and_empty_prefix_path():
    for original in ('', 'export PATH=/custom/bin:$PATH; export HTTP_PROXY=http://proxy; '):
        class Instance:
            def _proxy_command_prefix(self):
                return original
        worker = SimpleNamespace(ApptainerInstance=Instance)
        tmux_socket.install_worker(worker)
        method = Instance._proxy_command_prefix
        tmux_socket.install_worker(worker)
        assert Instance._proxy_command_prefix is method
        prefix = Instance()._proxy_command_prefix()
        assert prefix == (original or tmux_socket.FALLBACK_PATH) + tmux_socket.SOCKET_ENV


def test_cleanup_does_not_disconnect_terminal_or_other_instance():
    if not shutil.which('tmux'):
        pytest.skip('tmux is required')
    # A short path is necessary for Unix sockets. Simulate two private container
    # roots; never remove the host /tmp or connect to its default tmux server.
    with tempfile.TemporaryDirectory(prefix='ot-sock-') as temp:
        root = Path(temp)
        clients = []
        def run(env, *args, check=True):
            return subprocess.run(['tmux', *args], env=env, check=check,
                                  capture_output=True, text=True, timeout=5)
        try:
            for name in ('a', 'b'):
                runtime = root / name / 'run'
                scratch = root / name / 'tmp'
                runtime.mkdir(parents=True)
                scratch.mkdir()
                env = dict(os.environ, TMUX_TMPDIR=str(runtime))
                env.pop('TMUX', None)
                clients.append(env)
                run(env, '-f', '/dev/null', 'new-session', '-d', '-s', '_pilot_anchor', 'sleep 60')
                run(env, 'new-session', '-d', '-s', 'terminus-2', 'sleep 60')
                # Demonstrate the old failure mechanism with another private server.
                old = dict(env, TMUX_TMPDIR=str(scratch))
                run(old, '-f', '/dev/null', 'new-session', '-d', '-s', 'old', 'sleep 60')
                pid = int(run(old, 'display-message', '-p', '#{pid}').stdout.strip())
                try:
                    shutil.rmtree(scratch)
                    scratch.mkdir()
                    assert run(old, 'has-session', '-t', 'old', check=False).returncode != 0
                    assert run(env, 'has-session', '-t', 'terminus-2').returncode == 0
                finally:
                    os.kill(pid, 15)
            run(clients[0], 'kill-server')
            assert run(clients[1], 'has-session', '-t', 'terminus-2').returncode == 0
        finally:
            for env in clients:
                run(env, 'kill-server', check=False)
