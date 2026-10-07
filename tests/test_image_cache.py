from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from hpc.image_cache import publish, restore, prepare_images


def test_bundle_roundtrip_and_concurrent_publication(tmp_path):
    image = tmp_path / 'build_task-key.sif'
    image.write_bytes(b'image')
    image.with_suffix('.deferred.json').write_text('{}')
    image.with_suffix('.overlay.img').write_bytes(b'overlay')
    cache = tmp_path / 'shared'
    with ThreadPoolExecutor(max_workers=2) as pool:
        bundles = list(pool.map(lambda _: publish(image, cache), range(2)))
    assert bundles[0] == bundles[1]
    local = restore(bundles[0], tmp_path / 'local')
    assert local.read_bytes() == b'image'
    assert local.with_suffix('.overlay.img').read_bytes() == b'overlay'
    assert not list((cache / 'bundles-v1').glob('.pending-*'))


def test_missing_overlay_not_published(tmp_path):
    image = tmp_path / 'build_task-key.sif'
    image.write_bytes(b'image')
    image.with_suffix('.deferred.json').write_text('{}')
    with pytest.raises(ValueError, match='Incomplete'):
        publish(image, tmp_path / 'shared')
    assert not list((tmp_path / 'shared/bundles-v1').glob('*.sif'))


def test_corrupt_bundle_not_exposed_locally(tmp_path):
    image = tmp_path / 'build_task-key.sif'
    image.write_bytes(b'image')
    bundle = publish(image, tmp_path / 'shared')
    (bundle / image.name).write_bytes(b'wrong')
    with pytest.raises(ValueError, match='Corrupt'):
        restore(bundle, tmp_path / 'local')
    assert not (tmp_path / 'local' / image.name).exists()


def test_preparation_builds_once_and_next_run_stages_cache(tmp_path, monkeypatch):
    pytest.importorskip('harbor')
    from hpc import image_cache, local_assets
    task = tmp_path / 'task'
    task.mkdir()
    (task / 'Dockerfile').write_text('FROM test\n')
    images, shared = tmp_path / 'images', tmp_path / 'shared'
    images.mkdir()
    calls = []
    original = image_cache.subprocess.run
    def run(command, **kwargs):
        if command[0] == 'cp':
            return original(command, **kwargs)
        calls.append(command)
        Path(command[command.index('--output') + 1]).write_bytes(b'image')
    monkeypatch.setattr(image_cache.subprocess, 'run', run)
    monkeypatch.delenv('SLURM_JOB_ID', raising=False)
    monkeypatch.delenv('SLURM_MEM_PER_NODE', raising=False)
    args = SimpleNamespace(image_build_memory_mb=8192, image_build_cpus=4,
                           image_build_timeout_sec=3600, image_build_concurrency=2,
                           cpus=8, force_build=False)
    first = prepare_images([task], images, shared, args)
    assert first[0]['status'] == 'built' and len(calls) == 1
    second_images = local_assets.stage_images([task], shared, tmp_path / 'second')
    second = prepare_images([task], second_images, shared, args)
    assert second[0]['status'] == 'cached' and len(calls) == 1
    (task / 'Dockerfile').write_text('FROM changed\n')
    third = prepare_images([task], second_images, shared, args)
    assert third[0]['status'] == 'built' and len(calls) == 2
    monkeypatch.setenv('OT_REQUIRE_PREBUILT_IMAGES', '1')
    (task / 'Dockerfile').write_text('FROM another-change\n')
    with pytest.raises(RuntimeError, match='Required prebuilt image is missing'):
        prepare_images([task], second_images, shared, args)
    assert len(calls) == 2


