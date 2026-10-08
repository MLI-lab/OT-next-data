"""Keep bridge upload bookkeeping independent of task shell startup files."""
from contextvars import ContextVar
from functools import wraps


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
    return [*cmd[:index], '--pwd', '/', cmd[index], '/usr/bin/env',
            '-u', 'BASH_ENV', '-u', 'ENV', f'PATH={SYSTEM_PATH}',
            '/bin/bash', '--noprofile', '--norc', '-c', *cmd[index + 3:]]


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
