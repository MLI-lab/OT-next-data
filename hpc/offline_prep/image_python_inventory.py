"""Read-only inventory of the Python in cached task images, for verifier runtime planning.

    python hpc/offline_prep/image_python_inventory.py --images-manifest images-manifest.json \
        --bundles CACHE/bundles-v1 --output DIR [--workers 8]

For each image bundle: unpack the checksum-verified SIF to scratch ($TMPDIR),
record what `python3` resolves to, its version and sys.path, every installed
distribution (version, requirements, installer, location) and the import name
-> distribution map, then delete the copy. No builds, downloads or writes to
the cache. One <bundle>.json per image; finished bundles are skipped.
"""
import argparse
import concurrent.futures
import hashlib
import json
import os
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

PROBE = r'''
import json, sys
from importlib import metadata
def modules():
    # packages_distributions() is new in Python 3.10; derive the same map on older images.
    if hasattr(metadata, 'packages_distributions'):
        return {k: sorted(set(v)) for k, v in metadata.packages_distributions().items()}
    found = {}
    for d in metadata.distributions():
        names = (d.read_text('top_level.txt') or '').split()
        if not names:
            names = {f.parts[0].split('.')[0] for f in (d.files or []) if f.parts
                     and not f.parts[0].endswith(('.dist-info', '.egg-info')) and f.parts[0] != '..'}
        for n in names:
            found.setdefault(n, set()).add(d.metadata['Name'])
    return {k: sorted(v) for k, v in found.items()}
dists = {}
for d in metadata.distributions():
    name = d.metadata.get('Name')
    if name:
        dists.setdefault(name, {'version': d.version, 'requires': d.requires or [],
                                'installer': (d.read_text('INSTALLER') or '').strip(),
                                'location': str(d.locate_file(''))})
print(json.dumps({'executable': sys.executable, 'version': sys.version.split()[0], 'path': sys.path,
                  'stdlib': sorted(getattr(sys, 'stdlib_module_names', ())),
                  'distributions': dists, 'modules': modules()}))
'''


def exec_in(sif, *argv):
    return subprocess.run(['apptainer', 'exec', '--containall', '--no-home', '--pwd', '/', str(sif), *argv],
                          capture_output=True, text=True, timeout=300)


def inventory(sha, info, bundles, output):
    (name, meta), = info['files'].items()
    out = output / f'{sha}.json'
    if out.exists():
        return 'skip'
    with tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR')) as tmp:
        with tarfile.open(bundles / f'{name}.tar') as archive:
            archive.extract(name, tmp, filter='data')
        sif = Path(tmp) / name
        with sif.open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() != meta['sha256']:
                raise RuntimeError(f'{name}: checksum mismatch')
        probe = exec_in(sif, 'python3', '-I', '-c', PROBE)
        which = exec_in(sif, '/bin/sh', '-c', 'command -v python3; readlink -f "$(command -v python3)"')
    record = {'bundle': sha, 'sif': name, 'sif_sha256': meta['sha256'],
              'environment_sha256': info['provenance']['environment_sha256'],
              'return_code': probe.returncode, 'stderr': probe.stderr[-2000:], 'python3': which.stdout.split()}
    if probe.returncode == 0:
        record.update(json.loads(probe.stdout.strip().splitlines()[-1]))
    partial = out.with_suffix('.tmp')
    partial.write_text(json.dumps(record, indent=1, sort_keys=True))
    partial.rename(out)
    return probe.returncode


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--images-manifest', type=Path, required=True)
    parser.add_argument('--bundles', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--workers', type=int, default=8)
    args = parser.parse_args()
    bundles = json.loads(args.images_manifest.read_text())['bundles']
    args.output.mkdir(parents=True, exist_ok=True)
    failures = 0
    with concurrent.futures.ThreadPoolExecutor(args.workers) as pool:
        futures = {pool.submit(inventory, sha, info, args.bundles, args.output): sha for sha, info in bundles.items()}
        for future in concurrent.futures.as_completed(futures):
            try:
                result = future.result()
            except Exception as exc:
                result = repr(exc)
            print(futures[future], result, flush=True)
            failures += result not in (0, 'skip')
    print(json.dumps({'bundles': len(futures), 'failures': failures}), flush=True)
    sys.exit(1 if failures else 0)


if __name__ == '__main__':
    main()
