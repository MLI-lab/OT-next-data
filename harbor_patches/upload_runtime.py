"""Keep bridge upload bookkeeping independent of task shell startup files."""
from contextvars import ContextVar
from functools import wraps
import asyncio
import base64
import io
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
        script = [script[0].replace('tar xf ', 'tar --no-same-owner -xf ', 1), *script[1:]]
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
