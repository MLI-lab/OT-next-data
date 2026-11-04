"""Cache failures install only through trusted controller tools."""
import asyncio
import io
import json
from pathlib import Path
import tarfile

import pytest

from data.utils.verifier_runtime import preparation_command, runtime_reference, runtime_spec
from harbor_patches import verifier_bundle as bundle


def test_checksum_valid_archive_missing_declared_tool_requires_installation(tmp_path):
    spec = runtime_spec(python='3.13.9', requirements=['pytest==8.4.1'], tools=['pytest'])
    key = bundle.spec_id(spec)
    archive = tmp_path/(key+'.tar.gz')
    with tarfile.open(archive, 'w:gz') as stream:
        member = tarfile.TarInfo('python/bin/python3')
        member.mode = 0o755
        stream.addfile(member)
    manifest = dict(format=bundle.FORMAT, id=key, spec=spec, status='ready',
                    archive_sha256=bundle.digest(archive))
    (tmp_path/(key+'.json')).write_text(json.dumps(manifest))
    path, reason = bundle.lookup(manifest, root=tmp_path, preparation=True)
    assert path is None
    assert 'Incomplete runtime executable: pytest' in reason
