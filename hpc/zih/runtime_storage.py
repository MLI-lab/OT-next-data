"""Prepare a data-resident Python base and reuse existing data-resident packages.

Does not mutate the original environment. The replacement environment shares
its site-packages; retain the original environment's package directory.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile


def data_path(path):
    path = Path(path).resolve()
    if not any(str(path).startswith(p) for p in ('/data/horse/', '/data/ws/', '/data/cat/')):
        raise ValueError('Persistent runtime/packages must be in a data workspace: ' + str(path))
    return path


def inspect_python(python):
    return json.loads(subprocess.check_output([str(python), '-I', '-c',
        'import json,sys,sysconfig; print(json.dumps(dict(base=sys.base_prefix, '
        'version=sys.version, purelib=sysconfig.get_path("purelib"), '
        'platlib=sysconfig.get_path("platlib"), executable=sys.executable)))'], text=True, timeout=60))


def make_environment(base, source, destination, info):
    """Create launchers with a new Python base, sharing packages on data storage."""
    subprocess.run([str(base / 'bin/python3'), '-m', 'venv', '--without-pip', str(destination)],
                   check=True, timeout=120, env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'})
    target = inspect_python(destination / 'bin/python')
    sites = sorted({str(data_path(info[k])) for k in ('purelib', 'platlib')})
    # addsitedir also honors installed .pth files (e.g. editable Harbor installs).
    # Third-party/editable source paths are not silently relocated by this tool.
    (Path(target['purelib']) / 'zih-shared-packages.pth').write_text(
        'import site; ' + '; '.join('site.addsitedir(' + repr(p) + ')' for p in sites) + '\n')
    for entry in (source / 'bin').iterdir():
        if entry.name.startswith(('python', 'activate')) or entry.name == 'Activate.ps1':
            continue
        if not entry.is_file():
            continue
        out = destination / 'bin' / entry.name
        raw = entry.read_bytes()
        if raw.startswith(b'#!') and b'python' in raw.split(b'\n', 1)[0]:
            raw = ('#!' + str(destination / 'bin/python') + '\n').encode() + raw.split(b'\n', 1)[1]
        out.write_bytes(raw)
        out.chmod(entry.stat().st_mode & 0o777)
    return destination / 'bin/python'


def prepare(source, store, local=None):
    source, store = data_path(source), data_path(store)
    store.mkdir(parents=True, exist_ok=True)
    # Once prepared, do not execute the old home-backed interpreter at startup.
    # A changed venv config invalidates the pointer and triggers fresh inspection.
    config_key = hashlib.sha256((str(source) + (source / 'pyvenv.cfg').read_text()).encode()).hexdigest()[:16]
    pointer = store / ('environment-' + config_key + '.json')
    if pointer.exists():
        cached = json.loads(pointer.read_text())
        info = cached['original']
    else:
        config = dict(line.split(' = ', 1) for line in (source / 'pyvenv.cfg').read_text().splitlines() if ' = ' in line)
        info = None
        for ready in store.glob('*/ready.json'):
            cached = json.loads(ready.read_text())
            previous = cached['original']
            if (cached['source'] == str(source) and config.get('home') and config.get('version_info')
                    and Path(config['home']).resolve().parent == Path(previous['base']).resolve()
                    and previous['version'].split()[0] == config['version_info']):
                info = previous
                break
        if info is None:
            info = inspect_python(source / 'bin/python')
    base = Path(info['base'])
    key = hashlib.sha256((str(source) + str(base) + info['version']).encode()).hexdigest()[:16]
    target = store / key
    with (store / (key + '.lock')).open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        marker = target / 'ready.json'
        if not marker.exists():
            # Only first preparation touches the original base. A ready runtime
            # must remain usable even when home is unavailable.
            if not (base / 'bin/python3').exists() or base in (Path('/usr'), Path('/usr/local')):
                raise ValueError('Expected a relocatable standalone Python base, got ' + str(base))
            if target.exists():
                raise RuntimeError('Incomplete runtime preparation; inspect before removing: ' + str(target))
            target.mkdir()
            shutil.copytree(base, target / 'python', symlinks=True)
            # Detect interpreter symlinks escaping into home before using the copy.
            copied = inspect_python(target / 'python/bin/python3')
            data_path(copied['base'])
            if Path(copied['base']).resolve() != (target / 'python').resolve():
                raise ValueError('Python is not relocatable: ' + str(copied))
            make_environment(target / 'python', source, target / 'venv', info)
            with tarfile.open(target / 'python.tar', 'w') as tar:
                tar.add(target / 'python', arcname='python')
            marker.write_text(json.dumps({'source': str(source), 'original': info,
                'packages_shared_from': str(source),
                'archive_sha256': hashlib.sha256((target / 'python.tar').read_bytes()).hexdigest()}, indent=2) + '\n')
        if not pointer.exists():
            # Atomic publication while holding this runtime's preparation lock.
            temporary = pointer.with_suffix('.tmp')
            temporary.write_text(marker.read_text())
            temporary.replace(pointer)
    result = {'python': str(target / 'venv/bin/python'), 'runtime': str(target),
              'packages_shared_from': str(source)}
    if local:
        local = Path(local).resolve()
        dest = local / 'runtime'
        dest.mkdir(exist_ok=False)
        # Extract only the archive just built from our trusted Python tree.
        archive = dest / 'python.tar'
        shutil.copyfile(target / 'python.tar', archive)
        expected = json.loads((target / 'ready.json').read_text())['archive_sha256']
        if hashlib.sha256(archive.read_bytes()).hexdigest() != expected:
            raise ValueError('Runtime archive checksum mismatch')
        subprocess.run(['tar', '-xf', str(archive), '-C', str(dest)], check=True, timeout=120)
        archive.unlink()
        python = make_environment(dest / 'python', source, dest / 'venv', info)
        staged = inspect_python(python)
        if Path(staged['base']).resolve() != dest / 'python':
            raise ValueError('Staged interpreter still uses a non-local base')
        result['static_python'] = str(python)
    return result


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('venv', type=Path)
    ap.add_argument('store', type=Path)
    ap.add_argument('--local', type=Path)
    a = ap.parse_args()
    print(json.dumps(prepare(a.venv, a.store, a.local)))
