import io
import subprocess
import tarfile

import pytest

from data.seta.patch import TEST_SH, pack, patch_task, unpack


def test_repair_only_changes_image_dependencies_and_test_wrapper():
    original = {
        'task.toml': (b'version="1.0"\n[verifier]\ntimeout_sec=120\n[environment]\ncpus=1\nmemory="2G"\nstorage="10G"\n', 0o644),
        'instruction.md': (b'Solve the original task.', 0o644),
        'environment/Dockerfile': (b'FROM ubuntu:24.04\nWORKDIR /app\n', 0o644),
        'setup_files/setup.sh': (b'#!/bin/bash\necho setup\n', 0o755),
        'setup_files/context.sh': (b'cd /app\n', 0o644),
        'solution/solve.sh': (b'#!/bin/bash\necho gold\n', 0o755),
        'tests/test_outputs.py': (b'def test_output(): pass\n', 0o644),
    }
    source = pack(original)
    patched = patch_task(source)
    assert patched == patch_task(source)
    files = unpack(patched)
    for name in ('instruction.md', 'solution/solve.sh', 'tests/test_outputs.py', 'setup_files/context.sh'):
        assert files[name] == original[name]
    assert set(files) == set(original) | {'tests/test.sh'}
    for name in original:
        if name != 'environment/Dockerfile':
            assert files[name] == original[name]
    assert b'pytest==8.4.1 pytest-json-ctrf==0.3.5' in files['environment/Dockerfile'][0]
    assert b'--ctrf' in files['tests/test.sh'][0]
    with pytest.raises(ValueError, match='already been patched'):
        patch_task(patched)


@pytest.mark.parametrize('exit_code, reward', [(0, '1'), (1, '0'), (2, None), (3, None), (4, None), (5, None), (127, None)])
def test_verifier_does_not_reward_infrastructure_errors(tmp_path, exit_code, reward):
    python = tmp_path / 'python'
    python.write_text(f'#!/bin/bash\nexit {exit_code}\n')
    python.chmod(0o755)
    logs = tmp_path / 'logs'
    logs.mkdir()
    (logs / 'reward.txt').write_text('1')  # stale rewards must be removed
    script = TEST_SH.replace('/usr/bin/python3', str(python)).replace('/logs/verifier', str(logs))
    result = subprocess.run(['bash', '-c', script], capture_output=True)
    assert result.returncode == (0 if reward is not None else exit_code)
    assert ((logs / 'reward.txt').read_text().strip() if (logs / 'reward.txt').exists() else None) == reward


def test_patch_rejects_archive_symlinks():
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w') as archive:
        member = tarfile.TarInfo('tests/escape')
        member.type, member.linkname = tarfile.SYMTYPE, '/etc/passwd'
        archive.addfile(member)
    with pytest.raises(ValueError, match='Unsafe archive'):
        unpack(buf.getvalue())
