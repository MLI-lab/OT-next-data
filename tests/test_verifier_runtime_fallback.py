"""Cache failures install only through trusted controller tools."""
import asyncio
import io
import json
from pathlib import Path
import tarfile

import pytest

from data.utils.verifier_runtime import preparation_command, runtime_reference, runtime_spec
from harbor_patches import verifier_bundle as bundle


def test_checksum_valid_archive_missing_declared_tool_requires_installation(tmp_path):
    spec = runtime_spec(python='3.13.9', requirements=['pytest==8.4.1'], tools=['pytest'])
    key = bundle.spec_id(spec)
    archive = tmp_path/(key+'.tar.gz')
    with tarfile.open(archive, 'w:gz') as stream:
        member = tarfile.TarInfo('python/bin/python3')
        member.mode = 0o755
        stream.addfile(member)
    manifest = dict(format=bundle.FORMAT, id=key, spec=spec, status='ready',
                    archive_sha256=bundle.digest(archive))
    (tmp_path/(key+'.json')).write_text(json.dumps(manifest))
    path, reason = bundle.lookup(manifest, root=tmp_path)
    assert path is None
    assert 'Incomplete runtime executable: pytest' in reason


@pytest.mark.parametrize('cache_state', ['missing', 'incomplete', 'corrupt'])
@pytest.mark.parametrize('install_status', [0, 1])
@pytest.mark.parametrize('network_mode', ['0', '1'])
def test_private_fallback_preserves_cache_and_reference(tmp_path, monkeypatch, cache_state, install_status, network_mode):
    spec = runtime_spec(python='3.13.9', requirements=['pytest==8.4.1'], tools=['pytest'])
    key = bundle.spec_id(spec)
    cache = tmp_path / 'cache'
    cache.mkdir()
    context = tmp_path / 'tests'
    context.mkdir()
    manifest = dict(format=bundle.FORMAT, id=key, spec=spec, status='ready', archive_sha256='a' * 64)
    reference = runtime_reference(spec, {key: manifest})['tests/' + bundle.REFERENCE]
    (context / bundle.REFERENCE).write_bytes(reference)
    (context / 'setup.sh').write_text(preparation_command(spec, phase='verifier'))
    if cache_state != 'missing':
        (cache / (key + '.json')).write_text(json.dumps(manifest))
    if cache_state == 'corrupt':
        (cache / (key + '.tar.gz')).write_bytes(b'corrupt bytes')
    before = {p.name: p.read_bytes() for p in cache.iterdir()}
    monkeypatch.setenv('OT_RUNTIME_CACHE', str(cache))
    monkeypatch.setenv('OT_NET_ISOLATION', network_mode)
    called = []

    async def create(*args, **kwargs):
        called.append(args)
        assert any(arg.endswith('/hpc/offline_prep/verifier_bundles.py') for arg in args)
        if network_mode == '1':
            assert args[:5] == ('unshare', '--user', '--map-current-user', '--net', '--')
            assert kwargs['env']['UV_OFFLINE'] == '1'
        else:
            assert args[0] != 'unshare'
        root = Path(args[args.index('--cache') + 1])
        assert root != cache
        assert json.loads(Path(args[args.index('--specs') + 1]).read_text()) == [spec]
        if not install_status:
            archive = root / (key + '.tar.gz')
            with tarfile.open(archive, 'w:gz') as stream:
                member = tarfile.TarInfo('python/bin/python3')
                member.size, member.mode = 4, 0o755
                stream.addfile(member, io.BytesIO(b'test'))
            (root / (key + '.json')).write_text(json.dumps(
                {**manifest, 'archive_sha256': bundle.digest(archive)}))

        class Process:
            async def wait(self):
                return install_status
        return Process()

    async def stage(environment, archive, ref, evidence_dir, **kwargs):
        assert archive.is_file()
        assert ref['archive_sha256'] != manifest['archive_sha256']
        return {'clean': True}

    monkeypatch.setattr(asyncio, 'create_subprocess_exec', create)
    monkeypatch.setattr(bundle, '_stage', stage)
    evidence = tmp_path / 'evidence'
    if install_status:
        with pytest.raises(RuntimeError, match='installation failed'):
            asyncio.run(bundle.stage(object(), context, evidence))
    else:
        assert asyncio.run(bundle.stage(object(), context, evidence)) == {'clean': True}
    assert len(called) == 1
    assert before == {p.name: p.read_bytes() for p in cache.iterdir()}
    assert (context / bundle.REFERENCE).read_bytes() == reference
    report = json.loads((evidence / 'verifier-runtime-fallback.json').read_text())
    assert report['status'] == ('failed' if install_status else 'installed')
    assert report['cache_writeback'] is False
    assert report['installation_network'] == ('isolated' if network_mode == '1' else 'host')
