"""Keep bridge upload bookkeeping independent of task shell startup files."""
from contextvars import ContextVar
from functools import wraps
import asyncio
import base64
import io
import shlex
import tarfile


def directory_payload(source):
    """Transfer links as links, including dangling links, without reading their targets."""
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w', dereference=False) as archive:
        archive.add(str(source), arcname='.')
    return base64.b64encode(stream.getvalue()).decode()


def install_client(bridge):
    original = bridge.ApptainerEnvironment.upload_dir
    if getattr(original, '_preserve_links', False):
        return

    @wraps(original)
    async def upload_dir(self, source_dir, target_dir):
        payload = await asyncio.to_thread(directory_payload, source_dir)
        response = await bridge._async_http_post(f'{self._bridge_url}/env/upload', {
            'env_id': self._env_id, 'file_b64': payload,
            'target_path': target_dir, 'is_dir': True,
        })
        job_id = response.get('job_id')
        if not job_id:
            raise RuntimeError(f'Upload submit failed: {response}')
        result = await bridge._poll_job(self._bridge_url, job_id, timeout_sec=120)
        self._check_transfer_result('upload_dir', result)

    upload_dir._preserve_links = True
    bridge.ApptainerEnvironment.upload_dir = upload_dir


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
            return original(self, payload)
        finally:
            _upload.reset(token)

    upload._upload_runtime = True
    worker.ApptainerInstance.upload = upload
