"""Prepared task/verifier runtimes on HF under the shared `runtimes/` folder, pinned by commit.

Task references (`tests/ot-verifier-runtime.json`, `tests/ot-verifier-offline.json`,
`setup_files/ot-task-runtime.json`, `setup_files/ot-task-offline.json`) carry only an ID and
archive checksum. A per-run manifest `runtimes/manifests/<run>.json` records where each
referenced artifact was published (repository and commit). `fetch` restores those artifacts
into an empty or existing runtime cache and verifies every byte against the references.
An artifact identity is never overwritten with different bytes, locally or remotely.
"""
import argparse
import hashlib
import io
import json
import os
import re
import shutil
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath

from harbor_patches.verifier_bundle import (FORMAT, OFFLINE_FORMAT, OFFLINE_REFERENCE, REFERENCE, TASK_OFFLINE_REFERENCE,
                                            TOOLCHAIN_FORMAT, TOOLCHAIN_REFERENCE, validate_toolchain_archive,
                                            TASK_REFERENCE, cache_root, digest, spec_id, validate_archive,
                                            validate_offline_archive, verify)

FOLDER = 'runtimes'
KINDS = {REFERENCE: FORMAT, OFFLINE_REFERENCE: OFFLINE_FORMAT, TASK_REFERENCE: FORMAT, TASK_OFFLINE_REFERENCE: OFFLINE_FORMAT,
         TOOLCHAIN_REFERENCE: TOOLCHAIN_FORMAT}
_HEX = re.compile('[0-9a-f]{64}')


def _context_of(name):
    path = PurePosixPath(name)
    parts = path.parts
    if path.name not in KINDS or '..' in parts or path.is_absolute():
        return None
    parent = '/'.join(parts[:-1])
    tests = path.name in (REFERENCE, OFFLINE_REFERENCE, TOOLCHAIN_REFERENCE)
    if (tests and (parent == 'tests' or re.fullmatch(r'steps/[^/]+/tests', parent))) or (not tests and parent == 'setup_files'):
        return parent
    return None


def task_references(binary):
    """[(format, id, archive_sha256)] referenced by one task_binary archive."""
    found = []
    with tarfile.open(fileobj=io.BytesIO(binary), mode='r:*') as archive:
        for member in archive:
            name = member.name[2:] if member.name.startswith('./') else member.name
            if member.isfile() and _context_of(name) is not None:
                data = json.load(archive.extractfile(member))
                fmt = KINDS[PurePosixPath(name).name]
                if (data.get('format') != fmt or not _HEX.fullmatch(data.get('id', ''))
                        or not _HEX.fullmatch(data.get('archive_sha256') or '')):
                    raise ValueError(f'Invalid or unpinned runtime reference: {name}')
                found.append((fmt, data['id'], data['archive_sha256']))
    return found


def _validate(archive, fmt):
    {FORMAT: validate_archive, OFFLINE_FORMAT: validate_offline_archive,
     TOOLCHAIN_FORMAT: validate_toolchain_archive}[fmt](archive)


def _check_manifest(data, key, fmt, checksum):
    if (data.get('format') != fmt or data.get('id') != key or spec_id(data.get('spec')) != key
            or data.get('archive_sha256') != checksum or data.get('status') != 'ready'):
        raise ValueError(f'Runtime manifest does not match its reference: {key}')


def collect(rows):
    """{id: {format, archive_sha256, tasks}} for rows with path/task_binary."""
    artifacts = {}
    for row in rows:
        for fmt, key, checksum in task_references(row['task_binary']):
            entry = artifacts.setdefault(key, {'format': fmt, 'archive_sha256': checksum, 'tasks': []})
            if entry['format'] != fmt or entry['archive_sha256'] != checksum:
                raise ValueError(f'Conflicting runtime references for {key}')
            if row['path'] not in entry['tasks']:
                entry['tasks'].append(row['path'])
    return artifacts


