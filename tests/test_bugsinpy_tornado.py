"""Dependency regressions behind Tornado's 16 overnight conversion failures."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


spec = importlib.util.spec_from_file_location(
    "bugsinpy_tornado", Path(__file__).resolve().parents[1] / "data/tasktrove_bugsinpy/patch.py")
patch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patch)


@pytest.mark.parametrize("self_install", [False, True])
def test_upstream_setup_excludes_stdlib_and_project(tmp_path, self_install):
    setup = tmp_path / "setup.sh"
    setup.write_text("pip install unittest\npip install python-gettext\n"
                     + ("pip install tornado\n" if self_install else ""))
    commands, warnings, requirements = patch.setup_recipe(setup)
    kept, excluded = patch.tornado_dependency_seeds(requirements)
    assert not commands and not warnings
    assert kept == ["python-gettext"]
    assert {item["requirement"] for item in excluded} == (
        {"unittest", "tornado"} if self_install else {"unittest"})
    assert all(item["reason"] for item in excluded)


def test_verifier_exclusions_preserve_real_dependencies_and_markers():
    requirements = ["Unittest", 'Tornado[speedups]==6.0.4; python_version < "3.8"',
                    "tornado @ https://example.invalid/tornado.tar.gz",
                    "unittest2==1.1.0", "pytest-tornado", 'mock; python_version < "3.8"']
    kept, excluded = patch.tornado_dependency_seeds(requirements)
    assert kept == requirements[3:]
    assert len(excluded) == 3


@pytest.mark.parametrize('original', ['', '10.0.0.1 service', '127.0.0.1 localhost\n',
                                      '# localhost\n127.0.0.1 other\n::1 localhost\n'])
def test_loopback_setup_preserves_hosts_and_is_idempotent(tmp_path, original):
    hosts = tmp_path / 'hosts'
    hosts.write_text(original)
    command = patch.loopback_hosts_setup().replace('/etc/hosts', str(hosts))
    subprocess.run(['bash', '-e', '-c', command], check=True)
    first = hosts.read_text()
    subprocess.run(['bash', '-e', '-c', command], check=True)
    assert hosts.read_text() == first
    assert first.startswith(original)
    entries = [line.split('#', 1)[0].split() for line in first.splitlines()]
    for address in ('127.0.0.1', '::1'):
        assert sum(len(parts) > 1 and parts[0] == address and 'localhost' in parts[1:]
                   for parts in entries) == 1


@pytest.mark.parametrize("body,selector,expected", [
    ("    def test_case(self): pass\n", "sample.Check", 0),
    ("    def test_case(self): self.fail('regression')\n", "sample.Check", 1),
    ("    def test_case(self): raise AttributeError('buggy API')\n", "sample.Check", 1),
    ("    @unittest.skip('unavailable')\n    def test_case(self): pass\n", "sample.Check", 2),
    ("    @unittest.expectedFailure\n    def test_case(self): self.fail()\n", "sample.Check", 2),
    ("    def setUp(self): raise RuntimeError('broken fixture')\n    def test_case(self): pass\n", "sample.Check", 2),
    ("    @classmethod\n    def setUpClass(cls): raise RuntimeError('broken fixture')\n    def test_case(self): pass\n", "sample.Check", 2),
    ("    def test_case(self): pass\n", "sample.Check.test_missing", 2),
    ("    pass\n", "sample.Check", 2),
])
def test_unittest_report_distinguishes_regressions_from_infrastructure(tmp_path, body, selector, expected):
    (tmp_path / 'sample.py').write_text('import unittest\nclass Check(unittest.TestCase):\n' + body)
    report = tmp_path / 'unittest.jsonl'
    runner = tmp_path / 'run_unittest.py'
    runner.write_text(patch.unittest_reporter().replace('/logs/verifier/unittest.jsonl', str(report)))
    result = subprocess.run([sys.executable, str(runner), '-q', selector],
                            cwd=tmp_path, capture_output=True)
    assert result.returncode == expected, result.stderr.decode()
    record = json.loads(report.read_text())
    assert record['invalid'] == (expected == 2)


def test_unittest_runs_all_commands_after_failure(tmp_path):
    (tmp_path / 'sample.py').write_text('''import unittest
class Check(unittest.TestCase):
    def test_fail(self): self.fail('regression')
    def test_pass(self): pass
''')
    report = tmp_path / 'unittest.jsonl'
    runner = tmp_path / 'run_unittest.py'
    runner.write_text(patch.unittest_reporter().replace('/logs/verifier/unittest.jsonl', str(report)))
    command = 'python -m unittest -q sample.Check.test_fail\npython -m unittest -q sample.Check.test_pass'
    script = patch.unittest_run_script(command).replace('/tests/run_unittest.py', str(runner)).replace(
        '/logs/verifier/unittest.jsonl', str(report))
    result = subprocess.run(['bash', '-c', script], cwd=tmp_path, capture_output=True,
                            env={**os.environ, 'PYTHONPATH': str(tmp_path)})
    assert result.returncode == 1, result.stderr.decode()
    rows = [json.loads(line) for line in report.read_text().splitlines()]
    assert [row['successful'] for row in rows] == [False, True]
    assert all(row['tests_run'] == 1 and not row['invalid'] for row in rows)


@pytest.mark.parametrize('rows,success', [
    ([], False), ([{'invalid': False, 'tests_run': 1}], False),
    ([{'invalid': False, 'tests_run': 1}, {'invalid': True, 'tests_run': 1}], False),
    ([{'invalid': False, 'tests_run': 1}, {'invalid': False, 'tests_run': 0}], False),
    ([{'invalid': False, 'tests_run': 1}] * 2, True),
])
def test_unittest_guard_requires_every_invocation(tmp_path, rows, success):
    script = patch.test_script('python -m unittest first\npython -m unittest second',
                               'a', 'b', '/app', local_http=True, unittest_report=True)
    assert script.index('pip install') < script.index('unset HTTP_PROXY') < script.index('bash /tests/run_test.sh')
    report = tmp_path / 'unittest.jsonl'
    report.write_text(''.join(json.dumps(row) + '\n' for row in rows))
    check = script.split("python3 -I - <<'PY'\n", 1)[1].split('\nPY', 1)[0]
    result = subprocess.run([sys.executable, '-I', '-c', check.replace('/logs/verifier/unittest.jsonl', str(report))],
                            capture_output=True)
    assert (result.returncode == 0) == success
