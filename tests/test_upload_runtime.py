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
    assert adapted[-1] == 'tar --no-same-owner -xf /workspace/.upload_tmp -C /tests && rm /workspace/.upload_tmp'
    assert patch.upload_command(command) is command
