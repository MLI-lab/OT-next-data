"""Versioned HF image bundles, separate from task payloads and pinned by commit."""
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import platform
import re
import tarfile
import tempfile

from hpc.image_cache import copy_image, digest, restore, bundle_manifest, bundle_directory, bundle_name


def reference_path(tasks_root):
    root = Path(tasks_root)
    return root.parent / ('.' + root.name + '.images.json')


def validate_bundle(name, manifest, directory=None):
    if Path(name).name != name or not name.endswith('.sif'):
        raise ValueError('Invalid image bundle name')
    files = manifest['files']
    sidecar = Path(name).with_suffix('.deferred.json').name
    overlay = Path(name).with_suffix('.overlay.img').name
    if set(files) not in ({name}, {name, sidecar, overlay}):
        raise ValueError('Incomplete or unexpected image bundle members')
    for member, record in files.items():
        if not re.fullmatch('[0-9a-f]{64}', record['sha256']) or record['bytes'] < 0:
            raise ValueError('Invalid artifact checksum or size')
        if directory is not None:
            source = directory / member
            if source.is_symlink() or not source.is_file() or source.stat().st_size != record['bytes'] or digest(source) != record['sha256']:
                raise ValueError(f'Corrupt image bundle: {source}')


def prepare_release(tables, cache, out, repo):
    """Export only build-time bundles matching validated task environment contents.

    Legacy caches lack pristine-build provenance and are deliberately omitted.
    Returns small manifest plus explicit upload paths; never scans live instances.
    """
    from validation.contract import files_digest
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    manifest = {'version': 1, 'repo_id': repo, 'revision': None,
                'bundles': {}, 'tasks': {}, 'missing': []}
    artifacts = {}
    if cache is None:
        manifest['cache_status'] = 'not_configured'
        return manifest, artifacts
    from harbor_patches.image_context import environment_dir_hash
    candidates = {}
    if cache:
        for directory in sorted([*(Path(cache) / 'bundles-v1').glob('*.sif'), *(Path(cache) / 'bundles-v1').glob('*.sif.tar')]):
            if directory.is_symlink():
                continue
            entry = bundle_manifest(directory)
            provenance = entry.get('provenance') or {}
            if (provenance.get('pristine') is True and provenance.get('builder') == 'hpc.image_cache'
                    and provenance.get('format') == 'apptainer-sif'):
                candidates.setdefault(provenance['environment_sha256'], (directory, entry))
    if candidates and out.resolve().is_relative_to('/home'):
        raise ValueError('Image release files must be outside /home; set --out to a cluster workspace')
    for kept, _ in tables.values():
        for row in kept:
            if '3' not in row['stages_passed'].split(','):
                continue
            with tempfile.TemporaryDirectory(prefix='.image-task-', dir=out) as temporary:
                task = Path(temporary)
                with tarfile.open(fileobj=io.BytesIO(row['task_binary']), mode='r:*') as archive:
                    members = archive.getmembers()
                    for member in members:
                        path = PurePosixPath(member.name)
                        if path.is_absolute() or '..' in path.parts or not (member.isfile() or member.isdir()):
                            raise ValueError(f'Unsafe task archive member: {member.name}')
                    contents = [(str(PurePosixPath(m.name)), archive.extractfile(m).read()) for m in members if m.isfile()]
                    if files_digest(contents) != row['content_sha256']:
                        raise ValueError(f"Task changed since validation: {row['path']}")
                    archive.extractall(task, members=members, filter='data')
                environments = []
                for dockerfile in sorted(task.rglob('Dockerfile')):
                    env_hash = environment_dir_hash(dockerfile.parent)
                    candidate = candidates.get(env_hash)
                    if candidate is None:
                        manifest['missing'].append({'task': row['path'], 'environment_sha256': env_hash})
                        continue
                    directory, entry = candidate
                    bundle_id = hashlib.sha256(json.dumps(entry, sort_keys=True).encode()).hexdigest()
                    if bundle_id not in manifest['bundles']:
                        with bundle_directory(directory, out) as unpacked:
                            validate_bundle(bundle_name(directory), entry, unpacked)
                            prefix = f'images/bundles-v1/{bundle_id}/{bundle_name(directory)}'
                            # Upload immutable copies, never mutable build/trial files.
                            staged = out / prefix
                            staged.mkdir(parents=True, exist_ok=True)
                            for name, info in entry['files'].items():
                                copy_image(unpacked / name, staged / name)
                                if digest(staged / name) != info['sha256']:
                                    raise ValueError('Image changed during release preparation')
                                artifacts[f'{prefix}/{name}'] = staged / name
                            manifest['bundles'][bundle_id] = dict(entry, name=bundle_name(directory), prefix=prefix)
                    environments.append({'path': dockerfile.parent.relative_to(task).as_posix(),
                                         'environment_sha256': env_hash, 'bundle': bundle_id})
                if environments:
                    manifest['tasks'][row['path']] = {'content_sha256': row['content_sha256'], 'environments': environments}
    return manifest, artifacts


