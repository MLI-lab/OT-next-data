import io
import json
import tarfile

import pytest

from harbor_patches.verifier_bundle import verify_source_archive, FORMAT, REFERENCE, digest, preflight, spec_id, validate_archive, verify


def prepared(tmp_path):
    spec = {'python': '3.13.9', 'requirements': ['pytest==8.4.1']}
    key = spec_id(spec)
    path = tmp_path / (key + '.tar.gz')
    with tarfile.open(path, 'w:gz') as archive:
        member = tarfile.TarInfo('python/bin/python3')
        member.size = 4
        member.mode = 0o755
        archive.addfile(member, io.BytesIO(b'test'))
    ref = {'format': FORMAT, 'id': key, 'spec': spec, 'status': 'ready', 'archive_sha256': digest(path)}
    (tmp_path / (key + '.json')).write_text(json.dumps(ref))
    return path, ref


def test_prepared_bundle_is_verified_and_tampering_is_rejected(tmp_path):
    path, ref = prepared(tmp_path)
    assert verify_source_archive(ref, tmp_path) == path
    validate_archive(path)
    path.write_bytes(path.read_bytes() + b'tampering')
    with pytest.raises(ValueError, match='corrupt'):
        verify_source_archive(ref, tmp_path)


def test_incomplete_http_cache_selects_normal_installation_fallback(tmp_path):
    from harbor_patches.verifier_bundle import verify_source_archive, OFFLINE_FORMAT, lookup, validate_offline_archive
    spec = {'apt': [], 'python': [], 'uv': False,
            'http': {'commands': ['curl https://example.test/input'], 'home': '/root', 'workdir': '/app'}}
    key = spec_id(spec)
    path = tmp_path / (key + '.tar.gz')
    with tarfile.open(path, 'w:gz') as archive:
        archive.addfile(tarfile.TarInfo('repo/Packages'), io.BytesIO())
    ref = {'format': OFFLINE_FORMAT, 'id': key, 'spec': spec, 'status': 'ready', 'archive_sha256': digest(path)}
    (tmp_path / (key + '.json')).write_text(json.dumps(ref))
    found, reason = lookup(ref, OFFLINE_FORMAT, validate_offline_archive, tmp_path, preparation=True)
    assert found is None
    assert 'Incomplete HTTP archive' in reason
    assert digest(path) == ref['archive_sha256']


def test_unpinned_reference_cannot_start_validation(tmp_path):
    _, ref = prepared(tmp_path)
    del ref['archive_sha256']
    with pytest.raises(ValueError, match='identity'):
        verify_source_archive(ref, tmp_path)


@pytest.mark.parametrize('name', ['../escape', '/absolute', 'python/../../escape', '.'])
def test_unsafe_bundle_paths_are_rejected(tmp_path, name):
    path = tmp_path / 'bad.tar'
    with tarfile.open(path, 'w') as archive:
        archive.addfile(tarfile.TarInfo(name))
    with pytest.raises(ValueError, match='Unsafe'):
        validate_archive(path)


def test_preflight_does_not_skip_conflicting_reference_for_same_spec(tmp_path):
    _, ref = prepared(tmp_path)
    tasks = [tmp_path / 'task1', tmp_path / 'task2']
    for task in tasks:
        (task / 'tests').mkdir(parents=True)
        (task / 'tests' / REFERENCE).write_text(json.dumps(ref))
    ref['archive_sha256'] = '0' * 64
    (tasks[1] / 'tests' / REFERENCE).write_text(json.dumps(ref))
    with pytest.raises(ValueError, match='Conflicting'):
        preflight(tasks, tmp_path)




def test_structural_validation_memo_reads_bytes_and_keys_policy(tmp_path, monkeypatch):
    import io
    import tarfile
    import pytest
    from harbor_patches import verifier_bundle as module
    archive = tmp_path/'runtime.tar.gz'
    def write(name):
        with tarfile.open(archive,'w:gz') as out:
            member=tarfile.TarInfo(name);member.size=4
            out.addfile(member,io.BytesIO(b'data'))
    write('python/bin/python3')
    module._validated_archives.clear()
    original=module.tarfile.open;calls=[]
    def opened(*args,**kwargs):
        calls.append(args[0]);return original(*args,**kwargs)
    monkeypatch.setattr(module.tarfile,'open',opened)
    module.validate_archive(archive)
    module.validate_archive(archive)
    assert len(calls)==1
    with pytest.raises(ValueError):
        module.validate_archive(archive,roots=('repo',),required='repo/Packages')
    write('../escape')
    with pytest.raises(ValueError,match='Unsafe'):
        module.validate_archive(archive)


def test_structural_memo_refuses_bytes_changing_during_validation(tmp_path, monkeypatch):
    import io
    import tarfile
    import pytest
    from harbor_patches import verifier_bundle as module
    path=tmp_path/'changing.tar.gz'
    with tarfile.open(path,'w:gz') as out:
        member=tarfile.TarInfo('python/bin/python3');member.size=4
        out.addfile(member,io.BytesIO(b'data'))
    checks=iter(['a'*64,'b'*64]);module._validated_archives.clear()
    monkeypatch.setattr(module,'digest',lambda p:next(checks))
    with pytest.raises(ValueError,match='changed during'):
        module.validate_archive(path)
    assert not module._validated_archives
