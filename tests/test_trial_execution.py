"""The sitecustomize adapter records only an outer Twisted ``trial`` verifier run."""
import json
import os
from pathlib import Path
import subprocess
import sys
import types

import pytest

from harbor_patches import sitecustomize as adapter
from validation.stages.execution_evidence import scope_output

TOKEN = '2' * 32
PLUGIN = Path(__file__).resolve().parents[1] / 'harbor_patches'


@pytest.mark.parametrize('argv, environ, expected', [
    (['/opt/conda/envs/testbed/bin/trial', 'pkg.test'], {'OT_VERIFIER_EXECUTION_TOKEN': TOKEN}, True),
    (['/opt/conda/lib/python3.9/site-packages/twisted/trial/__main__.py'], {'OT_VERIFIER_EXECUTION_TOKEN': TOKEN}, True),
    (['/opt/conda/bin/pytest', 'tests'], {'OT_VERIFIER_EXECUTION_TOKEN': TOKEN}, False),
    (['/usr/bin/python', '/tests/grade.py'], {'OT_VERIFIER_EXECUTION_TOKEN': TOKEN}, False),
    (['trial', 'pkg.test'], {}, False),                                           # not a grading command
    (['trial', 'pkg.test'], {'OT_VERIFIER_EXECUTION_TOKEN': TOKEN,
                             'OT_PYTEST_EXECUTION_ACTIVE': TOKEN}, False),          # nested in the outer run
])
def test_only_the_outer_trial_runner_is_recorded(argv, environ, expected):
    assert adapter._is_outer_trial(argv, environ) is expected


class FakeReporter:
    def __init__(self, tests_run, failures=0, errors=(), skips=0):
        self.testsRun = tests_run
        self.failures = [('t', 'f')] * failures
        self.errors = list(errors)
        self.skips = [('t', 's')] * skips
        self.done_calls = 0

    def wasSuccessful(self):
        return not self.failures and not self.errors

    def done(self):
        self.done_calls += 1


def install_fake_twisted(monkeypatch):
    class ErrorHolder:
        pass
    reporter = types.ModuleType('twisted.trial.reporter')
    reporter.Reporter = FakeReporter
    runner = types.ModuleType('twisted.trial.runner')
    runner.ErrorHolder = ErrorHolder
    trial = types.ModuleType('twisted.trial')
    trial.reporter, trial.runner = reporter, runner
    twisted = types.ModuleType('twisted')
    twisted.trial = trial
    for name, module in [('twisted', twisted), ('twisted.trial', trial),
                         ('twisted.trial.reporter', reporter), ('twisted.trial.runner', runner)]:
        monkeypatch.setitem(sys.modules, name, module)
    return ErrorHolder


def test_counts_follow_the_reporter_and_separate_import_errors(monkeypatch):
    ErrorHolder = install_fake_twisted(monkeypatch)
    passing = FakeReporter(186)
    assert adapter.trial_counts(passing) == {'executed': 186, 'skipped': 0, 'setup_errors': 0,
                                             'collection_errors': 0, 'exitstatus': 0}
    broken_import = FakeReporter(3, errors=[(ErrorHolder(), 'ImportError'), ('t', 'AssertionError')], skips=1)
    assert adapter.trial_counts(broken_import) == {'executed': 2, 'skipped': 1, 'setup_errors': 0,
                                                   'collection_errors': 1, 'exitstatus': 1}


def test_install_emits_begin_and_one_end_record_in_the_shared_format(monkeypatch, capsys):
    install_fake_twisted(monkeypatch)
    environ = {'OT_VERIFIER_EXECUTION_TOKEN': TOKEN}
    state = adapter.install(['/opt/conda/envs/testbed/bin/trial', 'towncrier'], environ)
    assert state is not None and environ['OT_PYTEST_EXECUTION_ACTIVE'] == TOKEN
    reporter = FakeReporter(186, failures=1)
    reporter.done()        # the patched Reporter.done is on the class the real runner uses
    reporter.done()        # a second done() must not produce a second END record
    assert reporter.done_calls == 2
    output, states, problem = scope_output(capsys.readouterr().out, TOKEN)
    assert problem is None and len(states) == 1
    assert states[0]['executed'] == 186 and states[0]['exitstatus'] == 1
    assert states[0]['finished'] is True
    # Verifier text around the records is kept for the graders' own parsing.
    assert output.strip() == ''


def test_install_is_inert_for_other_interpreters(monkeypatch, capsys):
    assert adapter.install(['/opt/conda/bin/pytest'], {'OT_VERIFIER_EXECUTION_TOKEN': TOKEN}) is None
    assert adapter.install(['trial'], {}) is None
    assert capsys.readouterr().out == ''


def test_module_import_never_breaks_an_ordinary_python_process(tmp_path):
    env = dict(os.environ, PYTHONPATH=str(PLUGIN), OT_VERIFIER_EXECUTION_TOKEN=TOKEN)
    env.pop('OT_PYTEST_EXECUTION_ACTIVE', None)
    script = tmp_path / 'grade.py'
    script.write_text('import json, sys\nprint(json.dumps({"reward": 1.0, "sitecustomize": "sitecustomize" in sys.modules}))\n')
    result = subprocess.run([sys.executable, str(script)], env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    record = json.loads(result.stdout.strip().splitlines()[-1])
    assert record == {'reward': 1.0, 'sitecustomize': True}
    assert 'OT_VERIFIER_EXECUTION' not in result.stdout
