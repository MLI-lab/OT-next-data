import os
import subprocess
from types import SimpleNamespace

import pytest

from harbor_patches import upload_runtime as patch


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
