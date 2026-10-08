import json
from pathlib import Path
import platform

import pyarrow.parquet as pq
import pytest

from hpc.image_cache import publish as cache_publish
from validation.publishing import image_release as release
from validation.contract import task_digest
from validation.data.materialize import materialize
from validation.publishing.publish import pack_task, write
from harbor.utils.container_cache import environment_dir_hash


def fixture(tmp_path, legacy=False):
    task = tmp_path / 'original/task-1'
    env = task / 'environment'
    env.mkdir(parents=True)
    (env / 'Dockerfile').write_text('FROM example\nCOPY payload /payload\n')
    (env / 'payload').write_text('fixture')
    (task / 'instruction.md').write_text('Do something')
    key = environment_dir_hash(env)
    image = tmp_path / f'build_task-1-{key[:12]}.sif'
    image.write_bytes(b'pristine sif')
    image.with_suffix('.deferred.json').write_text('{"run": ["echo hi"]}')
    image.with_suffix('.overlay.img').write_bytes(b'pristine overlay')
    provenance = dict(pristine=True, builder='hpc.image_cache', environment_sha256=key,
                      architecture=platform.machine(), format='apptainer-sif')
    cache = tmp_path / 'cache'
    bundle = cache_publish(image, cache, provenance=None if legacy else provenance)
    row = dict(path=task.name, task_binary=pack_task(task), content_sha256=task_digest(task), stages_passed='1,3,4,5')
    tables = {'example': ([row, dict(row, path='task-2')], [])}
    return task, cache, bundle, tables


def write_parquet(tmp_path, tables, manifest, monkeypatch):
    monkeypatch.setattr('validation.publishing.publish.description', lambda record: 'test')
    write(tables, {}, 'runs/test.json', tmp_path / 'release', image_manifest=manifest)
    return tmp_path / 'release/example/tasks.parquet'


def test_publish_materialize_restore_and_reuse(tmp_path, monkeypatch):
    task, cache, bundle, tables = fixture(tmp_path)
    manifest, artifacts = release.prepare_release(tables, cache, tmp_path / 'release', 'org/tasks')
    assert len(manifest['bundles']) == 1 and len(manifest['tasks']) == 2
    assert len(artifacts) == 3
    manifest['revision'] = 'a' * 40
    parquet = write_parquet(tmp_path, tables, manifest, monkeypatch)
    assert 'image' not in pq.read_schema(parquet).names
    tasks = materialize(parquet, tmp_path / 'downloaded/tasks')
    calls = []
    def download(**kwargs):
        assert kwargs['revision'] == 'a' * 40
        calls.append(kwargs)
        return artifacts[kwargs['filename']]
    target = tmp_path / 'runtime'
    records = release.stage_published(tasks, tmp_path / 'fresh-cache', target, download)
    assert len(records) == 1 and len(calls) == 3
    assert (target / bundle.name).read_bytes() == b'pristine sif'
    assert (target / bundle.name).with_suffix('.overlay.img').read_bytes() == b'pristine overlay'
    from hpc.image_cache import prepare_images
    from types import SimpleNamespace
    args = SimpleNamespace(image_build_memory_mb=1024, image_build_cpus=1, cpus=1,
                           image_build_timeout_sec=60, image_build_concurrency=1, force_build=False)
    results = prepare_images(tasks, target, tmp_path / 'fresh-cache', args)
    assert all(r['status'] == 'cached' for r in results)


def test_legacy_unvalidated_and_changed_tasks_not_exported(tmp_path):
    task, cache, bundle, tables = fixture(tmp_path, legacy=True)
    manifest, artifacts = release.prepare_release(tables, cache, tmp_path / 'release', 'org/tasks')
    assert not artifacts and len(manifest['missing']) == 2
    tables['example'][0][0]['stages_passed'] = '1'
    manifest, _ = release.prepare_release(tables, cache, tmp_path / 'release', 'org/tasks')
    assert len(manifest['missing']) == 1
    tables['example'][0][1]['content_sha256'] = '0' * 64
    with pytest.raises(ValueError, match='changed since validation'):
        release.prepare_release(tables, cache, tmp_path / 'release', 'org/tasks')


def test_corrupt_download_not_visible_and_edited_task_skipped(tmp_path, monkeypatch):
    task, cache, bundle, tables = fixture(tmp_path)
    manifest, artifacts = release.prepare_release(tables, cache, tmp_path / 'release', 'org/tasks')
    manifest['revision'] = 'a' * 40
    parquet = write_parquet(tmp_path, tables, manifest, monkeypatch)
    tasks = materialize(parquet, tmp_path / 'downloaded/tasks', limit=1)
    bad = tmp_path / 'bad'
    bad.write_bytes(b'corrupt')
    with pytest.raises(ValueError, match='Corrupt'):
        release.stage_published(tasks, cache, tmp_path / 'runtime', lambda **kw: bad)
    assert not list((tmp_path / 'runtime').glob('*.sif'))
    (tasks[0] / 'environment/payload').write_text('changed')
    assert not release.stage_published(tasks, cache, tmp_path / 'runtime', lambda **kw: pytest.fail('stale image'))


