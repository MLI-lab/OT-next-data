"""Copy job inputs to node-local storage, preserving container cache identities."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import ssl


GIB = 1024 ** 3


def size(path):
    return sum(p.stat().st_size for p in path.rglob('*') if p.is_file()) if path.is_dir() else path.stat().st_size


def copy_asset(source, target, *, reserve=20 * GIB):
    source, target = Path(source), Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    required = size(source)
    free = shutil.disk_usage(target.parent).free
    if free < required + reserve:
        raise OSError(f'Insufficient local storage for {source}: need {required + reserve}, have {free}')
    temporary = target.with_name(target.name + '.staging')
    try:
        if source.is_dir():
            shutil.copytree(source, temporary, symlinks=False)
        else:
            shutil.copy2(source, temporary)
        temporary.replace(target)
    except BaseException:
        if temporary.is_dir():
            shutil.rmtree(temporary)
        else:
            temporary.unlink(missing_ok=True)
        raise
    return target, {'source': str(source), 'local': str(target), 'bytes': required}


def stage_images(tasks, source, target, *, selected_keys=None):
    from harbor_patches.image_context import environment_dir_hash_truncated
    target.mkdir(parents=True, exist_ok=True)
    selected = set()
    bundles = set()
    # Include both agent and separate verifier environments. Match content keys,
    # never the obsolete Dockerfile-only key used before COPY payload hashing.
    for task in tasks:
        for dockerfile in task.rglob('Dockerfile'):
            key = environment_dir_hash_truncated(dockerfile.parent)
            if selected_keys is not None and key not in selected_keys:
                continue
            bundles.update((source / 'bundles-v1').glob('*-' + key + '.sif'))
            bundles.update((source / 'bundles-v1').glob('*-' + key + '.sif.tar'))
            selected.update(source.glob('*-' + key + '.sif'))
            direct = source / (task.name + '.sif')
            if direct.is_file():
                selected.add(direct)
            for line in dockerfile.read_text().splitlines():
                parts = line.split()
                if len(parts) > 1 and parts[0].upper() == 'FROM':
                    base = source / ('swesmith_base_' + hashlib.sha256(parts[1].encode()).hexdigest()[:12] + '.sif')
                    if base.is_file():
                        selected.add(base)
    if (source / 'base.sif').is_file():
        selected.add(source / 'base.sif')
    records, copied = [], {}
    for image in sorted(selected):
        real = image.resolve()
        if real not in copied:
            local, record = copy_asset(real, target / image.name)
            copied[real] = local
            records.append(record)
            for suffix in ('.deferred.json', '.overlay.img'):
                sidecar = real.with_suffix(suffix)
                if sidecar.is_file():
                    _, record = copy_asset(sidecar, local.with_suffix(suffix))
                    records.append(record)
        elif (target / image.name) != copied[real]:
            (target / image.name).symlink_to(copied[real].name)
    from hpc.image_cache import restore
    for bundle in sorted(bundles):
        local = restore(bundle, target)
        records.append({'source': str(bundle), 'local': str(local), 'verified_bundle': True})
    from validation.publishing.image_release import stage_published
    records.extend(stage_published(tasks, source, target, selected_keys=selected_keys))
    (target / 'staging.json').write_text(json.dumps(records, indent=2))
    return target


def stage_multinode_weights(source, target, nodes, reserve=20 * GIB):
    """Ray needs one model path valid on every node; decide capacity together."""
    required = size(source)
    step = ['srun', '--overlap', '--gres=none', '--cpus-per-task=1', '--mem=1024M',
            f'--nodes={nodes}', f'--ntasks={nodes}', '--ntasks-per-node=1', '/usr/bin/python3', '-c']
    probe = 'import shutil,json,sys; from pathlib import Path; p=Path(sys.argv[1]); p.mkdir(parents=True,exist_ok=True); print(json.dumps({"free":shutil.disk_usage(p).free}))'
    output = subprocess.check_output([*step, probe, str(target.parent)], text=True)
    capacities = [json.loads(line)['free'] for line in output.splitlines() if line.startswith('{')]
    if len(capacities) != nodes:
        raise RuntimeError('Missing node-local capacity response; refusing an inconsistent Ray model path')
    record = {'source': str(source), 'bytes': required, 'nodes': nodes, 'free_bytes_per_node': capacities}
    if min(capacities) < required + reserve:
        raise OSError('Insufficient local storage for model weights on at least one node')
    # All nodes must finish copying before any Ray process starts. A concurrent
    # disk exhaustion fails this preparation step rather than mixing paths.
    copier = 'import shutil,sys; from pathlib import Path; dst=Path(sys.argv[2]); tmp=dst.with_name(dst.name+".staging"); shutil.copytree(sys.argv[1],tmp,symlinks=False); tmp.replace(dst)'
    subprocess.run([*step, copier, str(source), str(target)], check=True)
    return target, {**record, 'local': str(target)}


def stage_dependencies(spec, tasks, target):
    """Stage only selected tasks' archives and remember which files existed."""
    target.mkdir(parents=True, exist_ok=True)
    original = {}
    for task in tasks:
        source = Path(spec['directory']) / (task.name + '.tar')
        if source.is_file():
            local, _ = copy_asset(source, target / source.name)
            original[local.name] = local.stat().st_mtime_ns
    return dict(spec, directory=str(target)), original


def save_dependencies(local, destination, original):
    """Persist archives created by successful oracle trials after task execution."""
    from uuid import uuid4
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    for source in Path(local).glob('*.tar'):
        if original.get(source.name) == source.stat().st_mtime_ns:
            continue
        target = destination / source.name
        temporary = target.with_name(target.name + '.' + uuid4().hex + '.partial')
        try:
            shutil.copyfile(source, temporary)
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)


def stage_certificates(directory):
    import certifi
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    if os.environ.get('SSL_CERT_DIR') and not os.environ.get('SSL_CERT_FILE'):
        raise ValueError('SSL_CERT_DIR-only trust configuration requires explicit SSL_CERT_FILE before staging')
    sources = {
        'SSL_CERT_FILE': os.environ.get('SSL_CERT_FILE') or certifi.where(),
        'REQUESTS_CA_BUNDLE': os.environ.get('REQUESTS_CA_BUNDLE') or os.environ.get('CURL_CA_BUNDLE') or certifi.where(),
    }
    record = {}
    for variable, source in sources.items():
        source = Path(source)
        data = source.read_bytes()
        target = directory / (variable.lower() + '.pem')
        temporary = target.with_suffix('.tmp')
        temporary.write_bytes(data)
        ssl.create_default_context(cafile=str(temporary))
        temporary.replace(target)
        record[variable] = {'source': str(source), 'local': str(target.resolve()),
                            'sha256': hashlib.sha256(data).hexdigest()}
    (directory / 'certificates.json').write_text(json.dumps(record, indent=2) + '\n')
    os.environ.update({key: entry['local'] for key, entry in record.items()})
    return record


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['certificates'], help='stage HTTP trust stores')
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    print(json.dumps(stage_certificates(args.directory)))
