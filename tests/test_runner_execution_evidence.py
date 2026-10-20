"""Execution evidence beyond pytest: unittest records, and no-op zeros of tasks
without a reference whose new tests do not build yet (MiMo's Go tasks).

The go and node recorders (harbor_patches/ot_execution) need a go toolchain or
node in the image; they were run in golang:1.22 and node:20 containers on
2026-10-17 (go test with a build failure, jest via npm test, mocha, vitest).
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from validation.stages.execution_evidence import scope_output
from validation.stages.harbor import assess_trials, missing_api_explains_build_errors

PATCHES = Path(__file__).resolve().parents[1] / 'harbor_patches'
TOKEN = 'b' * 32


def run_unittest(tmp_path, *args):
    env = dict(os.environ, OT_VERIFIER_EXECUTION_TOKEN=TOKEN, PYTHONPATH=str(PATCHES))
    env.pop('OT_PYTEST_EXECUTION_ACTIVE', None)
    result = subprocess.run([sys.executable, *args], cwd=tmp_path, env=env, capture_output=True, text=True)
    return result, scope_output(result.stdout, TOKEN)


def test_unittest_counts_executed_skipped_and_import_failures(tmp_path):
    (tmp_path / 'test_ok.py').write_text(
        'import unittest\n'
        'class T(unittest.TestCase):\n'
        '    def test_pass(self): print("Ran 99 tests")\n'
        '    def test_fail(self): self.fail("expected")\n'
        '    @unittest.skip("later")\n'
        '    def test_skip(self): pass\n')
    (tmp_path / 'test_new.py').write_text('from feature import added\n')
    result, (_, invocations, problem) = run_unittest(tmp_path, '-m', 'unittest', 'test_ok', 'test_new')
    assert result.returncode == 1 and problem is None
    [record] = invocations
    assert record['runner'] == 'unittest' and record['finished'] is True
    assert (record['executed'], record['skipped'], record['collection_errors'], record['exitstatus']) == (2, 1, 1, 1)


def test_unittest_record_is_unfinished_when_no_run_completes(tmp_path):
    _, (_, invocations, problem) = run_unittest(tmp_path, '-m', 'unittest', '--no-such-flag')
    assert problem == 'verifier invocation did not finish' and invocations == []


def test_other_python_processes_stay_unrecorded(tmp_path):
    result, (_, invocations, problem) = run_unittest(tmp_path, '-c', 'print("plain")')
    assert result.stdout.strip() == 'plain' and invocations == [] and problem is None


def go_task(tmp_path, reference=False):
    task = tmp_path / 'task'
    (task / 'tests').mkdir(parents=True)
    (task / 'tests' / 'test.patch').write_text(
        'diff --git a/state/transition_recoverable_test.go b/state/transition_recoverable_test.go\n'
        '+++ b/state/transition_recoverable_test.go\n'
        'diff --git a/txpool/decrease_nonce_test.go b/txpool/decrease_nonce_test.go\n'
        '+++ b/txpool/decrease_nonce_test.go\n')
    if reference:
        (task / 'solution').mkdir()
        (task / 'solution' / 'fix.patch').write_text('diff --git a/txpool/pool.go b/txpool/pool.go\n+x\n')
    return task


def go_record(files=('txpool/decrease_nonce_test.go',), messages=None, **extra):
    messages = messages or ['txpool/decrease_nonce_test.go: pool.DecreaseAccountNonce undefined '
                            '(type *TxPool has no field or method DecreaseAccountNonce)']
    return {'version': 1, 'id': 'go', 'finished': True, 'runner': 'go test', 'executed': 12, 'skipped': 0,
            'setup_errors': 0, 'collection_errors': len(files), 'exitstatus': 1,
            'collection_error_files': list(files), 'collection_error_messages': messages, **extra}


@pytest.mark.parametrize('record, reference, accepted', [
    (go_record(), False, True),
    (go_record(files=('state/transition_recoverable_test.go', 'txpool/decrease_nonce_test.go'),
               messages=['state/transition_recoverable_test.go: undefined: IsRecoverable',
                         'txpool/decrease_nonce_test.go: not enough arguments in call to NewPool']), False, True),
    # The project's own code does not build: an environment problem, not the task's new test.
    (go_record(files=('txpool/pool.go',), messages=['txpool/pool.go: undefined: NewPool']), False, False),
    (go_record(messages=['txpool/decrease_nonce_test.go: could not import github.com/x/y (no required module)']),
     False, False),
    (go_record(setup_failed_packages=1), False, False),
    # With a reference solution the reference-based rules decide instead.
    (go_record(), True, False),
    # pytest records carry no files or messages.
    ({'collection_errors': 1, 'executed': 0}, False, False),
])
def test_missing_api_build_errors_rule(tmp_path, record, reference, accepted):
    assert missing_api_explains_build_errors(record, go_task(tmp_path, reference)) is accepted


def test_jest_relative_module_of_the_new_test_is_missing_api(tmp_path):
    task = tmp_path / 'task'
    (task / 'tests').mkdir(parents=True)
    (task / 'tests' / 'test.patch').write_text('diff --git a/test/new.test.js b/test/new.test.js\n')
    record = {'collection_error_files': ['test/new.test.js'],
              'collection_error_messages': ["Cannot find module '../src/sub' from 'test/new.test.js'"]}
    assert missing_api_explains_build_errors(record, task)
    record['collection_error_messages'] = ["Cannot find module 'lodash' from 'test/new.test.js'"]
    assert not missing_api_explains_build_errors(record, task)


def test_nop_accepts_go_zero_from_new_tests_that_do_not_build(tmp_path):
    task = go_task(tmp_path)
    trial = tmp_path / 'trial'
    (trial / 'verifier').mkdir(parents=True)
    (trial / 'verifier' / 'execution-context.json').write_text(json.dumps({'version': 1, 'token': TOKEN}))
    def output(record):
        return ('OT_VERIFIER_EXECUTION:' + TOKEN + ':BEGIN:' + json.dumps({'version': 1, 'id': 'go'}) + '\n'
                'FAIL\tgithub.com/x/txpool [build failed]\n'
                'OT_VERIFIER_EXECUTION:' + TOKEN + ':END:' + json.dumps(record) + '\n')
    result = {'verifier_result': {'rewards': {'reward': 0}, 'stdout': output(go_record())}}
    assert assess_trials([(trial, result)], 1, 0, task_path=task)['status'] == 'completed'
    broken = go_record(files=('txpool/pool.go',), messages=['txpool/pool.go: undefined: NewPool'])
    result = {'verifier_result': {'rewards': {'reward': 0}, 'stdout': output(broken)}}
    assert assess_trials([(trial, result)], 1, 0, task_path=task)['status'] == 'failed'


def python_hooks_site(tmp_path):
    """A venv whose site-packages took the recorders through the real install command."""
    from harbor_patches.verifier_setup import install_python_hooks_command
    target = tmp_path / 'target'
    target.mkdir()
    for name in ('ot_pytest_execution.py', 'sitecustomize.py'):
        (target / name).write_bytes((PATCHES / name).read_bytes())
    root = tmp_path / 'root'
    subprocess.run([sys.executable, '-m', 'venv', '--without-pip', str(root / 'venv')], check=True)
    result = subprocess.run(['sh', '-c', install_python_hooks_command(str(target), roots=str(root))],
                            capture_output=True, text=True)
    venv = [line for line in result.stdout.splitlines() if str(root) in line.split(' ', 1)[-1]]
    assert venv and venv[0].startswith('ok '), result.stdout + result.stderr
    return target, root / 'venv', Path(venv[0].rsplit(' ', 1)[1])


def test_python_hooks_survive_an_assigned_pythonpath(tmp_path):
    target, venv, site = python_hooks_site(tmp_path)
    assert sorted(p.name for p in site.iterdir() if p.name.startswith(('ot_', 'zz_ot'))) == [
        'ot_execution_hook.py', 'ot_pytest_execution.py', 'zz_ot_execution.pth']
    (tmp_path / 'test_u.py').write_text('import unittest\nclass T(unittest.TestCase):\n    def test_a(self): pass\n')
    env = dict(os.environ, OT_VERIFIER_EXECUTION_TOKEN=TOKEN, PYTHONPATH='src')
    env.pop('OT_PYTEST_EXECUTION_ACTIVE', None)
    result = subprocess.run([str(venv / 'bin' / 'python'), '-m', 'unittest', 'test_u'], cwd=tmp_path, env=env,
                            capture_output=True, text=True)
    _, [record], problem = scope_output(result.stdout, TOKEN)
    assert problem is None and record['runner'] == 'unittest' and record['executed'] == 1


def test_python_hooks_are_removed_exactly(tmp_path):
    from harbor_patches.verifier_setup import remove_python_hooks_command
    target, venv, site = python_hooks_site(tmp_path)
    subprocess.run([str(venv / 'bin' / 'python'), '-c', 'import ot_execution_hook'], check=True)
    (site / 'keep.py').write_text('')
    subprocess.run(['sh', '-c', remove_python_hooks_command(str(target))], check=True)
    left = sorted(p.name for p in site.rglob('*') if 'ot_' in p.name)
    assert left == [] and (site / 'keep.py').is_file()


def zero_test_call(extra=None):
    call = dict(go_record(files=(), messages=['-']), id='compile', executed=0, collection_errors=0,
                exitstatus=0, collection_error_files=[], collection_error_messages=None)
    call.update(extra or {})
    return call


@pytest.mark.parametrize('calls, accepted', [
    # MiMo 001782: `go test -run '^$'` compiles only, then the selected tests run.
    ([zero_test_call(), dict(go_record(files=(), messages=['-']), id='tests', collection_errors=0)], True),
    ([zero_test_call()], False),
    ([zero_test_call({'exitstatus': 1}), dict(go_record(files=(), messages=['-']), id='tests',
                                              collection_errors=0)], False),
    ([zero_test_call({'setup_errors': 1}), dict(go_record(files=(), messages=['-']), id='tests',
                                                collection_errors=0)], False),
])
def test_clean_call_without_tests_is_accepted_when_another_call_ran_tests(tmp_path, calls, accepted):
    trial = tmp_path / 'trial'
    (trial / 'verifier').mkdir(parents=True)
    (trial / 'verifier' / 'execution-context.json').write_text(json.dumps({'version': 1, 'token': TOKEN}))
    output = ''.join('OT_VERIFIER_EXECUTION:' + TOKEN + ':BEGIN:' + json.dumps({'version': 1, 'id': c['id']}) + '\n'
                     'OT_VERIFIER_EXECUTION:' + TOKEN + ':END:' + json.dumps(c) + '\n' for c in calls)
    result = {'verifier_result': {'rewards': {'reward': 0}, 'stdout': output}}
    status = assess_trials([(trial, result)], 1, 0, task_path=go_task(tmp_path))['status']
    assert status == ('completed' if accepted else 'failed')


@pytest.mark.parametrize('output, accepted', [
    ('FAIL: F1 missing --open help\n', True),
    ('build ok\nFAIL: F3 exit 2; want 0\n', True),
    # Preconditions carry no check number: the checks never ran.
    ('FAIL: manimgl not found after build\n', False),
    ('FAIL: xvfb-run is required for ManimGL rendering in this environment\n', False),
    ('bash: usecase.sh: No such file or directory\n', False),
])
def test_nop_accepts_numbered_usecase_check_failure(tmp_path, output, accepted):
    task = tmp_path / 'task'
    (task / 'tests').mkdir(parents=True)
    (task / 'tests' / 'test.patch').write_text(
        'diff --git a/usercase-test-coderl/usecase.sh b/usercase-test-coderl/usecase.sh\n'
        'diff --git a/mimo_test_command.sh b/mimo_test_command.sh\n')
    trial = tmp_path / 'trial'
    (trial / 'verifier').mkdir(parents=True)
    (trial / 'verifier' / 'execution-context.json').write_text(json.dumps({'version': 1, 'token': TOKEN}))
    result = {'verifier_result': {'rewards': {'reward': 0}, 'stdout': output}}
    status = assess_trials([(trial, result)], 1, 0, task_path=task)['status']
    assert status == ('completed' if accepted else 'failed')


def test_numbered_check_rule_needs_the_tasks_own_usecase_script(tmp_path):
    task = tmp_path / 'task'
    (task / 'tests').mkdir(parents=True)
    (task / 'tests' / 'test.patch').write_text('diff --git a/tests/test_x.py b/tests/test_x.py\n')
    trial = tmp_path / 'trial'
    (trial / 'verifier').mkdir(parents=True)
    (trial / 'verifier' / 'execution-context.json').write_text(json.dumps({'version': 1, 'token': TOKEN}))
    result = {'verifier_result': {'rewards': {'reward': 0}, 'stdout': 'FAIL: F1 printed by something else\n'}}
    assert assess_trials([(trial, result)], 1, 0, task_path=task)['status'] == 'failed'


def test_go_binaries_called_by_absolute_path_reach_the_shim(tmp_path):
    """MiMo pins toolchains such as /root/go/pkg/mod/golang.org/toolchain@.../bin/go;
    the wrapper forwards to the shim, and the cleanup restores the original byte for byte."""
    import shutil
    from harbor_patches.verifier_setup import install_go_wrappers_command, remove_go_wrappers_command
    target = tmp_path / 'target'
    shims = target / 'ot_execution'
    shutil.copytree(PATCHES / 'ot_execution', shims)
    (shims / 'bin' / 'go').chmod(0o755)
    toolchain = tmp_path / 'root' / 'toolchain@v0.0.1' / 'bin'
    toolchain.mkdir(parents=True)
    real = toolchain / 'go'
    real.write_text('#!/bin/sh\necho "real go $*"\n')
    real.chmod(0o755)
    toolchain.chmod(0o555)  # module caches are read-only
    original = real.read_bytes()
    env = dict(os.environ, PATH='/usr/bin:/bin')
    subprocess.run(['bash', '-c', install_go_wrappers_command(str(target), str(shims), roots=str(tmp_path / 'root'))],
                   check=True, env=env, capture_output=True)
    assert b'ot-validation go wrapper' in real.read_bytes()
    # Not a test call: the shim runs the original with the arguments unchanged.
    result = subprocess.run([str(real), 'version', '-run', 'A B'], env=env, capture_output=True, text=True)
    assert result.stdout.strip() == 'real go version -run A B'
    subprocess.run(['bash', '-c', remove_go_wrappers_command(str(target))], check=True, env=env)
    assert real.read_bytes() == original and not (toolchain / 'go.ot-real').exists()
    assert oct(toolchain.stat().st_mode & 0o777) == '0o555'
    toolchain.chmod(0o755)


@pytest.mark.parametrize('messages, accepted', [
    # The compiler's closing "too many errors" line does not block (MiMo 000539).
    (['txpool/decrease_nonce_test.go: undefined: NewPool', 'txpool/decrease_nonce_test.go: too many errors'], True),
    (['txpool/decrease_nonce_test.go: too many errors'], False),
])
def test_closing_compiler_line_is_ignored(tmp_path, messages, accepted):
    assert missing_api_explains_build_errors(go_record(messages=messages), go_task(tmp_path)) is accepted


@pytest.mark.parametrize('message, accepted', [
    # vite root-relative and node ESM absolute paths of the module still to write (000551, 001555).
    ("Cannot find module '/packages/messaging/src/service/StrategyMonitor.js' imported from x", True),
    ("Cannot find module '/workspace/repo/core/code/filter.js' imported from x", True),
    ("Cannot find module '/workspace/repo/node_modules/lodash/index.js' imported from x", False),
])
def test_absolute_module_paths_of_the_project(tmp_path, message, accepted):
    task = tmp_path / 'task'
    (task / 'tests').mkdir(parents=True)
    (task / 'tests' / 'test.patch').write_text('diff --git a/usercase-test-coderl/a.test.ts b/usercase-test-coderl/a.test.ts\n')
    record = {'collection_error_files': ['usercase-test-coderl/a.test.ts'], 'collection_error_messages': [message]}
    assert missing_api_explains_build_errors(record, task) is accepted


def test_nop_accepts_mocha_hook_importing_the_missing_module(tmp_path):
    task = tmp_path / 'task'
    (task / 'tests').mkdir(parents=True)
    (task / 'tests' / 'test.patch').write_text('diff --git a/usercase-test-coderl/f.test.js b/usercase-test-coderl/f.test.js\n')
    trial = tmp_path / 'trial'
    (trial / 'verifier').mkdir(parents=True)
    (trial / 'verifier' / 'execution-context.json').write_text(json.dumps({'version': 1, 'token': TOKEN}))
    record = {'version': 1, 'id': 'm', 'finished': True, 'runner': 'mocha', 'executed': 0, 'skipped': 0,
              'setup_errors': 1, 'collection_errors': 0, 'exitstatus': 1,
              'collection_error_files': ['usercase-test-coderl/f.test.js'],
              'collection_error_messages': ["Cannot find module '/workspace/repo/core/f.js' imported from x"]}
    output = ('OT_VERIFIER_EXECUTION:' + TOKEN + ':BEGIN:' + json.dumps({'version': 1, 'id': 'm'}) + '\n'
              'OT_VERIFIER_EXECUTION:' + TOKEN + ':END:' + json.dumps(record) + '\n')
    result = {'verifier_result': {'rewards': {'reward': 0}, 'stdout': output}}
    assert assess_trials([(trial, result)], 1, 0, task_path=task)['status'] == 'completed'


def test_pytest_maxfail_stop_is_a_test_failure(tmp_path):
    """pytest before 5 exits 2 after -x stops on a failure; current pytest exits 1."""
    (tmp_path / 'test_a.py').write_text('def test_a():\n    assert 0\n\ndef test_b():\n    pass\n')
    env = dict(os.environ, OT_VERIFIER_EXECUTION_TOKEN=TOKEN, PYTHONPATH=str(hook_dir(tmp_path)))
    for key in ('OT_PYTEST_EXECUTION_ACTIVE', 'PYTEST_PLUGINS'):
        env.pop(key, None)
    result = subprocess.run([sys.executable, '-m', 'pytest', '-x', '-p', 'no:cacheprovider', 'test_a.py'],
                            cwd=tmp_path, env=env, capture_output=True, text=True)
    _, invocations, problem = scope_output(result.stdout, TOKEN)
    assert problem is None and invocations[0]['exitstatus'] == 1 and invocations[0]['executed'] == 1


def test_unittest_detected_without_sys_argv(monkeypatch):
    """Python before 3.8 runs site before sys.argv exists (MiMo 000543, python3.5)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location('ot_hook_under_test', PATCHES / 'sitecustomize.py')
    hook = importlib.util.module_from_spec(spec)
    monkeypatch.setenv('OT_VERIFIER_EXECUTION_TOKEN', 'c' * 32)  # inert while loading
    monkeypatch.setenv('OT_PYTEST_EXECUTION_ACTIVE', 'c' * 32)
    spec.loader.exec_module(hook)
    monkeypatch.setattr(hook, '_command_line', lambda: ['-u', '-m', 'unittest', 'tests.test_x'])
    environ = {'OT_VERIFIER_EXECUTION_TOKEN': TOKEN}
    monkeypatch.setattr(hook.atexit, 'register', lambda f: f)  # no END line at interpreter exit
    monkeypatch.delattr(sys, 'argv')
    state = hook.install(environ=environ)
    assert state is not None and state['runner'] == 'unittest'


