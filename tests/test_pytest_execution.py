"""Real runner regressions for outer/inner pytest execution evidence."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from validation.stages.harbor import assess_trials
from validation.stages.execution_evidence import MANIFEST, scope_output

TOKEN = '1' * 32
PLUGIN = Path(__file__).resolve().parents[1] / 'harbor_patches'


def run_pytest(tmp_path, files, args=(), *, python=None):
    for name, body in files.items():
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    env = dict(os.environ, PYTHONPATH=str(PLUGIN), PYTEST_PLUGINS='ot_pytest_execution',
               PYTEST_DISABLE_PLUGIN_AUTOLOAD='1', OT_VERIFIER_EXECUTION_TOKEN=TOKEN)
    env.pop('OT_PYTEST_EXECUTION_ACTIVE', None)
    command = [sys.executable, '-c', python] if python else [sys.executable, '-m', 'pytest', '-q', *args]
    return subprocess.run(command, cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30)


def assessed(tmp_path, output, *, reward=0, expected=0):
    trial = tmp_path / 'trial'
    (trial / 'verifier').mkdir(parents=True, exist_ok=True)
    (trial / 'verifier' / MANIFEST).write_text(json.dumps({'version': 1, 'token': TOKEN}))
    return assess_trials([(trial, {'verifier_result': {'rewards': {'reward': reward}, 'stdout': output}})], 1, expected)


@pytest.mark.parametrize('subprocess_inner', [False, True])
def test_nested_empty_run_is_an_outer_assertion_failure(tmp_path, subprocess_inner):
    call = ("subprocess.run([sys.executable, '-m', 'pytest', '-q', 'inner'], capture_output=True, text=True)"
            if subprocess_inner else None)
    body = ("import subprocess, sys, pytest\n"
            "def test_plugin():\n")
    if call:
        body += '    result = ' + call + '\n    print(result.stdout)\n    assert result.returncode == 0\n'
    else:
        body += "    assert pytest.main(['-q', 'inner']) == 0\n"
    result = run_pytest(tmp_path, {'test_outer.py': body, 'inner/empty.py': '# no tests\n'}, ['test_outer.py'])
    assert result.returncode == 1, result.stdout + result.stderr
    assert 'no tests ran' in result.stdout
    remaining, runs, error = scope_output(result.stdout, TOKEN)
    assert error is None and len(runs) == 1 and runs[0]['executed'] == 1
    assert 'no tests ran' not in remaining
    assert assessed(tmp_path, result.stdout)['status'] == 'completed'
    assert assessed(tmp_path, result.stdout, expected=1)['status'] == 'failed'


@pytest.mark.parametrize('files,args', [
    ({'empty.py': '# no tests\n'}, []),
    ({'test_skip.py': 'import pytest\n@pytest.mark.skip\ndef test_skip(): pass\n'}, []),
    ({'test_import.py': 'import nonexistent_dependency_xyz\n'}, []),
    ({'test_setup.py': 'import pytest\n@pytest.fixture\ndef broken(): raise RuntimeError("setup failed")\ndef test_x(broken): pass\n'}, []),
])
def test_genuine_empty_or_broken_outer_runner_stays_failed(tmp_path, files, args):
    result = run_pytest(tmp_path, files, args)
    assert assessed(tmp_path, result.stdout)['status'] == 'failed', result.stdout


def test_each_sequential_outer_invocation_is_checked(tmp_path):
    result = run_pytest(tmp_path, {'test_ok.py': 'def test_ok(): pass\n', 'empty/no_tests.py': ''},
                        python="import pytest; pytest.main(['-q', 'empty']); pytest.main(['-q', 'test_ok.py'])")
    _, runs, error = scope_output(result.stdout, TOKEN)
    assert error is None and [r['executed'] for r in runs] == [0, 1]
    assert assessed(tmp_path, result.stdout)['status'] == 'failed'


def test_runner_diagnostics_are_not_inferred_from_prose(tmp_path):
    result = run_pytest(tmp_path, {'test_ok.py': 'def test_ok(): pass\n'})
    assert assessed(tmp_path, result.stdout + '\napplication message: pytest: command not found\n')['status'] == 'completed'


def test_stale_context_cannot_hide_failures(tmp_path):
    result = run_pytest(tmp_path, {'test_ok.py': 'def test_ok(): pass\n'})
    output = result.stdout.replace(TOKEN, '2' * 32) + '\nno tests ran in 0.01s\n'
    assert assessed(tmp_path, output)['status'] == 'failed'


def test_incomplete_invocation_is_rejected(tmp_path):
    output = 'OT_VERIFIER_EXECUTION:' + TOKEN + ':BEGIN:' + json.dumps({'version': 1, 'id': 'x'}) + '\n'
    assert assessed(tmp_path, output)['status'] == 'failed'


def test_missing_runner_adapter_requires_evidence(tmp_path):
    assert assessed(tmp_path, 'custom grader: incorrect answer\n')['status'] == 'failed'


def test_malformed_counts_are_rejected(tmp_path):
    begin = {'version': 1, 'id': 'x'}
    end = dict(begin, finished=True, executed=True, skipped=0, setup_errors=0, collection_errors=0, exitstatus=0)
    output = '\n'.join('OT_VERIFIER_EXECUTION:' + TOKEN + ':' + event + ':' + json.dumps(d)
                       for event, d in [('BEGIN', begin), ('END', end)])
    assert assessed(tmp_path, output)['status'] == 'failed'


def test_end_record_survives_tests_clearing_the_environment(tmp_path):
    # skew-92's suite replaced os.environ before the session ended; the END
    # record must still carry the token captured at configure time.
    result = run_pytest(tmp_path, {'test_env.py': 'import os\ndef test_clear():\n    os.environ.clear()\n'})
    output, states, problem = scope_output(result.stdout, TOKEN)
    assert problem is None and len(states) == 1
    assert states[0]['executed'] == 1 and states[0]['exitstatus'] == 0
    assert assessed(tmp_path, result.stdout, reward=1, expected=1)['status'] == 'completed'


def test_phase_counts_survive_a_dependency_shadowing_report_attributes(tmp_path):
    # sure 1.2.3 installs an object.when property with a no-op setter, so pytest's
    # report.when is lost; uncurl-25 then showed 33 passes for 11 tests and the
    # recorder counted none. Phases come from the hooks instead.
    conftest = ('import _pytest.reports\n'
                "_pytest.reports.BaseReport.when = property(lambda self: 'shadowed', lambda self, value: None)\n")
    tests = ('import pytest\n'
             '@pytest.fixture\ndef broken(): raise RuntimeError("setup failed")\n'
             'def test_ok(): pass\n'
             'def test_fail(): assert False\n'
             '@pytest.mark.skip\ndef test_skip(): pass\n'
             'def test_setup(broken): pass\n')
    result = run_pytest(tmp_path, {'conftest.py': conftest, 'test_shadow.py': tests}, ['-rA'])
    assert result.stdout.count('PASSED') > 1, 'the shadowing must disturb pytest as it did in the task'
    _, states, problem = scope_output(result.stdout, TOKEN)
    assert problem is None and len(states) == 1, result.stdout + result.stderr
    assert states[0]['executed'] == 2 and states[0]['skipped'] == 1 and states[0]['setup_errors'] == 1
    assert states[0]['exitstatus'] == 1


def test_xdist_controller_counts_phases_from_worker_reports():
    # pytest-xdist runs every test in a worker; the controller only receives
    # serialized reports, so the setup/call hooks never fire there (PyBaMM-4644
    # and github3.py-1167 under 'pytest -n 2' recorded executed=0 for passing
    # suites in job 964525). The counts come from the raw report dicts, whose
    # keys no dependency can shadow.
    from harbor_patches.ot_pytest_execution import ExecutionRecorder
    state = {'executed': 0, 'skipped': 0, 'setup_errors': 0, 'collection_errors': 0}
    recorder = ExecutionRecorder(state)

    def deliver(data):
        gen = recorder.pytest_report_from_serializable(config=None, data=data)
        next(gen)
        try:
            gen.send(None)
        except StopIteration:
            pass
    deliver({'$report_type': 'TestReport', 'when': 'setup', 'outcome': 'passed'})
    deliver({'$report_type': 'TestReport', 'when': 'call', 'outcome': 'passed'})
    deliver({'$report_type': 'TestReport', 'when': 'teardown', 'outcome': 'passed'})
    deliver({'$report_type': 'TestReport', 'when': 'setup', 'outcome': 'failed'})
    deliver({'$report_type': 'TestReport', 'when': 'call', 'outcome': 'failed'})
    deliver({'$report_type': 'CollectReport', 'outcome': 'failed'})
    deliver('not a report')
    assert state == {'executed': 2, 'skipped': 0, 'setup_errors': 1, 'collection_errors': 0}


def test_xdist_run_records_executed_tests(tmp_path):
    pytest.importorskip('xdist')
    tests = ('import pytest\n'
             '@pytest.fixture\ndef broken(): raise RuntimeError("setup failed")\n'
             'def test_a(): pass\n'
             'def test_b(): pass\n'
             'def test_fail(): assert False\n'
             '@pytest.mark.skip\ndef test_skip(): pass\n'
             'def test_setup(broken): pass\n')
    result = run_pytest(tmp_path, {'test_dist.py': tests}, ['-n', '2', '-p', 'xdist', '-rA'])
    assert 'passed' in result.stdout, result.stdout + result.stderr
    _, states, problem = scope_output(result.stdout, TOKEN)
    assert problem is None and len(states) == 1, result.stdout + result.stderr
    assert states[0]['executed'] == 3 and states[0]['skipped'] == 1 and states[0]['setup_errors'] == 1
    assert states[0]['exitstatus'] == 1
    assert assessed(tmp_path, result.stdout, expected=0)['status'] == 'completed'