def remote_state(api, repo, names):
    """{path: content identity} at an immutable snapshot of the main branch.

    LFS uses SHA256; regular Git files use their blob SHA1 including the header.
    """
    if not names:
        return {}, None
    info = api.repo_info(repo_id=repo, repo_type='dataset')
    found = {}
    names = sorted(names)
    for start in range(0, len(names), 100):
        for item in api.get_paths_info(repo, names[start:start + 100], repo_type='dataset', revision=info.sha, expand=True):
            lfs = getattr(item, 'lfs', None)
            found[item.path] = (lfs.sha256 if lfs is not None
                                else 'git-sha1:' + (getattr(item, 'blob_id', None) or 'unknown'))
    return found, info.sha


def prepare_release(tables, out, repo, *, cache=None, api=None):
    """Stage runtime artifacts referenced by kept tasks; return (manifest, upload artifacts).

    Artifacts already on the repository's main branch with identical bytes are reused
    (pinned to that commit) and not uploaded again; conflicting remote bytes are an error.
    """
    # Without an explicit cache, use the runner's own lookup, which also reads the
    # legacy verifier-bundles directory next to the default runtimes/ cache.
    cache = Path(cache) if cache is not None else None
    rows = [row for _, (kept, _) in sorted(tables.items()) for row in kept]
    referenced = collect(rows)
    manifest = {'version': 1, 'repo_id': repo, 'folder': FOLDER, 'artifacts': {}, 'missing': []}
    artifacts, local = {}, {}
    for key, entry in sorted(referenced.items()):
        try:
            archive = verify({'id': key, 'archive_sha256': entry['archive_sha256']}, cache, entry['format'])
            _validate(archive, entry['format'])
        except (OSError, ValueError, tarfile.TarError) as error:
            manifest['missing'].append({'id': key, 'tasks': entry['tasks'], 'reason': str(error)})
            continue
        local[key] = (archive, archive.with_suffix('').with_suffix('.json'))
    if manifest['missing']:
        return manifest, artifacts
    names = {f'{FOLDER}/{key}.{suffix}': key for key in local for suffix in ('tar.gz', 'json')}
    remote, main = ({}, None)
    if api is not None and repo:
        remote, main = remote_state(api, repo, names)
    manifest['source_revision'] = main
    out = Path(out)
    for key, (archive, sidecar) in sorted(local.items()):
        entry = referenced[key]
        record = {'format': entry['format'], 'archive_sha256': entry['archive_sha256'], 'bytes': archive.stat().st_size,
                  'manifest_sha256': digest(sidecar), 'archive': f'{FOLDER}/{key}.tar.gz',
                  'manifest': f'{FOLDER}/{key}.json', 'tasks': len(entry['tasks'])}
        sources = ((archive, record['archive']), (sidecar, record['manifest']))
        for source, name in sources:
            if name not in remote:
                continue
            identity = remote[name]
            if identity.startswith('git-sha1:'):
                hashed = hashlib.sha1(f'blob {source.stat().st_size}\0'.encode())
                with source.open('rb') as stream:
                    for block in iter(lambda: stream.read(8 * 1024 * 1024), b''):
                        hashed.update(block)
                expected = 'git-sha1:' + hashed.hexdigest()
            else:
                expected = digest(source)
            if identity != expected:
                raise ValueError(f'Remote runtime {name} has different or unverifiable bytes; '
                                 'never overwrite an artifact identity')
        missing = [(source, name) for source, name in sources if name not in remote]
        if not missing:
            record['revision'] = main
            record['uploaded'] = False
        else:
            staged = out / FOLDER
            staged.mkdir(parents=True, exist_ok=True)
            for source, name in missing:
                target = out / name
                shutil.copyfile(source, target)
                if digest(target) != digest(source):
                    raise ValueError(f'Runtime {key} changed during release preparation')
                artifacts[name] = target
            record['revision'] = None
            record['uploaded'] = True
        manifest['artifacts'][key] = record
    manifest['tasks'] = {row['path']: sorted({key for _, key, _ in task_references(row['task_binary'])}) for row in rows}
    manifest['tasks'] = {task: keys for task, keys in manifest['tasks'].items() if keys}
    return manifest, artifacts


