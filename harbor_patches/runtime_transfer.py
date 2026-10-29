"""Opt-in, checksum-bound worker-cache copies into private runtime staging."""
import hashlib
import os
from pathlib import Path
import re
import shlex
import stat
import time
import uuid


def identity(ref, target):
    if not all(isinstance(ref.get(k), str) and re.fullmatch('[a-f0-9]{64}', ref[k])
               for k in ('id', 'archive_sha256')):
        raise ValueError('Invalid runtime transfer identity')
    if not (re.fullmatch(r'/(?:tests/\.ot-verifier|setup_files/\.ot-task)-(?:runtime|offline)-[A-Za-z0-9]+/(?:runtime|packages)\.tar\.gz', target)
            or re.fullmatch(r'/tests/\.ot-verifier-toolchain-[A-Za-z0-9]+/toolchain\.tar\.gz', target)):
        raise ValueError('Runtime transfer requires a private staging archive')


async def upload(environment, archive, ref, target):
    """Keep ordinary backends and default transfers unchanged."""
    method = getattr(environment, 'upload_runtime_archive', None)
    if os.environ.get('OT_RUNTIME_CACHE_TRANSFER') == '1' and method is not None:
        identity(ref, target)
        result = await method(ref, target)
        if result is not None:
            return result
    return await environment.upload_file(source_path=archive, target_path=target)


async def request(bridge, environment, ref, target):
    identity(ref, target)
    status = await bridge._async_http_get(environment._bridge_url + '/status')
    if status.get('runtime_cache_upload_protocol') != 1:
        return None
    # Older workers may install this harmless placeholder into the generated
    # private archive path. Without an explicit acknowledgement, ordinary upload
    # overwrites it before any checksum/extraction. No arbitrary target is allowed.
    payload = {'env_id': environment._env_id, 'file_b64': 'AA==',
               'target_path': target, 'is_dir': False,
               'runtime_cache_upload': {k: ref[k] for k in ('id', 'archive_sha256')}}
    response = await bridge._async_http_post(environment._bridge_url + '/env/upload', payload)
    if not response.get('job_id'):
        raise RuntimeError('Runtime cache transfer submission failed')
    result = await bridge._poll_job(environment._bridge_url, response['job_id'], timeout_sec=120)
    if result.get('runtime_cache_upload_protocol') != 1 or result.get('runtime_cache_miss'):
        return None
    environment._check_transfer_result('upload_runtime_archive', result)
    if result.get('archive_sha256') != ref['archive_sha256']:
        raise RuntimeError('Runtime cache transfer checksum acknowledgement differs')
    return {'transport': 'worker-cache-copy', 'bytes': result['bytes'], 'requests': 1,
            'archive_sha256': result['archive_sha256'],
            'worker_timings_seconds': result['worker_timings_seconds']}


def receive(instance, payload, worker):
    ref, target = payload['runtime_cache_upload'], payload['target_path']
    identity(ref, target)
    miss = {'return_code': 0, 'runtime_cache_upload_protocol': 1, 'runtime_cache_miss': True}
    if os.environ.get('OT_RUNTIME_CACHE_TRANSFER') != '1':
        return miss
    from harbor_patches.verifier_bundle import cache_root
    started = time.monotonic()
    staged = Path(instance.staging_dir) / 'workspace' / ('.upload-' + uuid.uuid4().hex)
    created = False
    try:
        try:
            source = cache_root() / (ref['id'] + '.tar.gz')
            fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except (OSError, ValueError):
            return miss
        checksum, size = hashlib.sha256(), 0
        with os.fdopen(fd, 'rb') as incoming:
            if not stat.S_ISREG(os.fstat(incoming.fileno()).st_mode):
                return miss
            with staged.open('xb') as outgoing:
                created = True
                while block := incoming.read(1024 * 1024):
                    checksum.update(block); size += len(block); outgoing.write(block)
        if checksum.hexdigest() != ref['archive_sha256']:
            return miss
        copied = time.monotonic() - started
        installing = time.monotonic()
        container_path = '/workspace/' + staged.name
        script = ('mkdir -p -- ' + shlex.quote(str(Path(target).parent)) + ' && rm -f -- '
                  + shlex.quote(target) + ' && cp -- ' + shlex.quote(container_path) + ' ' + shlex.quote(target))
        result = worker.subprocess.run(
            [worker.APPTAINER, 'exec', f'instance://{instance.instance_name}', 'bash', '-c', script],
            capture_output=True, text=True, timeout=120)
        return {'return_code': result.returncode, 'stdout': (result.stdout or '')[:4000],
                'stderr': (result.stderr or '')[:2000], 'runtime_cache_upload_protocol': 1,
                'archive_sha256': ref['archive_sha256'], 'bytes': size,
                'worker_timings_seconds': {'copy_and_hash': copied, 'install': time.monotonic() - installing,
                                          'total': time.monotonic() - started}}
    finally:
        if created:
            staged.unlink(missing_ok=True)
