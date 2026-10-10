import os
import subprocess
import sys

import pytest

from data.scaleswe.patch import repair_verifier_imports


@pytest.mark.parametrize('task, workspace', [('appium_python-client_pr446', 'python-client'), ('dbcli_pgcli_pr942', 'pgcli'), ('sbdchd_flake8-pie_pr93', 'flake8-pie')])
def test_repo_helpers_import_and_feature_assertions_still_fail(tmp_path, task, workspace):
    repo = tmp_path / 'repo'
    helper = repo / 'test/unit/helper'
    helper.mkdir(parents=True)
    (helper / 'test_helper.py').write_text('answer = 42\n')
    tests = repo / 'test/unit/device'
    tests.mkdir()
    (tests / 'feature_test.py').write_text(
        'from test.unit.helper.test_helper import answer\n'
        'def test_feature():\n    assert answer == 43\n')
    runner = tmp_path / 'verifier/score.py'
    runner.parent.mkdir()
    runner.write_text('import pytest\nraise SystemExit(pytest.main(["test/unit/device", "-q"]))\n')
    env = {k: v for k, v in os.environ.items() if k not in ('PYTHONPATH', 'PYTEST_PLUGINS')}
    env['PYTEST_DISABLE_PLUGIN_AUTOLOAD'] = '1'
    before = subprocess.run([sys.executable, str(runner)], cwd=repo, env=env, capture_output=True, text=True)
    assert before.returncode == 2
    assert "No module named 'test'" in before.stdout
    command = 'python /tests/score.py /tests/test_ids.json | tee /logs/verifier/score.txt'
    contents = {'tests/test.sh': (f'cd /workspace/{workspace} || exit 1\n' + command).encode()}
    assert repair_verifier_imports(contents, task)
    assert repair_verifier_imports(contents, task) == []
    export = contents['tests/test.sh'].decode().splitlines()[1]
    invocation = [sys.executable, str(runner)]
    after = subprocess.run(['bash', '-c', export + '\nexec "$@"', 'bash', *invocation],
                           cwd=repo, env=env, capture_output=True, text=True)
    assert after.returncode == 1
    assert '1 failed' in after.stdout and 'assert 42 == 43' in after.stdout
    (helper / 'test_helper.py').write_text('answer = 43\n# reference solution\n')
    fixed = subprocess.run(['bash', '-c', export + '\nexec "$@"', 'bash', *invocation],
                           cwd=repo, env=env, capture_output=True, text=True)
    assert fixed.returncode == 0 and '1 passed' in fixed.stdout


def test_import_repair_is_scoped_and_rejects_unreviewed_verifier():
    assert repair_verifier_imports({}, 'unrelated_task') == []
    with pytest.raises(ValueError, match='changed since'):
        repair_verifier_imports({'tests/test.sh': b'echo changed'}, 'appium_python-client_pr521')
