import io
import hashlib
import json
import shutil
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from harbor_patches.verifier_bundle import OFFLINE_FORMAT, TASK_OFFLINE_REFERENCE, digest, preflight, spec_id
from validation.publishing import runtime_release

REVISION = 'a' * 40


def tar_bytes(files, mode='w:gz'):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode=mode) as archive:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def make_runtime(cache, marker=b'Package: demo\n'):
    spec = {'image_key': 'abc', 'cran': {'packages': ['demo'], 'repos': 'https://cran.example', 'type': 'source'}}
    key = spec_id(spec)
    cache.mkdir(parents=True, exist_ok=True)
    archive = cache / f'{key}.tar.gz'
    archive.write_bytes(tar_bytes({'repo/Packages': marker}))
    checksum = digest(archive)
    (cache / f'{key}.json').write_text(json.dumps({'format': OFFLINE_FORMAT, 'id': key, 'spec': spec,
                                                   'archive_sha256': checksum, 'status': 'ready'}))
    return key, checksum


def task_row(path, key, checksum):
    ref = json.dumps({'format': OFFLINE_FORMAT, 'id': key, 'archive_sha256': checksum}).encode()
    return {'path': path, 'task_binary': tar_bytes({'setup_files/' + TASK_OFFLINE_REFERENCE: ref,
                                                    'instruction.md': b'do it\n'})}


class Remote:
    """Files 'published' at one revision; download() mimics hf_hub_download."""

    def __init__(self, root):
        self.root = Path(root)
        self.calls = []

    def publish(self, artifacts):
        for name, path in artifacts.items():
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)

    def download(self, repo_id, repo_type, revision, filename, cache_dir):
        assert repo_type == 'dataset' and revision == REVISION
        self.calls.append(filename)
        target = Path(cache_dir) / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self.root / filename, target)
        return str(target)


def released(tmp_path):
    cache = tmp_path / 'cache'
    key, checksum = make_runtime(cache)
    tables = {'demo_v1': ([task_row('task_1', key, checksum), task_row('task_2', key, checksum)], [])}
    manifest, artifacts = runtime_release.prepare_release(tables, tmp_path / 'out', 'user/repo', cache=cache)
    remote = Remote(tmp_path / 'remote')
    remote.publish(artifacts)
    runtime_release.pin(manifest, REVISION)
    return key, checksum, manifest, remote


def test_release_stages_each_artifact_once_and_records_tasks(tmp_path):
    key, checksum, manifest, _ = released(tmp_path)
    assert manifest['missing'] == []
    record = manifest['artifacts'][key]
    assert record['archive'] == f'runtimes/{key}.tar.gz' and record['archive_sha256'] == checksum
    assert record['uploaded'] and record['revision'] == REVISION and record['tasks'] == 2
    assert manifest['tasks'] == {'task_1': [key], 'task_2': [key]}
    assert sorted(p.name for p in (tmp_path / 'out/runtimes').iterdir()) == [f'{key}.json', f'{key}.tar.gz']


def test_fetch_restores_fresh_cache_that_passes_preflight(tmp_path):
    key, checksum, manifest, remote = released(tmp_path)
    fresh = tmp_path / 'fresh'
    result = runtime_release.fetch(manifest, fresh, download=remote.download)
    assert result[key]['status'] == 'downloaded'
    task = tmp_path / 'task'
    (task / 'setup_files').mkdir(parents=True)
    (task / 'setup_files' / TASK_OFFLINE_REFERENCE).write_text(
        json.dumps({'format': OFFLINE_FORMAT, 'id': key, 'archive_sha256': checksum}))
    assert preflight([task], fresh)[key]['sha256'] == checksum
    # A second fetch reuses the verified entry without downloading.
    remote.calls.clear()
    assert runtime_release.fetch(manifest, fresh, download=remote.download)[key]['status'] == 'present'
    assert remote.calls == []
    assert not [p for p in fresh.iterdir() if p.name.startswith('.')]


