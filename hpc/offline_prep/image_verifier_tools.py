"""Copy declared verifier executables from a checksum-verified pristine image.

System libraries remain requirements of that exact task image, as for native
Python runtimes. No task data or agent-installed files enter the artifact.
"""
import re
import subprocess
import io
import posixpath
import tarfile
import json
from pathlib import Path
import tempfile


def canonical(value):
    if (not isinstance(value, dict) or set(value)-{'resources','debian','node_modules','shared_libraries','overlay_sha256','deferred_sha256'} != {'image_sha256', 'executables'}
            or not re.fullmatch(r'[a-f0-9]{64}', value.get('image_sha256', ''))
            or not isinstance(value.get('executables'), dict)
            or not (value['executables'] or value.get('debian'))):
        raise ValueError('Image tools require a source checksum and executable paths')
    for name, path in value['executables'].items():
        if (not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.+-]*', name)
                or not isinstance(path, str)
                or not re.fullmatch(r'/(?:usr/)?(?:local/)?(?:s?bin)/[A-Za-z0-9_][A-Za-z0-9_.+-]*', path)):
            raise ValueError('Image tool paths must be explicit system executables')
    result={'image_sha256': value['image_sha256'], 'executables': dict(sorted(value['executables'].items()))}
    if 'overlay_sha256' in value or 'deferred_sha256' in value:
        for field in ('overlay_sha256', 'deferred_sha256'):
            if not re.fullmatch('[a-f0-9]{64}', value.get(field, '')):
                raise ValueError('Native tools require complete image sidecar checksums')
            result[field] = value[field]
    if 'shared_libraries' in value:
        if value['shared_libraries'] is not True or value.get('debian'):
            raise ValueError('Shared libraries require pristine-image native executables')
        result['shared_libraries'] = True
    if 'node_modules' in value:
        modules=value['node_modules']
        if (not isinstance(modules,dict) or set(modules)!={'ws'} or 'node' not in value['executables']
                or not isinstance(modules['ws'],dict) or set(modules['ws'])!={'path','version'}
                or modules['ws']['path']!='/usr/local/lib/node_modules/ws'
                or not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+',modules['ws']['version'])):
            raise ValueError('Native Node modules require the reviewed pristine ws package')
        result['node_modules']=modules
    if 'debian' in value:
        from hpc.offline_prep.offline_sources import validate
        debian=value['debian']
        if not isinstance(debian,dict) or set(debian) != {'sources','libraries'}:
            raise ValueError('Native Debian inputs require pinned sources and explicit libraries')
        sources=validate(debian['sources'])
        libraries=debian['libraries']
        if (not sources or any(not source['filename'].endswith('.deb') for source in sources)
                or not isinstance(libraries,list) or not libraries
                or any(not isinstance(path,str) or not re.fullmatch(
                    r'/usr/lib/(?:x86_64|aarch64)-linux-gnu/lib[A-Za-z0-9_+.-]+\.so(?:\.[0-9]+)*',path)
                       for path in libraries)):
            raise ValueError('Native Debian inputs require explicit system libraries')
        result['debian']={'sources':sorted(sources,key=lambda item:item['filename']),
                          'libraries':sorted(set(libraries))}
    if 'resources' in value:
        resources=value['resources']
        # libmagic's compiled database is required by the file verifier tool.
        # Keep this allowlist narrow; no task data belongs in a shared runtime.
        magic = (resources in ({'magic.mgc':'/usr/share/misc/magic.mgc'}, {'magic.mgc':'/usr/lib/file/magic.mgc'})
                 and 'file' in value['executables'])
        valgrind = (resources == {'valgrind/'+name:'/usr/libexec/valgrind/'+name for name in (
            'memcheck-amd64-linux','vgpreload_core-amd64-linux.so','vgpreload_memcheck-amd64-linux.so','default.supp')}
            and value['executables'].get('valgrind') == '/usr/bin/valgrind.bin')
        if not (magic or valgrind):
            raise ValueError('Image tool resources require a reviewed native-tool data file')
        result['resources']=dict(resources)
    return result


def prepare_debian(debian, executables, runtime, scratch):
    """Copy only named ELF files from verified packages, never run maintainer scripts."""
    from hpc.offline_prep.offline_sources import prepare as prepare_sources
    prepare_sources(debian['sources'],scratch)
    selected={path:runtime/'native-libs'/path.rsplit('/',1)[1] for path in debian['libraries']}
    selected.update({path:runtime/'bin'/name for name,path in executables.items()})
    found=set()
    for source in debian['sources']:
        data=subprocess.check_output(['dpkg-deb','--fsys-tarfile',str(scratch/'sources'/source['filename'])])
        with tarfile.open(fileobj=io.BytesIO(data)) as archive:
            members={posixpath.normpath(member.name):member for member in archive}
            for path,target in selected.items():
                name=path.lstrip('/')
                if name not in members:continue
                if path in found:raise ValueError('Conflicting native package file: '+path)
                seen=set();member=members[name]
                while member.issym() or member.islnk():
                    if name in seen:raise ValueError('Native package symlink cycle')
                    seen.add(name)
                    name=posixpath.normpath(posixpath.join(posixpath.dirname(name),member.linkname)
                                           if member.issym() else member.linkname)
                    if name.startswith(('../','/')) or name not in members:
                        raise ValueError('Native package link escapes its archive')
                    member=members[name]
                if not member.isfile():raise ValueError('Native package target is not a regular file')
                payload=archive.extractfile(member).read()
                if payload[:4]!=b'\x7fELF':raise ValueError('Native package target must be ELF')
                target.parent.mkdir(parents=True,exist_ok=True)
                with target.open('xb') as stream:stream.write(payload)
                target.chmod(0o755)
                found.add(path)
    if found!=set(selected):raise ValueError('Missing native package files: '+str(sorted(set(selected)-found)))


def prepare(spec, runtime, scratch):
    from hpc.offline_prep.image_python_runtime import source_image, stage_source, command
    spec = canonical(spec)
    binding = {k: spec[k] for k in ('image_sha256', 'overlay_sha256', 'deferred_sha256') if k in spec}
    source_scratch = Path(tempfile.mkdtemp(prefix='tool-source-', dir=scratch))
    source = source_image({'image_python': binding}, scratch=source_scratch)
    image = stage_source(source, source_scratch)
    shims = runtime / 'native-tools'
    shims.mkdir()
    if spec.get('debian'):
        prepare_debian(spec['debian'],spec['executables'],runtime,scratch)
    batched = bool(spec.get('shared_libraries'))
    if batched:
        import shlex
        commands = []
        for name, path in spec['executables'].items():
            target = runtime/'bin'/name
            if target.exists() or target.is_symlink():
                raise ValueError('Image tool conflicts with a Python executable: ' + name)
            commands.append(shlex.join(['/bin/cp', '-L', '--preserve=mode', '--', path, str(target)]))
        subprocess.run(command(image, scratch, '/bin/sh', '-ec', '\n'.join(commands)), check=True)
    for name, path in spec['executables'].items():
        target = runtime / 'bin' / name
        if not spec.get('debian') and not batched and (target.exists() or target.is_symlink()):
            raise ValueError('Image tool conflicts with a Python executable: ' + name)
        if not spec.get('debian') and not batched:
            subprocess.run(command(image, scratch, '/bin/cp', '-L', '--preserve=mode', '--', path, str(target)),
                           check=True)
        with target.open('rb') as stream:
            if stream.read(4) != b'\x7fELF':
                raise ValueError('Image verifier tools must be native ELF executables: ' + name)
        if not target.stat().st_mode & 0o111:
            raise ValueError('Image verifier tool is not executable: ' + name)
        (shims / name).symlink_to('../bin/' + name)

    if spec.get('shared_libraries'):
        from hpc.offline_prep.image_tool_libraries import prepare as prepare_libraries
        dependencies = prepare_libraries(spec, runtime, image, scratch)
        (runtime / 'native-libs' / 'sources.json').write_text(json.dumps(dependencies, sort_keys=True) + '\n')

    for name,path in spec.get('resources',{}).items():
        resources=runtime/'native-resources';resources.mkdir(exist_ok=True)
        target=resources/name
        target.parent.mkdir(parents=True,exist_ok=True)
        subprocess.run(command(image,scratch,'/bin/cp','-L','--preserve=mode','--',path,str(target)),check=True)
        if not target.is_file() or target.is_symlink() or not 0<target.stat().st_size<=64*1024*1024:
            raise ValueError('Image tool resource is not a bounded regular file: '+name)
    for name,record in spec.get('node_modules',{}).items():
        root=runtime/'native-node';root.mkdir(exist_ok=True)
        target=root/name
        subprocess.run(command(image,scratch,'/bin/cp','-a','--',record['path'],str(target)),check=True)
        for path in target.rglob('*'):
            if not path.resolve().is_relative_to(target.resolve()) or not (path.is_dir() or path.is_file()):
                raise ValueError('Pristine Node package has an external or special file')
        package=json.loads((target/'package.json').read_text())
        if package.get('name')!=name or package.get('version')!=record['version']:
            raise ValueError('Pristine Node package version differs')
