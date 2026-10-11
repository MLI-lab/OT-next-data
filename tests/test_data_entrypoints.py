"""Exercise consolidated commands and the shared task archive dependency."""
import io
from pathlib import Path
import subprocess
import sys
import tarfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
COMMANDS = {
    'crosscodeeval': ['prepare', 'warmup', 'rewards'],
    'tasktrove_bugsinpy': ['instruction-loop'],
    'mimo': ['build-full', 'prepare-pilot'],
    'devopsgym': ['build-full', 'prepare-pilot'],
    'facet': ['build-source', 'prepare-pilot'],
    'seta': ['download'], 'calibforge': ['prepare-pilot'],
    'tmax': ['prepare-pilot'], 'termigen': ['prepare-pilot'],
}


@pytest.mark.parametrize('patch,args', [
    (patch, args)
    for patch in sorted((ROOT / 'data').glob('*/patch.py'))
    for args in [[], *[[command] for command in COMMANDS.get(patch.parent.name, [])]]
])
def test_direct_command_help_outside_repository(patch, args, tmp_path):
    result = subprocess.run([sys.executable, str(patch), *args, '--help'],
                            cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert 'usage:' in result.stdout.lower()
    assert not list(tmp_path.iterdir())


def test_pymethods_repairs_preserve_solution_and_payload():
    from data.tasktrove_pymethods2test.patch import patch_task, SOLVE_SH
    from data.utils.full_source.harbor_parquet import pack_task
    from data.utils.task_archive import read_members
    original = {
        'environment/Dockerfile': b'FROM python:3.12\nRUN pip install --no-cache-dir pytest\n',
        'tests/test.sh': b'pip3 install --quiet pytest 2>/dev/null || true\n',
        'solution/solution.py': b'print("reference")\n',
        'instruction.md': b'Implement the function.\n',
    }
    result = read_members(patch_task(pack_task(original)))
    assert result['solution/solve.sh'] == SOLVE_SH
    assert result['solution/solution.py'] == original['solution/solution.py']
    assert result['instruction.md'] == original['instruction.md']
    assert b'pytest==9.1.1' in result['environment/Dockerfile']
    assert b'pytest==9.1.1' in result['tests/test.sh']


@pytest.mark.parametrize('name', ['../escape', '/absolute'])
def test_archive_reader_rejects_unsafe_paths(name):
    from data.utils.task_archive import read_members
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode='w') as archive:
        archive.addfile(tarfile.TarInfo(name), io.BytesIO())
    with pytest.raises(ValueError, match='unsafe'):
        read_members(output.getvalue())
