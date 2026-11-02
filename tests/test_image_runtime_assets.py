from pathlib import Path

import pytest

from hpc.offline_prep.image_runtime_assets import canonical, prepare


@pytest.mark.parametrize('path', ['/root', '/testbed', '/etc', '/root/.ssh',
                                  '/testbed/node_modules/../../.ssh'])
def test_rejects_non_dependency_roots(path):
    with pytest.raises(ValueError):
        canonical({'image_sha256': 'a' * 64, 'trees': {'deps': path}})


def run_copy(tmp_path, monkeypatch, escaping=False):
    import hpc.offline_prep.image_python_runtime as image
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'module.js').write_text('module.exports = 42;\n')
    (source / 'alias.js').symlink_to('/etc/passwd' if escaping else 'module.js')
    monkeypatch.setattr(image, 'source_image', lambda *a, **kw: 'verified-source')
    monkeypatch.setattr(image, 'stage_source', lambda original, scratch: original)
    monkeypatch.setattr(image, 'command', lambda original, scratch, *args:
                        ['tar', '-cf', str(args[2]), '-C', str(source), '.'])
    runtime = tmp_path / 'runtime'
    runtime.mkdir()
    scratch = tmp_path / 'scratch'
    scratch.mkdir()
    spec = {'image_sha256': 'a' * 64, 'trees': {'deps': '/testbed/node_modules'}}
    return prepare(spec, runtime, scratch), runtime


def test_preserves_dependency_bytes_and_internal_links(tmp_path, monkeypatch):
    record, runtime = run_copy(tmp_path, monkeypatch)
    assert (runtime / 'assets/deps/alias.js').read_text() == 'module.exports = 42;\n'
    assert len(record['deps']['source_archive_sha256']) == 64


def test_rejects_external_dependency_link(tmp_path, monkeypatch):
    import tarfile
    with pytest.raises((ValueError, tarfile.FilterError)):
        run_copy(tmp_path, monkeypatch, escaping=True)


def test_assets_participate_in_runtime_identity():
    from data.utils.verifier_runtime import runtime_spec
    from harbor_patches.verifier_bundle import spec_id
    common = dict(python='3.14.5', requirements=[])
    first = runtime_spec(**common, image_assets={'image_sha256': 'a' * 64,
                                               'trees': {'go': '/opt/go-1.26'}})
    second = runtime_spec(**common, image_assets={'image_sha256': 'b' * 64,
                                                'trees': {'go': '/opt/go-1.26'}})
    assert spec_id(first) != spec_id(second)
    assert runtime_spec(**{k: v for k, v in first.items() if k != 'runtime_layout'}) == first


def test_incomplete_asset_cache_triggers_fallback(tmp_path):
    import io
    import json
    import tarfile
    from data.utils.verifier_runtime import runtime_spec
    from harbor_patches.verifier_bundle import FORMAT, digest, lookup, spec_id
    spec = runtime_spec(python='3.12.14', requirements=[], image_assets={
        'image_sha256': 'a' * 64, 'trees': {'node': '/opt/node-20'}})
    key = spec_id(spec)
    archive = tmp_path / (key + '.tar.gz')
    with tarfile.open(archive, 'w:gz') as stream:
        member = tarfile.TarInfo('python/bin/python3')
        member.mode = 0o755
        member.size = 1
        stream.addfile(member, io.BytesIO(b'x'))
    ref = dict(format=FORMAT, id=key, spec=spec, status='ready', archive_sha256=digest(archive))
    (tmp_path / (key + '.json')).write_text(json.dumps(ref))
    path, reason = lookup(ref, root=tmp_path)
    assert path is None
    assert reason == 'Incomplete runtime dependency tree: node'


def test_dependency_check_resolves_required_symlinks():
    import tarfile
    from hpc.offline_prep.image_runtime_assets import validate_members
    target = tarfile.TarInfo('python/assets/node_modules/mocha/bin/mocha.js')
    link = tarfile.TarInfo('python/assets/node_modules/.bin/mocha')
    link.type = tarfile.SYMTYPE
    link.linkname = '../mocha/bin/mocha.js'
    members = {m.name: m for m in (target, link)}
    spec = {'image_sha256': 'a' * 64, 'trees': {'node_modules': '/testbed/node_modules'},
            'required': {'node_modules': ['.bin/mocha']}}
    validate_members(spec, members)
    del members[target.name]
    with pytest.raises(ValueError, match='Incomplete runtime dependency'):
        validate_members(spec, members)
