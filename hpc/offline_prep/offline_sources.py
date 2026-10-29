"""Checksum-pinned task build inputs fetched by the trusted preparation host.

These are source bytes, not an installed runtime or an HTTP interception layer.
Datasource setup must copy/use them at the original build step explicitly.
"""
import hashlib
import os
from pathlib import Path
import re
import shutil
from urllib.parse import urlsplit
from urllib.request import urlopen


def validate(sources):
    if not isinstance(sources, list):
        raise ValueError('Offline sources must be a list')
    names = set()
    for source in sources:
        if not isinstance(source, dict) or set(source) != {'url', 'sha256', 'filename'}:
            raise ValueError('Offline source requires URL, filename and checksum')
        url = urlsplit(source['url'])
        if (url.scheme != 'https' or not url.hostname or url.username or url.password or url.fragment
                or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.+-]*', source['filename'])
                or not re.fullmatch(r'[0-9a-f]{64}', source['sha256'])
                or source['filename'] in names):
            raise ValueError('Invalid or duplicate pinned offline source')
        names.add(source['filename'])
    return sources


def prepare(sources, output, opener=urlopen):
    records = []
    for source in validate(sources):
        target = Path(output) / 'sources' / source['filename']
        target.parent.mkdir(parents=True, exist_ok=True)
        local = os.environ.get('OT_RUNTIME_SOURCE_FILES')
        stream = ((Path(local) / source['filename']).open('rb') if local
                  else opener(source['url'], timeout=180))
        with stream, target.open('xb') as destination:
            shutil.copyfileobj(stream, destination, 8 * 1024 * 1024)
        with target.open('rb') as stream:
            checksum = hashlib.file_digest(stream, 'sha256').hexdigest()
        if checksum != source['sha256']:
            raise ValueError('Offline source checksum mismatch: ' + source['filename'])
        records.append({**source, 'bytes': target.stat().st_size, 'verified': True})
    return records


def verify_staged(records, root):
    for source in records:
        path = Path(root) / 'sources' / source['filename']
        with path.open('rb') as stream:
            checksum = hashlib.file_digest(stream, 'sha256').hexdigest()
        if path.stat().st_size != source['bytes'] or checksum != source['sha256']:
            raise ValueError('Staged offline source differs: ' + source['filename'])