@pytest.mark.parametrize('output, task_name, accepted', [
    ('FAIL: out missing regex: (^|[[:space:]])-s,[[:space:]]+--skip-sc[[:space:]]+Skip SuperCollider '
     'detection and management entirely\n', 'format-code-task-002598', True),
    # Another failure of the reviewed task is judged as usual.
    ('FAIL: build\n', 'format-code-task-002598', False),
    # The same line from a task nobody reviewed is not accepted.
    ('FAIL: out missing regex: (^|[[:space:]])-s,[[:space:]]+--skip-sc[[:space:]]+Skip SuperCollider '
     'detection and management entirely\n', 'format-code-task-000001', False),
])
def test_reviewed_nop_zero_accepts_only_the_reviewed_line(tmp_path, output, task_name, accepted):
    task = tmp_path / task_name
    (task / 'tests').mkdir(parents=True)
    trial = tmp_path / 'trial'
    (trial / 'verifier').mkdir(parents=True)
    (trial / 'verifier' / 'execution-context.json').write_text(json.dumps({'version': 1, 'token': TOKEN}))
    result = {'verifier_result': {'rewards': {'reward': 0}, 'stdout': output}}
    assert assess_trials([(trial, result)], 1, 0, task_path=task)['status'] == ('completed' if accepted else 'failed')


