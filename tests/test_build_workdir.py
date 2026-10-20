"""Build fixtures using relative paths must land in the declared WORKDIR."""
from pathlib import Path
import shlex
import subprocess

import pytest

from harbor_patches.image_build import definition_build_home, definition_build_workdir


@pytest.mark.parametrize('prefix', [[], ['unshare', '-r']])
def test_relative_git_fixture_created_in_workdir(tmp_path, prefix):
    workdir = tmp_path / 'task workspace'
    quoted = shlex.quote(str(workdir))
    recipe = ('Bootstrap: docker\nFrom: ubuntu\n%post\n'
              f'    mkdir -p {quoted}\n'
              '    git init -q\n    echo fixture > input.txt\n    git add -A\n'
              f'%runscript\n    cd {quoted}\n    exec "$@"\n')
    definition = tmp_path / 'source.def'
    definition.write_text(recipe)
    other = tmp_path / 'unrelated'
    other.mkdir()
    (other / 'sentinel').write_text('not a fixture')
    generated = []

    def build(command, **kwargs):
        generated.append(Path(command[-1]))
        script = generated[-1].read_text().split('%post\n')[1].split('%runscript')[0]
        subprocess.run(['sh', '-ec', script], cwd=other, check=True)
        return 'built'

    wrapped = definition_build_workdir(definition_build_home(build))
    assert wrapped([*prefix, 'apptainer', 'build', 'image.sif', str(definition)]) == 'built'
    assert subprocess.check_output(['git', '-C', str(workdir), 'ls-files'], text=True) == 'input.txt\n'
    assert not (other / '.git').exists()
    assert definition.read_text() == recipe
    assert all(not path.exists() for path in generated)


def test_custom_definition_untouched(tmp_path):
    source = tmp_path / 'custom.def'
    source.write_text('%post\n    mkdir -p /data\n    echo hi\n')
    command = ['apptainer', 'build', 'output.sif', str(source)]
    assert definition_build_workdir(lambda cmd: cmd)(command) == command