def pin(manifest, revision):
    for record in manifest['artifacts'].values():
        if record['revision'] is None:
            record['revision'] = revision
    return manifest


def _download(manifest, record, name, directory, download):
    if download is None:
        from huggingface_hub import hf_hub_download
        download = hf_hub_download
    if not re.fullmatch('[0-9a-f]{40}', record.get('revision') or ''):
        raise ValueError('Published runtimes require a pinned HF commit revision')
    return Path(download(repo_id=manifest['repo_id'], repo_type='dataset', revision=record['revision'],
                         filename=name, cache_dir=str(Path(directory) / 'hf-hub')))


def _check_record(key, record):
    if (not _HEX.fullmatch(key) or record['archive'] != f'{FOLDER}/{key}.tar.gz'
            or record['manifest'] != f'{FOLDER}/{key}.json' or record['format'] not in (FORMAT, OFFLINE_FORMAT, TOOLCHAIN_FORMAT)):
        raise ValueError(f'Unsafe runtime record: {key}')


def verify_remote(manifest, download=None, scratch=None):
    """Download every published artifact at its pinned revision and compare all bytes."""
    results = {}
    with tempfile.TemporaryDirectory(prefix='.hf-runtime-verify-', dir=scratch) as temporary:
        for key, record in sorted(manifest['artifacts'].items()):
            _check_record(key, record)
            archive = _download(manifest, record, record['archive'], temporary, download)
            sidecar = _download(manifest, record, record['manifest'], temporary, download)
            if digest(archive) != record['archive_sha256'] or digest(sidecar) != record['manifest_sha256']:
                raise ValueError(f'Published runtime bytes differ from the release: {key}')
            _check_manifest(json.loads(sidecar.read_text()), key, record['format'], record['archive_sha256'])
            _validate(archive, record['format'])
            results[key] = {'revision': record['revision'], 'verified': True}
            for path in (archive, sidecar):
                path.unlink()
    return results


def _install(source, target):
    """Add a verified file to the cache without replacing an existing entry."""
    fd, name = tempfile.mkstemp(prefix=f'.{target.name}.', suffix='.tmp', dir=target.parent)
    os.close(fd)
    temporary = Path(name)
    try:
        shutil.copyfile(source, temporary)
        try:
            os.link(temporary, target)
        except FileExistsError:
            if digest(target) != digest(temporary):
                raise ValueError(f'Cache entry {target} exists with different bytes; not overwritten')
    finally:
        temporary.unlink(missing_ok=True)


def fetch(manifest, cache=None, ids=None, download=None):
    """Restore published runtimes into a cache (default `cache_root()`); returns per-ID records."""
    explicit = cache is not None
    cache = Path(cache) if explicit else cache_root()
    cache.mkdir(parents=True, exist_ok=True)
    wanted = sorted(manifest['artifacts'] if ids is None else ids)
    records = {}
    for key in wanted:
        record = manifest['artifacts'][key]
        _check_record(key, record)
        ref = {'id': key, 'archive_sha256': record['archive_sha256']}
        try:
            archive = verify(ref, cache if explicit else None, record['format'])
            _validate(archive, record['format'])
        except (OSError, ValueError, tarfile.TarError):
            pass
        else:
            records[key] = {'status': 'present', 'archive': str(archive)}
            continue
        with tempfile.TemporaryDirectory(prefix='.hf-runtime-', dir=cache) as temporary:
            archive = _download(manifest, record, record['archive'], temporary, download)
            sidecar = _download(manifest, record, record['manifest'], temporary, download)
            if digest(archive) != record['archive_sha256'] or digest(sidecar) != record['manifest_sha256']:
                raise ValueError(f'Downloaded runtime does not match its pinned checksum: {key}')
            _check_manifest(json.loads(sidecar.read_text()), key, record['format'], record['archive_sha256'])
            _validate(archive, record['format'])
            # Archive first: an entry is only visible once its manifest exists.
            _install(archive, cache / f'{key}.tar.gz')
            _install(sidecar, cache / f'{key}.json')
        verify(ref, cache, record['format'])
        records[key] = {'status': 'downloaded', 'revision': record['revision'], 'archive': str(cache / f'{key}.tar.gz')}
    return records


