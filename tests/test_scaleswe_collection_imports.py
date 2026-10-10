import ast
import subprocess
import sys

import pytest

from data.scaleswe.patch import defer_test_imports


def test_deferred_import_keeps_unrelated_tests_running(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'feature.py').write_text('existing = 1\n')
    test = tmp_path / 'test_feature.py'
    test.write_text(
        'from feature import existing, added\n'
        'def test_existing():\n    assert existing == 1\n'
        'def test_added():\n    """Keep this docstring."""\n    assert added() == 42\n')
    assertions = [ast.dump(n) for n in ast.walk(ast.parse(test.read_text())) if isinstance(n, ast.Assert)]
    defer_test_imports({'test_feature.py': {'feature': ['added']}})
    assert assertions == [ast.dump(n) for n in ast.walk(ast.parse(test.read_text())) if isinstance(n, ast.Assert)]
    result = subprocess.run([sys.executable, '-m', 'pytest', '-q', str(test)], capture_output=True, text=True)
    assert result.returncode == 1
    assert '1 failed, 1 passed' in result.stdout
    assert 'ImportError' in result.stdout
    (tmp_path / 'feature.py').write_text('existing = 1\ndef added(): return 42\n')
    result = subprocess.run([sys.executable, '-m', 'pytest', '-q', str(test)], capture_output=True, text=True)
    assert result.returncode == 0 and '2 passed' in result.stdout


def test_class_method_nested_decorator_and_alias(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    p = tmp_path / 'test_case.py'
    p.write_text('from feature import decorator as dec\n'
                 'class TestFeature:\n'
                 '    def test_decorator(self):\n'
                 '        class Inner:\n'
                 '            @dec\n'
                 '            def action(self): return 1\n'
                 '        assert Inner().action() == 1\n')
    defer_test_imports({'test_case.py': {'feature': ['decorator']}})
    tree = ast.parse(p.read_text())
    assert isinstance(tree.body[0], ast.ClassDef)
    method = tree.body[0].body[0]
    assert isinstance(method.body[0], ast.ImportFrom)
    assert method.body[0].names[0].asname == 'dec'


@pytest.mark.parametrize('use', [
    'value = added()\n',
    'class TestFeature(added):\n    pass\n',
    'def test_feature(arg=added):\n    pass\n',
    '@added\ndef test_feature():\n    pass\n',
])
def test_collection_time_dependencies_fail_closed(tmp_path, monkeypatch, use):
    monkeypatch.chdir(tmp_path)
    p = tmp_path / 'test_case.py'
    original = 'from feature import added\n' + use
    p.write_text(original)
    with pytest.raises(ValueError):
        defer_test_imports({'test_case.py': {'feature': ['added']}})
    assert p.read_text() == original
