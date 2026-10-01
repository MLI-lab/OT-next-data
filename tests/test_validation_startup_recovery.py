import json
import tarfile
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from hpc.helma import validation_worker as worker
from hpc.zih.recover_static_checkpoint import pack_logs


def test_service_launcher_uses_target_import_directory(tmp_path):
    (tmp_path / 'helper.py').write_text('VALUE = 42\n')
    target = tmp_path / 'service.py'
    target.write_text('import helper\nprint(helper.VALUE)\n')
    launcher = Path(__file__).resolve().parents[1] / 'validation/stages/service_entrypoint.py'
    result = subprocess.run([sys.executable, '-B', str(launcher), str(target)],
                            capture_output=True, text=True, check=True)
    assert result.stdout.endswith('42\n')


def test_health_check_bypasses_proxy_and_waits_for_workers(monkeypatch):
    calls = []
    class Response:
        def __enter__(self):
            import io
            return io.StringIO(json.dumps({'workers_alive': len(calls) > 1}))
        def __exit__(self, *args):
            pass
    def open_url(url, timeout):
        calls.append(url)
        return Response()
    def opener(handler):
        assert handler.proxies == {}
        return SimpleNamespace(open=open_url)
    monkeypatch.setattr(worker.urllib.request, 'build_opener', opener)
    monkeypatch.setattr(worker.time, 'sleep', lambda _: None)
    worker.wait_ready('http://127.0.0.1:9000/status', [], workers=True)
    assert len(calls) == 2


def test_startup_timeout_requests_stack_trace(monkeypatch):
    calls = []
    process = SimpleNamespace(poll=lambda: None, send_signal=calls.append)
    monkeypatch.setattr(worker.time, 'sleep', lambda _: None)
    with pytest.raises(RuntimeError, match='after 0s'):
        worker.wait_ready('http://127.0.0.1:9000/status', [process], timeout=0, diagnostics=True)
    assert calls == [worker.signal.SIGUSR2]


def test_checkpoint_preserves_shared_and_inherited_logs(tmp_path):
    source = tmp_path / 'checks.log'
    source.write_text('original check evidence\n')
    tasks = [{'task': f'task-{i}', 'checks': [{'check': 'one', 'log': str(source)}]} for i in range(3)]
    first = tmp_path / 'first.tar.gz'
    pack_logs(tasks, first)
    with tarfile.open(first) as archive:
        assert sum(m.isfile() for m in archive.getmembers()) == 1
        assert archive.extractfile('task-2/one.log').read() == source.read_bytes()
    inherited = [{'task': 'resumed', 'checks': [{'check': 'one', 'log': str(first) + '!/task-2/one.log'}]}]
    second = tmp_path / 'second.tar.gz'
    pack_logs(inherited, second)
    with tarfile.open(second) as archive:
        assert archive.extractfile('resumed/one.log').read() == source.read_bytes()