def hook_dir(tmp_path):
    """The hook as sitecustomize next to the plugin (the PYTHONPATH fallback layout)."""
    import shutil
    d = tmp_path / 'hook'
    d.mkdir()
    shutil.copy(PATCHES / 'sitecustomize.py', d / 'sitecustomize.py')
    shutil.copy(PATCHES / 'ot_pytest_execution.py', d / 'ot_pytest_execution.py')
    return d


def test_hook_names_the_plugin_inside_pytest_only(tmp_path):
    """No PYTEST_PLUGINS in the environment: `python -m pytest` with the hook records, and
    processes the tests start do not inherit PYTEST_PLUGINS."""
    (tmp_path / 'test_x.py').write_text(
        'import os\n'
        'def test_a():\n    assert "ot_pytest_execution" not in os.environ.get("PYTEST_PLUGINS", "")\n'
        'def test_b():\n    assert False\n')
    env = dict(os.environ, OT_VERIFIER_EXECUTION_TOKEN=TOKEN, PYTHONPATH=str(hook_dir(tmp_path)))
    for key in ('OT_PYTEST_EXECUTION_ACTIVE', 'PYTEST_PLUGINS'):
        env.pop(key, None)
    result = subprocess.run([sys.executable, '-m', 'pytest', '-q', '-p', 'no:cacheprovider', 'test_x.py'],
                            cwd=tmp_path, env=env, capture_output=True, text=True)
    _, [record], problem = scope_output(result.stdout, TOKEN)
    assert problem is None and (record['executed'], record['exitstatus']) == (2, 1), result.stdout