def stage_published(tasks, cache, target, download=None, *, selected_keys=None):
    """Restore published references carried from Parquet by materialize()."""
    from harbor_patches.image_context import environment_dir_hash
    from validation.contract import task_digest
    if download is None:
        from huggingface_hub import hf_hub_download
        download = hf_hub_download
    references, done, records = {}, set(), []
    for task in tasks:
        sidecar = reference_path(task.parent)
        if sidecar not in references:
            references[sidecar] = json.loads(sidecar.read_text()) if sidecar.is_file() else {}
        manifest = references[sidecar].get(task.name)
        if not manifest:
            continue
        if manifest['version'] != 1 or not re.fullmatch('[0-9a-f]{40}', manifest.get('revision') or ''):
            raise ValueError('Published images require a pinned HF commit revision')
        entry = manifest['tasks'][task.name]
        if task_digest(task) != entry['content_sha256']:
            continue  # Edited tasks need their own build, never a stale image.
        for environment in entry['environments']:
            relative = PurePosixPath(environment['path'])
            if relative.is_absolute() or '..' in relative.parts:
                raise ValueError('Unsafe environment path')
            env_hash = environment_dir_hash(task / str(relative))
            if selected_keys is not None and env_hash[:12] not in selected_keys:
                continue
            if env_hash != environment['environment_sha256']:
                raise ValueError('Published environment hash mismatch')
            bundle = manifest['bundles'][environment['bundle']]
            provenance = bundle['provenance']
            if provenance['environment_sha256'] != env_hash or provenance.get('pristine') is not True:
                raise ValueError('Published image provenance mismatch')
            if provenance['architecture'] != platform.machine() or provenance['format'] != 'apptainer-sif':
                raise ValueError('Published image is incompatible with this runtime architecture')
            validate_bundle(bundle['name'], bundle)
            if not bundle['name'].endswith('-' + env_hash[:12] + '.sif'):
                raise ValueError('Published image cache key mismatch')
            prefix = f"images/bundles-v1/{environment['bundle']}/{bundle['name']}"
            if bundle['prefix'] != prefix or not re.fullmatch('[0-9a-f]{64}', environment['bundle']):
                raise ValueError('Unsafe artifact path')
            identity = (manifest['repo_id'], manifest['revision'], prefix)
            if identity in done:
                continue
            target = Path(target)
            target.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix='.hf-image-', dir=target) as temporary:
                directory = Path(temporary) / bundle['name']
                directory.mkdir()
                for name in bundle['files']:
                    downloaded = download(repo_id=manifest['repo_id'], repo_type='dataset',
                        revision=manifest['revision'], filename=f'{prefix}/{name}',
                        cache_dir=str(Path(cache) / 'hf-hub'))
                    copy_image(downloaded, directory / name)
                (directory / 'manifest.json').write_text(json.dumps(bundle))
                local = restore(directory, target)
            done.add(identity)
            records.append({'source': prefix, 'revision': manifest['revision'],
                            'local': str(local), 'verified_bundle': True})
    return records


def copy_references(tasks, destination):
    references = {}
    for task in tasks:
        path = reference_path(task.parent)
        if path.is_file():
            data = json.loads(path.read_text())
            if task.name in data:
                references[task.name] = data[task.name]
    if references:
        reference_path(destination).write_text(json.dumps(references))
