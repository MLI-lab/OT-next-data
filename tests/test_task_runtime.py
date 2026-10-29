import asyncio
import io
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tarfile
from types import SimpleNamespace

import pytest

from data.utils.verifier_runtime import task_runtime_reference, runtime_spec, python_install_or_prepared
from harbor_patches import task_runtime
from harbor_patches.verifier_bundle import FORMAT, TASK_REFERENCE, digest, preflight, spec_id
from validation.stages.task_setup import detect, upload_setup


def prepared(tmp_path):
    spec = runtime_spec(python='3.10.19', requirements=['pytest==8.4.1'], tools=['pytest'])
    key = spec_id(spec)
    cache = tmp_path / 'cache'
    cache.mkdir()
    archive = cache / (key + '.tar.gz')
    with tarfile.open(archive, 'w:gz') as out:
        item = tarfile.TarInfo('python/bin/python3')
        item.size, item.mode = 4, 0o755
        out.addfile(item, io.BytesIO(b'test'))
    manifest = dict(format=FORMAT, spec=spec, id=key, status='ready', archive_sha256=digest(archive))
    (cache / (key + '.json')).write_text(json.dumps(manifest))
    task = tmp_path / 'task'
    for name, content in task_runtime_reference(spec, {key: manifest}).items():
        destination = task / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
    return task, cache, archive


def test_runtime_only_task_triggers_setup_and_cache_preflight(tmp_path):
    task, cache, archive = prepared(tmp_path)
    assert detect(task) == 'true'
    checked = preflight([task], cache)
    assert next(iter(checked.values()))['archive'] == str(archive)
    (task / 'setup_files/setup.sh').write_text('echo initialized')
    assert detect(task) == 'bash /setup_files/setup.sh'
    archive.write_bytes(b'corrupt')
    assert next(iter(preflight([task], cache).values()))['status'] == 'missing'


def test_no_declaration_does_not_change_normal_setup(tmp_path):
    env = SimpleNamespace()
    assert asyncio.run(task_runtime.stage(env, tmp_path)) is None
    assert detect(tmp_path) is None
    assert not hasattr(env, '_persistent_env')


@pytest.mark.parametrize('cleanup_fails', [False, True])
def test_private_task_runtime_is_discarded_before_environment_stop(cleanup_fails):
    calls = []
    class Environment:
        _persistent_env = {'PATH': '/private/bin:/image/bin'}
        _ot_task_runtime_previous_env = {'PATH': '/image/bin'}
        async def exec(self, command, **kwargs):
            calls.append(command)
            return SimpleNamespace(return_code=int(cleanup_fails), stdout='', stderr='cleanup error')
        async def stop(self, **kwargs):
            calls.append('stop')
            assert self._persistent_env == {'PATH': '/image/bin'}
    env = Environment()
    task_runtime.ensure_cleanup(env)
    task_runtime.retain(env, '/setup_files/.ot-task-runtime-abc123')
    if cleanup_fails:
        with pytest.raises(RuntimeError, match='cleanup error'):
            asyncio.run(env.stop(delete=True))
    else:
        asyncio.run(env.stop(delete=True))
    assert calls[-1] == 'stop'
    assert '/setup_files/.ot-task-runtime-abc123' in calls[0]
    assert task_runtime.PROFILE in calls[0]
    assert 'verifier-bundles' not in calls[0]


def test_missing_declared_cache_does_not_install_or_touch_environment(tmp_path, monkeypatch):
    task, cache, archive = prepared(tmp_path)
    monkeypatch.setenv('OT_VERIFIER_BUNDLE_CACHE', str(cache))
    archive.unlink()
    assert asyncio.run(task_runtime.stage(SimpleNamespace(), task)) is None


