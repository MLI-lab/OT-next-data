"""Capture ELF dependency closures from the same pristine image as verifier tools."""
import re
import subprocess


def validate_members(archive, spec, members, prefix='python'):
    import json
    import tarfile
    member = members.get(prefix + '/native-libs/sources.json')
    if member is None or not member.isfile() or member.size > 1024 * 1024:
        raise ValueError('Incomplete native library inventory')
    with tarfile.open(archive) as stream:
        inventory = json.load(stream.extractfile(member))
    if not isinstance(inventory, dict):
        raise ValueError('Invalid native library inventory')
    for name in inventory:
        if not re.fullmatch(r'[A-Za-z0-9_.+-]+', name):
            raise ValueError('Invalid native library name')
        library = members.get(prefix + '/native-libs/' + name)
        if library is None or not library.isfile():
            raise ValueError('Incomplete native library: ' + name)
    for name in spec['executables']:
        wrapper = members.get(prefix + '/native-tools/' + name)
        if wrapper is None or not (wrapper.issym() or (wrapper.isfile() and wrapper.mode & 0o111)):
            raise ValueError('Incomplete native tool launcher: ' + name)
        if wrapper.isfile() and not any(re.fullmatch(
                r'ld(?:-linux[^/]*|-[0-9.]+)\.so(?:\.[0-9]+)*', name) for name in inventory):
            raise ValueError('Incomplete native ELF loader inventory')


def dependencies(output):
    if 'not found' in output:
        raise ValueError('Pristine verifier tool has an unresolved shared library')
    return sorted(set(re.findall(r'(?<!\S)(/[^\s()]+)', output)))


def prepare(spec, runtime, source, scratch):
    import shlex
    from hpc.offline_prep.image_python_runtime import command
    libraries = runtime / 'native-libs'
    libraries.mkdir(exist_ok=True)
    # Batch pristine-image commands: starting a container for every library is
    # expensive, especially for images with overlay sidecars.
    scripts = []
    for name, executable in spec['executables'].items():
        output = scratch / ('ldd-' + name + '.txt')
        status = scratch / ('ldd-' + name + '.status')
        scripts.append('status=0; /usr/bin/ldd ' + shlex.quote(executable) + ' > ' +
                       shlex.quote(str(output)) + ' 2>&1 || status=$?; printf "%s" "$status" > ' +
                       shlex.quote(str(status)))
    subprocess.run(command(source, scratch, '/bin/sh', '-ec', '\n'.join(scripts)), check=True)
    closures, all_paths = {}, set()
    for name in spec['executables']:
        output = (scratch / ('ldd-' + name + '.txt')).read_text()
        status = int((scratch / ('ldd-' + name + '.status')).read_text())
        if status and ('not a dynamic executable' in output or 'statically linked' in output):
            continue
        if status:
            raise ValueError('Unable to inspect native tool dependencies: ' + name + ': ' + output[-1000:])
        paths = dependencies(output)
        loaders = [p for p in paths if re.fullmatch(r'ld(?:-linux[^/]*|-[0-9.]+)\.so(?:\.[0-9]+)*', p.rsplit('/', 1)[-1])]
        if len(loaders) != 1:
            raise ValueError('Expected one native ELF loader for ' + name)
        closures[name] = loaders[0].rsplit('/', 1)[-1]
        all_paths.update(paths)
    paths = sorted(all_paths)
    scripts = ['readlink -f ' + shlex.quote(path) + ' > ' + shlex.quote(str(scratch/('library-path-'+str(index))))
               for index,path in enumerate(paths)]
    if scripts:
        subprocess.run(command(source, scratch, '/bin/sh', '-ec', '\n'.join(scripts)), check=True)
    copied = {}
    for index,path in enumerate(paths):
        basename = path.rsplit('/', 1)[-1]
        if not re.fullmatch(r'[A-Za-z0-9_.+-]+', basename):
            raise ValueError('Invalid native library filename')
        resolved = (scratch/('library-path-'+str(index))).read_text().strip()
        if basename in copied and copied[basename] != resolved:
            raise ValueError('Conflicting native library paths for ' + basename)
        copied[basename] = resolved
    scripts = [shlex.join(['/bin/cp', '-L', '--preserve=mode', '--', path, str(libraries/name)])
               for name,path in copied.items()]
    if scripts:
        subprocess.run(command(source, scratch, '/bin/sh', '-ec', '\n'.join(scripts)), check=True)
    for name in copied:
        with (libraries/name).open('rb') as stream:
            if stream.read(4) != b'\x7fELF':
                raise ValueError('Native dependency is not ELF: ' + name)
    for name,loader in closures.items():
        wrapper = runtime / 'native-tools' / name
        wrapper.unlink()
        wrapper.write_text('#!/bin/sh\nset -eu\n'
                           'root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)\n'
                           'export LD_LIBRARY_PATH="$root/native-libs${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"\n'
                           'exec "$root/native-libs/' + loader + '" '
                           '--library-path "$LD_LIBRARY_PATH" "$root/bin/' + name + '" "$@"\n')
        wrapper.chmod(0o755)
    return copied
