"""Per-trial reference capture never reuses post-agent or shared-cache bytes."""
import asyncio
import io
import json
from pathlib import Path
import tarfile
from types import SimpleNamespace

import pytest

from harbor_patches import verifier_assets as assets


class Environment:
    def __init__(self, files):
        self.files = files
        self.commands = []
        self.uploaded = {}

    async def stop(self):
        pass

    async def exec(self, command, **kwargs):
        self.commands.append(command)
        if '/missing;' in command:
            self.nonempty_missing = 'printf "\\n"' in command
        return SimpleNamespace(return_code=0, stderr='')

    async def download_file(self, source_path, target_path):
        if source_path.endswith('/missing'):
            data = ''.join(p + '\n' for p, data in self.files.items() if data is None)
            if getattr(self, 'nonempty_missing', False):
                data = '\n' + data
            if not data:
                raise FileNotFoundError('Pinned bridge rejects zero-byte downloads')
            Path(target_path).write_text(data)
        else:
            with tarfile.open(target_path, 'w') as archive:
                for path, data in self.files.items():
                    if data is None:
                        continue
                    entry = tarfile.TarInfo(path.lstrip('/'))
                    entry.size = len(data)
                    entry.mode = 0o755
                    archive.addfile(entry, io.BytesIO(data))

    async def upload_dir(self, source_dir, target_dir):
        self.uploaded = {p.relative_to(source_dir).as_posix(): p.read_bytes()
                         for p in Path(source_dir).rglob('*') if p.is_file()}


def task(tmp_path, paths, **extra):
    tests = tmp_path / 'task/tests'
    tests.mkdir(parents=True)
    (tests / assets.REFERENCE).write_text(json.dumps(dict(format=assets.FORMAT, paths=paths, **extra)))
    return tests.parent


def test_capture_precedes_tamper_and_is_discarded_after_stop(tmp_path):
    async def run():
        root = task(tmp_path, ['/app/oracle', '/app/missing'])
        original = b'\x7fELForiginal executable'
        env = Environment({'/app/oracle': original, '/app/missing': None})
        assets.register(env, root, tmp_path / 'evidence')
        record = await assets.capture(env)
        private = Path(env._ot_verifier_asset_snapshot['private'].name)
        assert private.is_dir() and record['missing'] == ['/app/missing']
        env.files = {'/app/oracle': b'forged executable', '/app/missing': b'forged missing file'}
        with pytest.raises(RuntimeError, match='recapture'):
            await assets.capture(env)
        restored = await assets.stage(env, root / 'tests', '/clean/python', tmp_path / 'evidence')
        assert env.uploaded['app/oracle'] == original
        assert 'app/missing' not in env.uploaded
        assert restored['missing'] == ['/app/missing']
        # Even changing the last uploaded copy cannot change the private baseline.
        env.uploaded['app/oracle'] = b'poisoned staged copy'
        await assets.stage(env, root / 'tests', '/clean/python', tmp_path / 'evidence')
        assert env.uploaded['app/oracle'] == original
        assert restored['shared_cache'] is False and restored['cache_writeback'] is False
        await env.stop()
        assert not private.exists() and env._ot_verifier_asset_snapshot is None
    asyncio.run(run())


def test_missing_capture_fails_without_reading_agent_files(tmp_path):
    async def run():
        root = task(tmp_path, ['/app/oracle'])
        env = Environment({'/app/oracle': b'forged'})
        assets.register(env, root)
        with pytest.raises(RuntimeError, match='refusing to read agent-modified'):
            await assets.stage(env, root / 'tests', '/clean/python', tmp_path / 'evidence')
        assert env.commands == []
    asyncio.run(run())


def test_capture_all_present_survives_bridge_empty_download_limitation(tmp_path):
    async def run():
        root = task(tmp_path, ['/app/oracle'])
        env = Environment({'/app/oracle': b'\x7fELFbaseline'})
        assets.register(env, root)
        record = await assets.capture(env)
        assert record['missing'] == [] and len(record['files']) == 1
        await env.stop()
    asyncio.run(run())


