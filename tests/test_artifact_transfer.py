import asyncio
from functools import wraps
import base64
import io
import logging
from pathlib import Path
import shutil
import subprocess
import tarfile
from types import SimpleNamespace

import pytest

from harbor.trial.artifact_handler import ArtifactHandler
from harbor.models.trial.config import ArtifactConfig
from harbor_patches import artifact_transfer


def run_async(function):
    @wraps(function)
    def run(*args, **kwargs):
        return asyncio.run(function(*args, **kwargs))
    return run


@pytest.fixture
def packed_transfer(monkeypatch):
    # Restore all installed methods after this test.
    for name in ('_download_artifact', 'upload_artifacts'):
        monkeypatch.setattr(ArtifactHandler, name, getattr(ArtifactHandler, name))
    monkeypatch.setattr(ArtifactHandler, '_packed_bridge_transfer', False, raising=False)

    class Environment:
        _bridge_url = 'http://bridge'
        _env_id = 'target'
        async def is_dir(self, path, **kwargs):
            return True
        async def exec(self, command, **kwargs):
            result = subprocess.run(command, shell=True, capture_output=True, text=True)
            return SimpleNamespace(return_code=result.returncode, stdout=result.stdout, stderr=result.stderr)
        async def download_file(self, source_path, target_path):
            shutil.copyfile(source_path, target_path)
        async def empty_dirs(self, paths, **kwargs):
            self.emptied = paths
        def _check_transfer_result(self, operation, result):
            assert result['state'] == 'done'

    uploaded = []
    async def post(url, payload):
        uploaded.append(payload)
        return {'job_id': 'upload'}
    async def poll(*args, **kwargs):
        return {'state': 'done'}
    bridge = SimpleNamespace(ApptainerEnvironment=Environment, _async_http_post=post, _poll_job=poll)
    artifact_transfer.install(bridge)
    return Environment, uploaded


@run_async
async def test_submission_keeps_absolute_and_dangling_links_packed(tmp_path, packed_transfer):
    Environment, uploaded = packed_transfer
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'absolute').symlink_to('/Users/developer/dependency.jar')
    (source / 'dangling').symlink_to('missing/browserify')
    (source / 'target.java').write_text('submitted source')
    (source / '.git').mkdir()
    (source / '.git/config').write_text('excluded')
    artifact = ArtifactConfig(source=str(source), exclude=['.git'])
    handler = ArtifactHandler(artifacts=[artifact], logger=logging.getLogger(__name__))
    host = tmp_path / 'artifacts'
    entry = await handler._download_artifact(source_env=Environment(), artifacts_dir=host,
                                             artifact=artifact, convention_source='/logs/artifacts')
    assert entry.status == 'ok'
    assert not list(host.rglob('target.java'))
    assert not any(p.is_symlink() for p in host.rglob('*'))
    target = Environment()
    await handler.upload_artifacts(target, host, source_artifacts_dir='/logs/artifacts',
                                    target_artifacts_dir='/logs/artifacts')
    assert len(uploaded) == 1
    assert target.emptied == [str(source)]
    with tarfile.open(fileobj=io.BytesIO(base64.b64decode(uploaded[0]['file_b64']))) as archive:
        assert archive.getmember('./absolute').linkname == '/Users/developer/dependency.jar'
        assert archive.getmember('./dangling').linkname == 'missing/browserify'
        assert archive.extractfile('./target.java').read() == b'submitted source'
        assert not any('.git' in p for p in archive.getnames())


@run_async
async def test_required_submission_download_failure_stops_handoff(tmp_path, packed_transfer):
    Environment, uploaded = packed_transfer
    class Broken(Environment):
        async def download_file(self, **kwargs):
            raise RuntimeError('connection interrupted')
    source = tmp_path / 'source'
    source.mkdir()
    artifact = ArtifactConfig(source=str(source))
    handler = ArtifactHandler(artifacts=[artifact], logger=logging.getLogger(__name__))
    with pytest.raises(RuntimeError, match='connection interrupted'):
        await handler._download_artifact(source_env=Broken(), artifacts_dir=tmp_path/'artifacts',
                                         artifact=artifact, convention_source='/logs/artifacts')
    assert not getattr(handler, '_packed_submissions', {})
    assert not uploaded


@run_async
async def test_missing_conventional_artifacts_dir_is_recorded_empty_without_a_bridge_copy(tmp_path, packed_transfer):
    Environment, uploaded = packed_transfer

    class NoArtifacts(Environment):
        async def is_dir(self, path, **kwargs):
            return False
        async def download_file(self, source_path, target_path):
            raise AssertionError('no copy must be attempted for a missing artifacts directory')
        async def download_dir(self, source_dir, target_dir):
            raise AssertionError('no copy must be attempted for a missing artifacts directory')

    artifact = ArtifactConfig(source='/logs/artifacts', destination='/logs/artifacts')
    handler = ArtifactHandler(artifacts=[artifact], logger=logging.getLogger(__name__))
    entry = await handler._download_artifact(source_env=NoArtifacts(), artifacts_dir=tmp_path / 'artifacts',
                                             artifact=artifact, convention_source='/logs/artifacts')
    assert (entry.status, entry.type, entry.source) == ('empty', 'directory', '/logs/artifacts')
