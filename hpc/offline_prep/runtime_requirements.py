"""Verify pinned source distributions before passing them to trusted installers."""
import hashlib
import os
from pathlib import Path
import shutil
from urllib.request import urlopen

from data.utils.verifier_runtime import requirement_source


def prepare(requirements, scratch, opener=urlopen):
    inputs, provenance = [], []
    for value in requirements:
        source = requirement_source(value)
        if source is None:
            inputs.append(value)
            continue
        target = Path(scratch) / 'source-inputs' / source['sha256'] / source['filename']
        target.parent.mkdir(parents=True, exist_ok=True)
        wheelhouse = os.environ.get('OT_RUNTIME_WHEELHOUSE')
        if target.is_file():
            pass
        elif wheelhouse:
            candidate = Path(wheelhouse) / source['filename']
            with candidate.open('rb') as stream, target.open('xb') as output:
                shutil.copyfileobj(stream, output, 8 * 1024 * 1024)
        else:
            with opener(source['url'].split('#', 1)[0], timeout=180) as stream, target.open('xb') as output:
                shutil.copyfileobj(stream, output, 8 * 1024 * 1024)
        with target.open('rb') as stream:
            checksum = hashlib.file_digest(stream, 'sha256').hexdigest()
        if checksum != source['sha256']:
            raise ValueError('Pinned dependency source checksum mismatch: ' + source['name'])
        inputs.append(source['name'] + ' @ ' + target.as_uri())
        provenance.append({**source, 'bytes': target.stat().st_size, 'verified': True})
    return inputs, provenance
