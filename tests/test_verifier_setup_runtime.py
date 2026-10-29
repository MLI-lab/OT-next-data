"""Exercise the real Harbor Verifier with an instrumented environment."""
import asyncio
import json
import shlex
from pathlib import Path
from types import SimpleNamespace

import pytest

from harbor_patches import verifier_setup


@pytest.mark.parametrize('mode', ['shared', 'separate', 'step', 'separate-step'])
@pytest.mark.parametrize('outcome', ['pass', 'absent', 'failure', 'timeout', 'scored-failure'])
@pytest.mark.parametrize('stale_reward', ['0', '1'])
@pytest.mark.parametrize('prepared_runtime', [False, True])
def test_normal_verification_runs_setup_before_checks(tmp_path, mode, outcome, stale_reward, prepared_runtime, monkeypatch):
    from harbor.models.task.task import Task
    from harbor.models.trial.paths import TrialPaths
    from harbor.verifier.verifier import Verifier, VerifierRuntimeError
    task_dir = tmp_path/'task'
    for d in ('tests', 'environment'): (task_dir/d).mkdir(parents=True)
    (task_dir/'instruction.md').write_text('task')
    (task_dir/'task.toml').write_text('[verifier]\n[verifier.env]\nTEST_VAR="from-config"\n')
    (task_dir/'tests/test.sh').write_text('grade')
    if outcome != 'absent': (task_dir/'tests/setup.sh').write_text('prepare')
    task = Task(task_dir)
    if mode.endswith('step'):
        step_dir = task.paths.step_tests_dir('first')
        step_dir.mkdir(parents=True)
        (step_dir/'test.sh').write_text('step grade')
        if outcome != 'absent': (step_dir/'setup.sh').write_text('step prepare')
    paths = TrialPaths(tmp_path/'trial'); paths.mkdir()
    # Even an old reward must not turn setup failure into a successful result.
    paths.reward_text_path.write_text(stale_reward)
    events = []
    runtime_root = '/tests/.ot-verifier-runtime-abc123'
    runtime_prefix = (f'export OT_VERIFIER_PYTHON={runtime_root}/active/bin/python3; '
                      f'export OT_VERIFIER_BIN={runtime_root}/active/bin; ')
    if prepared_runtime:
        from harbor_patches import verifier_bundle
        async def stage(*args):
            return dict(directory=runtime_root, python=runtime_root + '/active/bin/python3',
                        bin=runtime_root + '/active/bin', mount_mode='readonly')
        monkeypatch.setattr(verifier_bundle, 'stage', stage)
    class Environment:
        os = task.config.environment.os
        capabilities = SimpleNamespace(mounted=True)
        async def upload_dir(self, source_dir, target_dir):
            if Path(source_dir).name == 'ot_execution':
                # go/node execution-evidence shims, next to the recorders
                assert target_dir.startswith('/tmp/ot-pytest-execution-') and target_dir.endswith('/ot_execution')
                events.append(('shims', target_dir))
            else:
                events.append(('upload', source_dir))
        async def upload_file(self, source_path, target_path):
            assert Path(source_path).name in ('ot_pytest_execution.py', 'sitecustomize.py')
            assert target_path.startswith('/tmp/ot-pytest-execution-')
            events.append(('recorder', target_path))
        async def exec(self, command, **kwargs):
            if command.startswith('unshare '):
                events.append(('mount', command))
                command = shlex.split(command)[-1]
                assert command.startswith(runtime_prefix)
                command = command.removeprefix(runtime_prefix)
            if command.startswith('if [ -d ' + runtime_root):
                events.append(('runtime-discard', command))
                return SimpleNamespace(return_code=0, stdout='', stderr='')
            if command == verifier_setup.CLEAR_REWARDS_COMMAND:
                paths.reward_text_path.unlink(missing_ok=True)
                events.append(('clear-reward', command))
                return SimpleNamespace(return_code=0, stdout='', stderr='')
            if command == verifier_setup.FAILED_SETUP_REWARD_COMMAND:
                return SimpleNamespace(return_code=0 if outcome == 'scored-failure' else 1, stdout='', stderr='')
            if command.startswith(('chmod', 'rm -f', 'rm -rf')):
                events.append(('clear' if command.startswith('rm') else 'chmod', command))
                return SimpleNamespace(return_code=0, stdout='', stderr='')
            if command.startswith('T=/tmp/ot-pytest-execution-'):
                # python recorders copied into the interpreter's site-packages, as root
                assert kwargs['user'] == 'root'
                events.append(('python-hooks', command))
                return SimpleNamespace(return_code=0, stdout='ok /usr/bin/python3 /usr/lib/python3/dist-packages\n', stderr='')
            if command.startswith('out=/tmp/ot-pytest-execution-') and 'go-wrappers.txt; : >' in command:
                # go binaries replaced by wrappers to the evidence shim, as root
                assert kwargs['user'] == 'root'
                events.append(('go-wrappers', command))
                return SimpleNamespace(return_code=0, stdout='', stderr='')
            if command.startswith('touch /tmp/ot-pytest-execution-') and command.endswith('/junit-start'):
                assert kwargs['user'] == 'root'
                events.append(('junit-start', command))
                return SimpleNamespace(return_code=0, stdout='', stderr='')
            if command.startswith('sh /tmp/ot-pytest-execution-') and '/junit_reports.sh ' in command:
                # JUnit reports of this call recorded after the grading command, as root
                assert kwargs['user'] == 'root' and verifier_setup.EXECUTION_LOG in command
                events.append(('junit-reports', command))
                return SimpleNamespace(return_code=0, stdout='', stderr='')
            if command.startswith('out=/tmp/ot-pytest-execution-'):
                assert 'go-wrappers.txt' in command  # the cleanup puts go binaries back too
                events.append(('cleanup', command))
                return SimpleNamespace(return_code=0, stdout='', stderr='')
            assert kwargs['env']['TEST_VAR'] == 'from-config'
            if command == verifier_setup.SETUP_COMMAND:
                events.append(('setup', command))
                if outcome == 'timeout': await asyncio.sleep(1)
                if outcome == 'scored-failure': paths.reward_text_path.write_text('0')
                return SimpleNamespace(return_code=1 if outcome in ('failure', 'scored-failure') else 0, stdout='setup output', stderr='')
            events.append(('grade', command))
            paths.reward_text_path.write_text('1')
            return SimpleNamespace(return_code=0, stdout='grade output', stderr='')
    env = Environment()
    verifier = Verifier(task, paths, env, skip_tests_upload=mode.startswith('separate'),
                        step_name='first' if mode.endswith('step') else None)
    verifier_setup.install(); verifier_setup.install()
    async def run():
        return await asyncio.wait_for(verifier.verify(), timeout=.05 if outcome == 'timeout' else 5)
    if outcome in ('failure', 'timeout'):
        with pytest.raises(VerifierRuntimeError if outcome == 'failure' else TimeoutError): asyncio.run(run())
        assert not any(e[0] == 'grade' for e in events)
    else:
        result = asyncio.run(run())
        assert result.rewards == {'reward': 0 if outcome == 'scored-failure' else 1}
        assert [e[0] for e in events if e[0] in ('setup', 'grade')] == (['setup'] if outcome == 'scored-failure' else ['setup', 'grade'])
    assert verifier.environment is env
    assert sum(e[0] == 'runtime-discard' for e in events) == int(prepared_runtime)
    if prepared_runtime:
        assert events[-1][0] == 'runtime-discard'
        assert sum(e[0] == 'mount' for e in events) == (1 if outcome in ('failure', 'timeout', 'scored-failure') else 2)
    assert verifier._skip_tests_upload == mode.startswith('separate')
    uploads = [e for e in events if e[0] == 'upload']
    assert len(uploads) == (2 if mode.endswith('step') else 1)
    assert sum(e[0] == 'clear' and e[1].startswith('rm -f ') for e in events) == 1
    if outcome not in ('failure', 'timeout', 'scored-failure'):
        assert sum(e[0] == 'recorder' for e in events) == 2  # pytest plugin and trial sitecustomize
        assert sum(e[0] == 'shims' for e in events) == 1
        grade = next(e[1] for e in events if e[0] == 'grade')
        assert 'OT_VERIFIER_EXECUTION_BIN=' in grade and 'NODE_OPTIONS="--require ' in grade
        # Installed into site-packages: the task keeps its own PYTHONPATH.
        assert 'export PYTHONPATH=' not in grade and 'PYTEST_PLUGINS' not in grade
        assert sum(e[0] == 'python-hooks' for e in events) == 1 and sum(e[0] == 'cleanup' for e in events) == 1
        names = [e[0] for e in events]
        assert names.index('junit-start') < names.index('grade') < names.index('junit-reports') < names.index('cleanup')
        record = json.loads((paths.verifier_dir / 'execution-instrumentation.json').read_text())
        assert record == {'python_hooks': [{'status': 'ok', 'python': '/usr/bin/python3',
                                            'site': '/usr/lib/python3/dist-packages'}],
                          'pythonpath_fallback': False, 'go_wrappers': []}
        assert (paths.verifier_dir / 'execution-context.json').is_file()
        grade = next(e[1] for e in events if e[0] == 'grade')
        assert 'PYTEST_PLUGINS' not in grade  # named inside pytest processes by the hook
    if uploads: assert events.index(uploads[-1]) < next(i for i,e in enumerate(events) if e[0] == 'setup')


