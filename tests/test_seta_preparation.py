import json
import hashlib
from pathlib import Path
import re
import subprocess

import pytest

from data.seta.patch import JAVA_ARTIFACTS, JAVA_PREPARATION, java_artifact, prebuild_java_preparation


@pytest.mark.parametrize('task', JAVA_PREPARATION)
def test_reviewed_java_setup_keeps_task_files_and_uses_local_dependencies(task, tmp_path, monkeypatch):
    root = Path(__file__).parent / 'fixtures/seta_preparation' / task
    files = {p.relative_to(root).as_posix(): (p.read_bytes(), 0o755)
             for p in root.rglob('*') if p.is_file()}
    original = dict(files)
    for name, (url, checksum) in list(JAVA_ARTIFACTS.items()):
        payload = name.encode()
        (tmp_path / name).write_bytes(payload)
        monkeypatch.setitem(JAVA_ARTIFACTS, name, (url, hashlib.sha256(payload).hexdigest()))
    prebuild_java_preparation(files, task, tmp_path)
    setup = files['setup_files/setup.sh'][0].decode()
    before = original['setup_files/setup.sh'][0].decode()
    # Preserve every non-install operation byte for byte, including COPY hashes,
    # ownership, task permissions and the partial-setup failure guard.
    moved = set(JAVA_PREPARATION[task][:3]) | ({3} if task.endswith('9112770') else set())
    def operations(text):
        return dict((int(index), block) for index, block in
                    re.findall(r'(?ms)^operation=(\d+)\n(.*?)(?=^operation=|^printf "%s)', text))
    for index, block in operations(before).items():
        if index not in moved:
            assert operations(setup)[index] == block
    assert 'SETA_SETUP_PARTIAL: use a fresh trial' in setup
    assert 'curl ' not in setup and 'wget ' not in setup and 'pip3 install' not in setup
    for name in ('setup_files/setup.sh', 'tests/test.sh'):
        path = tmp_path / Path(name).name
        path.write_bytes(files[name][0])
        subprocess.run(['bash', '-n', str(path)], check=True)
    verifier = files['tests/test.sh'][0]
    assert b'uvx ' not in verifier
    assert b'/opt/seta-verifier/bin/pytest --ctrf' in verifier
    assert verifier.split(b'if [ $?')[1] == original['tests/test.sh'][0].split(b'if [ $?')[1]
    metadata = json.loads(files['setup_files/operations.json'][0])
    assert metadata['recipe'].encode() == files['environment/Dockerfile'][0]
    assert b'repo.maven' not in files['environment/Dockerfile'][0]
    artifact = Path(JAVA_PREPARATION[task][3]).name
    assert files['setup_files/dependencies/' + artifact][0] == artifact.encode()
    with pytest.raises(ValueError, match='unexpected setup operation'):
        prebuild_java_preparation(files, task, tmp_path)


def test_corrupted_cached_jar_is_rejected(tmp_path):
    (tmp_path / 'h2.jar').write_bytes(b'incomplete download')
    with pytest.raises(ValueError, match='checksum mismatch'):
        java_artifact('h2.jar', tmp_path)


@pytest.mark.parametrize('task', [
    'ask_ubuntu__evolve__0__d1', 'ask_ubuntu__evolve__833__b1',
    'unix_linux_se__synth__90345'])
def test_measured_preparation_moves_leave_task_state_operations_unchanged(task):
    from data.seta.patch import prebuild_reviewed_preparation, PREPARATION_IMAGE_OPERATIONS
    root = Path(__file__).parent / 'fixtures/seta_preparation_review' / task
    files = {p.relative_to(root).as_posix(): (p.read_bytes(), 0o755) for p in root.rglob('*') if p.is_file()}
    before = dict(files)
    prebuild_reviewed_preparation(files, task)
    old = json.loads(before['setup_files/operations.json'][0])
    new = json.loads(files['setup_files/operations.json'][0])
    moved = PREPARATION_IMAGE_OPERATIONS[task]
    for i, operation in enumerate(old['operations']):
        if i not in moved:
            assert new['operations'][i] == operation
        else:
            assert new['operations'][i]['prebuilt_command'] == operation['command']
    # The old base dependencies and package state are retained, including dbus
    # and openssh; only reusable work moves, no broken config/fixtures are baked.
    for line in before['environment/Dockerfile'][0].splitlines():
        assert line in files['environment/Dockerfile'][0].splitlines()
    assert b'package_report' not in files['environment/verifier-preparation.sh'][0]
    assert b'/tests/test_outputs.py' not in files['environment/verifier-preparation.sh'][0]
    assert b'apt-get' not in files['tests/setup.sh'][0]
    assert b'pip3 install' not in files['tests/setup.sh'][0]
    assert b'&& cd /app && bash /opt/seta-verifier-preparation.sh' in files['environment/Dockerfile'][0]
    for name in ['tests/setup.sh', 'setup_files/setup.sh', 'environment/verifier-preparation.sh']:
        subprocess.run(['bash', '-n'], input=files[name][0], check=True)
    if moved:
        with pytest.raises(ValueError, match='unexpected preparation operation'):
            prebuild_reviewed_preparation(files, task)
