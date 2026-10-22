"""Keep bridge upload bookkeeping independent of task shell startup files."""
from contextvars import ContextVar
from functools import wraps
import asyncio
import base64
import io
import os
from pathlib import Path
import re
import tempfile
import uuid
import shlex
import tarfile


def directory_payload(source):
    """Transfer links as links, including dangling links, without reading their targets."""
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w', dereference=False) as archive:
        archive.add(str(source), arcname='.')
    return base64.b64encode(stream.getvalue()).decode()


CHUNK_BYTES = 4 * 1024 * 1024


def directory_archive(source, destination):
    """Spool a tar to disk; never materialize the directory in memory."""
    with tarfile.open(destination, mode='w', dereference=False) as archive:
        archive.add(str(source), arcname='.')


async def send_stream(bridge, instance, stream, target, is_dir):
    # Preserve the single-request path for small files and archives. The probe
    # itself is bounded; callers provide seekable on-disk streams.
    probe = await asyncio.to_thread(stream.read, CHUNK_BYTES + 1)
    if len(probe) <= CHUNK_BYTES:
        response = await bridge._async_http_post(f'{instance._bridge_url}/env/upload', {
            'env_id': instance._env_id, 'file_b64': base64.b64encode(probe).decode(),
            'target_path': target, 'is_dir': is_dir,
        })
        if not response.get('job_id'):
            raise RuntimeError(f'Upload submit failed: {response}')
        result = await bridge._poll_job(instance._bridge_url, response['job_id'], timeout_sec=120)
        instance._check_transfer_result('upload_dir' if is_dir else 'upload_file', result)
        return
    del probe
    stream.seek(0)
    transfer = uuid.uuid4().hex
    offset = 0
    while True:
        block = await asyncio.to_thread(stream.read, CHUNK_BYTES)
        final = not block
        response = await bridge._async_http_post(f'{instance._bridge_url}/env/upload', {
            'env_id': instance._env_id, 'file_b64': base64.b64encode(block).decode(),
            'target_path': target, 'is_dir': is_dir,
            'upload_chunk': {'id': transfer, 'offset': offset, 'final': final},
        })
        job_id = response.get('job_id')
        if not job_id:
            raise RuntimeError(f'Upload submit failed: {response}')
        result = await bridge._poll_job(instance._bridge_url, job_id, timeout_sec=120)
        instance._check_transfer_result('upload_dir' if is_dir else 'upload_file', result)
        if result.get('upload_offset') != offset + len(block):
            raise RuntimeError('Bridge did not acknowledge chunked upload; update server and worker')
        if final:
            return
        offset += len(block)


def install_client(bridge):
    original = bridge.ApptainerEnvironment.upload_dir
    if getattr(original, '_preserve_links', False):
        return

    @wraps(original)
    async def upload_dir(self, source_dir, target_dir):
        with tempfile.TemporaryDirectory(prefix='harbor-upload-') as directory:
            archive = Path(directory) / 'input.tar'
            await asyncio.to_thread(directory_archive, source_dir, archive)
            with archive.open('rb') as stream:
                await send_stream(bridge, self, stream, target_dir, True)

    async def upload_file(self, source_path, target_path):
        with open(source_path, 'rb') as stream:
            await send_stream(bridge, self, stream, target_path, False)

    upload_dir._preserve_links = True
    bridge.ApptainerEnvironment.upload_dir = upload_dir
    bridge.ApptainerEnvironment.upload_file = upload_file


