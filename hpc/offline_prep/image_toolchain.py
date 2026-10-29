"""Prepare exact non-Python toolchains from pristine checksum-bound images.

Consumed by the verifier runner through tests/ot-verifier-toolchain.json (see validation/RUNTIME.md).
No task repository, module cache, or trial state is copied.
"""
import argparse
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile
import tempfile

from harbor_patches.verifier_bundle import digest, spec_id, validate_archive, verify

FORMAT = 'ot-image-toolchain-v1'


def toolchain_spec(*, kind, version, root, image_sha256, architecture='x86_64',
                   overlay_sha256=None, deferred_sha256=None):
    if kind != 'go' or not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', version):
        raise ValueError('Requires an observed exact Go patch version')
    path = PurePosixPath(root)
    if (not re.fullmatch(r'/(?:opt|usr/local)/[A-Za-z0-9_.-]+', root)
            or '..' in path.parts or path.name in ('.','..')):
        raise ValueError('Requires a resolved toolchain directory under /opt or /usr/local')
    if architecture not in ('x86_64','aarch64'):
        raise ValueError('Unsupported toolchain architecture')
    binding = {'image_sha256':image_sha256}
    if overlay_sha256 is not None or deferred_sha256 is not None:
        binding.update(overlay_sha256=overlay_sha256,deferred_sha256=deferred_sha256)
    if any(not isinstance(v,str) or not re.fullmatch('[a-f0-9]{64}',v) for v in binding.values()):
        raise ValueError('Image binding must contain complete pinned checksums')
    return dict(kind=kind,version=version,root=root,architecture=architecture,source_image=binding,
                layout=1)


def canonical(spec):
    if not isinstance(spec,dict) or set(spec)!={'kind','version','root','architecture','source_image','layout'}:
        raise ValueError('Noncanonical toolchain specification')
    expected=toolchain_spec(kind=spec['kind'],version=spec['version'],root=spec['root'],
                            architecture=spec['architecture'],**spec['source_image'])
    if expected!=spec:raise ValueError('Noncanonical toolchain specification')
    return spec


def expected_version(spec):
    arch={'x86_64':'amd64','aarch64':'arm64'}[spec['architecture']]
    return f"go version go{spec['version']} linux/{arch}"


def build(spec, image, cache):
    canonical(spec)
    from hpc.offline_prep.image_python_runtime import source_image,stage_source,command
    # Reuse only the shared pristine-image checksum/overlay handling. The
    # artifact specification and copied tree contain no Python runtime fields.
    binding={'image_python':spec['source_image']}
    original=source_image(binding,image)
    cache=Path(cache);cache.mkdir(parents=True,exist_ok=True)
    key=spec_id(spec);manifest=cache/(key+'.json');archive=cache/(key+'.tar.gz')
    if manifest.exists():
        record=json.loads(manifest.read_text())
        checked=verify({**record,'spec':spec},cache,FORMAT)
        validate_archive(checked,('toolchain',),'toolchain/bin/go')
        return record
    if archive.exists():raise FileExistsError(archive)
    # Retain preparation scratch for diagnosis; never remove source/cache state.
    scratch=Path(tempfile.mkdtemp(prefix='image-toolchain-',dir=os.environ.get('TMPDIR')))
    source=stage_source(original,scratch)
    def execute(*args):
        result=subprocess.run(command(source,scratch,*args),capture_output=True,text=True,timeout=180)
        if result.returncode:raise RuntimeError('Pristine toolchain command failed: '+result.stderr[-2000:])
        return result.stdout.strip()
    def probe(root):
        # No user go env configuration or automatic toolchain download during
        # preparation probes. Runtime activation policy belongs to the runner.
        return execute('/usr/bin/env','GOENV=off','GOTOOLCHAIN=local',f'GOROOT={root}',root+'/bin/go','version')
    if execute('readlink','-f',spec['root'])!=spec['root']:
        raise ValueError('Toolchain root must be its observed resolved directory')
    observed=probe(spec['root'])
    if observed!=expected_version(spec):raise ValueError('Observed toolchain version/architecture differs: '+observed)
    packed=scratch/'source.tar'
    execute('tar','-cf',str(packed),'-C',spec['root'],'.')
    # Validate source tar before extracting on the controller. Reject device
    # nodes and external links; no dereferencing of image links on the host.
    destination=scratch/'toolchain';destination.mkdir()
    with tarfile.open(packed) as tf:
        tf.extractall(destination,filter='data')
    if not (destination/'bin/go').is_file():raise ValueError('Toolchain executable absent')
    relocated=probe(str(destination))
    if relocated!=observed:raise ValueError('Relocated toolchain version differs')
    target=scratch/'artifact.tar.gz'
    with tarfile.open(target,'w:gz') as tf:tf.add(destination,arcname='toolchain')
    validate_archive(target,('toolchain',),'toolchain/bin/go')
    record=dict(format=FORMAT,id=key,spec=spec,status='ready',archive_sha256=digest(target),
                archive_bytes=target.stat().st_size,observed_version=observed,relocated_version=relocated,
                scope='Pristine toolchain preparation only; runner integration and task validation required')
    # Recheck pristine inputs before publication into this local cache.
    source_image(binding,image)
    # Exclusive creation never overwrites conflicting cache bytes.
    with archive.open('xb') as output,target.open('rb') as incoming:
        while block:=incoming.read(1024*1024):output.write(block)
    if digest(archive)!=record['archive_sha256']:raise ValueError('Copied toolchain artifact checksum differs')
    with manifest.open('x') as output:json.dump(record,output,indent=2);output.write('\n')
    return record


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--spec',required=True);parser.add_argument('--image',required=True);parser.add_argument('--cache',required=True)
    args=parser.parse_args()
    print(json.dumps(build(json.loads(Path(args.spec).read_text()),args.image,args.cache),indent=2))

if __name__=='__main__':main()
