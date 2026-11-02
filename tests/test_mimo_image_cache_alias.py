"""MiMo's legacy images must also be visible to environment-labelled workers."""
from pathlib import Path
from types import SimpleNamespace

from hpc.offline_prep.image_cache import prepare_images
from harbor_patches.image_context import environment_dir_hash_truncated


def test_prebuilt_legacy_image_resolves_by_context_with_sidecars(tmp_path, monkeypatch):
    task = tmp_path / 'task'
    (task / 'environment').mkdir(parents=True)
    (task / 'environment/Dockerfile').write_text('FROM pinned\n')
    images = tmp_path / 'images'
    images.mkdir()
    original = images / 'task.sif'
    original.write_bytes(b'image')
    original.with_suffix('.overlay.img').write_bytes(b'overlay')
    original.with_suffix('.deferred.json').write_bytes(b'{}')
    monkeypatch.setenv('OT_REQUIRE_PREBUILT_IMAGES', '1')
    args = SimpleNamespace(image_build_memory_mb=8192, image_build_cpus=4,
        image_build_timeout_sec=3600, image_build_concurrency=1, cpus=4, force_build=False)
    for _ in range(2):
        records = prepare_images([task], images, tmp_path / 'shared', args)
        key = environment_dir_hash_truncated(task / 'environment')
        matches = list(images.glob('*-' + key + '.sif'))
        assert len(matches) == 1
        assert matches[0].resolve() == original
        assert matches[0].with_suffix('.overlay.img').read_bytes() == b'overlay'
        assert matches[0].with_suffix('.deferred.json').read_bytes() == b'{}'
        assert records[0]['status'] == 'cached'
    assert original.read_bytes() == b'image'
