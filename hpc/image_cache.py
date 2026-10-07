"""Prepare immutable task image bundles before starting timed task trials."""
import argparse
from contextlib import contextmanager
import tarfile
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
import platform
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def copy_image(source, target):
    # Deferred ext3 overlays are sparse; preserve holes on shared storage.
    subprocess.run(['cp', '--sparse=always', '--reflink=auto', '--', str(source), str(target)], check=True)


def publish(image, cache, provenance=None, *, packed=False):
    """Publish the SIF and deferred overlay atomically; never publish live instances."""
    image, cache = Path(image), Path(cache) / 'bundles-v1'
    cache.mkdir(parents=True, exist_ok=True)
    files = [image]
    sidecar = image.with_suffix('.deferred.json')
    if sidecar.exists():
        overlay = image.with_suffix('.overlay.img')
        if not overlay.is_file():
            raise ValueError(f'Incomplete deferred image: {image}')
        files += [sidecar, overlay]
    destination = cache / (image.name + ('.tar' if packed else ''))
    temporary = Path(tempfile.mkdtemp(prefix='.pending-', dir=cache))
    try:
        records = {}
        for source in files:
            target = temporary / source.name
            copy_image(source, target)
            records[source.name] = {'bytes': target.stat().st_size, 'sha256': digest(target)}
        (temporary / 'manifest.json').write_text(json.dumps({'files': records, 'provenance': provenance}, indent=2))
        try:
            if packed:
                archive = temporary / 'bundle.tar'
                subprocess.run(['tar', '--sparse', '--format=pax', '-cf', str(archive), '-C', str(temporary),
                                'manifest.json', *records], check=True)
                try:
                    os.link(archive, destination)
                except FileExistsError:
                    pass
            else:
                temporary.rename(destination)
        except OSError:
            if not destination.is_dir():
                raise
            # Another successful builder won. Never replace a published bundle.
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return destination



def bundle_name(bundle):
    return Path(bundle).name.removesuffix('.tar')


def bundle_manifest(bundle):
    bundle = Path(bundle)
    if bundle.is_dir():
        return json.loads((bundle / 'manifest.json').read_text())
    with tarfile.open(bundle) as archive:
        return json.load(archive.extractfile('manifest.json'))


@contextmanager
def bundle_directory(bundle, scratch):
    bundle = Path(bundle)
    if bundle.is_dir():
        yield bundle
        return
    with tempfile.TemporaryDirectory(prefix='.unpack-image-', dir=scratch) as temporary:
        directory = Path(temporary) / bundle_name(bundle)
        directory.mkdir()
        with tarfile.open(bundle) as archive:
            members = archive.getmembers()
            names = [m.name for m in members]
            if len(set(names)) != len(names) or any(Path(m.name).name != m.name or not m.isfile() for m in members):
                raise ValueError('Unsafe packed image bundle')
            manifest = json.load(archive.extractfile('manifest.json'))
            if set(names) != {'manifest.json', *manifest['files']}:
                raise ValueError('Unexpected packed image members')
            archive.extractall(directory, members=members, filter='data')
        yield directory

def restore(bundle, target):
    """Check a complete immutable bundle before making its image visible locally."""
    bundle, target = Path(bundle), Path(target)
    if not bundle.is_dir():
        target.mkdir(parents=True, exist_ok=True)
        with bundle_directory(bundle, target) as directory:
            return restore(directory, target)
    manifest = bundle_manifest(bundle)
    records = manifest['files']
    if bundle.name not in records or not bundle.name.endswith('.sif'):
        raise ValueError(f'Invalid image bundle: {bundle}')
    sidecar = Path(bundle.name).with_suffix('.deferred.json').name
    overlay = Path(bundle.name).with_suffix('.overlay.img').name
    if sidecar in records and overlay not in records:
        raise ValueError(f'Incomplete deferred bundle: {bundle}')
    target.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.restore-', dir=target) as temporary:
        temporary = Path(temporary)
        for name, record in records.items():
            if Path(name).name != name or (bundle / name).is_symlink():
                raise ValueError(f'Invalid bundle member: {name}')
            local = temporary / name
            copy_image(bundle / name, local)
            if local.stat().st_size != record['bytes'] or digest(local) != record['sha256']:
                raise ValueError(f'Corrupt cached image member: {bundle / name}')
        # SIF last: a visible SIF always has all its sidecars.
        for name in sorted(records, key=lambda name: name.endswith('.sif')):
            (temporary / name).replace(target / name)
    return target / bundle.name


