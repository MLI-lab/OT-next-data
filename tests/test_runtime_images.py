import asyncio
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tarfile
from types import SimpleNamespace

import pytest
from harbor_patches import runtime_images as images
from harbor_patches import verifier_bundle as bundles


def archive(tmp_path):
    cache = tmp_path / 'cache'
    cache.mkdir()
    tree = tmp_path / 'tree'
    (tree / 'python/bin').mkdir(parents=True)
    (tree / 'python/bin/python3').write_text('#!/bin/sh\necho fixture\n')
    (tree / 'python/bin/python3').chmod(0o755)
    spec = {'python': '3.12.14', 'tools': []}
    key = bundles.spec_id(spec)
    path = cache / (key + '.tar.gz')
    with tarfile.open(path, 'w:gz') as tar:
        tar.add(tree / 'python', arcname='python')
    ref = dict(id=key, format=bundles.FORMAT, archive_sha256=bundles.digest(path))
    (cache / (key + '.json')).write_text(json.dumps(dict(ref, spec=spec, status='ready')))
    return cache, ref


def test_build_real_squashfs_and_reuse_without_changing_source(tmp_path):
    if not shutil.which('mksquashfs'):
        pytest.skip('squashfs-tools not installed')
    cache, ref = archive(tmp_path)
    record = images.build(ref, cache, processors=1)
    assert record == images.build(ref, cache, processors=1)
    assert bundles.digest(cache / (ref['id'] + '.tar.gz')) == ref['archive_sha256']
    unpacked = tmp_path / 'unpacked'
    subprocess.run(['unsquashfs', '-processors', '1', '-d', str(unpacked), record['path']], check=True, capture_output=True)
    assert (unpacked / record['image_root'].lstrip('/') / 'python/bin/python3').read_bytes() == b'#!/bin/sh\necho fixture\n'
    Path(record['path']).chmod(0o644)
    Path(record['path']).write_bytes(b'corrupt')
    with pytest.raises(ValueError, match='checksum'):
        images.load(ref, cache)
    with pytest.raises(ValueError, match='checksum'):
        images.build(ref, cache)


def test_builder_rejects_corrupt_archive_before_unpacking(tmp_path):
    cache, ref = archive(tmp_path)
    (cache / (ref['id'] + '.tar.gz')).write_bytes(b'corrupt')
    with pytest.raises(ValueError, match='corrupt'):
        images.build(ref, cache)
    assert not list(cache.glob('*.squashfs'))


def test_start_mounts_only_checksum_bound_image_not_cache(tmp_path, monkeypatch):
    image = tmp_path / 'a.squashfs'
    ref = dict(id='a'*64, archive_sha256='b'*64, image_sha256='c'*64)
    monkeypatch.setenv('OT_VERIFIER_RUNTIME_IMAGES', '1')
    monkeypatch.setattr(images, 'load', lambda r: dict(ref, path=str(image), mount=images.MOUNT + ref['id'], image_root=images.BASE + ref['id']))
    flags = images.start_flags({'task_env_config': {'verifier_runtime_images': [ref]}})
    assert flags == ['--bind', str(image) + ':' + images.MOUNT + ref['id'] + ':ro,image-src=' + images.BASE + ref['id']]
    with pytest.raises(ValueError, match='differ'):
        images.start_flags({'task_env_config': {'verifier_runtime_images': [dict(ref, image_sha256='d'*64)]}})
    monkeypatch.setenv('OT_VERIFIER_RUNTIME_IMAGES', '0')
    with pytest.raises(ValueError, match='removed'):
        images.start_flags({'task_env_config': {'verifier_runtime_images': [ref]}})