def test_incompatible_architecture_and_unpinned_revision_rejected(tmp_path, monkeypatch):
    task, cache, bundle, tables = fixture(tmp_path)
    manifest, artifacts = release.prepare_release(tables, cache, tmp_path / 'release', 'org/tasks')
    parquet = write_parquet(tmp_path, tables, manifest, monkeypatch)
    tasks = materialize(parquet, tmp_path / 'downloaded/tasks', limit=1)
    with pytest.raises(ValueError, match='pinned'):
        release.stage_published(tasks, cache, tmp_path / 'runtime', lambda **kw: None)
    refs = release.reference_path(tasks[0].parent)
    data = json.loads(refs.read_text())
    data[tasks[0].name]['revision'] = 'a' * 40
    next(iter(data[tasks[0].name]['bundles'].values()))['provenance']['architecture'] = 'other'
    refs.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='incompatible'):
        release.stage_published(tasks, cache, tmp_path / 'runtime', lambda **kw: None)


def test_publisher_pins_artifact_commit_in_same_pr(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import huggingface_hub
    from validation.publishing import publish
    task, cache, bundle, tables = fixture(tmp_path)
    monkeypatch.setattr(publish, 'read', lambda _: {'sha256': 'contract', 'dataset': {}})
    monkeypatch.setattr(publish, 'stage_reports', lambda _: {})
    monkeypatch.setattr(publish, 'build', lambda *a: (tables, {'run': 'test'}, 'runs/test.json'))
    monkeypatch.setattr(publish.patch_provenance, 'attach', lambda *a: None)
    monkeypatch.setattr(publish, 'description', lambda *a: 'Image release')
    commits = []
    class Api:
        def create_commit(self, **kwargs):
            commits.append(kwargs)
            if len(commits) == 1:
                assert kwargs['create_pr'] is True
                assert all(op.path_in_repo.startswith('images/bundles-v1/') for op in kwargs['operations'])
                return SimpleNamespace(oid='a' * 40, pr_revision='refs/pr/7', pr_url='https://example/pr/7')
            assert kwargs['create_pr'] is False
            assert kwargs['revision'] == 'refs/pr/7'
            assert kwargs['parent_commit'] == 'a' * 40
            parquet = next(op for op in kwargs['operations'] if op.path_in_repo == 'example/tasks.parquet')
            metadata = pq.read_schema(parquet.path_or_fileobj).metadata
            assert json.loads(metadata[b'ot.images.v1'])['revision'] == 'a' * 40
            return SimpleNamespace(oid='b' * 40, pr_url=None, pr_revision=None)
    monkeypatch.setattr(huggingface_hub, 'HfApi', Api)
    result = publish.publish(tmp_path, tmp_path / 'contract', 'org/tasks', out=tmp_path / 'release', image_cache=cache)
    assert len(commits) == 2 and result['pull_request'] == 'https://example/pr/7'
    tasks = materialize(tmp_path / 'release/example/tasks.parquet', tmp_path / 'fresh/tasks')
    artifact_paths = {op.path_in_repo: op.path_or_fileobj for op in commits[0]['operations']}
    monkeypatch.setattr(huggingface_hub, 'hf_hub_download', lambda **kw: artifact_paths[kw['filename']])
    from hpc.local_assets import stage_images
    target = stage_images(tasks, tmp_path / 'empty-cache', tmp_path / 'runtime')
    assert (target / bundle.name).read_bytes() == b'pristine sif'


def test_corrupt_export_and_partial_bundle_rejected(tmp_path):
    task, cache, bundle, tables = fixture(tmp_path)
    (bundle / bundle.name).write_bytes(b'corruption')
    with pytest.raises(ValueError, match='Corrupt'):
        release.prepare_release(tables, cache, tmp_path / 'release', 'org/tasks')
    with pytest.raises(ValueError, match='Incomplete'):
        release.validate_bundle(bundle.name, {'files': {bundle.name: {}, bundle.with_suffix('.deferred.json').name: {}}})


def test_directory_staging_preserves_references(tmp_path, monkeypatch):
    task, cache, bundle, tables = fixture(tmp_path)
    manifest, _ = release.prepare_release(tables, cache, tmp_path / 'release', 'org/tasks')
    manifest['revision'] = 'a' * 40
    parquet = write_parquet(tmp_path, tables, manifest, monkeypatch)
    tasks = materialize(parquet, tmp_path / 'fresh/tasks')
    release.copy_references(tasks[:1], tmp_path / 'staged')
    references = json.loads(release.reference_path(tmp_path / 'staged').read_text())
    assert list(references) == ['task-1']


def test_packed_cache_exports_and_stages_without_rebuild(tmp_path):
    from hpc.image_cache import bundle_manifest
    from hpc.local_assets import stage_images
    task, cache, bundle, tables = fixture(tmp_path)
    entry = bundle_manifest(bundle)
    packed_cache = tmp_path / 'packed-cache'
    packed = cache_publish(bundle / bundle.name, packed_cache, provenance=entry['provenance'], packed=True)
    assert packed.is_file()
    manifest, artifacts = release.prepare_release(tables, packed_cache, tmp_path / 'release', 'org/tasks')
    assert len(manifest['bundles']) == 1 and len(artifacts) == 3
    assert not manifest['missing']
    stage_images([task], packed_cache, tmp_path / 'local')
    assert (tmp_path / 'local' / bundle.name).read_bytes() == b'pristine sif'
