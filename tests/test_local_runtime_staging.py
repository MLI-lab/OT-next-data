"""Local runtime relocation, asset staging, capacity and dependency archives."""
import json
from types import SimpleNamespace

import pytest

from hpc import local_assets, local_runtime


def test_assets_require_local_capacity(tmp_path, monkeypatch):
    source = tmp_path / 'shared'; source.write_bytes(b'weights')
    monkeypatch.setattr(local_assets.shutil, 'disk_usage', lambda _: SimpleNamespace(free=0))
    with pytest.raises(OSError, match='Insufficient local storage'):
        local_assets.copy_asset(source, tmp_path / 'local/image')


def test_images_deduplicate_aliases_and_keep_overlay_local(tmp_path):
    from harbor.utils.container_cache import environment_dir_hash_truncated
    task = tmp_path / 'task'; env = task / 'environment'; env.mkdir(parents=True)
    (env / 'Dockerfile').write_text('FROM example\nCOPY payload /payload\n')
    (env / 'payload').write_text('payload')
    key = environment_dir_hash_truncated(env)
    shared = tmp_path / 'shared'; shared.mkdir()
    image = shared / 'actual.sif'; image.write_bytes(b'image')
    image.with_suffix('.overlay.img').write_bytes(b'overlay')
    image.with_suffix('.deferred.json').write_text('{"run":["true"]}')
    for prefix in ('build_a', 'build_b'):
        (shared / (prefix + '-' + key + '.sif')).symlink_to(image)
    local = local_assets.stage_images([task], shared, tmp_path / 'local')
    import shutil
    shutil.rmtree(shared)
    for staged in local.glob('*.sif'):
        assert staged.read_bytes() == b'image'
        assert staged.resolve().with_suffix('.overlay.img').read_bytes() == b'overlay'
    assert len(json.loads((local / 'staging.json').read_text())) == 3


def test_relocation_rewrites_entrypoints_and_rejects_external_paths(tmp_path):
    root = tmp_path / 'runtime'
    (root / 'env/bin').mkdir(parents=True)
    (root / 'python/bin').mkdir(parents=True)
    (root / 'python/bin/python3.12').touch()
    (root / 'env/bin/python').touch()
    entry = root / 'env/bin/vllm'
    entry.write_text('#!/old/env/bin/python\nprint("entry")\n')
    local_runtime.relocate(root, {'executable': 'python3.12'})
    assert (root / 'env/bin/python').resolve() == root / 'python/bin/python3.12'
    assert entry.read_text().splitlines()[0] == '#!' + str(root / 'env/bin/python')
    site = root / 'env/lib/python3.12/site-packages'
    site.mkdir(parents=True)
    (site / 'external.pth').write_text('/outside/environment\n')
    with pytest.raises(ValueError, match='Nonlocal package path'):
        local_runtime.relocate(root, {'executable': 'python3.12'})



def test_partial_asset_is_removed_on_copy_failure(tmp_path, monkeypatch):
    source = tmp_path / 'source'; source.write_text('input')
    def fail(src, dst):
        dst.write_text('partial')
        raise OSError('copy interrupted')
    monkeypatch.setattr(local_assets.shutil, 'copy2', fail)
    with pytest.raises(OSError, match='copy interrupted'):
        local_assets.copy_asset(source, tmp_path / 'target', reserve=0)
    assert not (tmp_path / 'target').exists()
    assert not (tmp_path / 'target.staging').exists()


def test_multinode_weights_share_one_capacity_decision(tmp_path, monkeypatch):
    source = tmp_path / 'weights'; source.mkdir()
    (source / 'shard').write_bytes(b'weights')
    calls = []
    monkeypatch.setattr(local_assets.subprocess, 'check_output', lambda *a, **kw: '{"free":100}\n{"free":0}\n')
    monkeypatch.setattr(local_assets.subprocess, 'run', lambda *a, **kw: calls.append(a))
    with pytest.raises(OSError, match='Insufficient local storage'):
        local_assets.stage_multinode_weights(source, tmp_path / 'local/model', 2, reserve=0)
    assert not calls
    monkeypatch.setattr(local_assets.subprocess, 'check_output', lambda *a, **kw: '{"free":100}\n{"free":100}\n')
    result, record = local_assets.stage_multinode_weights(source, tmp_path / 'local/model', 2, reserve=0)
    assert result == tmp_path / 'local/model' and record['local'] == str(result)
    assert len(calls) == 1 and '--ntasks-per-node=1' in calls[0][0]


def test_dependency_archives_are_local_and_only_new_outputs_are_saved(tmp_path, monkeypatch):
    monkeypatch.setattr(local_assets.shutil, 'disk_usage', lambda _: SimpleNamespace(free=10**12))
    shared = tmp_path / 'shared'; shared.mkdir()
    (shared / 'selected.tar').write_bytes(b'existing')
    (shared / 'other.tar').write_bytes(b'not selected')
    task = tmp_path / 'selected'
    spec = {'directory': str(shared), 'target': '/cache.tar', 'folder': '/cache'}
    staged, original = local_assets.stage_dependencies(spec, [task], tmp_path / 'local')
    local = tmp_path / 'local'
    assert sorted(p.name for p in local.iterdir()) == ['selected.tar']
    assert staged['directory'] == str(local) and spec['directory'] == str(shared)
    (shared / 'selected.tar').unlink()
    assert (local / 'selected.tar').read_bytes() == b'existing'
    (local / 'new.tar').write_bytes(b'oracle result')
    local_assets.save_dependencies(local, shared, original)
    assert not (shared / 'selected.tar').exists()  # unchanged input is not rewritten
    assert (shared / 'new.tar').read_bytes() == b'oracle result'


def test_runtime_capacity_rejects_before_copying(tmp_path, monkeypatch):
    archive = tmp_path / 'bundle.tar'; archive.write_bytes(b'archive')
    manifest = tmp_path / 'bundle.json'
    manifest.write_text(json.dumps({'archive': str(archive)}))
    monkeypatch.setattr(local_runtime.shutil, 'disk_usage', lambda _: SimpleNamespace(free=0))
    monkeypatch.setattr(local_runtime.shutil, 'copyfile', lambda *a, **kw: pytest.fail('copied before capacity check'))
    with pytest.raises(OSError, match='Insufficient node-local scratch before staging'):
        local_runtime.stage(manifest, tmp_path / 'code', tmp_path / 'local')
    record = json.loads((tmp_path / 'local/scratch-capacity.json').read_text())
    assert record['free_bytes'] == 0


def test_whole_run_scratch_budget_is_enforced(tmp_path, monkeypatch):
    archive = tmp_path / 'bundle.tar'; archive.write_bytes(b'archive')
    gib = 1024**3
    monkeypatch.setenv('OT_MIN_SCRATCH_FREE_BYTES', str(200 * gib))
    monkeypatch.setattr(local_runtime.shutil, 'disk_usage', lambda _: SimpleNamespace(free=150 * gib))
    with pytest.raises(OSError, match='need 200.0 GiB'):
        local_runtime.check_stage_capacity({'archive': str(archive)}, tmp_path)
    monkeypatch.setattr(local_runtime.shutil, 'disk_usage', lambda _: SimpleNamespace(free=250 * gib))
    result = local_runtime.check_stage_capacity({'archive': str(archive)}, tmp_path)
    assert result['required_bytes'] == 200 * gib
    assert result['free_bytes'] == 250 * gib
