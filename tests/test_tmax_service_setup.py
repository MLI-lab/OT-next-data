import os
from pathlib import Path
import signal
import socket
import subprocess
import sys

import pytest

from data.tmax import patch


def repaired_program(monkeypatch, script, port):
    monkeypatch.setitem(patch.SERVICE_SETUP, "test-task", ("99-test.sh", str(script), port))
    original = (
        "echo preserve-task-setup\n"
        "    cat << 'EOF' > /.singularity.d/env/99-test.sh\n"
        "#!/bin/sh\npython3 /app/server.py &\nsleep 1\nEOF\n"
        "    chmod +x /.singularity.d/env/99-test.sh\n"
    )
    files = {"setup_files/task_install.sh": (original.encode(), 0o644),
             "tests/test.sh": (b"unchanged verifier", 0o755)}
    patch.service_setup_compatible(files, "test-task")
    result = files["setup_files/task_install.sh"][0].decode()
    assert result.startswith("echo preserve-task-setup\n")
    assert "/.singularity.d/env/" not in result
    assert files["tests/test.sh"] == (b"unchanged verifier", 0o755)
    return result.split("<<'TMAX_SERVICE_READY'\n", 1)[1].split("\nTMAX_SERVICE_READY", 1)[0]


def test_service_is_ready_and_repeated_setup_does_not_spawn_again(tmp_path, monkeypatch):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    pidfile = tmp_path / "server.pid"
    launches = tmp_path / "launches"
    server = tmp_path / "server.py"
    server.write_text(
        "import os,socket\nfrom pathlib import Path\n"
        f"Path({str(pidfile)!r}).write_text(str(os.getpid()))\n"
        f"with open({str(launches)!r}, 'a') as f: f.write('started\\n')\n"
        "s=socket.socket();s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)\n"
        f"s.bind(('127.0.0.1',{port}));s.listen()\n"
        "while True:\n c,_=s.accept();c.close()\n"
    )
    program = repaired_program(monkeypatch, server, port)
    try:
        for _ in range(2):
            subprocess.run([sys.executable, "-c", program], check=True,
                           capture_output=True, timeout=15)
        assert launches.read_text().splitlines() == ["started"]
    finally:
        if pidfile.exists():
            os.kill(int(pidfile.read_text()), signal.SIGTERM)
        Path(f"/tmp/tmax-service-{port}.log").unlink(missing_ok=True)


def test_service_exit_fails_setup_instead_of_reporting_ready(tmp_path, monkeypatch):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    server = tmp_path / "server.py"
    server.write_text("raise SystemExit(2)\n")
    program = repaired_program(monkeypatch, server, port)
    try:
        result = subprocess.run([sys.executable, "-c", program], capture_output=True,
                                text=True, timeout=15)
        assert result.returncode != 0
        assert "failed readiness check" in result.stderr
    finally:
        Path(f"/tmp/tmax-service-{port}.log").unlink(missing_ok=True)


def test_unrecognized_service_hook_is_rejected():
    with pytest.raises(ValueError, match="exactly one"):
        patch.service_setup_compatible({"setup_files/task_install.sh": (b"different\n", 0o644)},
                                       "task_002392_b43fd016")
