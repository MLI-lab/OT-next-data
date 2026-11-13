"""Private Python for diagnostics in images without an interpreter.

Copies the controller's interpreter, standard library and ELF dependencies.
No installation, network access or changes to the task's Python are needed.
"""
from contextlib import asynccontextmanager
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import sysconfig
import tarfile
import tempfile


@lru_cache(maxsize=1)
def bundle():
    temporary = tempfile.TemporaryDirectory(prefix='ot-python-probe-')
    root = Path(temporary.name)
    executable = Path(sys._base_executable).resolve()
    stdlib = Path(sysconfig.get_path('stdlib'))
    version = f'python{sys.version_info.major}.{sys.version_info.minor}'
    (root / 'bin').mkdir()
    shutil.copy2(executable, root / 'bin/python3')
    shutil.copytree(stdlib, root / 'lib' / version,
                    ignore=shutil.ignore_patterns('site-packages', '__pycache__', 'test', 'tests', 'ensurepip', 'idlelib', 'tkinter', '_tkinter*', '_test*'))
    dependencies = {}
    for binary in [executable, *(stdlib / 'lib-dynload').glob('*.so')]:
        if binary.name.startswith(('_tkinter', '_test')):
            continue
        linked = subprocess.run(['ldd', str(binary)], check=True, text=True,
                                capture_output=True, timeout=10).stdout
        if 'not found' in linked:
            raise RuntimeError('Controller Python has missing ELF dependencies')
        for name in re.findall(r'(/[^\s()]+)', linked):
            source = Path(name).resolve()
            key = Path(name).name
            if key in dependencies and dependencies[key] != source:
                raise RuntimeError('Conflicting Python probe dependency: ' + key)
            dependencies[key] = source
    loader = next((name for name in dependencies if name.startswith('ld-linux')), None)
    if loader is None:
        raise RuntimeError('Python probe requires a Linux glibc ELF loader')
    for name, source in dependencies.items():
        shutil.copy2(source, root / 'lib' / name)
    command = [str(root / 'lib' / loader), '--library-path', str(root / 'lib'),
               str(root / 'bin/python3'), '-I', '-S']
    subprocess.run([*command, '-c', 'import json, hashlib, tarfile, subprocess, stat; print("ok")'],
                   check=True, capture_output=True, timeout=15)
    manifest = {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in root.rglob('*') if p.is_file()}
    (root / 'manifest.json').write_text(json.dumps(manifest, sort_keys=True))
    archive = root / 'probe.tar.gz'
    with tarfile.open(archive, 'w:gz') as tar:
        for name in ('bin', 'lib', 'manifest.json'):
            tar.add(root / name, arcname=name)
    return temporary, archive, hashlib.sha256(archive.read_bytes()).hexdigest(), loader


@asynccontextmanager
async def staged(environment):
    _, archive, digest, loader = bundle()
    result = await environment.exec('mktemp -d /tmp/.ot-python-probe-XXXXXXXXXXXX', timeout_sec=15)
    root = (result.stdout or '').strip()
    if result.return_code or not re.fullmatch(r'/tmp/\.ot-python-probe-[A-Za-z0-9]+', root):
        raise RuntimeError('Could not allocate private Python probe storage')
    q = shlex.quote(root)
    try:
        await environment.upload_file(source_path=archive, target_path=root + '/probe.tar.gz')
        result = await environment.exec(
            f'cd {q} && printf "%s  probe.tar.gz\\n" {shlex.quote(digest)} | sha256sum -c - && '
            'tar --no-same-owner -xzf probe.tar.gz && chmod -R a+rX .', timeout_sec=60)
        if result.return_code:
            raise RuntimeError('Could not stage Python probe: ' + (result.stderr or '')[-1000:])
        command = 'LD_PRELOAD= LD_LIBRARY_PATH= ' + shlex.join([
            root + '/lib/' + loader, '--library-path', root + '/lib', root + '/bin/python3', '-I', '-S'])
        yield command, digest
    finally:
        result = await environment.exec('rm -rf -- ' + q, timeout_sec=15)
        if result.return_code:
            raise RuntimeError('Could not remove the private Python probe runtime')