def test_stage_precedes_setup_and_persists_selection_for_agent(tmp_path, monkeypatch):
    task, cache, archive = prepared(tmp_path)
    monkeypatch.setenv('OT_VERIFIER_BUNDLE_CACHE', str(cache))
    before = digest(archive)
    calls = []
    root = '/setup_files/.ot-task-runtime-abc123'
    class Environment:
        _persistent_env = {'EXISTING': 'keep', 'PATH': '/image/bin'}
        async def exec(self, command, **kwargs):
            calls.append(command)
            stdout = root if command.startswith('mktemp ') else '/image/bin' if command == 'printf %s "$PATH"' else ''
            return SimpleNamespace(return_code=0, stdout=stdout, stderr='')
        async def upload_file(self, source_path, target_path):
            assert digest(source_path) == before
            calls.append('upload archive')
        async def upload_dir(self, **kwargs):
            calls.append('upload setup')
        async def empty_dirs(self, *args, **kwargs):
            calls.append('empty setup')
    env = Environment()
    asyncio.run(upload_setup(env, task))
    assert calls.index('upload setup') < calls.index('upload archive')
    assert env._persistent_env['OT_TASK_PYTHON'] == root + '/python/bin/python3'
    assert env._persistent_env['PATH'] == root + '/python/bin:/image/bin'
    assert env._persistent_env['EXISTING'] == 'keep'
    assert any(task_runtime.PROFILE in c for c in calls)
    assert digest(archive) == before
    task_runtime.reset(env)
    assert env._persistent_env == {'EXISTING': 'keep', 'PATH': '/image/bin'}


def test_separate_verifier_upload_does_not_activate_task_runtime(tmp_path):
    task, _, _ = prepared(tmp_path)
    calls = []
    class Environment:
        async def empty_dirs(self, *args, **kwargs):
            pass
        async def upload_dir(self, **kwargs):
            calls.append('upload')
        async def exec(self, command, **kwargs):
            calls.append(command)
    asyncio.run(upload_setup(Environment(), task, task_runtime=False))
    assert calls == ['upload', 'chmod -R a+rX /setup_files']


def test_task_offline_packages_stage_before_agent_without_replacing_python(tmp_path, monkeypatch):
    from data.utils.verifier_runtime import offline_package_reference
    from harbor_patches.verifier_bundle import OFFLINE_FORMAT
    spec = {'image_key': 'image123', 'apt': ['tree'], 'python': [], 'uv': False}
    key = spec_id(spec)
    cache = tmp_path / 'cache'; cache.mkdir()
    archive = cache / (key + '.tar.gz')
    with tarfile.open(archive, 'w:gz') as out:
        out.addfile(tarfile.TarInfo('repo/Packages'))
    manifest = dict(format=OFFLINE_FORMAT, id=key, spec=spec, status='ready', archive_sha256=digest(archive))
    (cache / (key + '.json')).write_text(json.dumps(manifest))
    task = tmp_path / 'task'
    for name, data in offline_package_reference(spec, {key: manifest}, phase='task').items():
        path = task / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data)
    monkeypatch.setenv('OT_VERIFIER_BUNDLE_CACHE', str(cache))
    assert detect(task) == 'true' and key in preflight([task], cache)
    calls = []
    class Environment:
        _persistent_env = {'PATH': '/image/bin'}
        async def exec(self, command, **kwargs):
            calls.append(command)
            return SimpleNamespace(return_code=0, stderr='', stdout=(
                '/setup_files/.ot-task-offline-abc123' if command.startswith('mktemp ') else ''))
        async def upload_file(self, source_path, target_path):
            assert target_path.endswith('/packages.tar.gz')
    env = Environment()
    record = asyncio.run(task_runtime.stage(env, task, tmp_path / 'evidence'))
    assert record['runtime'] is None and record['offline']['phase'] == 'before-agent'
    assert set(record['offline']['timings_seconds']) == {'verify_structure','allocate','archive_upload','checksum_extract_configuration'}
    assert all(v >= 0 for v in record['offline']['timings_seconds'].values())
    assert env._persistent_env == {'PATH': '/image/bin', 'OT_TASK_OFFLINE': '/setup_files/.ot-task-offline-abc123'}
    assert any('apt.conf' in call for call in calls)