def task_ids(parquet_paths):
    import pyarrow.parquet as pq
    ids = set()
    for path in parquet_paths:
        for batch in pq.ParquetFile(path).iter_batches(batch_size=64, columns=['task_binary']):
            for binary in batch.column('task_binary').to_pylist():
                ids.update(key for _, key, _ in task_references(binary))
    return ids


def fetch_for_tasks(tasks, manifest, cache=None, download=None):
    """Fetch only exact references from materialized tasks, before isolated execution."""
    wanted = {}
    for task in tasks:
        task = Path(task)
        contexts = [task / 'setup_files', task / 'tests', *task.glob('steps/*/tests')]
        for context in contexts:
            for name, fmt in KINDS.items():
                path = context / name
                if not path.is_file() or _context_of(path.relative_to(task).as_posix()) is None:
                    continue
                ref = json.loads(path.read_text())
                key = ref.get('id')
                if not isinstance(key, str) or not _HEX.fullmatch(key):
                    raise ValueError(f'Invalid runtime reference: {path}')
                record = manifest['artifacts'].get(key)
                if (record is None or ref.get('format') != fmt or record['format'] != fmt
                        or ref.get('archive_sha256') != record['archive_sha256']):
                    raise ValueError(f'Runtime release does not match task reference: {path}')
                _check_record(key, record)
                wanted[key] = record
    return fetch(manifest, cache, ids=wanted, download=download)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest='command', required=True)
    f = sub.add_parser('fetch', help='restore published runtimes into a runtime cache')
    f.add_argument('manifest', help='local runtimes manifest JSON, or REPO@REVISION:runtimes/manifests/<run>.json')
    f.add_argument('--cache', type=Path, help='default: $OT_RUNTIME_CACHE or $OT_WORKSPACE/runtimes')
    f.add_argument('--tasks', type=Path, nargs='*', help='only runtimes referenced by these task Parquets')
    v = sub.add_parser('verify-remote', help='download and compare every artifact of a published manifest')
    v.add_argument('manifest')
    a = ap.parse_args(argv)
    manifest = load_manifest(a.manifest, cache=getattr(a, 'cache', None))
    if a.command == 'verify-remote':
        result = verify_remote(manifest)
    else:
        ids = None
        if a.tasks:
            ids = task_ids(a.tasks)
            unknown = sorted(ids - set(manifest['artifacts']))
            if unknown:
                raise SystemExit(f'{len(unknown)} referenced runtimes are not in the manifest, e.g. {unknown[:3]}')
        result = fetch(manifest, a.cache, ids)
    print(json.dumps(result, indent=1))


def load_manifest(value, cache=None):
    match = re.fullmatch(r'([^@:]+/[^@:]+)@([0-9a-f]{40}):(runtimes/manifests/[^/]+\.json)', value)
    if match:
        from huggingface_hub import hf_hub_download
        directory = Path(cache) if cache is not None else cache_root()
        value = hf_hub_download(repo_id=match[1], repo_type='dataset', revision=match[2], filename=match[3],
                                cache_dir=str(directory / 'hf-hub'))
    manifest = json.loads(Path(value).read_text())
    if manifest.get('version') != 1 or manifest.get('folder') != FOLDER:
        raise ValueError('Not a runtime release manifest')
    return manifest


if __name__ == '__main__':
    sys.exit(main())
