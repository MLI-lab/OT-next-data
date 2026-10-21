import os
import subprocess
from types import SimpleNamespace

import pytest

from harbor_patches import upload_runtime as patch


def test_directory_payload_preserves_links_without_reading_targets(tmp_path):
    import base64
    import io
    import tarfile
    source = tmp_path / 'submission'
    source.mkdir()
    (source / 'source.java').write_text('source')
    (source / 'browserify').symlink_to('../absent/browserify')
    (source / 'outside').symlink_to('/must/not/be/read')
    with tarfile.open(fileobj=io.BytesIO(base64.b64decode(patch.directory_payload(source)))) as archive:
        assert archive.getmember('./browserify').issym()
        assert archive.getmember('./browserify').linkname == '../absent/browserify'
        assert archive.getmember('./outside').linkname == '/must/not/be/read'
        assert archive.extractfile('./source.java').read() == b'source'


def test_upload_ignores_poisoned_shell_startup(tmp_path):
    startup = tmp_path / 'startup'
    startup.write_text('export PATH=/missing\nexit 127\n')
    target = tmp_path / 'tests'
    target.mkdir()
    (target / 'test.sh').touch()
    cmd = ['apptainer', 'exec', 'instance://trial', 'bash', '-lc',
           f'find {target} -type f | sort | sed -n 1p']

    class Instance:
        def upload(self, payload):
            adapted = patch.upload_command(cmd)
            assert adapted[:5] == ['apptainer', 'exec', '--pwd', '/', 'instance://trial']
            return subprocess.run(adapted[5:], capture_output=True, text=True,
                                  env={**os.environ, 'PATH': '/missing',
                                       'BASH_ENV': str(startup), 'ENV': str(startup)})

    patch.install_worker(SimpleNamespace(ApptainerInstance=Instance))
    patch.install_worker(SimpleNamespace(ApptainerInstance=Instance))
    result = Instance().upload({})
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(target / 'test.sh')
    assert patch.upload_command(cmd) is cmd  # Ordinary task exec unchanged.


def test_upload_scope_is_reset_after_failure():
    class Instance:
        def upload(self, payload):
            raise RuntimeError('failed extraction')

    patch.install_worker(SimpleNamespace(ApptainerInstance=Instance))
    with pytest.raises(RuntimeError, match='failed extraction'):
        Instance().upload({})
    cmd = ['apptainer', 'exec', 'instance://trial', 'bash', '-lc', 'echo task']
    assert patch.upload_command(cmd) is cmd


def test_upload_tar_does_not_restore_foreign_owners():
    command = ['apptainer', 'exec', 'instance://trial', 'bash', '-c',
               'tar xf /workspace/.upload_tmp -C /tests && rm /workspace/.upload_tmp']
    class Instance:
        def upload(self, payload):
            return patch.upload_command(command)
    patch.install_worker(SimpleNamespace(ApptainerInstance=Instance))
    adapted = Instance().upload({})
    assert adapted[-1] == patch.extraction_command('/workspace/.upload_tmp', '/tests')
    assert '--checkpoint=3276' in adapted[-1]
    assert '--checkpoint-action=exec=' in adapted[-1]
    assert patch.upload_command(command) is command


