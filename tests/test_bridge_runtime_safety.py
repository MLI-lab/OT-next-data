import base64
import importlib.util
from pathlib import Path

import pytest


@pytest.fixture
def patch():
    pytest.importorskip('harbor')
    path = Path(__file__).resolve().parents[1] / 'harbor_patches/bridge_worker.py'
    spec = importlib.util.spec_from_file_location('test_bridge_patch', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_worker_corrects_old_client_cache_key(patch, tmp_path):
    from harbor.utils.container_cache import environment_dir_hash_truncated
    (tmp_path / 'Dockerfile').write_text('FROM ubuntu\nCOPY data /app/data\n')
    (tmp_path / 'data').write_text('one')
    def payload():
        return {'dockerfile_hash': 'old', 'sif_path': '/cache/build_task-old.sif',
            'files_b64': {p.name: base64.b64encode(p.read_bytes()).decode() for p in tmp_path.iterdir()}}
    a = patch.content_keyed_payload(payload())
    assert a['dockerfile_hash'] == environment_dir_hash_truncated(tmp_path)
    (tmp_path / 'data').write_text('two')
    b = patch.content_keyed_payload(payload())
    assert a['sif_path'] != b['sif_path']
    assert patch.content_keyed_payload(b) == b


def test_network_fallback_warns_and_records_effective_host(patch, monkeypatch, tmp_path, capsys):
    import json
    monkeypatch.setenv('PILOT_NET_ISOLATION', '1')
    status = tmp_path / 'network.json'
    monkeypatch.setenv('PILOT_NETWORK_STATUS_PATH', str(status))
    assert patch.network_isolation_available(str(tmp_path)) is False
    assert 'WARNING' in capsys.readouterr().err
    assert json.loads(status.read_text())['effective'] == 'host'


def test_explicit_host_mode_supplies_dns_and_proxy(patch, monkeypatch):
    monkeypatch.setenv('PILOT_NET_ISOLATION', '0')
    monkeypatch.delenv('APPTAINER_BINDPATH', raising=False)
    monkeypatch.setenv('https_proxy', 'http://proxy.example:8080')
    patch.configure_explicit_host_network()
    import os
    assert '/etc/resolv.conf:/etc/resolv.conf:ro' in os.environ['APPTAINER_BINDPATH']
    assert os.environ['APPTAINERENV_https_proxy'] == 'http://proxy.example:8080'
