"""Published-input integrity and setup instruction migration."""
import hashlib

import pytest

from data.multiswe.patch import export_published_runtime, release_repair_files


@pytest.mark.parametrize('setting', ['', 'allow_internet = true\n', 'allow_internet = false\n'])
def test_audit_patch_always_starts_offline(setting):
    import tomllib
    from data.multiswe.patch import offline_configuration
    original = ('[agent]\ntimeout_sec = 1200\n[environment]\n' + setting + 'cpus = 4\n').encode()
    result = offline_configuration(original)
    parsed = tomllib.loads(result.decode())
    assert parsed['environment'] == {'allow_internet': False, 'cpus': 4}
    assert parsed['agent']['timeout_sec'] == 1200
    assert offline_configuration(result) == result


def test_runtime_release_removes_manual_setup_instruction():
    files = {
        'instruction.md': (b'Fix the bug.\n\nBefore working on the task, run '
                           b'`bash /setup_files/setup.sh`. It initializes this task once '
                           b'and safely does nothing on subsequent calls.\n\n'),
        'task.toml': b'[agent]\ntimeout_sec = 1200\n',
        'tests/test.sh': b'#!/bin/bash\nbash /home/fix-run.sh\n',
        'setup_files/upstream/fix-run.sh': b'go test ./...\n',
    }
    repaired = release_repair_files(files)
    assert b'/setup_files' not in repaired['instruction.md']
    assert b'Fix the bug.' in repaired['instruction.md']
    assert repaired['setup_files/upstream/fix-run.sh'] == files['setup_files/upstream/fix-run.sh']
    assert repaired['tests/upstream/fix-run.sh'] == files['setup_files/upstream/fix-run.sh']
    assert release_repair_files(repaired) == repaired


def test_runtime_release_rejects_wrong_parquet_pin_before_writing(tmp_path):
    source = tmp_path / 'source.parquet'
    source.write_bytes(b'not the published bytes')
    output = tmp_path / 'output'
    with pytest.raises(ValueError, match='Published source Parquet checksum mismatch'):
        export_published_runtime(source, output, {}, {}, {}, 'a' * 40,
                                 source_sha256=hashlib.sha256(b'published bytes').hexdigest())
    assert not output.exists()