def test_pytest_without_the_hook_runs_unrecorded(tmp_path):
    """An interpreter the install did not reach: no plugin is named, so pytest runs the tests
    instead of aborting on a missing plugin."""
    (tmp_path / 'test_x.py').write_text('def test_a():\n    pass\n')
    env = dict(os.environ, OT_VERIFIER_EXECUTION_TOKEN=TOKEN)
    for key in ('OT_PYTEST_EXECUTION_ACTIVE', 'PYTEST_PLUGINS', 'PYTHONPATH'):
        env.pop(key, None)
    result = subprocess.run([sys.executable, '-m', 'pytest', '-q', '-p', 'no:cacheprovider', 'test_x.py'],
                            cwd=tmp_path, env=env, capture_output=True, text=True)
    assert result.returncode == 0 and 'OT_VERIFIER_EXECUTION' not in result.stdout


@pytest.mark.parametrize('command, expected', [
    (['-m', 'pytest', '-x'], True), (['-u', '-m', 'py.test'], True),
    (['/usr/local/bin/pytest', 'tests'], True), (['-m', 'unittest'], False),
    (['-c', 'import pytest; pytest.main()'], False), (['manage.py', 'test'], False),
])
def test_pytest_process_detection(command, expected):
    import importlib.util
    spec = importlib.util.spec_from_file_location('ot_hook_detect', PATCHES / 'sitecustomize.py')
    hook = importlib.util.module_from_spec(spec)
    os.environ.setdefault('OT_PYTEST_EXECUTION_ACTIVE', '')  # loading stays inert without a token
    spec.loader.exec_module(hook)
    assert hook._is_pytest_process(command) is expected