def test_agent_writes_use_container_overlay_and_clean_bind_stays_unchanged(tmp_path, monkeypatch):
    cache, ref = archive(tmp_path)
    record = images.build(ref, cache, processors=1)
    image = Path('/hnvme/workspace/y500bb12-crosscodeeval-pilot/validation-smoke/images/build_0001_656_1656907_qa_4-agent-016e9c9f617d.sif')
    if not image.is_file() or not shutil.which('apptainer'):
        pytest.skip('local cached Apptainer test image unavailable')
    monkeypatch.setenv('OT_VERIFIER_IMAGE_CACHE', str(cache))
    monkeypatch.setenv('OT_VERIFIER_RUNTIME_IMAGES', '1')
    flags = images.start_flags({'task_env_config': {'verifier_runtime_images': [dict(ref,
        image_sha256=record['image_sha256'], agent_runtime=True)]}})
    lower = record['image_root'] + '/python/bin/python3'
    clean = record['mount'] + '/python/bin/python3'
    for number in range(2):
        overlay = tmp_path / ('overlay-' + str(number))
        (overlay / 'upper').mkdir(parents=True)
        (overlay / 'work').mkdir()
        command = ['apptainer', 'exec', '--fakeroot', '--contain', '--no-mount', 'hostfs,bind-paths,cwd',
                   *flags, '--overlay', str(overlay), str(image), '/bin/sh', '-ec']
        script = (f'grep -q fixture {lower}; printf "agent change" > {lower}; '
                  f'grep -q "agent change" {lower}; grep -q fixture {clean}; '
                  f'if printf bad > {clean}; then exit 42; fi')
        result = subprocess.run([*command, script], capture_output=True, text=True, timeout=90)
        assert result.returncode == 0, result.stdout + result.stderr
        result = subprocess.run([*command, f'grep -q "agent change" {lower}; grep -q fixture {clean}'],
                                capture_output=True, text=True, timeout=90)
        assert result.returncode == 0, result.stderr
    assert bundles.digest(Path(record['path'])) == record['image_sha256']


def test_task_stage_selects_image_overlay_without_upload_or_extraction(tmp_path, monkeypatch):
    from harbor_patches import task_runtime
    cache, ref = archive(tmp_path)
    record = dict(images.build(ref, cache, processors=1), agent_runtime=True)
    task = tmp_path / 'task'
    (task / 'setup_files').mkdir(parents=True)
    (task / 'setup_files' / bundles.TASK_REFERENCE).write_text(json.dumps(ref))
    monkeypatch.setenv('OT_RUNTIME_CACHE', str(cache))
    commands = []
    class Environment:
        _persistent_env = {'PATH': '/usr/bin'}
        _verifier_runtime_images = {ref['id']: record}
        async def exec(self, command, **kwargs):
            commands.append(command)
            return SimpleNamespace(return_code=0, stdout='/usr/bin', stderr='')
        async def stop(self):
            commands.append('stopped')
    env = Environment()
    result = asyncio.run(task_runtime.stage(env, task, tmp_path / 'evidence'))
    assert result['runtime']['storage'] == 'shared-squashfs'
    assert env._persistent_env['OT_TASK_PYTHON'] == record['image_root'] + '/python/bin/python3'
    assert not any('tar ' in c or 'mktemp' in c for c in commands)
    asyncio.run(env.stop())
    assert env._persistent_env == {'PATH': '/usr/bin'}
    assert not any('rm -rf' in c for c in commands)  # Shared image/agent tree not removed by staging cleanup.
    assert bundles.digest(Path(record['path'])) == record['image_sha256']


def test_new_task_reference_pins_image_bytes(tmp_path):
    from data.utils.verifier_runtime import runtime_reference
    cache, ref = archive(tmp_path)
    record = images.build(ref, cache, processors=1)
    data = json.loads(runtime_reference(record['spec'], {ref['id']: record})['tests/' + bundles.REFERENCE])
    assert data['storage'] == 'squashfs' and data['image_sha256'] == record['image_sha256']
    with pytest.raises(ValueError, match='checksum differs'):
        images.load(dict(data, image_sha256='f' * 64), cache)


def test_repacking_same_archive_into_two_empty_caches_is_identical(tmp_path):
    cache, ref = archive(tmp_path)
    first = images.build(ref, cache, tmp_path / 'one', processors=1)
    second = images.build(ref, cache, tmp_path / 'two', processors=1)
    assert first['image_sha256'] == second['image_sha256']
    assert (tmp_path / 'one' / (ref['id'] + '.squashfs.json')).read_bytes() == (
        tmp_path / 'two' / (ref['id'] + '.squashfs.json')).read_bytes()


def test_image_stage_never_uploads_or_extracts_archive(tmp_path, monkeypatch):
    from harbor_patches import verifier_mount
    ref = dict(id='a'*64, archive_sha256='b'*64)
    commands = []
    class Environment:
        _verifier_runtime_images = {ref['id']: dict(ref, image_sha256='c'*64, image_bytes=100)}
        async def exec(self, command, **kwargs):
            commands.append(command)
            return SimpleNamespace(return_code=0, stdout='/tests/.ot-verifier-runtime-Ab123' if command.startswith('mktemp') else '', stderr='')
    async def prepare(*a, **kw): pass
    monkeypatch.setattr(verifier_mount, 'prepare', prepare)
    record = asyncio.run(bundles.stage_image(Environment(), ref, tmp_path))
    assert record['storage'] == 'shared-squashfs'
    assert not any('tar ' in c or 'runtime.tar.gz' in c for c in commands)
    assert record['archive_sha256'] == ref['archive_sha256']
    assert 'ln -s ' + images.MOUNT in commands[1]
    assert verifier_mount.command(dict(record, mount_mode='readonly'), 'true').find('readonly-overlay') > 0


