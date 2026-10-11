from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from hpc.image_cache import publish, restore, prepare_images, use_image_certificate_store


def test_image_build_removes_host_ca_overrides_but_keeps_other_binds():
    environment = {
        'SSL_CERT_FILE': '/tmp/host/roots.pem',
        'SSL_CERT_DIR': '/tmp/host/certs',
        'REQUESTS_CA_BUNDLE': '/tmp/host/roots.pem',
        'CURL_CA_BUNDLE': '/tmp/host/roots.pem',
        'APPTAINERENV_SSL_CERT_FILE': '/run/ot-certificates/ssl_cert_file.pem',
        'APPTAINERENV_REQUESTS_CA_BUNDLE': '/run/ot-certificates/requests_ca_bundle.pem',
        'APPTAINER_BINDPATH': '/tmp/host/roots.pem:/run/ot-certificates/ssl_cert_file.pem:ro,/data:/data:ro',
    }
    use_image_certificate_store(environment)
    assert not any(name in environment for name in (
        'SSL_CERT_FILE', 'SSL_CERT_DIR', 'REQUESTS_CA_BUNDLE', 'CURL_CA_BUNDLE',
        'APPTAINERENV_SSL_CERT_FILE', 'APPTAINERENV_REQUESTS_CA_BUNDLE'))
    assert environment['APPTAINER_BINDPATH'] == '/data:/data:ro'


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


@pytest.mark.parametrize('directory_setting', [None, '0', '1'])
def test_builder_initializes_apptainer(tmp_path, monkeypatch, directory_setting):
    pytest.importorskip('harbor')
    from hpc import image_cache
    from harbor_patches import bridge_worker as patch
    from harbor_patches import image_build
    directory_adapters = []
    original_adapter = image_build.directory_overlay_builds
    def directory_adapter(run, apptainer):
        directory_adapters.append(apptainer)
        return original_adapter(run, apptainer)
    monkeypatch.setattr(image_build, 'directory_overlay_builds', directory_adapter)
    if directory_setting is None:
        monkeypatch.delenv('OT_IMAGE_DIRECTORY_BUILD', raising=False)
    else:
        monkeypatch.setenv('OT_IMAGE_DIRECTORY_BUILD', directory_setting)
    context = tmp_path / 'environment'
    context.mkdir()
    (context / 'Dockerfile').write_text('FROM test\nRUN false && echo unreachable\nRUN true\n')
    output = tmp_path / 'image.sif'
    monkeypatch.setattr('sys.argv', ['image_cache', '--build', str(context / 'Dockerfile'),
                                  '--output', str(output), '--memory-mb', '8192', '--cpus', '4'])
    monkeypatch.setattr(image_cache.shutil, 'which', lambda name: '/bin/apptainer')
    monkeypatch.setattr(patch.worker, 'APPTAINER', None)
    monkeypatch.setattr(patch.worker.subprocess, 'run', patch.worker.subprocess.run)
    monkeypatch.setattr(patch, 'configure_container_certificates', lambda: None)
    monkeypatch.setenv('https_proxy', 'http://proxy.example:3128')
    monkeypatch.setenv('APPTAINER_BINDPATH', '')
    for key in ('http_proxy', 'https_proxy', 'HTTP_PROXY', 'HTTPS_PROXY', 'no_proxy', 'NO_PROXY'):
        monkeypatch.setenv('APPTAINERENV_' + key, '')
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
        assert image_cache.os.environ['APPTAINERENV_https_proxy'] == 'http://proxy.example:3128'
        assert '/etc/resolv.conf:/etc/resolv.conf:ro' in image_cache.os.environ['APPTAINER_BINDPATH']
        Path(destination).write_bytes(b'image')
    monkeypatch.setattr(patch.worker.ApptainerInstance, '_build_sif', build)
    image_cache.main()
    assert directory_adapters == ([] if directory_setting == '0' else ['/bin/apptainer'])
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


@pytest.mark.parametrize('failed', [False, True])
def test_prebuild_streams_each_image_and_releases_all_scratch(tmp_path, monkeypatch, failed):
    from hpc import image_cache, local_assets
    from harbor.utils.container_cache import environment_dir_hash_truncated
    task = tmp_path / 'task'
    for name in ('agent', 'verifier'):
        env = task / name
        env.mkdir(parents=True)
        (env / 'Dockerfile').write_text(f'FROM test:{name}\n')
    images, shared = tmp_path / 'images', tmp_path / 'shared'
    images.mkdir()
    monkeypatch.delenv('SLURM_JOB_ID', raising=False)
    monkeypatch.delenv('SLURM_MEM_PER_NODE', raising=False)
    monkeypatch.delenv('OT_REQUIRE_PREBUILT_IMAGES', raising=False)
    original = image_cache.subprocess.run
    built = []
    def run(command, **kwargs):
        if command[0] == 'cp':
            return original(command, **kwargs)
        image = Path(command[command.index('--output') + 1])
        assert image.parent.parent == images
        assert len([p for p in images.iterdir() if p.is_dir()]) == 1
        assert kwargs['env']['APPTAINER_CACHEDIR'] == str(image.parent / 'cache')
        image.write_bytes(b'image')
        image.with_suffix('.overlay.img').write_bytes(b'overlay')
        image.with_suffix('.deferred.json').write_text('{}')
        (image.parent / 'abandoned-context').mkdir()
        (image.parent / 'abandoned-context/file').write_text('temporary')
        built.append(image)
        if failed:
            raise image_cache.subprocess.TimeoutExpired(command, kwargs['timeout'])
    monkeypatch.setattr(image_cache.subprocess, 'run', run)
    args = SimpleNamespace(image_build_memory_mb=8192, image_build_cpus=4,
                           image_build_timeout_sec=3600, image_build_concurrency=1,
                           cpus=8, force_build=False, prebuild_only=True)
    first = prepare_images([task], images, shared, args)
    assert [r['status'] for r in first] == ['error' if failed else 'built'] * 2
    assert len(built) == 2
    assert all(p.is_file() and (p.suffix == '.log' or p.name == 'preparation.json')
               for p in images.iterdir())
    assert len(list(images.glob('*.build.log'))) == 2
    if not failed:
        # Restore verifies each matching bundle, without staging its sibling environment.
        original_restore = image_cache.restore
        restored = []
        def restore(bundle, target):
            key = target.name
            assert key in {environment_dir_hash_truncated(p) for p in task.iterdir()}
            assert bundle.name.endswith(f'-{key}.sif')
            restored.append(bundle)
            return original_restore(bundle, target)
        monkeypatch.setattr(image_cache, 'restore', restore)
        second = prepare_images([task], images, shared, args)
        assert [r['status'] for r in second] == ['cached'] * 2
        assert len(built) == len(restored) == 2
        assert all(not p.is_dir() for p in images.iterdir())