def test_pytest_collection_errors_are_recorded_and_judged(tmp_path):
    """MiMo 002615: the new test file imports a name the project does not define yet."""
    pkg = tmp_path / 'src' / 'proj'
    pkg.mkdir(parents=True)
    (pkg / '__init__.py').write_text('existing = 1\n')
    (tmp_path / 'usercase-test-coderl').mkdir()
    (tmp_path / 'usercase-test-coderl' / 'test_new.py').write_text('from proj import sequence_dist\n')
    env = dict(os.environ, OT_VERIFIER_EXECUTION_TOKEN=TOKEN,
               PYTHONPATH=os.pathsep.join([str(hook_dir(tmp_path)), str(tmp_path / 'src')]))
    for key in ('OT_PYTEST_EXECUTION_ACTIVE', 'PYTEST_PLUGINS'):
        env.pop(key, None)
    result = subprocess.run([sys.executable, '-m', 'pytest', '-q', '-p', 'no:cacheprovider', 'usercase-test-coderl'],
                            cwd=tmp_path, env=env, capture_output=True, text=True)
    _, [record], problem = scope_output(result.stdout, TOKEN)
    assert problem is None and record['collection_errors'] == 1
    assert record['collection_error_files'] == ['usercase-test-coderl/test_new.py']
    assert "cannot import name 'sequence_dist' from 'proj'" in record['collection_error_messages'][0]
    task = tmp_path / 'task'
    (task / 'tests').mkdir(parents=True)
    (task / 'tests' / 'test.patch').write_text('diff --git a/usercase-test-coderl/test_new.py b/usercase-test-coderl/test_new.py\n')
    assert missing_api_explains_build_errors(record, task)
    record['collection_error_messages'] = [
        "ImportError: cannot import name 'x' from 'numpy' (/usr/lib/python3/dist-packages/numpy/__init__.py)"]
    assert not missing_api_explains_build_errors(record, task)


def test_records_go_to_the_calls_log_not_into_the_graded_output(tmp_path):
    """Multi-SWE graders parse runner output; an indentation-based mocha parser takes the
    last unindented line as a name prefix, so a record line there changes every name."""
    (tmp_path / 'test_ok.py').write_text('import unittest\nclass T(unittest.TestCase):\n    def test_pass(self): pass\n')
    log = tmp_path / 'execution.log'
    log.write_text('')
    env = dict(os.environ, OT_VERIFIER_EXECUTION_TOKEN=TOKEN, PYTHONPATH=str(PATCHES), OT_VERIFIER_EXECUTION_LOG=str(log))
    env.pop('OT_PYTEST_EXECUTION_ACTIVE', None)
    for args in (['-m', 'unittest', 'test_ok'], ['-m', 'pytest', '-q', '-p', 'ot_pytest_execution', 'test_ok.py']):
        log.write_text('')
        result = subprocess.run([sys.executable, *args], cwd=tmp_path, env=env, capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        assert 'OT_VERIFIER_EXECUTION' not in result.stdout + result.stderr
        _, invocations, problem = scope_output(log.read_text(), TOKEN)
        assert problem is None and [i['executed'] for i in invocations] == [1]
