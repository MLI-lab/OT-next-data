#!/usr/bin/env python3
"""Download the original TaskTrove parquet directly to ZIH data storage."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import urllib.request

REVISION = '946884702046be1a7dcea2638186ad6b0d2ea103'
REMOTE_PATH = 'deprecated/DCAgent__inferredbugs-sandboxes-verifier/tasks.parquet'
SHA256 = '096d1405799fdc9b208bc879ab4a6a9cdbab4c90533cf77921279eb9c2b19caa'
SIZE = 54823994
URL = f'https://huggingface.co/datasets/open-thoughts/TaskTrove/resolve/{REVISION}/{REMOTE_PATH}'


def main():
    root = Path(os.environ.get('IB_ROOT', '/data/horse/ws/frwe188h-trp-shared/inferredbugs')).resolve()
    if not any(base in root.parents for base in map(Path, ('/data/horse', '/data/ws', '/data/cat'))):
        raise SystemExit('IB_ROOT must be a workspace under /data/horse, /data/ws, or /data/cat; never home')
    destination = root / 'upstream' / 'tasks.parquet'
    destination.parent.mkdir(parents=True, exist_ok=True)

    def digest(path):
        value = hashlib.sha256()
        with path.open('rb') as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b''):
                value.update(chunk)
        return value.hexdigest()

    if destination.exists():
        if destination.stat().st_size != SIZE or digest(destination) != SHA256:
            raise SystemExit(f'Existing file differs from pinned upstream: {destination}')
    else:
        partial = None
        try:
            with tempfile.NamedTemporaryFile(dir=destination.parent, suffix='.partial', delete=False) as out:
                partial = Path(out.name)
                with urllib.request.urlopen(URL, timeout=120) as response:
                    for chunk in iter(lambda: response.read(1024 * 1024), b''):
                        out.write(chunk)
            if partial.stat().st_size != SIZE or digest(partial) != SHA256:
                raise RuntimeError('Upstream size/SHA-256 mismatch')
            partial.replace(destination)
        finally:
            if partial is not None:
                partial.unlink(missing_ok=True)
    manifest = dict(repository='open-thoughts/TaskTrove', revision=REVISION,
                    path=REMOTE_PATH, url=URL, sha256=SHA256, bytes=SIZE)
    destination.with_suffix('.manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(f'{destination}: {SIZE} bytes, SHA-256 verified')


if __name__ == '__main__':
    main()