def receive_chunk(instance, payload, worker):
    """Reassemble bounded requests on disk outside the task's filesystem."""
    chunk = payload['upload_chunk']
    transfer, offset, final = chunk['id'], chunk['offset'], chunk['final']
    if not re.fullmatch('[a-f0-9]{32}', transfer) or type(offset) is not int or offset < 0 or type(final) is not bool:
        raise ValueError('Invalid upload chunk metadata')
    encoded = payload.get('file_b64', '')
    if len(encoded) > ((CHUNK_BYTES + 2) // 3) * 4:
        raise ValueError('Upload chunk too large')
    data = base64.b64decode(encoded, validate=True)
    transfers = instance.__dict__.setdefault('_chunk_uploads', {})
    identity = (payload['target_path'], bool(payload.get('is_dir')))
    path = Path(instance.staging_dir) / ('incoming-' + transfer)
    if transfer not in transfers:
        if offset != 0:
            raise ValueError('Upload must start at offset zero')
        with path.open('xb'):
            pass
        transfers[transfer] = identity
    if transfers[transfer] != identity or path.stat().st_size != offset:
        raise ValueError('Upload target or offset changed')
    if final and data:
        raise ValueError('Final upload chunk must be empty')
    with path.open('ab') as stream:
        stream.write(data)
    if not final:
        return {'return_code': 0, 'stdout': '', 'stderr': '', 'upload_offset': offset + len(data)}
    # Move, rather than copy, into the existing workspace bind. The complete
    # archive is never passed through JSON or the task executor's memory.
    staged = Path(instance.staging_dir) / 'workspace' / ('.upload-' + transfer)
    os.replace(path, staged)
    container_path = '/workspace/' + staged.name
    target = payload['target_path']
    if identity[1]:
        script = ('mkdir -p -- ' + shlex.quote(target) + ' && ' +
                  extraction_command(container_path, target))
        if target == '/tests':
            script += ' && test -f /tests/test.sh'
    else:
        script = ('mkdir -p -- ' + shlex.quote(str(Path(target).parent)) +
                  ' && rm -f -- ' + shlex.quote(target) + ' && cp -- ' +
                  shlex.quote(container_path) + ' ' + shlex.quote(target))
    try:
        result = worker.subprocess.run(
            [worker.APPTAINER, 'exec', f'instance://{instance.instance_name}',
             'bash', '-c', script], capture_output=True, text=True, timeout=120)
        return {'return_code': result.returncode, 'stdout': (result.stdout or '')[:4000],
                'stderr': (result.stderr or '')[:2000], 'upload_offset': offset}
    finally:
        staged.unlink(missing_ok=True)
        transfers.pop(transfer, None)


_upload = ContextVar('bridge_upload', default=False)
SYSTEM_PATH = '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin'


def extraction_command(archive, target, checkpoint=3276):
    """Flush extraction writes about every 32 MiB under task memory limits.

    GNU tar's default record is 20 * 512 bytes. Without periodic writeback,
    unpacking large inputs can stall on dirty-page reclaim in a 1 GiB cgroup.
    Keep tar's link/mode handling and the existing upload timeout unchanged.
    """
    flush = shlex.join(['sync', '-f', target])
    extract = shlex.join(['tar', '--no-same-owner', f'--checkpoint={checkpoint}',
                         '--checkpoint-action=exec=' + flush, '-xf', archive, '-C', target])
    ordinary = shlex.join(['tar', '--no-same-owner', '-xf', archive, '-C', target])
    # Keep the established transfer behavior on images with non-GNU tar/sync.
    supported = "tar --version 2>/dev/null | grep -q 'GNU tar'"
    supported += " && sync --version 2>/dev/null | grep -q 'GNU coreutils'"
    return (f'if {supported}; then {extract} && {flush}; else {ordinary}; fi'
            + ' && ' + shlex.join(['rm', archive]))


def upload_command(cmd):
    if not _upload.get() or not isinstance(cmd, list) or cmd[1:2] != ['exec']:
        return cmd
    index = next((i for i, value in enumerate(cmd)
                  if value.startswith('instance://')), None)
    if index is None or cmd[index + 1:index + 3] not in (
            ['bash', '-c'], ['bash', '-lc']):
        return cmd
    # Both the executable and its environment are independent of login profiles.
    # This applies only to upload mkdir/extraction/verification, never task exec.
    script = cmd[index + 3:]
    if script and script[0].startswith('tar xf /workspace/.upload_tmp -C '):
        parts = shlex.split(script[0])
        if len(parts) != 8 or parts[:4] != ['tar', 'xf', '/workspace/.upload_tmp', '-C'] or parts[5:] != ['&&', 'rm', '/workspace/.upload_tmp']:
            raise ValueError('unrecognized bridge upload extraction command')
        script = [extraction_command(parts[2], parts[4]), *script[1:]]
    return [*cmd[:index], '--pwd', '/', cmd[index], '/usr/bin/env',
            '-u', 'BASH_ENV', '-u', 'ENV', f'PATH={SYSTEM_PATH}',
            '/bin/bash', '--noprofile', '--norc', '-c', *script]


def install_worker(worker):
    original = worker.ApptainerInstance.upload
    if getattr(original, '_upload_runtime', False):
        return

    @wraps(original)
    def upload(self, payload):
        token = _upload.set(True)
        try:
            if 'upload_chunk' in payload:
                return receive_chunk(self, payload, worker)
            return original(self, payload)
        finally:
            _upload.reset(token)

    upload._upload_runtime = True
    worker.ApptainerInstance.upload = upload
