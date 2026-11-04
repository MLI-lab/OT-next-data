"""Checksum-bound SquashFS bases for private agent and verifier overlays.

The image contains only the already prepared runtime, never tests or solutions.
Apptainer mounts it read-only at instance startup; only this individual artifact,
not the cache directory or its writable host backing file, is exposed.
"""
from contextvars import ContextVar
from functools import wraps
import json
import os
from pathlib import Path
import re
import tempfile
import subprocess
import tarfile

FORMAT = 'ot-runtime-squashfs-v2'
MOUNT = '/opt/ot-verifier-images/'
BASE = '/opt/ot-runtime-bases/'
_selected = ContextVar('ot_verifier_images', default=None)


def enabled():
    if any(os.environ.get(name, '1') != '1' for name in ('OT_RUNTIME_IMAGES', 'OT_VERIFIER_RUNTIME_IMAGES')):
        raise ValueError('Archive runtime mode has been removed; prepare SquashFS images')
    return True


def root():
    from harbor_patches.verifier_bundle import cache_root
    value = os.environ.get('OT_RUNTIME_IMAGE_CACHE') or os.environ.get('OT_VERIFIER_IMAGE_CACHE')
    return Path(value) if value else cache_root()


def identity(ref):
    for key in ('id', 'archive_sha256'):
        if not isinstance(ref.get(key), str) or not re.fullmatch('[a-f0-9]{64}', ref[key]):
            raise ValueError('Invalid runtime image identity')


def load(ref, directory=None):
    from harbor_patches.verifier_bundle import digest
    identity(ref)
    directory = Path(directory) if directory is not None else root()
    image = directory / (ref['id'] + '.squashfs')
    manifest = directory / (ref['id'] + '.squashfs.json')
    if image.is_symlink() or manifest.is_symlink() or not image.is_file():
        raise ValueError('Runtime image must be a prepared regular file: ' + str(image))
    record = json.loads(manifest.read_text())
    if (record.get('image_format') != FORMAT or record.get('id') != ref['id'] or
            record.get('archive_sha256') != ref['archive_sha256'] or
            record.get('image_sha256') != digest(image)):
        raise ValueError('Runtime image identity/checksum differs: ' + str(image))
    if ref.get('image_sha256') and ref['image_sha256'] != record['image_sha256']:
        raise ValueError('Runtime reference image checksum differs')
    if record.get('image_format') == FORMAT:
        from harbor_patches.verifier_bundle import spec_id
        if (record.get('image_root') != BASE + ref['id'] or record.get('status') != 'ready'
                or spec_id(record.get('spec')) != ref['id']):
            raise ValueError('Invalid runtime image provenance/layout')
        if ref.get('format') and ref['format'] != record['source_format']:
            raise ValueError('Runtime image kind differs from task reference')
    return dict(record, path=str(image.resolve()), mount=MOUNT + ref['id'])


def build(ref, cache, output=None, *, processors=2):
    """Build separately from validation. Existing identities are never overwritten."""
    from harbor_patches.verifier_bundle import lookup, digest, FORMAT as PYTHON_FORMAT, OFFLINE_FORMAT, TOOLCHAIN_FORMAT, validate_archive, validate_offline_archive, validate_toolchain_archive
    import fcntl
    identity(ref)
    cache = Path(cache)
    output = Path(output) if output is not None else cache
    output.mkdir(parents=True, exist_ok=True)
    target = output / (ref['id'] + '.squashfs')
    manifest = output / (ref['id'] + '.squashfs.json')
    with (output / (ref['id'] + '.squashfs.lock')).open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if target.exists() or manifest.exists():
            return load(ref, output)
        source_manifest = json.loads((cache / (ref['id'] + '.json')).read_text())
        kind = source_manifest['format']
        archive, reason = lookup(ref, kind, {PYTHON_FORMAT: validate_archive,
            OFFLINE_FORMAT: validate_offline_archive, TOOLCHAIN_FORMAT: validate_toolchain_archive}[kind], cache, preparation=True)
        if archive is None:
            raise ValueError(reason)
        if archive.suffix == '.squashfs':
            raise ValueError('Copy the existing image and manifest together instead of rebuilding')
        # Disk-backed preparation workspace beside the final artifact, not a
        # measured task's TMPDIR. TemporaryDirectory removes only this build.
        with tempfile.TemporaryDirectory(prefix='.runtime-image-', dir=output) as temporary:
            temporary = Path(temporary)
            tree = temporary / 'tree'
            tree.mkdir()
            artifact = tree / (BASE + ref['id']).lstrip('/')
            artifact.mkdir(parents=True)
            with tarfile.open(archive) as tar:
                tar.extractall(artifact, filter='data')
            # The immutable image protects these bytes. Writable container/verification
            # overlays must allow the task user to install or change packages.
            for path in artifact.rglob('*'):
                if not path.is_symlink():
                    path.chmod(path.stat().st_mode | (0o777 if path.is_dir() else 0o666))
            for path in [tree, *tree.rglob('*')]:
                if path.is_dir() and not path.is_symlink():
                    os.utime(path, (0, 0))
            if digest(archive) != ref['archive_sha256']:
                raise ValueError('Runtime archive changed during image preparation')
            image = temporary / 'runtime.squashfs'
            subprocess.run(['mksquashfs', str(tree), str(image), '-noappend', '-all-root',
                            '-comp', 'zstd', '-processors', str(processors), '-no-progress',
                            '-mkfs-time', '0', '-no-xattrs'], check=True)
            subprocess.run(['unsquashfs', '-s', str(image)], check=True, capture_output=True)
            record = dict(source_manifest, image_format=FORMAT, storage='squashfs',
                          image_sha256=digest(image), image_bytes=image.stat().st_size,
                          tools=source_manifest['spec'].get('tools', []), source_format=kind,
                          image_root=BASE + ref['id'])
            image.chmod(0o444)
            image.rename(target)
            staged_manifest = temporary / 'manifest.json'
            staged_manifest.write_text(json.dumps(record, indent=2) + '\n')
            staged_manifest.rename(manifest)
    return load(ref, output)