def test_checkpoint_extraction_preserves_files_links_modes_and_quotes(tmp_path):
    import io
    import tarfile
    archive = tmp_path / "input ' $(must-not-run).tar"
    target = tmp_path / "target ' $(must-not-run)"
    target.mkdir()
    data = b'input data\n' * 4000
    with tarfile.open(archive, 'w') as output:
        member = tarfile.TarInfo('input.bin')
        member.size, member.mode, member.uid, member.gid = len(data), 0o755, 12345, 12345
        output.addfile(member, io.BytesIO(data))
        link = tarfile.TarInfo('link')
        link.type, link.linkname = tarfile.SYMTYPE, 'input.bin'
        output.addfile(link)
    result = subprocess.run(['/bin/sh', '-c', patch.extraction_command(str(archive), str(target), checkpoint=1)],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert (target / 'input.bin').read_bytes() == data
    assert (target / 'input.bin').stat().st_uid == os.getuid()
    assert (target / 'input.bin').stat().st_mode & 0o777 == 0o755
    assert (target / 'link').is_symlink()
    assert os.readlink(target / 'link') == 'input.bin'
    assert not archive.exists()


def test_extraction_keeps_legacy_behavior_without_gnu_sync(tmp_path):
    import io
    import tarfile
    archive, target = tmp_path / 'input.tar', tmp_path / 'output'
    target.mkdir()
    with tarfile.open(archive, 'w') as output:
        member = tarfile.TarInfo('file')
        member.size = 4
        output.addfile(member, io.BytesIO(b'data'))
    binaries = tmp_path / 'bin'
    binaries.mkdir()
    sync = binaries / 'sync'
    sync.write_text('#!/bin/sh\nexit 1\n')
    sync.chmod(0o755)
    result = subprocess.run(['/bin/sh', '-c', patch.extraction_command(str(archive), str(target), checkpoint=1)],
                            env={**os.environ, 'PATH': str(binaries) + ':' + os.environ['PATH']},
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert (target / 'file').read_bytes() == b'data'
    assert not archive.exists()

@pytest.mark.parametrize('directory', [True, False])
def test_chunked_upload_roundtrip_and_bounded_requests(tmp_path, monkeypatch, directory):
    import asyncio
    import base64
    from pathlib import Path
    monkeypatch.setattr(patch, 'CHUNK_BYTES', 4096)
    source = tmp_path / 'source'
    source.mkdir()
    contents = bytes(range(256)) * 81
    (source / 'input.bin').write_bytes(contents)
    (source / 'input.bin').chmod(0o755)
    (source / 'dangling').symlink_to('absent')
    staging = tmp_path / 'staging'
    (staging / 'workspace').mkdir(parents=True)
    target = tmp_path / "target ' $(never-executed)"
    instance = SimpleNamespace(staging_dir=str(staging), instance_name='test')
    def run(command, **kwargs):
        command = patch.upload_command(command)
        script = command[-1].replace('/workspace/', str(staging / 'workspace') + '/')
        return subprocess.run(['/bin/bash', '--noprofile', '--norc', '-c', script], **kwargs)
    worker = SimpleNamespace(APPTAINER='apptainer', subprocess=SimpleNamespace(run=run))
    sizes, results = [], {}
    async def post(url, payload):
        sizes.append(len(base64.b64decode(payload['file_b64'])))
        token = patch._upload.set(True)
        try:
            results[str(len(sizes))] = patch.receive_chunk(instance, payload, worker)
        finally:
            patch._upload.reset(token)
        return {'job_id': str(len(sizes))}
    async def poll(url, job, **kwargs):
        return results.pop(job)
    class Environment:
        _bridge_url, _env_id = 'http://bridge', 'env'
        async def upload_dir(self, *args):
            raise AssertionError('old upload')
        def _check_transfer_result(self, operation, result):
            assert result['return_code'] == 0, result
    bridge = SimpleNamespace(ApptainerEnvironment=Environment, _async_http_post=post, _poll_job=poll)
    patch.install_client(bridge)
    env = Environment()
    if directory:
        asyncio.run(env.upload_dir(source, str(target)))
        assert (target / 'input.bin').read_bytes() == contents
        assert (target / 'input.bin').stat().st_mode & 0o777 == 0o755
        assert os.readlink(target / 'dangling') == 'absent'
    else:
        asyncio.run(env.upload_file(source / 'input.bin', str(target)))
        assert target.read_bytes() == contents
    assert len(sizes) > 3 and max(sizes) <= 4096 and sizes[-1] == 0
    assert not list(staging.glob('incoming-*'))
    assert not list((staging / 'workspace').iterdir())


def test_chunk_rejects_out_of_order_and_target_changes(tmp_path):
    import base64
    instance = SimpleNamespace(staging_dir=str(tmp_path))
    payload = {'upload_chunk': {'id': 'a' * 32, 'offset': 0, 'final': False},
               'file_b64': base64.b64encode(b'data').decode(), 'target_path': '/inputs', 'is_dir': True}
    assert patch.receive_chunk(instance, payload, None)['upload_offset'] == 4
    with pytest.raises(ValueError, match='offset'):
        patch.receive_chunk(instance, payload, None)
    payload['upload_chunk']['offset'] = 4
    payload['target_path'] = '/different'
    with pytest.raises(ValueError, match='target'):
        patch.receive_chunk(instance, payload, None)


def test_large_directory_upload_under_memory_limit(tmp_path):
    """A 256 MiB upload must fit a 192 MiB process, including encoding."""
    import sys
    script = r'''
import asyncio, base64, resource, sys
from pathlib import Path
from types import SimpleNamespace
from harbor_patches import upload_runtime as patch
resource.setrlimit(resource.RLIMIT_AS, (192 * 2**20, 192 * 2**20))
root = Path(sys.argv[1]); source = root / 'source'; source.mkdir()
with (source / 'large.bin').open('wb') as f:
    f.truncate(256 * 2**20)
archive = root / 'input.tar'
patch.directory_archive(source, archive)
staging = root / 'staging'; (staging / 'workspace').mkdir(parents=True)
receiver = SimpleNamespace(staging_dir=str(staging), instance_name='test')
worker = SimpleNamespace(APPTAINER='apptainer', subprocess=SimpleNamespace(
    run=lambda *a, **kw: SimpleNamespace(returncode=0, stdout='', stderr='')))
result = None
async def post(url, payload):
    global result
    result = patch.receive_chunk(receiver, payload, worker)
    return {'job_id': 'chunk'}
async def poll(*args, **kwargs):
    return result
class Client:
    _bridge_url, _env_id = 'http://bridge', 'env'
    def _check_transfer_result(self, operation, result):
        assert result['return_code'] == 0
bridge = SimpleNamespace(_async_http_post=post, _poll_job=poll)
with archive.open('rb') as stream:
    asyncio.run(patch.send_stream(bridge, Client(), stream, '/input', True))
assert result['upload_offset'] == archive.stat().st_size
assert not list(staging.glob('incoming-*'))
print(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
'''
    result = subprocess.run([sys.executable, '-c', script, str(tmp_path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert int(result.stdout.strip()) < 192 * 1024


def test_small_upload_keeps_single_request():
    import asyncio
    import base64
    import io
    calls = []
    async def post(url, payload):
        calls.append(payload)
        return {'job_id': 'small'}
    async def poll(*args, **kwargs):
        return {'return_code': 0}
    instance = SimpleNamespace(_bridge_url='http://bridge', _env_id='env',
                               _check_transfer_result=lambda *args: None)
    bridge = SimpleNamespace(_async_http_post=post, _poll_job=poll)
    asyncio.run(patch.send_stream(bridge, instance, io.BytesIO(b'small'), '/input', False))
    assert len(calls) == 1 and 'upload_chunk' not in calls[0]
    assert base64.b64decode(calls[0]['file_b64']) == b'small'
