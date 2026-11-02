import io
import json
from pathlib import Path
import shutil
import subprocess
import tarfile

import pytest

from hpc.offline_prep.image_tool_libraries import dependencies, prepare, validate_members


def test_missing_library_is_not_a_ready_dependency_closure():
    with pytest.raises(ValueError, match='unresolved'):
        dependencies('libnode.so.108 => not found\n')
    assert dependencies('linux-vdso.so.1 (0x123)\nlibc.so.6 => /lib/libc.so.6 (0x456)\n') == ['/lib/libc.so.6']


def test_copied_elf_closure_runs_after_relocation(tmp_path, monkeypatch):
    import hpc.offline_prep.image_python_runtime as image
    monkeypatch.setattr(image, 'command', lambda source, scratch, *args: list(map(str, args)))
    runtime = tmp_path/'runtime'
    (runtime/'bin').mkdir(parents=True)
    (runtime/'native-tools').mkdir()
    shutil.copyfile('/usr/bin/true', runtime/'bin/true')
    (runtime/'bin/true').chmod(0o755)
    (runtime/'native-tools/true').symlink_to('../bin/true')
    records = prepare({'executables': {'true': '/usr/bin/true'}}, runtime, 'pristine', tmp_path)
    assert any(name.startswith('libc.so') for name in records)
    relocated = tmp_path/'relocated'
    runtime.rename(relocated)
    subprocess.run([str(relocated/'native-tools/true')], check=True)


def test_valid_checksum_archive_still_needs_all_declared_native_libraries(tmp_path):
    archive = tmp_path/'runtime.tar.gz'
    with tarfile.open(archive, 'w:gz') as stream:
        payload = json.dumps({'libnode.so.108': '/usr/lib/libnode.so.108'}).encode()
        info = tarfile.TarInfo('python/native-libs/sources.json')
        info.size = len(payload)
        stream.addfile(info, io.BytesIO(payload))
    with tarfile.open(archive) as stream:
        members = {m.name: m for m in stream}
    with pytest.raises(ValueError, match='Incomplete native library: libnode'):
        validate_members(archive, {'executables': {'node': '/usr/bin/node'}}, members)