@pytest.mark.parametrize('script, expected', [(None, 0), ('echo prepared', 0), ('exit 7', 7)])
def test_optional_setup_shell_propagates_exit_code(tmp_path, script, expected):
    import subprocess
    if script is not None: (tmp_path/'setup.sh').write_text(script + '\n')
    command = verifier_setup.SETUP_COMMAND.replace('/tests', str(tmp_path))
    result = subprocess.run(['bash', '-c', command], capture_output=True, text=True)
    assert result.returncode == expected
    if script == 'echo prepared': assert result.stdout.strip() == 'prepared'


def test_real_verifier_records_outer_run_and_preserves_nested_failure(tmp_path):
    import os
    import shutil
    import subprocess
    import sys
    from harbor.models.task.task import Task
    from harbor.models.trial.paths import TrialPaths
    from harbor.verifier.verifier import Verifier
    from validation.stages.harbor import assess_trials
    task_dir = tmp_path / 'task'
    (task_dir / 'tests').mkdir(parents=True)
    (task_dir / 'environment').mkdir()
    (task_dir / 'instruction.md').write_text('Fix the plugin')
    (task_dir / 'task.toml').write_text('[verifier]\n')
    (task_dir / 'tests/inner').mkdir()
    (task_dir / 'tests/test_outer.py').write_text(
        'import pytest\ndef test_plugin():\n    assert pytest.main(["-q", "inner"]) == 0\n')
    paths = TrialPaths(tmp_path / 'trial'); paths.mkdir()
    (task_dir / 'tests/test.sh').write_text(
        '#!/bin/bash\ncd ' + str(task_dir / 'tests') + '\n' + sys.executable + ' -m pytest -q test_outer.py\n'
        'printf "0" > ' + str(paths.reward_text_path) + '\n')
    task = Task(task_dir)
    class LocalEnvironment:
        os = task.config.environment.os
        capabilities = SimpleNamespace(mounted=True)
        async def upload_dir(self, source_dir, target_dir): pass
        async def upload_file(self, source_path, target_path):
            target = Path(target_path.replace('/tmp/ot-pytest-execution-', str(tmp_path / 'recorder-')))
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source_path, target)
        async def exec(self, command, **kwargs):
            command = command.replace('/tmp/ot-pytest-execution-', str(tmp_path / 'recorder-'))
            command = command.replace('/tests/', str(task_dir / 'tests') + '/').replace('/logs/verifier', str(paths.verifier_dir))
            result = subprocess.run(['bash', '-c', command], capture_output=True, text=True,
                                    env=dict(os.environ, PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'), timeout=20)
            return SimpleNamespace(return_code=result.returncode, stdout=result.stdout, stderr=result.stderr)
    verifier_setup.install()
    result = asyncio.run(Verifier(task, paths, LocalEnvironment()).verify())
    output = paths.test_stdout_path.read_text()
    assert 'no tests ran' in output and '1 failed' in output
    assert 'OT_VERIFIER_EXECUTION:' in output
    assert not list(tmp_path.glob('recorder-*'))
    assessed = assess_trials([(tmp_path / 'trial', {'verifier_result': result.model_dump()})], 1, 0)
    assert assessed['status'] == 'completed', assessed
