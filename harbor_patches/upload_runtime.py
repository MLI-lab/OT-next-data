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
import time


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
    # Return phase measurements to the caller, rather than mutating the shared
    # environment: uploads for one environment can run concurrently.
    timing = {'read_encode': 0.0, 'submit': 0.0, 'wait_result': 0.0,
              'worker_receive': 0.0, 'worker_install': 0.0, 'worker_total': 0.0}
    count, size, worker_reports = 0, 0, 0
    started = time.monotonic()

    async def send(block, chunk=None):
        nonlocal count, size, worker_reports
        phase = time.monotonic()
        payload = {'env_id': instance._env_id,
                   'file_b64': base64.b64encode(block).decode(),
                   'target_path': target, 'is_dir': is_dir}
        timing['read_encode'] += time.monotonic() - phase
        if chunk is not None:
            payload['upload_chunk'] = chunk
        phase = time.monotonic()
        response = await bridge._async_http_post(f'{instance._bridge_url}/env/upload', payload)
        timing['submit'] += time.monotonic() - phase
        job_id = response.get('job_id')
        if not job_id:
            raise RuntimeError(f'Upload submit failed: {response}')
        phase = time.monotonic()
        result = await bridge._poll_job(instance._bridge_url, job_id, timeout_sec=120)
        timing['wait_result'] += time.monotonic() - phase
        instance._check_transfer_result('upload_dir' if is_dir else 'upload_file', result)
        if chunk is not None and result.get('upload_offset') != chunk['offset'] + len(block):
            raise RuntimeError('Bridge did not acknowledge chunked upload; update server and worker')
        worker_timing = result.get('upload_timings_seconds') or {}
        if worker_timing:
            worker_reports += 1
        for name in ('receive', 'install', 'total'):
            value = worker_timing.get(name)
            if isinstance(value, (int, float)) and value >= 0:
                timing['worker_' + name] += value
        count += 1
        size += len(block)

    async def read(length):
        phase = time.monotonic()
        block = await asyncio.to_thread(stream.read, length)
        timing['read_encode'] += time.monotonic() - phase
        return block

    # Preserve the single-request path and the bounded probe for small uploads.
    probe = await read(CHUNK_BYTES + 1)
    if len(probe) <= CHUNK_BYTES:
        await send(probe)
    else:
        del probe
        stream.seek(0)
        transfer = uuid.uuid4().hex
        offset = 0
        while True:
            block = await read(CHUNK_BYTES)
            final = not block
            await send(block, {'id': transfer, 'offset': offset, 'final': final})
            if final:
                break
            offset += len(block)
    return {'bytes': size, 'requests': count, 'chunk_bytes': CHUNK_BYTES,
            'worker_timing_reports': worker_reports,
            'timings_seconds': dict(timing, total=time.monotonic() - started)}


def install_client(bridge):
    original = bridge.ApptainerEnvironment.upload_dir
    if getattr(original, '_preserve_links', False):
        return

    @wraps(original)
    async def upload_dir(self, source_dir, target_dir):
        started = time.monotonic()
        with tempfile.TemporaryDirectory(prefix='harbor-upload-') as directory:
            archive = Path(directory) / 'input.tar'
            packing = time.monotonic()
            await asyncio.to_thread(directory_archive, source_dir, archive)
            packed = time.monotonic() - packing
            with archive.open('rb') as stream:
                result = await send_stream(bridge, self, stream, target_dir, True)
        result['directory_timings_seconds'] = {
            'archive': packed, 'total': time.monotonic() - started}
        return result

    async def upload_file(self, source_path, target_path):
        with open(source_path, 'rb') as stream:
            return await send_stream(bridge, self, stream, target_path, False)

    async def upload_runtime_archive(self, ref, target):
        from harbor_patches.runtime_transfer import request
        return await request(bridge, self, ref, target)

    upload_dir._preserve_links = True
    bridge.ApptainerEnvironment.upload_dir = upload_dir
    bridge.ApptainerEnvironment.upload_file = upload_file
    bridge.ApptainerEnvironment.upload_runtime_archive = upload_runtime_archive


