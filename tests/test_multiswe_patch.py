import io
from pathlib import Path
import subprocess
import sys
import tarfile

import pytest

from data.multiswe.patch import patch_blob, patch_files


def original_files():
    from data.utils.full_source import multiswe_grade
    return {
        'environment/Dockerfile': b'FROM mswebench/example:pr-1\nWORKDIR /home/repo\n',
        'tests/test.sh': b'''#!/bin/bash
set -uo pipefail
mkdir -p /logs/verifier
python3 -m pip install --target /tmp/multiswe-grader 'multi-swe-bench @ url' || true
export PYTHONPATH=/tmp/multiswe-grader:${PYTHONPATH:-}
bash /home/fix-run.sh > /logs/verifier/test_output.txt 2>&1 || true
python3 /tests/grade.py /tests/row.json /logs/verifier/test_output.txt /logs/verifier/reward.json
''',
        'tests/grade.py': Path(multiswe_grade.__file__).read_bytes(),
        'instruction.md': b'Fix this task.',
        'solution/solve.sh': b'#!/bin/bash\nset -e\ngit apply /solution/gold.patch\n',
    }


def test_patch_preserves_task_and_bakes_grader():
    files = original_files()
    patched = patch_files(files)
    assert patched['instruction.md'] == files['instruction.md']
    assert patched['solution/solve.sh'] == files['solution/solve.sh']
    assert b'pip install' not in patched['tests/test.sh']
    assert b'/opt/multiswe-python/bin/python3 -I' in patched['tests/test.sh']
    assert b'curl ca-certificates' in patched['environment/Dockerfile']
    assert b'sha256sum --check' in patched['environment/Dockerfile']
    assert b'COPY ' not in patched['environment/Dockerfile']
    assert b'install-grader.sh' not in patched['environment/Dockerfile']
    assert b'file:///build/' not in patched['environment/Dockerfile']
    assert patch_files(patched) == patched
    subprocess.run(['bash', '-n'], input=patched['tests/test.sh'], check=True)
    for line in patched['environment/Dockerfile'].splitlines():
        if line.startswith(b'RUN '):
            subprocess.run(['sh', '-n'], input=line[4:], check=True)


def test_grader_import_failure_cannot_produce_nop_reward(tmp_path):
    files = patch_files(original_files())
    (tmp_path/'grade.py').write_bytes(files['tests/grade.py'])
    (tmp_path/'row.json').write_text('{}')
    (tmp_path/'output.txt').write_text('')
    reward = tmp_path/'reward.json'
    reward.write_text('{"reward": 0}')
    result = subprocess.run([sys.executable, '-I', '-S', str(tmp_path/'grade.py'),
                             str(tmp_path/'row.json'), str(tmp_path/'output.txt'), str(reward)],
                            capture_output=True, text=True)
    assert result.returncode != 0
    assert 'Multi-SWE grader error' in result.stderr
    assert not reward.exists()


def test_archive_preserves_modes_and_is_idempotent():
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w') as archive:
        for name, data in original_files().items():
            member = tarfile.TarInfo(name)
            member.mode = 0o755 if name.endswith('.sh') else 0o644
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
    patched = patch_blob(stream.getvalue())
    assert patch_blob(patched) == patched
    with tarfile.open(fileobj=io.BytesIO(patched)) as archive:
        assert archive.getmember('tests/test.sh').mode == 0o755
        assert 'environment/install-grader.sh' not in archive.getnames()


def test_unrecognized_verifier_is_not_partially_repaired():
    files = original_files()
    files['tests/test.sh'] = b'unknown verifier'
    with pytest.raises(ValueError, match='layout'):
        patch_files(files)


def shared_recipe():
    return {'original_image': 'mswebench/example:pr-1', 'base': 'mswebench/example:base',
            'base_sha': 'a'*40, 'repo': 'repo',
            'files': {'prepare.sh': '#!/bin/bash\ntrue\n', 'test.patch': 'test patch',
                      'fix-run.sh': 'git apply /home/test.patch /home/fix.patch\n'}}


def test_shared_images_keep_task_specific_files_out_of_build_context(tmp_path):
    from data.multiswe.patch import share_image
    from validation.stages.task_setup import detect
    recipe = shared_recipe()
    lock = {recipe['base']: 'mswebench/example@sha256:' + 'b'*64}
    first = share_image(patch_files(original_files()), recipe, lock)
    second_recipe = {**recipe, 'base_sha': 'c'*40, 'files': {**recipe['files'], 'test.patch': 'other test'}}
    second = share_image(patch_files(original_files()), second_recipe, lock)
    assert first['environment/Dockerfile'] == second['environment/Dockerfile']
    assert first['setup_files/setup.sh'] != second['setup_files/setup.sh']
    assert first['setup_files/upstream/test.patch'] != second['setup_files/upstream/test.patch']
    assert 'setup_files/upstream/fix.patch' not in first
    for name, content in first.items():
        p = tmp_path/name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)
    assert detect(tmp_path) == 'bash /setup_files/setup.sh'
    subprocess.run(['bash', '-n', str(tmp_path/'setup_files/setup.sh')], check=True)


def test_shared_setup_is_idempotent_after_agent_edits(tmp_path):
    from data.multiswe.patch import share_image
    recipe = shared_recipe()
    setup = share_image(patch_files(original_files()), recipe,
                        {recipe['base']: 'mswebench/example@sha256:'+'b'*64})['setup_files/setup.sh'].decode()
    marker = setup.split('if [ -f ', 1)[1].split(' ];', 1)[0]
    local_marker = tmp_path/'ready'
    local_marker.touch()
    # Once initialized, neither copying nor repository preparation may run again.
    setup = setup.replace(marker, str(local_marker))
    result = subprocess.run(['bash'], input=setup, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('problem', ['unpinned', 'wrong_image', 'answer_file'])
def test_shared_conversion_rejects_unreviewed_inputs(problem):
    from data.multiswe.patch import share_image
    recipe = shared_recipe()
    locks = {recipe['base']: 'mswebench/example@sha256:' + 'b'*64}
    if problem == 'unpinned': locks[recipe['base']] = 'mswebench/example:base'
    if problem == 'wrong_image': recipe['original_image'] = 'another:tag'
    if problem == 'answer_file': recipe['files']['fix.patch'] = 'answer'
    with pytest.raises(ValueError):
        share_image(patch_files(original_files()), recipe, locks)
