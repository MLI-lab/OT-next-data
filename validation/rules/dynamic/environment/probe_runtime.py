"""A private, relocatable copy of the controller's curl for minimal images.

No package installation or downloads. The bundled ELF loader and libraries keep
the probe independent of the image's libc; requests still run inside the task's
network namespace with its DNS and proxy settings. Unsupported hosts fail closed.
"""
from contextlib import asynccontextmanager
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import re
import shlex
import shutil
import ssl
import subprocess
import tarfile
import tempfile


@lru_cache(maxsize=1)
def bundle():
    executable = shutil.which('curl')
    if not executable:
        raise RuntimeError('The validation controller has no curl to supply')
    libraries = subprocess.run(['ldd', executable], check=True, text=True,
                               capture_output=True, timeout=10).stdout
    if 'not found' in libraries:
        raise RuntimeError('Controller curl has missing libraries')
    dependencies = [Path(p) for p in re.findall(r'(/[^\s()]+)', libraries)]
    loader = next((p for p in dependencies if p.name.startswith('ld-linux')), None)
    if loader is None:
        raise RuntimeError('Probe runtime requires a Linux glibc ELF loader')
    ca = ssl.get_default_verify_paths().cafile
    if not ca or not Path(ca).is_file():
        raise RuntimeError('No trusted CA bundle for the network probe')
    temporary = tempfile.TemporaryDirectory(prefix='ot-network-probe-')
    root = Path(temporary.name)
    files = {'curl': Path(executable), 'ca.pem': Path(ca)}
    for p in dependencies:
        name = 'lib/' + p.name
        if name in files and files[name].resolve() != p.resolve():
            raise RuntimeError('Conflicting network probe library: ' + name)
        files[name] = p
    manifest = {}
    for name, source in files.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        target.chmod(0o755 if name == 'curl' or name.startswith('lib/') else 0o644)
        manifest[name] = hashlib.sha256(target.read_bytes()).hexdigest()
    subprocess.run([str(root / 'lib' / loader.name), '--library-path', str(root / 'lib'),
                    str(root / 'curl'), '--version'], check=True, capture_output=True, timeout=10)
    (root / 'manifest.json').write_text(json.dumps(manifest, sort_keys=True))
    archive = root / 'probe.tar.gz'
    with tarfile.open(archive, 'w:gz') as tar:
        for name in [*files, 'manifest.json']:
            tar.add(root / name, arcname=name, recursive=False)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    return temporary, archive, digest, loader.name


@asynccontextmanager
async def staged(environment):
    """Stage only for this inspection and remove the private copy afterwards."""
    _, archive, digest, loader = bundle()
    result = await environment.exec('mktemp -d /tmp/.ot-network-probe-XXXXXXXXXXXX', timeout_sec=15)
    root = (result.stdout or '').strip()
    if result.return_code or not re.fullmatch(r'/tmp/\.ot-network-probe-[A-Za-z0-9]+', root):
        raise RuntimeError('Could not allocate private network probe storage')
    q = shlex.quote(root)
    try:
        await environment.upload_file(source_path=archive, target_path=root + '/probe.tar.gz')
        result = await environment.exec(
            f'cd {q} && printf "%s  probe.tar.gz\\n" {shlex.quote(digest)} | sha256sum -c - && '
            'tar --no-same-owner -xzf probe.tar.gz && chmod -R a+rX . && '
            + shlex.join([root + '/lib/' + loader, '--library-path', root + '/lib', root + '/curl', '--version']),
            timeout_sec=30)
        if result.return_code:
            raise RuntimeError('Network probe runtime could not start: ' + (result.stderr or '')[-1000:])
        # Ignore task LD_PRELOAD/LD_LIBRARY_PATH and curlrc, but preserve proxy
        # variables and the container's resolv.conf for the actual network test.
        command = 'LD_PRELOAD= LD_LIBRARY_PATH= ' + shlex.join([
            root + '/lib/' + loader, '--library-path', root + '/lib', root + '/curl',
            '-q', '--cacert', root + '/ca.pem'])
        yield command, digest
    finally:
        result = await environment.exec('rm -rf -- ' + q, timeout_sec=15)
        if result.return_code:
            raise RuntimeError('Could not remove the private network probe runtime')