@pytest.mark.parametrize('path', ['/app/../etc/passwd', '/app//oracle', '/app/x\n', 42])
def test_invalid_declarations_rejected(tmp_path, path):
    root = task(tmp_path, [path])
    with pytest.raises(ValueError, match='Invalid verifier asset path'):
        assets.register(Environment({}), root)


def test_unexpected_archive_member_rejected(tmp_path):
    async def run():
        root = task(tmp_path, ['/app/oracle'])
        env = Environment({'/app/../../escape': b'invalid'})
        assets.register(env, root)
        with pytest.raises(ValueError, match='Unexpected member'):
            await assets.capture(env)
        assert getattr(env, '_ot_verifier_asset_snapshot', None) is None
    asyncio.run(run())


def test_python_reference_keeps_original_import_directory(tmp_path):
    directory = tmp_path / 'app'
    directory.mkdir()
    (directory / 'helper.py').write_text('VALUE = 42\n')
    source = b'"""Reference."""\nfrom __future__ import annotations\nimport helper\nprint(helper.VALUE)\n'
    import subprocess
    import sys
    script = directory / 'oracle.py'
    script.write_bytes(assets.clean_script(source, str(script), sys.executable, []))
    result = subprocess.run([sys.executable, '-I', str(script)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout == '42\n'


def test_declared_task_package_edit_remains_visible(tmp_path):
    import subprocess
    import sys
    package_root = tmp_path / 'vendored'
    package_root.mkdir()
    module = package_root / 'task_package.py'
    module.write_text('VALUE = 13\n')
    script = tmp_path / 'oracle.py'
    script.write_bytes(assets.clean_script(b'import task_package\nprint(task_package.VALUE)\n',
                                          str(script), sys.executable, [str(package_root)]))
    def run():
        return subprocess.check_output([sys.executable, '-I', str(script)], text=True).strip()
    assert run() == '13'
    module.write_text('VALUE = 42 # intended submitted package fix\n')
    assert run() == '42'


def test_nested_grader_calls_preserve_task_interpreter(monkeypatch):
    import subprocess
    calls = []
    class Recorder:
        def __init__(self, args, *positional, **kwargs):
            calls.append((args, kwargs))
    monkeypatch.setattr(subprocess, 'Popen', Recorder)
    monkeypatch.setenv('OT_VERIFIER_PYTHON', '/clean/bin/python3')
    monkeypatch.setenv('PATH', '/task/bin:/usr/bin')
    exec(assets.child_environment(['/app/reference.py'], '/task/bin/python3'), {})
    subprocess.Popen(['/clean/bin/python3', '/home/user/answer.py'])
    subprocess.Popen(['/task/bin/python3', '/app/reference.py'])
    subprocess.Popen(['/task/bin/python3', '/home/user/answer.py', '/app/reference.py'])
    assert calls[0][0] == ['/task/bin/python3', '/home/user/answer.py']
    assert calls[1][0] == ['/clean/bin/python3', '-I', '/app/reference.py']
    assert calls[1][1]['env']['PATH'] == '/task/bin:/usr/bin'
    assert calls[2][0] == ['/task/bin/python3', '/home/user/answer.py', '/app/reference.py']


def test_shell_grader_only_routes_reference_python():
    source = b'#!/bin/bash\npython3 /app/reference.py\npython3 /home/user/answer.py\n'
    result = assets.clean_script(source, '/app/check.sh', '/clean/python3', [], ['/app/reference.py'])
    assert b'/clean/python3 -I /app/reference.py' in result
    assert b'python3 /home/user/answer.py' in result


def test_restored_service_launcher_keeps_task_runtime(tmp_path):
    async def run():
        source = b'#!/usr/bin/python3\nimport repaired_package\nrepaired_package.serve()\n'
        root = task(tmp_path, ['/app/start.py'], task_launchers=['/app/start.py'])
        env = Environment({'/app/start.py': source})
        assets.register(env, root)
        await assets.capture(env)
        env.files['/app/start.py'] = b'forged launcher'
        await assets.stage(env, root/'tests', '/clean/python3', tmp_path/'evidence')
        assert env.uploaded['app/start.py'] == source
        await env.stop()
    asyncio.run(run())