def receive_chunk(instance, payload, worker):
    """Reassemble bounded requests on disk outside the task's filesystem."""
    started = time.monotonic()
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
        return {'return_code': 0, 'stdout': '', 'stderr': '', 'upload_offset': offset + len(data),
                'upload_timings_seconds': {'receive': time.monotonic() - started, 'install': 0.0}}
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
    received = time.monotonic() - started
    installing = time.monotonic()
    try:
        result = worker.subprocess.run(
            [worker.APPTAINER, 'exec', f'instance://{instance.instance_name}',
             'bash', '-c', script], capture_output=True, text=True, timeout=120)
        return {'return_code': result.returncode, 'stdout': (result.stdout or '')[:4000],
                'stderr': (result.stderr or '')[:2000], 'upload_offset': offset,
                'upload_timings_seconds': {'receive': received, 'install': time.monotonic() - installing}}
    finally:
        staged.unlink(missing_ok=True)
        transfers.pop(transfer, None)


_upload = ContextVar('bridge_upload', default=False)
SYSTEM_PATH = '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin'


def receive_directory(instance, payload, worker):
    """Install a small tar with one container invocation and a private spool."""
    started = time.monotonic()
    data = base64.b64decode(payload['file_b64'], validate=True)
    target = payload.get('target_path', '/workspace/upload')
    staged = Path(instance.staging_dir) / 'workspace' / ('.upload-' + uuid.uuid4().hex)
    with staged.open('xb') as stream:
        stream.write(data)
    container_path = '/workspace/' + staged.name
    quoted = shlex.quote(target)
    if target == '/tests':
        verify = ("if [ -f /tests/test.sh ]; then echo '__BRIDGE_UPLOAD_OK__'; "
                  "find /tests -maxdepth 2 -type f | sort | sed -n '1,50p'; "
                  "else echo '__BRIDGE_UPLOAD_MISSING__'; ls -la /tests; "
                  "find /tests -maxdepth 3 | sort | sed -n '1,80p'; exit 97; fi")
    else:
        verify = (f'if [ -e {quoted} ]; then find {quoted} -maxdepth 2 | sort | '
                  "sed -n '1,40p'; else printf '%s\\n' "
                  + shlex.quote('upload target missing: ' + target) + '; exit 98; fi')
    script = ('mkdir -p -- ' + quoted + ' && '
              + extraction_command(container_path, target) + ' && ' + verify)
    received = time.monotonic() - started
    installing = time.monotonic()
    try:
        result = worker.subprocess.run(
            [worker.APPTAINER, 'exec', f'instance://{instance.instance_name}',
             'bash', '-c', script], capture_output=True, text=True, timeout=120)
        return {'return_code': result.returncode, 'stdout': (result.stdout or '')[:4000],
                'stderr': (result.stderr or '')[:2000],
                'upload_timings_seconds': {'receive': received,
                    'install': time.monotonic() - installing,
                    'total': time.monotonic() - started}}
    finally:
        staged.unlink(missing_ok=True)


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
            if 'runtime_cache_upload' in payload:
                from harbor_patches.runtime_transfer import receive
                return receive(self, payload, worker)
            if 'upload_chunk' in payload:
                return receive_chunk(self, payload, worker)
            if (payload.get('is_dir') and isinstance(payload.get('file_b64'), str)
                    and len(payload['file_b64']) <= ((CHUNK_BYTES + 2) // 3) * 4):
                return receive_directory(self, payload, worker)
            started = time.monotonic()
            result = original(self, payload)
            if isinstance(result, dict):
                # The legacy worker owns decoding, staging and installation;
                # report their combined duration without inventing a split.
                result = dict(result)
                result['upload_timings_seconds'] = {
                    **(result.get('upload_timings_seconds') or {}),
                    'total': time.monotonic() - started}
            return result
        finally:
            _upload.reset(token)

    upload._upload_runtime = True
    worker.ApptainerInstance.upload = upload
