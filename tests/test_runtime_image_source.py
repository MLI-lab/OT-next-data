"""Shared native runtimes can regenerate without a sibling task's cached image."""
import hashlib
import io
import json
import urllib.request

import pytest

from data.utils import verifier_runtime as runtime
from harbor_patches.verifier_bundle import FORMAT, spec_id
from hpc.offline_prep.image_python_runtime import source_image

URL = 'https://huggingface.co/datasets/owner/tasks/resolve/' + 'a' * 40 + '/images/source.sif'


def recipe(payload=b'pristine image'):
    return runtime.runtime_spec(python='3.12.3', requirements=[], image_python={
        'image_sha256': hashlib.sha256(payload).hexdigest(), 'executable': '/usr/bin/python3.12'})


def test_source_option_does_not_change_existing_artifact_identity():
    spec = recipe()
    plain = json.loads(runtime.runtime_reference(spec)['tests/ot-verifier-runtime.json'])
    remote = json.loads(runtime.runtime_reference(spec, image_source=URL)['tests/ot-verifier-runtime.json'])
    assert remote.pop('image_source') == URL
    assert remote == plain
    script = runtime.preparation_command(spec, phase='verifier', image_source=URL)
    assert '"${1:-}" = "--prepare"' in script
    assert '--image-source ' + URL in script


@pytest.mark.parametrize('url', [URL.replace('/' + 'a' * 40 + '/', '/main/'),
                                  URL.replace('https:', 'http:'), URL + '?token=secret',
                                  URL.replace('huggingface.co', 'example.com')])
def test_source_requires_immutable_public_location(url):
    with pytest.raises(ValueError, match='immutable'):
        runtime.runtime_reference(recipe(), image_source=url)


def test_missing_image_download_is_private_and_checksum_checked(tmp_path, monkeypatch):
    cache = tmp_path / 'shared-cache'
    scratch = tmp_path / 'private-build'
    scratch.mkdir()
    monkeypatch.setenv('OT_RUNTIME_IMAGE_CACHE', str(cache))
    monkeypatch.setenv('OT_RUNTIME_IMAGE_SOURCE', URL)
    calls = []
    def fetch(url, timeout):
        calls.append(url)
        return io.BytesIO(b'pristine image')
    monkeypatch.setattr(urllib.request, 'urlopen', fetch)
    image = source_image(recipe(), scratch=scratch)
    assert image.parent == scratch and image.read_bytes() == b'pristine image'
    assert calls == [URL] and not cache.exists()
    image.unlink()
    monkeypatch.setattr(urllib.request, 'urlopen', lambda *a, **kw: io.BytesIO(b'wrong image'))
    with pytest.raises(ValueError, match='checksum mismatch'):
        source_image(recipe(), scratch=scratch)


def test_prepare_records_source_and_passes_it_to_builder(tmp_path, monkeypatch, capsys):
    from hpc.offline_prep import verifier_bundles
    spec = recipe()
    key = spec_id(spec)
    def build(value, cache):
        import os
        assert value == spec and os.environ['OT_RUNTIME_IMAGE_SOURCE'] == URL
        return dict(id=key, spec=spec, format=FORMAT, status='ready', archive_sha256='b' * 64)
    monkeypatch.setattr(verifier_bundles, 'build', build)
    monkeypatch.setattr('sys.stdin', io.StringIO(json.dumps(spec)))
    reference = tmp_path / 'ot-verifier-runtime.json'
    monkeypatch.setattr('sys.argv', ['prepare', '--prepare', '--phase', 'verifier',
                                   '--reference', str(reference), '--image-source', URL])
    monkeypatch.setenv('OT_RUNTIME_IMAGE_SOURCE', '')
    monkeypatch.setenv('OT_RUNTIME_CACHE', str(tmp_path / 'cache'))
    runtime.main()
    ref = json.loads(reference.read_text())
    assert ref['image_source'] == URL and ref['id'] == key and 'spec' not in ref


@pytest.mark.parametrize('isolated', ['0', '1'])
def test_fallback_passes_source_without_bypassing_network_isolation(tmp_path, monkeypatch, isolated):
    import asyncio
    from harbor_patches import verifier_bundle as bundle
    spec = recipe()
    context = tmp_path / 'tests'
    context.mkdir()
    (context / 'setup.sh').write_text(runtime.preparation_command(spec, phase='verifier', image_source=URL))
    ref = json.loads(runtime.runtime_reference(spec, image_source=URL)['tests/ot-verifier-runtime.json'])
    monkeypatch.setenv('OT_NET_ISOLATION', isolated)
    async def create(*argv, **kwargs):
        assert kwargs['env']['OT_RUNTIME_IMAGE_SOURCE'] == URL
        if isolated == '1':
            assert argv[:5] == ('unshare', '--user', '--map-current-user', '--net', '--')
        else:
            assert argv[0] != 'unshare'
        class Process:
            async def wait(self):
                return 1
        return Process()
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', create)
    with pytest.raises(RuntimeError, match='installation failed'):
        asyncio.run(bundle.install_and_stage(object(), context, ref, tmp_path / 'evidence', 'missing'))
