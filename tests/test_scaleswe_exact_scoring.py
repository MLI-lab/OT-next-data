import json
import os
import subprocess
import sys

import pytest

from data.scaleswe.patch import repair_exact_test_scoring


@pytest.mark.parametrize('case,expected_reward', [
    ('collision', 1), ('assertion', 0), ('skip', 0), ('teardown', 0),
    ('missing', 0), ('empty', 0), ('group', 1), ('collection', 0),
])
def test_exact_scorer_real_pytest(tmp_path, case, expected_reward):
    source = '''import pytest
@pytest.mark.parametrize('value', ['2022/11/01', '2022.11.01', 'a b', 'ab'])
def test_value(value):
    assert value
'''
    ids = [f'test_sample.py::test_value[{v}]' for v in ['2022/11/01', '2022.11.01', 'a b', 'ab']]
    if case == 'assertion':
        source = source.replace('assert value', "assert value != '2022/11/01'")
    elif case == 'skip':
        source = source.replace('assert value', "pytest.skip('not executed')")
    elif case == 'teardown':
        source += '\n@pytest.fixture(autouse=True)\ndef bad_cleanup():\n    yield\n    raise RuntimeError("cleanup failed")\n'
    elif case == 'missing':
        ids.append('test_sample.py::test_nonexistent')
    elif case == 'empty':
        ids = []
    elif case == 'group':
        ids = ['test_sample.py::test_value']
    elif case == 'collection':
        source += '\nraise ImportError("broken collection")\n'
    (tmp_path / 'test_sample.py').write_text(source)
    contents = {'tests/score.py': b'def all_passed(xml_content: str, expected: list[str]):\n    pass\n'}
    assert repair_exact_test_scoring(contents, 'comtravo_ctparse_pr129')
    saved = dict(contents)
    assert repair_exact_test_scoring(contents, 'comtravo_ctparse_pr129') == []
    assert saved == contents
    (tmp_path / 'score.py').write_bytes(contents['tests/score.py'])
    (tmp_path / 'ids.json').write_text(json.dumps(ids))
    env = dict(os.environ, PYTEST_DISABLE_PLUGIN_AUTOLOAD='1')
    env.pop('PYTEST_ADDOPTS', None)
    env.pop('PYTEST_PLUGINS', None)
    result = subprocess.run([sys.executable, 'score.py', 'ids.json'], cwd=tmp_path,
                            env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert f'<score>{expected_reward}.0</score>' in result.stdout