def test_default_cache_reads_legacy_verifier_bundles(tmp_path, monkeypatch):
    # Runtimes prepared before the runtimes/ cache existed stay in verifier-bundles/;
    # the default lookup is the runner's, so release and fetch must find them too.
    workspace = tmp_path / 'workspace'
    key, checksum = make_runtime(workspace / 'verifier-bundles')
    monkeypatch.delenv('OT_RUNTIME_CACHE', raising=False)
    monkeypatch.delenv('OT_VERIFIER_BUNDLE_CACHE', raising=False)
    monkeypatch.setenv('OT_WORKSPACE', str(workspace))
    tables = {'demo_v1': ([task_row('task_1', key, checksum)], [])}
    manifest, artifacts = runtime_release.prepare_release(tables, tmp_path / 'out', 'user/repo')
    assert manifest['missing'] == [] and manifest['artifacts'][key]['archive_sha256'] == checksum
    assert sorted(artifacts) == [f'runtimes/{key}.json', f'runtimes/{key}.tar.gz']
    runtime_release.pin(manifest, REVISION)
    assert runtime_release.fetch(manifest, download=None)[key]['status'] == 'present'
    assert not (workspace / 'runtimes' / f'{key}.tar.gz').exists()
    # An explicit cache never searches another directory.
    missing, _ = runtime_release.prepare_release(tables, tmp_path / 'out2', 'user/repo', cache=workspace / 'runtimes')
    assert [m['id'] for m in missing['missing']] == [key]


def test_fetch_rejects_tampered_remote_bytes(tmp_path):
    key, _, manifest, remote = released(tmp_path)
    (remote.root / f'runtimes/{key}.tar.gz').write_bytes(tar_bytes({'repo/Packages': b'tampered\n'}))
    fresh = tmp_path / 'fresh'
    with pytest.raises(ValueError, match='pinned checksum'):
        runtime_release.fetch(manifest, fresh, download=remote.download)
    assert not (fresh / f'{key}.tar.gz').exists() and not (fresh / f'{key}.json').exists()
    with pytest.raises(ValueError, match='differ'):
        runtime_release.verify_remote(manifest, download=remote.download)


def test_fetch_never_overwrites_conflicting_cache_entry(tmp_path):
    key, _, manifest, remote = released(tmp_path)
    fresh = tmp_path / 'fresh'
    fresh.mkdir()
    (fresh / f'{key}.tar.gz').write_bytes(b'other bytes')
    with pytest.raises(ValueError, match='not overwritten'):
        runtime_release.fetch(manifest, fresh, download=remote.download)
    assert (fresh / f'{key}.tar.gz').read_bytes() == b'other bytes'


def test_verify_remote_accepts_identical_bytes(tmp_path):
    key, _, manifest, remote = released(tmp_path)
    assert runtime_release.verify_remote(manifest, download=remote.download) == {key: {'revision': REVISION, 'verified': True}}


def test_fetch_requires_pinned_revision(tmp_path):
    cache = tmp_path / 'cache'
    key, checksum = make_runtime(cache)
    tables = {'demo_v1': ([task_row('task_1', key, checksum)], [])}
    manifest, _ = runtime_release.prepare_release(tables, tmp_path / 'out', 'user/repo', cache=cache)
    with pytest.raises(ValueError, match='pinned HF commit'):
        runtime_release.fetch(manifest, tmp_path / 'fresh', download=lambda **_: None)


def test_missing_local_runtime_is_reported(tmp_path):
    cache = tmp_path / 'cache'
    key, checksum = make_runtime(cache)
    (cache / f'{key}.tar.gz').write_bytes(b'corrupt')
    tables = {'demo_v1': ([task_row('task_1', key, checksum)], [])}
    manifest, artifacts = runtime_release.prepare_release(tables, tmp_path / 'out', 'user/repo', cache=cache)
    assert artifacts == {} and manifest['missing'][0]['id'] == key


class FakeApi:
    def __init__(self, files):
        self.files = files

    def repo_info(self, repo_id, repo_type):
        return SimpleNamespace(sha='b' * 40)

    def get_paths_info(self, repo, paths, repo_type, revision, expand):
        return [SimpleNamespace(path=p,
                                lfs=(SimpleNamespace(sha256=self.files[p]) if isinstance(self.files[p], str) else None),
                                blob_id=(hashlib.sha1(f'blob {len(self.files[p])}\0'.encode() + self.files[p]).hexdigest()
                                         if isinstance(self.files[p], bytes) else None))
                for p in paths if p in self.files]


