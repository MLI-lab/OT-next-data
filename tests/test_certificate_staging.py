"""Local TLS trust stores preserve configured roots and certificate verification."""
import json
import os
import ssl
from pathlib import Path

import certifi
import httpx
import pytest

from hpc.local_assets import stage_certificates


def test_client_uses_local_ca_after_shared_bundle_disappears(tmp_path, monkeypatch):
    source = tmp_path / 'shared.pem'
    source.write_bytes(Path(certifi.where()).read_bytes())
    monkeypatch.setenv('SSL_CERT_FILE', str(source))
    monkeypatch.setenv('REQUESTS_CA_BUNDLE', str(source))
    record = stage_certificates(tmp_path / 'local')
    assert Path(record['SSL_CERT_FILE']['local']).read_bytes() == source.read_bytes()
    source.unlink()
    with httpx.Client() as client:
        context = client._transport._pool._ssl_context
        assert context.verify_mode == ssl.CERT_REQUIRED
        assert context.check_hostname
    assert json.loads((tmp_path / 'local/certificates.json').read_text()) == record


def test_custom_requests_roots_are_preserved(tmp_path, monkeypatch):
    source = Path(certifi.where()).read_bytes()
    http = tmp_path / 'http.pem'; http.write_bytes(source)
    requests = tmp_path / 'requests.pem'; requests.write_bytes(source + b'\n')
    monkeypatch.setenv('SSL_CERT_FILE', str(http))
    monkeypatch.setenv('REQUESTS_CA_BUNDLE', str(requests))
    record = stage_certificates(tmp_path / 'local')
    assert Path(record['SSL_CERT_FILE']['local']).read_bytes() == http.read_bytes()
    assert Path(record['REQUESTS_CA_BUNDLE']['local']).read_bytes() == requests.read_bytes()


def test_invalid_bundle_fails_without_disabling_verification(tmp_path, monkeypatch):
    source = tmp_path / 'invalid.pem'; source.write_text('not a certificate')
    monkeypatch.setenv('SSL_CERT_FILE', str(source))
    with pytest.raises(ssl.SSLError):
        stage_certificates(tmp_path / 'local')
    assert os.environ['SSL_CERT_FILE'] == str(source)