def references(context, *, agent_only=False):
    from harbor_patches.verifier_bundle import reference, REFERENCE, FORMAT as PYTHON_FORMAT
    from validation.publishing.runtime_release import KINDS, _context_of
    context = Path(context)
    # Agent environments need the runtimes of all their shared verifier steps.
    task = context.parent if context.name == 'environment' else None
    if task is None:
        return [reference(p.parent, p.name, KINDS[p.name]) for name in KINDS
                for p in sorted(context.rglob(name))]
    refs = []
    for name, kind in KINDS.items():
        for p in sorted(task.rglob(name)):
            if _context_of(p.relative_to(task).as_posix()) is not None and (not agent_only or p.parent == task / 'setup_files'):
                ref = json.loads(p.read_text())
                ref['agent_runtime'] = p.parent.name == 'setup_files' and ref.get('format') == PYTHON_FORMAT
                refs.append(ref)
    return refs


def install_client(bridge):
    cls = bridge.ApptainerEnvironment
    if getattr(cls, '_ot_runtime_images', False):
        return
    original_start, original_post = cls.start, bridge._async_http_post

    @wraps(original_start)
    async def start(self, *args, **kwargs):
        records = []
        if enabled():
            for ref in references(self.environment_dir, agent_only=getattr(self, '_ot_git_checks_only', False)):
                record = dict(load(ref), agent_runtime=ref.get('agent_runtime', False))
                previous = next((r for r in records if r['id'] == record['id']), None)
                if previous is None:
                    records.append(record)
                else:
                    previous['agent_runtime'] |= record['agent_runtime']
        token = _selected.set(records)
        try:
            result = await original_start(self, *args, **kwargs)
            self._verifier_runtime_images = {r['id']: r for r in records}
            return result
        finally:
            _selected.reset(token)

    async def post(url, data, *args, **kwargs):
        if url.endswith('/env/create') and _selected.get():
            data = dict(data, task_env_config=dict(data.get('task_env_config', {}),
                verifier_runtime_images=[{k: r[k] for k in ('id', 'archive_sha256', 'image_sha256', 'agent_runtime')}
                                         for r in _selected.get()]))
        return await original_post(url, data, *args, **kwargs)

    cls.start = start
    bridge._async_http_post = post
    cls._ot_runtime_images = True


def start_flags(payload):
    refs = (payload.get('task_env_config') or {}).get('verifier_runtime_images', [])
    if refs and not enabled():
        raise RuntimeError('Worker runtime image support is not enabled')
    flags = []
    for ref in refs:
        record = load(ref)
        if record['image_sha256'] != ref.get('image_sha256'):
            raise ValueError('Controller and worker runtime images differ')
        if any(c in record['path'] for c in ':,\n'):
            raise ValueError('Runtime image path contains mount separators')
        source = record['image_root']
        flags += ['--bind', f"{record['path']}:{record['mount']}:ro,image-src={source}"]
        if ref.get('agent_runtime'):
            if not record.get('image_root'):
                raise RuntimeError('Agent runtime requires a v2 runtime image; prepare it in a new cache')
            flags += ['--overlay', record['path'] + ':ro']
    return flags


def mounted(environment, ref):
    record = getattr(environment, '_verifier_runtime_images', {}).get(ref['id'])
    if not record or record['archive_sha256'] != ref['archive_sha256']:
        raise RuntimeError('Required runtime image was not mounted at container startup')
    return record


def source_manifest(path):
    path = Path(path)
    suffix = '.squashfs.json' if path.suffix == '.squashfs' else '.json'
    key = path.name.split('.')[0]
    return json.loads((path.parent / (key + suffix)).read_text())


def finalize(record, cache):
    """Preparation publishes a mountable image; keep old archives as local inputs."""
    enabled()
    image = build(record, cache)
    if image.get('image_format') != FORMAT:
        raise ValueError('Legacy pilot image: prepare the v2 image in a new cache; existing images are immutable')
    return {k: v for k, v in image.items() if k not in ('path', 'mount')}


def preflight(tasks, *, agent_only=False):
    checked = {}
    for task in tasks:
        for ref in references(Path(task) / 'environment', agent_only=agent_only):
            record = load(ref)
            if ref.get('agent_runtime') and not record.get('image_root'):
                raise ValueError('Agent runtime requires a v2 image')
            checked[ref['id']] = {k: record[k] for k in ('path', 'image_sha256', 'archive_sha256')}
    return checked


def main():
    import argparse
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('reference', type=Path)
    p.add_argument('--cache', required=True, type=Path)
    p.add_argument('--output', type=Path)
    p.add_argument('--processors', type=int, default=2)
    args = p.parse_args()
    print(json.dumps(build(json.loads(args.reference.read_text()), args.cache, args.output,
                           processors=args.processors), indent=2))


if __name__ == '__main__':
    main()
