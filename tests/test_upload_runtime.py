import os
import subprocess
from types import SimpleNamespace

import pytest

from harbor_patches import upload_runtime as patch


def test_directory_payload_preserves_links_without_reading_targets(tmp_path):
    import base64
    import io
    import tarfile
    source = tmp_path / 'submission'
    source.mkdir()
    (source / 'source.java').write_text('source')
    (source / 'browserify').symlink_to('../absent/browserify')
    (source / 'outside').symlink_to('/must/not/be/read')
    with tarfile.open(fileobj=io.BytesIO(base64.b64decode(patch.directory_payload(source)))) as archive:
        assert archive.getmember('./browserify').issym()
        assert archive.getmember('./browserify').linkname == '../absent/browserify'
        assert archive.getmember('./outside').linkname == '/must/not/be/read'
        assert archive.extractfile('./source.java').read() == b'source'


def test_upload_ignores_poisoned_shell_startup(tmp_path):
    startup = tmp_path / 'startup'
    startup.write_text('export PATH=/missing\nexit 127\n')
    target = tmp_path / 'tests'
    target.mkdir()
    (target / 'test.sh').touch()
    cmd = ['apptainer', 'exec', 'instance://trial', 'bash', '-lc',
           f'find {target} -type f | sort | sed -n 1p']

    class Instance:
        def upload(self, payload):
            adapted = patch.upload_command(cmd)
            assert adapted[:5] == ['apptainer', 'exec', '--pwd', '/', 'instance://trial']
            return subprocess.run(adapted[5:], capture_output=True, text=True,
                                  env={**os.environ, 'PATH': '/missing',
                                       'BASH_ENV': str(startup), 'ENV': str(startup)})

    patch.install_worker(SimpleNamespace(ApptainerInstance=Instance))
    patch.install_worker(SimpleNamespace(ApptainerInstance=Instance))
    result = Instance().upload({})
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(target / 'test.sh')
    assert patch.upload_command(cmd) is cmd  # Ordinary task exec unchanged.


def test_upload_scope_is_reset_after_failure():
    class Instance:
        def upload(self, payload):
            raise RuntimeError('failed extraction')

    patch.install_worker(SimpleNamespace(ApptainerInstance=Instance))
    with pytest.raises(RuntimeError, match='failed extraction'):
        Instance().upload({})
    cmd = ['apptainer', 'exec', 'instance://trial', 'bash', '-lc', 'echo task']
    assert patch.upload_command(cmd) is cmd


def test_upload_tar_does_not_restore_foreign_owners():
    command = ['apptainer', 'exec', 'instance://trial', 'bash', '-c',
               'tar xf /workspace/.upload_tmp -C /tests && rm /workspace/.upload_tmp']
    class Instance:
        def upload(self, payload):
            return patch.upload_command(command)
    patch.install_worker(SimpleNamespace(ApptainerInstance=Instance))
    adapted = Instance().upload({})
    assert adapted[-1] == patch.extraction_command('/workspace/.upload_tmp', '/tests')
    assert '--checkpoint=3276' in adapted[-1]
    assert '--checkpoint-action=exec=' in adapted[-1]
    assert patch.upload_command(command) is command


def test_checkpoint_extraction_preserves_files_links_modes_and_quotes(tmp_path):
    import io
    import tarfile
    archive = tmp_path / "input ' $(must-not-run).tar"
    target = tmp_path / "target ' $(must-not-run)"
    target.mkdir()
    data = b'input data\n' * 4000
    with tarfile.open(archive, 'w') as output:
        member = tarfile.TarInfo('input.bin')
        member.size, member.mode, member.uid, member.gid = len(data), 0o755, 12345, 12345
        output.addfile(member, io.BytesIO(data))
        link = tarfile.TarInfo('link')
        link.type, link.linkname = tarfile.SYMTYPE, 'input.bin'
        output.addfile(link)
    result = subprocess.run(['/bin/sh', '-c', patch.extraction_command(str(archive), str(target), checkpoint=1)],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert (target / 'input.bin').read_bytes() == data
    assert (target / 'input.bin').stat().st_uid == os.getuid()
    assert (target / 'input.bin').stat().st_mode & 0o777 == 0o755
    assert (target / 'link').is_symlink()
    assert os.readlink(target / 'link') == 'input.bin'
    assert not archive.exists()


def test_extraction_keeps_legacy_behavior_without_gnu_sync(tmp_path):
    import io
    import tarfile
    archive, target = tmp_path / 'input.tar', tmp_path / 'output'
    target.mkdir()
    with tarfile.open(archive, 'w') as output:
        member = tarfile.TarInfo('file')
        member.size = 4
        output.addfile(member, io.BytesIO(b'data'))
    binaries = tmp_path / 'bin'
    binaries.mkdir()
    sync = binaries / 'sync'
    sync.write_text('#!/bin/sh\nexit 1\n')
    sync.chmod(0o755)
    result = subprocess.run(['/bin/sh', '-c', patch.extraction_command(str(archive), str(target), checkpoint=1)],
                            env={**os.environ, 'PATH': str(binaries) + ':' + os.environ['PATH']},
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert (target / 'file').read_bytes() == b'data'
    assert not archive.exists()
