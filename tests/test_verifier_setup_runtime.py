"""Exercise the real Harbor Verifier with an instrumented environment."""
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from harbor_patches import verifier_setup


@pytest.mark.parametrize('mode', ['shared', 'separate', 'step'])
@pytest.mark.parametrize('outcome', ['pass', 'absent', 'failure', 'timeout'])
def test_normal_verification_runs_setup_before_checks(tmp_path, mode, outcome):
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
    if mode == 'step':
        step_dir = task.paths.step_tests_dir('first')
        step_dir.mkdir(parents=True)
        (step_dir/'test.sh').write_text('step grade')
        if outcome != 'absent': (step_dir/'setup.sh').write_text('step prepare')
    paths = TrialPaths(tmp_path/'trial'); paths.mkdir()
    # Even an old reward must not turn setup failure into a successful result.
    paths.reward_text_path.write_text('1')
    events = []
    class Environment:
        os = task.config.environment.os
        capabilities = SimpleNamespace(mounted=True)
        async def upload_dir(self, source_dir, target_dir): events.append(('upload', source_dir))
        async def exec(self, command, **kwargs):
            if command.startswith(('chmod', 'rm -f')):
                events.append(('clear' if command.startswith('rm') else 'chmod', command))
                return SimpleNamespace(return_code=0, stdout='', stderr='')
            assert kwargs['env']['TEST_VAR'] == 'from-config'
            if command == verifier_setup.SETUP_COMMAND:
                events.append(('setup', command))
                if outcome == 'timeout': await asyncio.sleep(1)
                return SimpleNamespace(return_code=1 if outcome == 'failure' else 0, stdout='setup output', stderr='')
            events.append(('grade', command))
            return SimpleNamespace(return_code=0, stdout='grade output', stderr='')
    env = Environment()
    verifier = Verifier(task, paths, env, skip_tests_upload=mode == 'separate',
                        step_name='first' if mode == 'step' else None)
    verifier_setup.install(); verifier_setup.install()
    async def run():
        return await asyncio.wait_for(verifier.verify(), timeout=.05 if outcome == 'timeout' else 5)
    if outcome in ('failure', 'timeout'):
        with pytest.raises(VerifierRuntimeError if outcome == 'failure' else TimeoutError): asyncio.run(run())
        assert not any(e[0] == 'grade' for e in events)
    else:
        result = asyncio.run(run())
        assert result.rewards == {'reward': 1}
        assert [e[0] for e in events if e[0] in ('setup', 'grade')] == ['setup', 'grade']
    assert verifier.environment is env
    uploads = [e for e in events if e[0] == 'upload']
    assert len(uploads) == (0 if mode == 'separate' else 2 if mode == 'step' else 1)
    assert sum(e[0] == 'clear' for e in events) == (0 if mode == 'separate' else 1)
    if uploads: assert events.index(uploads[-1]) < next(i for i,e in enumerate(events) if e[0] == 'setup')


@pytest.mark.parametrize('script, expected', [(None, 0), ('echo prepared', 0), ('exit 7', 7)])
def test_optional_setup_shell_propagates_exit_code(tmp_path, script, expected):
    import subprocess
    if script is not None: (tmp_path/'setup.sh').write_text(script + '\n')
    command = verifier_setup.SETUP_COMMAND.replace('/tests', str(tmp_path))
    result = subprocess.run(['bash', '-c', command], capture_output=True, text=True)
    assert result.returncode == expected
    if script == 'echo prepared': assert result.stdout.strip() == 'prepared'
