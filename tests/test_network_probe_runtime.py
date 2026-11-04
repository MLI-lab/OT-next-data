import asyncio
from contextlib import asynccontextmanager
import shlex
import shutil
import subprocess
from types import SimpleNamespace

import pytest
from validation.rules.dynamic.environment import check, probe_runtime


@pytest.mark.parametrize('failure', [False, True])
def test_missing_native_client_uses_private_runtime(monkeypatch, failure):
    events = []
    class Environment:
        async def upload_file(self, **kwargs): pass
        async def exec(self, command, **kwargs):
            supplied = command.startswith('trusted-curl')
            events.append(command)
            return SimpleNamespace(return_code=7 if supplied else 125, stderr='', stdout='')
    @asynccontextmanager
    async def staged(environment):
        events.append('stage')
        if failure:
            raise RuntimeError('unsupported runtime')
        try:
            yield 'trusted-curl', 'a' * 64
        finally:
            events.append('cleanup')
    monkeypatch.setattr(probe_runtime, 'staged', staged)
    monkeypatch.delenv('OT_NET_ISOLATION', raising=False)
    monkeypatch.delenv('OT_NET_SIMULATED', raising=False)
    result = asyncio.run(check.inspect_network(Environment(), False))
    assert result['status'] == ('error' if failure else 'passed')
    if failure:
        assert 'unsupported runtime' in result['observations'][0]['runtime_error']
    else:
        assert events[-1] == 'cleanup'
        assert all(o['runtime_sha256'] == 'a' * 64 for o in result['observations'])
        assert any('--noproxy "*"' in c for c in events)


def test_private_bundle_runs_and_is_removed():
    class Local:
        async def exec(self, command, timeout_sec):
            r = subprocess.run(['bash', '-c', command], capture_output=True, text=True, timeout=timeout_sec)
            return SimpleNamespace(return_code=r.returncode, stdout=r.stdout, stderr=r.stderr)
        async def upload_file(self, source_path, target_path):
            shutil.copyfile(source_path, target_path)
    async def exercise():
        async with probe_runtime.staged(Local()) as (command, digest):
            result = await Local().exec(command + ' --version', 10)
            assert result.return_code == 0 and 'curl ' in result.stdout
            assert len(digest) == 64
            loader = shlex.split(command)[2]
        from pathlib import Path
        assert not Path(loader).exists()
    asyncio.run(exercise())