def test_existing_identical_remote_artifact_is_reused_and_conflict_rejected(tmp_path):
    cache = tmp_path / 'cache'
    key, checksum = make_runtime(cache)
    tables = {'demo_v1': ([task_row('task_1', key, checksum)], [])}
    same = FakeApi({f'runtimes/{key}.tar.gz': checksum, f'runtimes/{key}.json': (cache / f'{key}.json').read_bytes()})
    manifest, artifacts = runtime_release.prepare_release(tables, tmp_path / 'out', 'user/repo', cache=cache, api=same)
    assert artifacts == {}
    assert manifest['artifacts'][key]['revision'] == 'b' * 40 and not manifest['artifacts'][key]['uploaded']
    other = FakeApi({f'runtimes/{key}.tar.gz': 'c' * 64, f'runtimes/{key}.json': None})
    with pytest.raises(ValueError, match='never overwrite'):
        runtime_release.prepare_release(tables, tmp_path / 'out2', 'user/repo', cache=cache, api=other)


@pytest.mark.parametrize('sidecar', [b'conflicting sidecar', None])
def test_partial_remote_sidecar_conflict_never_overwritten(tmp_path, sidecar):
    cache = tmp_path / 'cache'
    key, checksum = make_runtime(cache)
    tables = {'demo_v1': ([task_row('task_1', key, checksum)], [])}
    with pytest.raises(ValueError, match='never overwrite'):
        runtime_release.prepare_release(tables, tmp_path / 'out', 'user/repo', cache=cache,
                                        api=FakeApi({f'runtimes/{key}.json': sidecar}))
    assert not (tmp_path / 'out/runtimes').exists()


def test_partial_identical_remote_sidecar_only_uploads_archive(tmp_path):
    cache = tmp_path / 'cache'
    key, checksum = make_runtime(cache)
    tables = {'demo_v1': ([task_row('task_1', key, checksum)], [])}
    manifest, artifacts = runtime_release.prepare_release(
        tables, tmp_path / 'out', 'user/repo', cache=cache,
        api=FakeApi({f'runtimes/{key}.json': (cache / f'{key}.json').read_bytes()}))
    assert set(artifacts) == {f'runtimes/{key}.tar.gz'}
    assert manifest['source_revision'] == 'b' * 40


def test_invalid_task_reference_is_rejected():
    row = {'path': 't', 'task_binary': tar_bytes({'setup_files/' + TASK_OFFLINE_REFERENCE:
                                                  json.dumps({'format': OFFLINE_FORMAT, 'id': '0' * 64}).encode()})}
    with pytest.raises(ValueError, match='unpinned'):
        runtime_release.collect([row])


def test_materialized_preflight_fetches_exact_reference(tmp_path, monkeypatch):
    key, checksum, manifest, remote = released(tmp_path)
    task = tmp_path / 'task'
    (task / 'setup_files').mkdir(parents=True)
    ref = task / 'setup_files' / TASK_OFFLINE_REFERENCE
    ref.write_text(json.dumps({'format': OFFLINE_FORMAT, 'id': key, 'archive_sha256': checksum}))
    release_path = tmp_path / 'release.json'
    release_path.write_text(json.dumps(manifest))
    monkeypatch.setenv('OT_RUNTIME_RELEASE_MANIFEST', str(release_path))
    import huggingface_hub
    monkeypatch.setattr(huggingface_hub, 'hf_hub_download', remote.download)
    fresh = tmp_path / 'fresh'
    assert preflight(iter([task]), fresh)[key]['sha256'] == checksum
    assert len(remote.calls) == 2
    remote.calls.clear()
    ref.write_text(json.dumps({'format': OFFLINE_FORMAT, 'id': key, 'archive_sha256': 'f' * 64}))
    with pytest.raises(ValueError, match='does not match task reference'):
        preflight([task], fresh)
    assert not remote.calls


def test_materialized_fetch_does_not_download_unreferenced_artifacts(tmp_path):
    key, checksum, manifest, remote = released(tmp_path)
    task = tmp_path / 'empty-task'
    task.mkdir()
    assert runtime_release.fetch_for_tasks([task], manifest, tmp_path / 'fresh', remote.download) == {}
    assert remote.calls == []