def test_builder_initializes_apptainer(tmp_path, monkeypatch):
    pytest.importorskip('harbor')
    from hpc import image_cache
    from harbor_patches import bridge_worker as patch
    context = tmp_path / 'environment'
    context.mkdir()
    (context / 'Dockerfile').write_text('FROM test\nRUN false && echo unreachable\nRUN true\n')
    output = tmp_path / 'image.sif'
    monkeypatch.setattr('sys.argv', ['image_cache', '--build', str(context / 'Dockerfile'),
                                  '--output', str(output), '--memory-mb', '8192', '--cpus', '4'])
    monkeypatch.setattr(image_cache.shutil, 'which', lambda name: '/bin/apptainer')
    monkeypatch.setattr(patch.worker, 'APPTAINER', None)
    monkeypatch.setattr(patch, 'configure_container_certificates', lambda: None)
    verified = []
    monkeypatch.setattr(image_cache, 'verify_image_runtime', lambda image, tool: verified.append((image, tool)))
    monkeypatch.setattr(patch, 'run_container_commands', lambda run: run)
    monkeypatch.setattr(patch.worker, '_parse_copies', patch.worker._parse_copies)
    monkeypatch.setenv('OT_IMAGE_COMPRESSION_ARGS', '')
    def build(self, dockerfile, destination):
        assert patch.worker.APPTAINER == '/bin/apptainer'
        assert Path(dockerfile).parent != context
        import subprocess
        steps = [line[4:] for line in Path(dockerfile).read_text().splitlines() if line.startswith('RUN ')]
        # A later successful RUN must not hide failure in an earlier && chain.
        assert subprocess.run(['/bin/sh', '-ec', '\n'.join(steps)]).returncode != 0
        assert image_cache.os.environ['OT_IMAGE_COMPRESSION_ARGS'] == '-processors 4 -mem 2048M'
        Path(destination).write_bytes(b'image')
    monkeypatch.setattr(patch.worker.ApptainerInstance, '_build_sif', build)
    image_cache.main()
    assert output.read_bytes() == b'image'
    assert verified == [(output, '/bin/apptainer')]


@pytest.mark.parametrize('returncode', [0, 1])
def test_image_runtime_gate_checks_readonly_overlay_and_rejects_failure(tmp_path, monkeypatch, returncode):
    from hpc import image_cache
    image = tmp_path / 'image.sif'
    image.with_suffix('.deferred.json').write_text('{}')
    def run(command, **kwargs):
        assert command[:2] == ['unshare', '-r']
        assert command[command.index('--overlay') + 1] == str(image.with_suffix('.overlay.img')) + ':ro'
        assert 'tmux -V' in command[-1] and 'dpkg --audit' in command[-1]
        assert 'apt-get' not in command[-1]
        return SimpleNamespace(returncode=returncode, stdout='', stderr='broken package')
    monkeypatch.setattr(image_cache.subprocess, 'run', run)
    if returncode:
        with pytest.raises(RuntimeError, match='image will not be cached'):
            image_cache.verify_image_runtime(image, 'apptainer')
    else:
        image_cache.verify_image_runtime(image, 'apptainer')


def test_failed_slurm_build_keeps_separate_limits_and_is_not_published(tmp_path, monkeypatch):
    pytest.importorskip('harbor')
    from hpc import image_cache
    task = tmp_path / 'task'
    task.mkdir()
    (task / 'Dockerfile').write_text('FROM test\n')
    images = tmp_path / 'images'
    images.mkdir()
    calls = []
    def fail(command, **kwargs):
        calls.append((command, kwargs))
        Path(command[command.index('--output') + 1]).write_bytes(b'partial')
        raise image_cache.subprocess.TimeoutExpired(command, kwargs['timeout'])
    monkeypatch.setattr(image_cache.subprocess, 'run', fail)
    monkeypatch.setenv('SLURM_JOB_ID', '123')
    monkeypatch.setenv('SLURMD_NODENAME', 'build-node')
    monkeypatch.setenv('SLURM_MEM_PER_NODE', '32768')
    args = SimpleNamespace(image_build_memory_mb=8192, image_build_cpus=4,
                           image_build_timeout_sec=3600, image_build_concurrency=2,
                           cpus=8, force_build=False)
    result = prepare_images([task], images, tmp_path / 'shared', args)
    command, options = calls[0]
    assert '--mem=8192M' in command and '--cpus-per-task=4' in command
    assert '--time=60' in command and options['timeout'] == 3600
    assert result[0]['status'] == 'error'
    assert not list(images.glob('*.sif'))
    assert not (tmp_path / 'shared').exists()


def test_packed_sparse_bundle_roundtrip_and_concurrent_publish(tmp_path):
    image = tmp_path / 'build_task-key.sif'
    image.write_bytes(b'image')
    image.with_suffix('.deferred.json').write_text('{}')
    overlay = image.with_suffix('.overlay.img')
    with overlay.open('wb') as stream:
        stream.seek(32 * 1024 * 1024)
        stream.write(b'end')
    cache = tmp_path / 'shared'
    with ThreadPoolExecutor(2) as pool:
        bundles = list(pool.map(lambda _: publish(image, cache, provenance={'pristine': True}, packed=True), range(2)))
    assert bundles[0] == bundles[1]
    assert len(list((cache / 'bundles-v1').iterdir())) == 1
    assert bundles[0].stat().st_size < 1024 * 1024
    restored = restore(bundles[0], tmp_path / 'local')
    assert restored.read_bytes() == b'image'
    assert restored.with_suffix('.overlay.img').stat().st_size == overlay.stat().st_size
    assert restored.with_suffix('.overlay.img').stat().st_blocks * 512 < 1024 * 1024
