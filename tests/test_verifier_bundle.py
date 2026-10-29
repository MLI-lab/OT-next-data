import io
import json
import tarfile

import pytest

from harbor_patches.verifier_bundle import FORMAT, REFERENCE, digest, preflight, spec_id, validate_archive, verify


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
    assert verify(ref, tmp_path) == path
    validate_archive(path)
    path.write_bytes(path.read_bytes() + b'tampering')
    with pytest.raises(ValueError, match='corrupt'):
        verify(ref, tmp_path)


def test_unpinned_reference_cannot_start_validation(tmp_path):
    _, ref = prepared(tmp_path)
    del ref['archive_sha256']
    with pytest.raises(ValueError, match='Unpinned'):
        verify(ref, tmp_path)


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


@pytest.mark.parametrize('upload_measurement', [None, {'bytes': 50, 'requests': 1, 'timings_seconds': {'total': 0.1}},
    # Worker-cache-copy acknowledgements carry worker timings only; still evidence.
    {'transport': 'worker-cache-copy', 'bytes': 50, 'requests': 1, 'archive_sha256': 'b' * 64,
     'worker_timings_seconds': {'copy_and_hash': 0.01, 'install': 0.02}}])
def test_stage_uses_disk_backed_tests_mount_and_records_verified_runtime(tmp_path, monkeypatch, upload_measurement):
    import asyncio
    from types import SimpleNamespace
    from harbor_patches.verifier_bundle import stage
    _, ref = prepared(tmp_path)
    context = tmp_path / 'tests'
    context.mkdir()
    (context / REFERENCE).write_text(json.dumps(ref))
    monkeypatch.setenv('OT_VERIFIER_BUNDLE_CACHE', str(tmp_path))
    target = '/tests/.ot-verifier-runtime-123456789abc'
    calls = []
    class Environment:
        async def exec(self, command, **kwargs):
            calls.append(command)
            if command.startswith('mktemp '):
                assert '/tests/' in command
                return SimpleNamespace(return_code=0, stdout=target + '\n', stderr='')
            return SimpleNamespace(return_code=0, stdout='OT_VERIFIER_NAMESPACE=mount\n' if command.startswith('if unshare ') else '', stderr='')
        async def upload_file(self, source_path, target_path):
            if target_path.endswith('/runtime.tar.gz'):
                assert target_path == target + '/runtime.tar.gz'
                assert digest(source_path) == ref['archive_sha256']
                return upload_measurement
            else:
                assert target_path == target + '/mount.sh'
                assert source_path.name == 'verifier_mount.sh'
    evidence = tmp_path / 'evidence'
    result = asyncio.run(stage(Environment(), context, evidence))
    assert result.get('archive_upload') == upload_measurement
    assert result['python'] == target + '/active/bin/python3'
    assert result['mount_mode'] == 'writable'
    assert result['archive_sha256'] == ref['archive_sha256']
    assert set(result['timings_seconds']) == {
        'allocate', 'archive_upload', 'checksum_extract_permissions', 'mount_preparation'}
    assert all(value >= 0 for value in result['timings_seconds'].values())
    assert any('sha256sum -c' in call for call in calls)
    assert calls[-1].startswith('if unshare ')
    assert any('> mount.sh' in call for call in calls)
    assert json.loads((evidence / 'verifier-runtime.json').read_text()) == result


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