def prepare_images(tasks, images, shared, args):
    """Run outside task timers; independent srun steps enforce build-only resources."""
    from harbor.utils.container_cache import environment_dir_hash_truncated, environment_dir_hash
    images, shared = Path(images), Path(shared)
    selected = {}
    for task in tasks:
        for dockerfile in task.rglob('Dockerfile'):
            key = environment_dir_hash_truncated(dockerfile.parent)
            selected.setdefault(key, (task.name, dockerfile))
    memory = args.image_build_memory_mb
    cpus = min(args.image_build_cpus, args.cpus)
    timeout = args.image_build_timeout_sec
    allocated_memory = int(os.environ.get('SLURM_MEM_PER_NODE') or 0)
    concurrency = min(args.image_build_concurrency, max(1, args.cpus // cpus))
    if allocated_memory:
        concurrency = min(concurrency, max(1, allocated_memory // memory))
    records = []

    def build(item):
        key, (name, dockerfile) = item
        image = images / f'build_{name}-{key}.sif'
        matches = sorted(images.glob(f'*-{key}.sif'))
        # Preserve explicitly staged legacy caches and shared base-image behavior.
        direct = images / (name + '.sif')
        if direct.is_file():
            matches.append(direct)
        for line in dockerfile.read_text().splitlines():
            parts = line.split()
            if len(parts) > 1 and parts[0].upper() == 'FROM':
                base = images / ('swesmith_base_' + hashlib.sha256(parts[1].encode()).hexdigest()[:12] + '.sif')
                if base.is_file() and not args.force_build:
                    matches.append(base)
        if matches and not args.force_build:
            return {'key': key, 'status': 'cached', 'image': str(matches[0])}
        if allocated_memory and memory > allocated_memory:
            raise ValueError('Image build memory exceeds the job allocation; '
                             'increase --memory or lower --image-build-memory-mb')
        if args.force_build:
            for old in matches:
                for suffix in ('.sif', '.deferred.json', '.overlay.img'):
                    old.with_suffix(suffix).unlink(missing_ok=True)
        print(f'WARNING: no reusable cached image selected for {name} ({key}); '
              f'building separately: {memory} MB, {cpus} CPUs, {timeout}s timeout', flush=True)
        command = [sys.executable, '-m', 'hpc.image_cache', '--build', str(dockerfile),
                   '--output', str(image), '--memory-mb', str(memory), '--cpus', str(cpus)]
        if os.environ.get('SLURM_JOB_ID'):
            command = ['srun', '--quiet', '--exact', '--nodes=1', '--ntasks=1',
                       '--cpu-bind=none', '--gres=none', '--kill-on-bad-exit=1',
                       '-w', os.environ.get('SLURMD_NODENAME') or socket.gethostname().split('.')[0],
                       f'--cpus-per-task={cpus}', f'--mem={memory}M',
                       f'--time={max(1, (timeout + 59) // 60)}', *command]
        started = time.monotonic()
        log = images / f'{key}.build.log'
        record = {'key': key, 'image': str(image), 'memory_mb': memory, 'cpus': cpus,
                  'timeout_sec': timeout, 'command': command, 'log': str(log)}
        try:
            with log.open('w') as output:
                subprocess.run(command, stdout=output, stderr=subprocess.STDOUT, check=True, timeout=timeout)
            record['status'] = 'built'
            try:
                record['published'] = str(publish(image, shared, provenance={
                    'pristine': True, 'environment_sha256': environment_dir_hash(dockerfile.parent),
                    'architecture': platform.machine(), 'format': 'apptainer-sif',
                    'builder': 'hpc.image_cache', 'runtime': 'apptainer',
                    'builder_sha256': digest(Path(__file__)),
                    'bridge_patch_sha256': digest(Path(__file__).parents[1] / 'harbor_patches/bridge_worker.py'),
                    'memory_mb': memory, 'cpus': cpus}, packed=getattr(args, 'pack_image_cache', False)))
            except Exception as exc:
                record['cache_warning'] = str(exc)
                print(f'WARNING: image built but shared cache publication failed: {exc}', flush=True)
        except Exception as exc:
            record.update(status='error', error=str(exc))
            image.unlink(missing_ok=True)
        record['seconds'] = round(time.monotonic() - started, 3)
        return record

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        for record in pool.map(build, selected.items()):
            records.append(record)
            (images / 'preparation.json').write_text(json.dumps(records, indent=2))
    return records


def verify_image_runtime(image, apptainer):
    """Reject incomplete terminal tooling before publishing a newly built image."""
    image = Path(image)
    overlay = image.with_suffix('.overlay.img')
    command = ['unshare', '-r', apptainer, 'exec', '--containall', '--cleanenv',
               '--no-home', '--pwd', '/tmp', '--writable-tmpfs']
    if image.with_suffix('.deferred.json').exists():
        command += ['--overlay', str(overlay) + ':ro']
    command += [str(image), 'sh', '-ec',
                'tmux -V; '
                'if command -v dpkg >/dev/null 2>&1; then '
                'audit=$(dpkg --audit); '
                '[ -z "$audit" ] || { printf "%s\\n" "$audit" >&2; exit 1; }; fi']
    checked = subprocess.run(command, capture_output=True, text=True, timeout=120)
    if checked.returncode:
        raise RuntimeError('Image runtime verification failed; image will not be cached. '
                           'Provide working tmux and complete package installation in the image. ' +
                           ((checked.stderr or '') + (checked.stdout or ''))[-2000:])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--build', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--memory-mb', type=int, required=True)
    parser.add_argument('--cpus', type=int, required=True)
    args = parser.parse_args()
    from harbor_patches import bridge_worker as patch
    patch.worker.APPTAINER = shutil.which('apptainer') or shutil.which('singularity')
    if not patch.worker.APPTAINER:
        raise RuntimeError('Image preparation requires Apptainer or Singularity on the build node')
    # Size compression from the dedicated build allowance, never physical node RAM.
    os.environ['OT_IMAGE_COMPRESSION_ARGS'] = f'-processors {args.cpus} -mem {max(1, args.memory_mb // 4)}M'
    patch.configure_container_certificates()
    patch.worker._parse_copies = patch.parse_copies_docker_semantics
    patch.worker.subprocess.run = patch.run_container_commands(patch.worker.subprocess.run)
    if os.environ.get('OT_IMAGE_BASE_MANIFEST'):
        from harbor_patches.local_image_base import local_base_builds
        patch.worker.subprocess.run = local_base_builds(
            patch.worker.subprocess.run, os.environ['OT_IMAGE_BASE_MANIFEST'])
    # Build from an isolated context; upstream preparation can modify task files.
    with tempfile.TemporaryDirectory(prefix='image-context-', dir=args.output.parent) as temporary:
        context = Path(temporary) / 'context'
        shutil.copytree(args.build.parent, context)
        patch.worker._patch_test_sh_for_offline_pip(str(context))
        patch.worker.ApptainerInstance._build_sif(None, str(context / 'Dockerfile'), str(args.output))
        verify_image_runtime(args.output, patch.worker.APPTAINER)


if __name__ == '__main__':
    main()
