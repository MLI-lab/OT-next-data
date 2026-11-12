"""R19: restore a reviewed Docker ENTRYPOINT startup as agent setup, bound to its sources."""
import hashlib
import io
import os
import signal
import subprocess
import tarfile
import time

import pytest

from data.calibforge import repairs

TASK = "multi_solver-system-administration_20260415_123352_009"
SERVICE = b"import os\n"
DOCKERFILE = r"""FROM ubuntu:24.04

WORKDIR /app

RUN mkdir -p /app && cd /app && apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    procps \
    && rm -rf /var/lib/apt/lists/*

COPY files/logsvc.py /app/logsvc.py

# Create startup script that launches the background service
RUN mkdir -p /app && cd /app && printf '#!/bin/bash\npython3 /app/logsvc.py &\nsleep 3\nexec "$@"\n' > /entrypoint.sh && \
    chmod +x /entrypoint.sh

# Set the entrypoint to start the service before any command
ENTRYPOINT ["/entrypoint.sh"]
CMD ["bash"]
"""


@pytest.fixture
def reviewed(monkeypatch):
    record = dict(repairs.ENTRYPOINT_STARTUPS[TASK],
                  inputs_sha256={"environment/files/logsvc.py": hashlib.sha256(SERVICE).hexdigest()})
    monkeypatch.setitem(repairs.ENTRYPOINT_STARTUPS, TASK, record)
    return {"environment/Dockerfile": DOCKERFILE.encode(), "environment/files/logsvc.py": SERVICE,
            "instruction.md": b"Free disk space.\n", "tests/test.sh": b"pytest\n"}


def test_reviewed_entrypoint_becomes_agent_setup(reviewed):
    updates, entrypoint = repairs.restore_entrypoint_startup(TASK, reviewed)
    assert entrypoint == "/entrypoint.sh" and list(updates) == ["setup_files/setup.sh"]
    script = updates["setup_files/setup.sh"].decode()
    assert "setsid -w /entrypoint.sh /bin/true </dev/null >/dev/null 2>&1\n" in script
    assert 'if [ "${1:-}" = "--prepare" ]; then exit 0; fi\n' in script
    assert "until [ -s /app/.logsvc.pid ]; do\n" in script
    subprocess.run(["bash", "-n"], input=script, text=True, check=True)
    assert repairs.is_entrypoint_startup_setup("setup_files/setup.sh", updates["setup_files/setup.sh"])
    assert not repairs.is_entrypoint_startup_setup("setup_files/setup.sh", b"#!/bin/bash\n")
    # Already applied: unchanged, still recorded.
    assert repairs.restore_entrypoint_startup(TASK, {**reviewed, **updates}) == ({}, "/entrypoint.sh")
    assert repairs.restore_entrypoint_startup("other", reviewed) == ({}, None)


@pytest.mark.parametrize("change", [
    lambda f: f.update({"environment/Dockerfile": f["environment/Dockerfile"].replace(b'CMD ["bash"]', b'CMD ["sh"]')}),
    lambda f: f.update({"environment/Dockerfile": f["environment/Dockerfile"].replace(b"sleep 3", b"sleep 1")}),
    lambda f: f.update({"environment/Dockerfile": f["environment/Dockerfile"].replace(b"WORKDIR /app", b"WORKDIR /srv")}),
    lambda f: f.update({"environment/files/logsvc.py": b"import sys\n"}),
    lambda f: f.update({"setup_files/setup.sh": b"#!/bin/bash\necho task setup\n"}),
    lambda f: f.update({"setup_files/other.sh": b"#!/bin/bash\n"}),
])
def test_source_drift_requires_review(reviewed, change):
    change(reviewed)
    with pytest.raises(ValueError, match="review|changed"):
        repairs.restore_entrypoint_startup(TASK, reviewed)


def test_runtime_history_ignores_generated_setup(reviewed):
    from data.calibforge.patch import comparable
    updates, _ = repairs.restore_entrypoint_startup(TASK, reviewed)
    assert comparable("setup_files/setup.sh", updates["setup_files/setup.sh"]) is None
    assert comparable("setup_files/setup.sh", b"#!/bin/bash\n") == b"#!/bin/bash\n"


def test_generated_setup_detaches_service_and_waits_for_readiness(reviewed, tmp_path):
    """Run the script against a stand-in entrypoint: captured output must not keep the command
    open, the service must outlive it, and setup must return only once the PID file exists."""
    app = tmp_path / "app"
    app.mkdir()
    entrypoint = tmp_path / "entrypoint.sh"
    entrypoint.write_text("#!/bin/bash\n(sleep 2; echo $BASHPID > %s/.logsvc.pid; exec sleep 300) &\nsleep 1\nexec \"$@\"\n" % app)
    entrypoint.chmod(0o755)
    updates, _ = repairs.restore_entrypoint_startup(TASK, reviewed)
    script = (updates["setup_files/setup.sh"].decode()
              .replace("/entrypoint.sh", str(entrypoint)).replace("/app", str(app)))
    started = time.monotonic()
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert time.monotonic() - started < 30
    pid = int((app / ".logsvc.pid").read_text())
    try:
        os.kill(pid, 0)
    finally:
        os.kill(pid, signal.SIGTERM)
    assert subprocess.run(["bash", "-c", script, "setup", "--prepare"], timeout=10).returncode == 0


def test_patcher_adds_setup_after_conversion(reviewed):
    patch = pytest.importorskip("data.calibforge.patch")
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as archive:
        for name, data in reviewed.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    binary, entrypoint = patch.add_entrypoint_startup(TASK, stream.getvalue())
    with tarfile.open(fileobj=io.BytesIO(binary)) as archive:
        files = {m.name: (m.mode, archive.extractfile(m).read()) for m in archive if m.isfile()}
    assert entrypoint == "/entrypoint.sh"
    assert files["setup_files/setup.sh"] == (0o755, repairs.entrypoint_startup_script(TASK).encode())
    assert all(files[name][1] == data for name, data in reviewed.items())
    assert patch.add_entrypoint_startup(TASK, binary) == (binary, "/entrypoint.sh")
    assert "R19" in patch.RULES
