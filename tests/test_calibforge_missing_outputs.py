"""Missing agent outputs must fail a test, without masking existing-output checks."""
import ast
import importlib.util
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.fixture
def patcher():
    path = Path(__file__).resolve().parents[1] / 'data/calibforge/patch.py'
    spec = importlib.util.spec_from_file_location('calibforge_output_patch', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('task', [
    'contrastive_solver-security_20260604_003113_006',
    'contrastive_solver-data-science_20260610_112157_008',
])
def test_missing_output_is_a_collected_assertion_failure(patcher, task, tmp_path, monkeypatch):
    missing = tmp_path / 'missing.py'
    monkeypatch.setitem(patcher.REQUIRED_SOLUTION_FILES, task, str(missing))
    original = 'import unavailable_agent_output\n\ndef test_original():\n    assert False\n'
    patched = patcher.assert_required_solution_file(task, original)
    assert patcher.assert_required_solution_file(task, patched) == patched
    assert ast.dump(ast.Module(body=ast.parse(patched).body[-1].orelse, type_ignores=[])) == ast.dump(ast.parse(original))
    test = tmp_path / 'test_output.py'
    test.write_text(patched)
    result = subprocess.run([sys.executable, '-m', 'pytest', '-q', str(test)], capture_output=True, text=True)
    assert result.returncode == 1, result.stdout + result.stderr
    assert '1 failed' in result.stdout and 'Required agent output is missing' in result.stdout
    assert 'ModuleNotFoundError' not in result.stdout


def test_present_output_runs_original_checks_and_does_not_pass_invalid_solution(patcher, tmp_path, monkeypatch):
    task = next(iter(patcher.REQUIRED_SOLUTION_FILES))
    output = tmp_path / 'solution.py'
    output.write_text('invalid solution')
    monkeypatch.setitem(patcher.REQUIRED_SOLUTION_FILES, task, str(output))
    test = tmp_path / 'test_present.py'
    test.write_text(patcher.assert_required_solution_file(task, 'def test_original():\n    assert False, "original check retained"\n'))
    result = subprocess.run([sys.executable, '-m', 'pytest', '-q', str(test)], capture_output=True, text=True)
    assert result.returncode == 1 and 'original check retained' in result.stdout


def test_oom_archive_is_explicit_and_does_not_claim_unsolvability(patcher):
    entry = patcher.DROPPED['multi_solver-software-engineering_20260414_211532_024']
    assert entry['category'] == 'verifier-task-memory-limit'
    assert "user's request" in entry['reason'] and 'not proof' in entry['reason']


@pytest.mark.parametrize('dependency, expected', [('calibforge_nonexistent_dependency', 'Required verifier dependency is missing'), ('json', 'original check retained')])
def test_dependency_guard_preserves_real_checks(patcher, tmp_path, monkeypatch, dependency, expected):
    task = 'contrastive_solver-data-processing_20260604_003814_003'
    monkeypatch.setitem(patcher.REQUIRED_VERIFIER_MODULES, task, dependency)
    original = f'import {dependency}\n\ndef test_original():\n    assert False, "original check retained"\n'
    patched = patcher.assert_required_verifier_module(task, original)
    assert patcher.assert_required_verifier_module(task, patched) == patched
    assert ast.dump(ast.Module(body=ast.parse(patched).body[-1].orelse, type_ignores=[])) == ast.dump(ast.parse(original))
    test = tmp_path / 'test_dependency.py'
    test.write_text(patched)
    result = subprocess.run([sys.executable, '-m', 'pytest', '-q', str(test)], capture_output=True, text=True)
    assert result.returncode == 1 and '1 failed' in result.stdout
    assert expected in result.stdout
    assert 'ERROR collecting' not in result.stdout


def test_guard_preserves_multiline_c_and_python_literals(patcher):
    task = next(iter(patcher.REQUIRED_SOLUTION_FILES))
    original = 'VALUE = """first\nsecond\n    third\n"""\n\ndef test_literal():\n    assert VALUE == "first\\nsecond\\n    third\\n"\n'
    changed = patcher.assert_required_solution_file(task, original)
    assert ast.dump(ast.Module(body=ast.parse(changed).body[-1].orelse, type_ignores=[])) == ast.dump(ast.parse(original))


def test_postgres_unavailable_is_test_failure_and_available_keeps_checks(patcher, tmp_path):
    task = 'contrastive_solver-system-administration_20260610_111702_004'
    original = '''import pytest
@pytest.fixture(scope="module")
def pg_connection():
    pytest.fail("Could not connect to any PostgreSQL instance. "
                "Is the server running?")
def test_original(pg_connection):
    """Retain the real check."""
    assert pg_connection == ["expected"], "original check retained"
'''
    changed = patcher.assert_requested_service(task, original)
    assert patcher.assert_requested_service(task, changed) == changed
    for name, content, message in [('missing', changed, 'Required recovered PostgreSQL'), ('present', changed.replace('return None  # Expected until the agent recovers and starts PostgreSQL.', 'return ["invalid"]'), 'original check retained')]:
        path = tmp_path / f'test_{name}.py'
        path.write_text(content)
        result = subprocess.run([sys.executable, '-m', 'pytest', '-q', str(path)], capture_output=True, text=True)
        assert result.returncode == 1 and '1 failed' in result.stdout
        assert '1 error' not in result.stdout and message in result.stdout


def test_go_failed_build_is_test_failure_and_success_keeps_checks(patcher, tmp_path):
    task = 'contrastive_solver-software-engineering_20260604_002443_020'
    original = '''from types import SimpleNamespace
result = SimpleNamespace(returncode=1, stdout="", stderr="missing libvalidate")
def setup_module():
    assert result.returncode == 0, (
        f"Go build failed.\\nSTDOUT: {result.stdout}\\nSTDERR: {result.stderr}"
    )
    if False:
        raise RuntimeError("Server failed to start within 15 seconds")
def test_original():
    assert False, "original check retained"
'''
    changed = patcher.assert_requested_service(task, original)
    assert patcher.assert_requested_service(task, changed) == changed
    for name, content, message in [('missing', changed, 'Go build failed'), ('built', changed.replace('returncode=1', 'returncode=0'), 'original check retained')]:
        path = tmp_path / f'test_{name}.py'
        path.write_text(content)
        result = subprocess.run([sys.executable, '-m', 'pytest', '-q', str(path)], capture_output=True, text=True)
        assert result.returncode == 1 and '1 failed' in result.stdout
        assert '1 error' not in result.stdout and message in result.stdout