def test_trial_stages_declared_runtime_before_setup_and_agent(tmp_path, monkeypatch):
    from harbor.trial import trial as trial_module
    from validation.stages import task_setup
    import logging
    task, _, _ = prepared(tmp_path)
    events = []
    class Trial:
        async def _start_agent_environment(self):
            raise AssertionError('runtime-only task must enter preparation')
        async def _upload_setup_files(self):
            events.append('upload')
    class Environment:
        async def start(self, **kwargs):
            events.append('start')
        async def exec(self, command, **kwargs):
            assert command == 'true' and events[-1] == 'runtime'
            events.append('setup')
            return SimpleNamespace(return_code=0, stdout='', stderr='')
    async def stage(*args):
        assert events == ['start', 'upload']
        events.append('runtime')
    monkeypatch.setattr(task_runtime, 'stage', stage)
    monkeypatch.setattr(trial_module, 'Trial', Trial)
    task_setup.install()
    trial = Trial()
    trial.task = SimpleNamespace(paths=SimpleNamespace(task_dir=task))
    trial.config = SimpleNamespace(environment=SimpleNamespace(force_build=False))
    trial.agent_environment = Environment()
    trial._environment_build_timeout_sec = 600
    trial.logger = logging.getLogger('task-runtime-test')
    async def run():
        await trial._start_agent_environment()
        await trial._upload_setup_files()
        events.append('agent')
    asyncio.run(run())
    assert events == ['start', 'upload', 'runtime', 'setup', 'agent']


@pytest.mark.parametrize('phase', ['task', 'verifier'])
def test_script_helper_validates_prepared_packages_without_running_install(tmp_path, phase):
    import importlib.metadata
    requirement = 'pytest==' + importlib.metadata.version('pytest')
    command = python_install_or_prepared([requirement], phase=phase, python='/must-not-run')
    variable = 'OT_TASK_PYTHON' if phase == 'task' else 'OT_VERIFIER_PYTHON'
    env = {**os.environ, variable: sys.executable, 'PIP_NO_INDEX': '1'}
    result = subprocess.run(['/bin/sh', '-c', command], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    missing = python_install_or_prepared(['ot-nonexistent-package==1.0'], phase=phase, python='/must-not-run')
    result = subprocess.run(['/bin/sh', '-c', missing], env=env, capture_output=True, text=True)
    assert result.returncode != 0 and '/must-not-run' not in result.stderr


@pytest.mark.parametrize('phase', ['task', 'verifier'])
@pytest.mark.parametrize('offline', [False, True])
def test_script_helper_install_branch_uses_local_wheels_when_supplied(tmp_path, phase, offline):
    fake = tmp_path / 'python'
    fake.write_text('#!/bin/sh\nprintf "%s\\n" "$PIP_NO_INDEX" "$PIP_FIND_LINKS" "$@"\n')
    fake.chmod(0o755)
    command = python_install_or_prepared(['pytest==8.4.1'], phase=phase, python=str(fake))
    env = {k: v for k, v in os.environ.items() if not k.startswith(('OT_TASK_', 'OT_VERIFIER_', 'PIP_'))}
    if offline:
        env['OT_' + phase.upper() + '_OFFLINE'] = str(tmp_path / 'packages with spaces')
    result = subprocess.run(['/bin/sh', '-c', command], env=env, capture_output=True, text=True)
    if phase == 'verifier':
        assert result.returncode != 0 and 'Trusted prepared verifier runtime required' in result.stderr
        assert not result.stdout
        return
    assert result.returncode == 0, result.stderr
    output = result.stdout.splitlines()
    assert output[:2] == (['1', str(tmp_path / 'packages with spaces/wheels')] if offline else ['', ''])
    assert output[2:] == ['-m', 'pip', 'install', 'pytest==8.4.1']
