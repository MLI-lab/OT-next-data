import os
from pathlib import Path
import subprocess
import sys

import pytest

from data.swelego.subject_imports import bootstrap, reviewed, SUBJECTS


def test_subject_changes_and_plugin_are_visible_without_shadowing_clean_pytest(tmp_path):
    clean = tmp_path / 'clean'; clean.mkdir()
    task = tmp_path / 'task'; task.mkdir()
    (clean / 'subject.py').write_text('value = -1\n')
    package = task / 'subject'; package.mkdir()
    (package / 'plugin.py').write_text('import subject\ndef pytest_configure(config):\n    assert subject.value > 0\n')
    (task / 'pytest.py').write_text('raise RuntimeError("agent pytest imported")\n')
    test = tmp_path / 'test_subject.py'
    code = ('import sys\nimport pytest\nsys.path.insert(0, ' + repr(str(clean)) + ')\n'
            'import subject\nassert subject.value == -1\n' +
            bootstrap({'module':'subject', 'directory':str(task)}) +
            'import subject\nassert subject.value > 0\n'
            'raise SystemExit(pytest.main(["-q", "-p", "subject.plugin", "-p", "no:cacheprovider", ' + repr(str(test)) + ']))\n')
    env = dict(os.environ, PYTEST_DISABLE_PLUGIN_AUTOLOAD='1', PYTHONDONTWRITEBYTECODE='1')
    for value in (2, 3):
        (package / '__init__.py').write_text('value = ' + str(value) + '\n')
        test.write_text('import subject\ndef test_change():\n    assert subject.value == ' + str(value) + '\n')
        run = subprocess.run([sys.executable, '-I', '-B', '-c', code], env=env, capture_output=True, text=True)
        assert run.returncode == 0, run.stdout + run.stderr
        assert '1 passed' in run.stdout


def test_missing_task_subject_cannot_silently_fall_back_to_clean_package(tmp_path):
    clean = tmp_path / 'clean'; clean.mkdir()
    (clean / 'subject.py').write_text('value = -1\n')
    code = ('import sys\nsys.path.insert(0, ' + repr(str(clean)) + ')\n' +
            bootstrap({'module':'subject','directory':str(tmp_path / 'missing')}) + 'import subject\n')
    run = subprocess.run([sys.executable, '-I', '-B', '-c', code], capture_output=True, text=True)
    assert run.returncode != 0 and 'Task subject is missing' in run.stderr


@pytest.mark.parametrize('repository', SUBJECTS)
def test_only_reviewed_task_revisions_get_subject_exception(repository):
    with pytest.raises(ValueError, match='Unreviewed'):
        reviewed({'repo':repository, 'base_commit':'0'*40})
    for commit in SUBJECTS[repository][2]:
        assert reviewed({'repo':repository,'base_commit':commit})['module'] == SUBJECTS[repository][0]


def test_private_subject_hook_survives_clean_python_subprocesses(tmp_path):
    task = tmp_path / 'task'; task.mkdir()
    (task / 'subject.py').write_text('value = 27\n')
    (task / 'pytest.py').write_text('raise RuntimeError("agent pytest imported")\n')
    venv = tmp_path / 'venv'
    subprocess.run([sys.executable, '-I', '-m', 'venv', '--without-pip', '--system-site-packages', str(venv)],
                   check=True, capture_output=True)
    python = venv / 'bin/python'
    site = Path(subprocess.check_output([str(python), '-I', '-c',
        'import sysconfig; print(sysconfig.get_path("purelib"))'], text=True).strip())
    (site / 'clean_test_tools.pth').write_text(str(Path(pytest.__file__).parent.parent) + '\n')
    (site / 'subject.py').write_text('value = -1\n')
    (site / 'ot_swelego_subject.py').write_text('import sys\n' + bootstrap({'module':'subject','directory':str(task)}))
    (site / 'ot_swelego_subject.pth').write_text('import ot_swelego_subject\n')
    child = 'import subject, pytest; assert subject.value == 27'
    parent = ('import subject, sys, subprocess\nassert subject.value == 27\n'
              'subprocess.run([sys.executable, "-I", "-c", ' + repr(child) + '], check=True)\n')
    run = subprocess.run([str(python), '-I', '-c', parent], capture_output=True, text=True)
    assert run.returncode == 0, run.stdout + run.stderr
