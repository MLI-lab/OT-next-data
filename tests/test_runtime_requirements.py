import hashlib
import io

import pytest

from data.utils.verifier_runtime import runtime_spec
from hpc.offline_prep.runtime_requirements import prepare


def requirement(data=b'source bytes'):
    return 'package @ https://example.org/package-1.0.tar.gz#sha256=' + hashlib.sha256(data).hexdigest()


def test_source_identity_and_checksum_are_preserved(tmp_path):
    value = requirement()
    spec = runtime_spec(python='3.10.12', requirements=[value])
    assert spec['requirements'] == [value]
    calls = []
    def download(url, timeout):
        calls.append(url)
        return io.BytesIO(b'source bytes')
    inputs, records = prepare([value, value], tmp_path, download)
    assert inputs[0].startswith('package @ file://') and inputs[0] == inputs[1]
    assert len(calls) == 1 and records[0]['url'] == value.split(' @ ')[1]
    assert records[0]['verified'] is True


def test_source_checksum_failure_stops_installation(tmp_path):
    with pytest.raises(ValueError, match='checksum mismatch'):
        prepare([requirement()], tmp_path, lambda *a, **kw: io.BytesIO(b'wrong bytes'))


def test_local_source_inputs_do_not_download(tmp_path, monkeypatch):
    wheelhouse = tmp_path / 'wheels'; wheelhouse.mkdir()
    (wheelhouse / 'package-1.0.tar.gz').write_bytes(b'source bytes')
    monkeypatch.setenv('OT_RUNTIME_WHEELHOUSE', str(wheelhouse))
    inputs, records = prepare([requirement()], tmp_path / 'build', lambda *a, **kw: pytest.fail('network used'))
    assert len(inputs) == len(records) == 1


@pytest.mark.parametrize('value', ['package @ https://example.org/pkg.tar.gz',
    'package @ http://example.org/pkg.tar.gz#sha256=' + 'a' * 64,
    'package @ https://user:password@example.org/pkg.tar.gz#sha256=' + 'a' * 64])
def test_unpinned_or_credential_sources_rejected(value):
    with pytest.raises(ValueError):
        runtime_spec(python='3.10.12', requirements=[value])