def test_missing_image_does_not_fall_back_to_ram_extraction(tmp_path):
    with pytest.raises(RuntimeError, match='not mounted'):
        asyncio.run(bundles.stage_image(SimpleNamespace(), {'id':'a'*64, 'archive_sha256':'b'*64}, tmp_path))


def test_client_binds_references_to_create_requests_per_environment(tmp_path, monkeypatch):
    refs = {}
    for name, digit in [('one', 'a'), ('two', 'b')]:
        task = tmp_path / name
        (task / 'environment').mkdir(parents=True)
        (task / 'tests').mkdir()
        ref = dict(id=digit*64, archive_sha256='c'*64, format=bundles.FORMAT)
        (task / 'tests' / bundles.REFERENCE).write_text(json.dumps(ref))
        refs[name] = ref
    seen = []
    async def post(url, data):
        await asyncio.sleep(0)
        seen.append(data)
    bridge = SimpleNamespace(_async_http_post=post)
    class Environment:
        def __init__(self, name): self.environment_dir = tmp_path / name / 'environment'
        async def start(self):
            await asyncio.sleep(0)
            await bridge._async_http_post('http://bridge/env/create', {'task_env_config': {'memory_mb': 2048}})
    bridge.ApptainerEnvironment = Environment
    monkeypatch.setenv('OT_VERIFIER_RUNTIME_IMAGES', '1')
    monkeypatch.setattr(images, 'load', lambda r: dict(r, image_sha256='d'*64))
    images.install_client(bridge)
    first, second = Environment('one'), Environment('two')
    async def exercise():
        await asyncio.gather(first.start(), second.start())
    asyncio.run(exercise())
    assert {r['task_env_config']['verifier_runtime_images'][0]['id'] for r in seen} == {'a'*64, 'b'*64}
    assert all(r['task_env_config']['memory_mb'] == 2048 for r in seen)
    assert list(first._verifier_runtime_images) == ['a'*64]
    assert list(second._verifier_runtime_images) == ['b'*64]


def test_legacy_mode_is_rejected_even_when_image_also_cached(tmp_path, monkeypatch):
    cache, ref = archive(tmp_path)
    images.build(ref, cache, processors=1)
    monkeypatch.setenv('OT_RUNTIME_IMAGES', '0')
    with pytest.raises(ValueError, match='removed'):
        images.enabled()


@pytest.mark.parametrize('phase', ['agent', 'verifier'])
def test_archive_only_cache_cannot_be_staged(tmp_path, monkeypatch, phase):
    cache, ref = archive(tmp_path)
    monkeypatch.setenv('OT_RUNTIME_CACHE', str(cache))
    task = tmp_path / 'task'
    folder = task / ('setup_files' if phase == 'agent' else 'tests')
    folder.mkdir(parents=True)
    name = bundles.TASK_REFERENCE if phase == 'agent' else bundles.REFERENCE
    (folder / name).write_text(json.dumps(ref))
    env = SimpleNamespace(_persistent_env={})
    from harbor_patches import task_runtime
    with pytest.raises(RuntimeError, match='not mounted'):
        asyncio.run(task_runtime.stage(env, task) if phase == 'agent' else bundles.stage(env, folder, tmp_path / 'evidence'))
    assert not list(cache.glob('*.squashfs'))


def test_git_only_runtime_selection_omits_unused_verifier(tmp_path):
    task = tmp_path / 'task'
    (task / 'environment').mkdir(parents=True)
    for folder, key in [('setup_files', 'a'), ('tests', 'b')]:
        context = task / folder
        context.mkdir()
        (context / (bundles.TASK_REFERENCE if folder == "setup_files" else bundles.REFERENCE)).write_text(json.dumps(
            dict(id=key * 64, format=bundles.FORMAT, archive_sha256='c' * 64)))
    assert {r['id'] for r in images.references(task / 'environment')} == {'a' * 64, 'b' * 64}
    assert [r['id'] for r in images.references(task / 'environment', agent_only=True)] == ['a' * 64]
