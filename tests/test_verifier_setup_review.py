"""Stage 3 uses the same setup script as normal verification, without grading."""
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from harbor_patches.verifier_setup import SETUP_COMMAND
from validation.stages import verifier_setup
from validation.stages.runner import parser


@pytest.mark.parametrize('separate', [False, True])
@pytest.mark.parametrize('failure', [None, 'setup', 'absent', 'timeout', 'inspection', 'agent_start', 'verifier_start', 'collect'])
def test_review_orders_setup_transfers_and_cleans_up(tmp_path, monkeypatch, separate, failure):
    from harbor.environments.factory import EnvironmentFactory
    from harbor.models.task.config import TaskConfig
    from harbor.trial.trial import ArtifactHandler
    from validation.checks import environment as checks
    task = tmp_path/'task'
    for name in ('environment', 'tests', 'solution', 'setup_files'):
        (task/name).mkdir(parents=True)
    (task/'instruction.md').write_text('task')
    (task/'environment/Dockerfile').write_text('FROM example\n')
    (task/'task.toml').write_text('[environment]\nbuild_timeout_sec=20\n[verifier]\ntimeout_sec=' +
        ('0.01' if failure == 'timeout' else '100') + '\nenvironment_mode="' + ('separate' if separate else 'shared') + '"\n')
    with (task/'task.toml').open('a') as stream:
        stream.write('[[verifier.collect]]\ncommand="capture-submission"\ntimeout_sec=10\n')
    (task/'solution/solve.sh').write_text('bash /setup_files/setup.sh\n')
    (task/'setup_files/setup.sh').write_text('true')
    (task/'tests/test.sh').write_text('DO NOT RUN GRADING')
    if failure != 'absent':
        (task/'tests/setup.sh').write_text('echo prepare')
    events, created = [], []
    clock = [0.0]
    if failure != 'timeout':
        monkeypatch.setattr(verifier_setup, 'time', SimpleNamespace(monotonic=lambda: clock[0]))
    class Environment:
        os = TaskConfig().environment.os
        def __init__(self, **kw):
            self.label = 'agent' if kw['environment_name'].endswith('-agent') else 'verifier'
            self.setup = False
            created.append(self)
        async def start(self, **kw):
            events.append((self.label, 'start')); clock[0] += 120
            if failure == self.label + '_start': raise TimeoutError('startup deadline')
        async def stop(self, **kw): events.append((self.label, 'stop'))
        async def empty_dirs(self, paths, **kw): events.append((self.label, 'clear'))
        async def upload_dir(self, source_dir, target_dir):
            events.append((self.label, 'upload', target_dir)); clock[0] += 1
            if target_dir == '/tests': self.setup = (source_dir/'setup.sh').exists()
        async def exec(self, command, **kw):
            assert 'test.sh' not in command and 'GRADING' not in command
            events.append((self.label, command)); clock[0] += 2
            if command == 'capture-submission' and failure == 'collect':
                return SimpleNamespace(return_code=1, stdout='', stderr='capture failed')
            if command == SETUP_COMMAND:
                assert self.setup == (failure != 'absent')
                if failure == 'timeout': await asyncio.sleep(.1)
                if failure == 'setup': return SimpleNamespace(return_code=1, stdout='', stderr='install failed')
            return SimpleNamespace(return_code=0, stdout='prepared', stderr='')
    async def download(self, source, saved, **kw):
        events.append(('agent', 'artifact-download')); clock[0] += 3
    async def upload(self, target, saved, **kw):
        events.append(('verifier', 'artifact-upload')); clock[0] += 4
    monkeypatch.setattr(ArtifactHandler, 'download_artifacts', download)
    monkeypatch.setattr(ArtifactHandler, 'upload_artifacts', upload)
    monkeypatch.setattr(EnvironmentFactory, 'create_environment', lambda **kw: Environment(**kw))
    async def inspect(*a):
        if failure == 'inspection' and a[-1] != 'agent': raise RuntimeError('inspection failed')
        return {'status': 'passed', 'checks': []}
    monkeypatch.setattr(checks, 'inspect_environment', inspect)
    args = parser().parse_args([str(task), '--review-setup', '--backend', 'docker'])
    result = asyncio.run(verifier_setup.review_task(task, tmp_path/'out', args))
    if failure == 'agent_start' or (failure == 'verifier_start' and separate):
        record = result['environments'][0] if failure == 'agent_start' else result['verifier_preparation'][0]
        assert result['status'] == 'error'
        assert record['failure_phase'] == 'container_start'
        assert record['startup_timeout_seconds'] == 20
        assert 'preparation' not in record['timings_seconds']
        assert sum(e[1] == 'stop' for e in events) == len(created)
        return
    assert result['environments'][0]['status'] == 'passed'
    if failure != 'timeout':
        timing = result['environments'][0]['timings_seconds']
        assert timing['container_start'] == 120
        assert timing['preparation'] == 5
        assert timing['start'] == 125
    assert ('verifier', 'bash /setup_files/setup.sh') not in events
    verifier = result['verifier_preparation'][0]
    success = failure in (None, 'absent', 'verifier_start') or (failure == 'inspection' and not separate)
    assert verifier['status'] == ('passed' if success else 'error')
    if failure == 'collect':
        assert not any(e[1] in ('artifact-download', SETUP_COMMAND) for e in events)
        assert 'submission_collect_0 exited 1' in verifier['error']
        return
    assert events.index(('agent', 'capture-submission')) < next(i for i, e in enumerate(events) if e[1] == SETUP_COMMAND)
    if separate:
        assert events.index(('agent', 'capture-submission')) < events.index(('agent', 'artifact-download'))
    assert events.index(('agent', 'bash /setup_files/setup.sh')) < next(i for i, e in enumerate(events) if e[1] == SETUP_COMMAND)
    assert sum(e[1] == 'stop' for e in events) == len(created)
    if success:
        assert verifier['timings_seconds']['preparation'] == (17 if separate else 7)
        assert verifier['mean_target_seconds'] == 5
        assert len(created) == (2 if separate else 1)
