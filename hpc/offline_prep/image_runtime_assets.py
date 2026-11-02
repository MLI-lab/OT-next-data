"""Prepare explicit dependency directories from a verified pristine task image.

These trees are dependencies, not whole image snapshots or task repositories.
The datasource decides which paths must remain submitted code during grading.
"""
from pathlib import Path, PurePosixPath
import json
import re
import subprocess
import tarfile
import tempfile

JDK_ROOT = r'/usr/lib/jvm/(?:java-(?:8|11|17|21)-openjdk-amd64|zulu(?:8|11|21)-ca-amd64)'
PROJECT_MODULES = r'/home/[A-Za-z0-9_.-]+/(?:[A-Za-z0-9_@.+-]+/)*node_modules'
SDK_ROOTS = (r'/(?:usr/lib/ruby|usr/lib/x86_64-linux-gnu/(?:ruby|rubygems-integration)|'
             r'usr/share/rubygems-integration|var/lib/gems/[0-9.]+|'
             r'opt/multiswe-(?:maven|legacy-maven|maven-user/wrapper/dists|gradle/caches/modules-2|gradle/wrapper/dists)|'
             r'usr/share/cmake-[0-9.]+|usr/(?:local/)?lib(?:exec)?/gcc/[A-Za-z0-9_-]+/[0-9.]+|'
             r'usr/(?:local/)?include/(?:x86_64-linux-gnu/)?c\+\+/[0-9.]+|'
             r'usr/share/(?:perl5|perl/[0-9.]+|autoconf|automake-[0-9.]+|aclocal(?:-[0-9.]+)?|libtool)|'
             r'usr/lib/x86_64-linux-gnu/(?:perl(?:5)?/[0-9.]+|perl-base)|etc/perl|'
             r'root/\.npm|root/\.cache/(?:yarn|node/corepack|node-gyp|electron|Cypress|ms-playwright|puppeteer)|'
             r'root/\.local/share/pnpm/(?:store|\.tools)|root/\.pnpm-store|usr/local/share/\.cache/yarn|tmp/\.yarn-cache-0)')


def dereference_policy(path):
    """Explicit source-image roots allowed by each reviewed SDK layout."""
    root = Path(path)
    if re.fullmatch(r'/usr/lib/go-[0-9]+\.[0-9]+', path):
        return [path, '/usr/share/' + root.name,
                '/usr/share/doc/golang-' + root.name.removeprefix('go-') + '-doc'], []
    if re.fullmatch(JDK_ROOT, path):
        major = re.search(r'(?:java-|zulu)([0-9]+)', root.name)[1]
        return [path, '/etc/java-' + major + '-openjdk', '/etc/ssl/certs/java',
                '/usr/share/doc', '/usr/lib/x86_64-linux-gnu/jni/libatk-wrapper.so'], ['lib/src.zip', 'src.zip']
    if path == '/usr/share/maven':
        return [path, '/usr/share/java', '/usr/share/maven-repo', '/etc/maven'], []
    if path == '/usr/share/nodejs':
        return [path, '/usr/share/javascript', '/usr/bin/gyp'], ['npm/man']
    if re.fullmatch(r'/usr/(?:local/)?lib(?:exec)?/gcc/[A-Za-z0-9_-]+/[0-9.]+', path):
        counterpart = path.replace('/lib/gcc/', '/libexec/gcc/') if '/lib/gcc/' in path else path.replace('/libexec/gcc/', '/lib/gcc/')
        return [path, counterpart, '/usr/lib/x86_64-linux-gnu', '/lib/x86_64-linux-gnu',
                '/usr/local/lib', '/usr/local/lib64'], []
    if re.fullmatch(r'/(?:usr/lib/ruby|usr/lib/x86_64-linux-gnu/(?:ruby|rubygems-integration)|usr/share/rubygems-integration|var/lib/gems/[0-9.]+)', path):
        return [path, '/usr/lib/ruby', '/usr/lib/x86_64-linux-gnu/ruby',
                '/usr/lib/x86_64-linux-gnu/rubygems-integration', '/usr/share/rubygems-integration',
                '/var/lib/gems', '/usr/share/fonts', '/usr/share/javascript'], []
    if re.fullmatch(r'/(?:usr/share/(?:perl5|perl/[0-9.]+|autoconf|automake-[0-9.]+|aclocal(?:-[0-9.]+)?|libtool)|usr/lib/x86_64-linux-gnu/(?:perl(?:5)?/[0-9.]+|perl-base)|etc/perl)', path):
        return [path, '/etc/perl', '/usr/share/perl', '/usr/share/perl5', '/usr/share/autoconf',
                '/usr/share/libtool', '/usr/share/doc', '/usr/lib/x86_64-linux-gnu/perl',
                '/usr/lib/x86_64-linux-gnu/perl5', '/usr/lib/x86_64-linux-gnu/perl-base',
                '/usr/share/misc/config.guess', '/usr/share/misc/config.sub', '/usr/share/common-licenses'], []
    raise ValueError('No reviewed symlink closure for dependency directory: ' + path)


def split_project_links(members, source, project):
    """Store workspace links as data; never extract a link outside the cache tree."""
    import copy
    import posixpath
    selected, links = [], {}
    aliases = {posixpath.normpath(posixpath.join(source, m.name)): m.linkname
               for m in members if m.issym()}
    def resolve(target):
        for _ in range(40):
            parts = PurePosixPath(target).parts
            for length in range(2, len(parts) + 1):
                prefix = str(PurePosixPath(*parts[:length]))
                if prefix in aliases:
                    replacement = posixpath.join(posixpath.dirname(prefix), aliases[prefix])
                    target = posixpath.normpath(posixpath.join(replacement, *parts[length:]))
                    break
            else:
                return target
        raise ValueError('Cyclic or excessive project dependency link chain')
    for original in members:
        member = copy.copy(original)
        if member.issym():
            parent = posixpath.dirname(posixpath.join(source, member.name))
            direct = posixpath.normpath(posixpath.join(parent, member.linkname))
            target = resolve(direct)
            if (PurePosixPath(member.name).parts[-3:] == ('build', 'node_gyp_bins', 'python3')
                    and re.fullmatch(r'/usr/(?:local/)?bin/python3(?:\.[0-9]+)?', target)):
                # node-gyp recreates this temporary interpreter link for each
                # build. It is build state, not an installed package input.
                continue
            if target == source or target.startswith(source + '/'):
                member.linkname = posixpath.relpath(direct, parent)
            elif (target == project or target.startswith(project + '/')) and '.git' not in PurePosixPath(target).parts:
                links[str(PurePosixPath(member.name))] = posixpath.relpath(target, project)
                continue
            else:
                raise ValueError('Dependency link leaves the reviewed project: ' + member.name + ' -> ' + target)
        selected.append(member)
    return selected, links


def canonical(value):
    if (not isinstance(value, dict) or set(value) - {'required', 'dereference', 'project_links', 'files', 'overlay_sha256', 'deferred_sha256'} != {'image_sha256', 'trees'}
            or not isinstance(value['image_sha256'], str)
            or not re.fullmatch('[a-f0-9]{64}', value['image_sha256'])
            or not isinstance(value['trees'], dict) or (not value['trees'] and not value.get('files'))):
        raise ValueError('Image assets require a source checksum and explicit dependency trees')
    trees = {}
    for name, source in sorted(value['trees'].items()):
        if not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]*', name):
            raise ValueError('Invalid dependency tree name')
        # Deliberately exclude repositories, homes, configuration and credentials.
        if (not isinstance(source, str) or any(part in ('..', '.git') for part in PurePosixPath(source).parts)
                or not (re.fullmatch(SDK_ROOTS, source) or re.fullmatch(PROJECT_MODULES, source) or re.fullmatch(
                r'/(?:opt/(?:go|node)-[0-9][0-9.]*|usr/lib/go-[0-9][0-9.]*|usr/local/(?:go|lib/node_modules)|'
                r'root/go/pkg/mod|(?:testbed|workspace/repo)/node_modules|'
                r'home/[A-Za-z0-9_.-]+/node_modules|usr/lib/node_modules|'
                r'opt/yarn-v[0-9]+\.[0-9]+\.[0-9]+|usr/share/(?:maven|nodejs)|'
                + JDK_ROOT.removeprefix('/') + r')', source))):
            raise ValueError('Unreviewed dependency directory: ' + str(source))
        trees[name] = source
    result = {'image_sha256': value['image_sha256'], 'trees': trees}
    if 'files' in value:
        files = value['files']
        allowed = {'/opt/multiswe-maven-settings.xml', '/opt/multiswe-junit-local.gradle',
                   '/opt/multiswe-gradle/init.d/multiswe-grgit.gradle',
                   '/opt/multiswe-gradle/gradle.properties',
                   '/root/.npmrc', '/root/.yarnrc', '/root/.yarnrc.yml',
                   '/etc/npmrc', '/etc/yarnrc', '/etc/yarnrc.yml',
                   '/root/.config/go/env',
                   *('/usr/bin/'+name for name in ('autoreconf', 'autoconf', 'autoheader', 'automake', 'aclocal', 'libtoolize'))}
        if (not isinstance(files, dict) or not files or set(files) & set(trees) or any(
                not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]*', name) or path not in allowed
                for name, path in files.items())):
            raise ValueError('Unreviewed standalone SDK resource')
        result['files'] = dict(sorted(files.items()))
    if 'overlay_sha256' in value or 'deferred_sha256' in value:
        for field in ('overlay_sha256', 'deferred_sha256'):
            if not re.fullmatch('[a-f0-9]{64}', value.get(field, '')):
                raise ValueError('Dependency trees require complete image sidecar checksums')
            result[field] = value[field]
    if 'project_links' in value:
        links = value['project_links']
        def project_root(path):
            if re.fullmatch(PROJECT_MODULES, path):
                return '/' + '/'.join(PurePosixPath(path).parts[1:3])
            if path in ('/testbed/node_modules', '/workspace/repo/node_modules'):
                return str(PurePosixPath(path).parent)
            return None
        if (not isinstance(links, dict) or not links or any(name not in trees or
                root != project_root(trees[name]) for name, root in links.items())):
            raise ValueError('Live project links require the matching node_modules project root')
        result['project_links'] = dict(sorted(links.items()))
    if 'dereference' in value:
        names = value['dereference']
        if not isinstance(names, list) or not names or any(name not in trees for name in names):
            raise ValueError('Dereferencing requires declared dependency trees')
        for name in names:
            dereference_policy(trees[name])
        result['dereference'] = sorted(set(names))
    if 'required' in value:
        required = value['required']
        if not isinstance(required, dict) or not required or not set(required) <= set(trees):
            raise ValueError('Required dependency files must belong to declared trees')
        for name, paths in required.items():
            if (not isinstance(paths, list) or not paths or any(not isinstance(path, str)
                    or not path or PurePosixPath(path).is_absolute() or '..' in PurePosixPath(path).parts
                    for path in paths)):
                raise ValueError('Invalid required dependency path')
        result['required'] = {name: sorted(set(paths)) for name, paths in sorted(required.items())}
    return result


def validate_members(spec, members, prefix='python'):
    """Reject incomplete cached dependency trees before choosing installation fallback."""
    import posixpath
    spec = canonical(spec)
    for name in spec.get('files', {}):
        member = members.get(prefix + '/assets/' + name)
        if member is None or not member.isfile():
            raise ValueError('Incomplete SDK resource: ' + name)
    for name in spec['trees']:
        root = prefix + '/assets/' + name
        # A workspace node_modules can consist solely of live project links.
        # Those links are deliberately stored in the adjacent inventory rather
        # than copied into the trusted dependency tree.
        directory = members.get(root)
        links = members.get(root + '.project-links.json')
        links_only = (name in spec.get('project_links', {}) and directory is not None
                      and directory.isdir() and links is not None and links.isfile()
                      and links.size > 2)
        if not links_only and not any(path.startswith(root + '/') and member.isfile()
                                     for path, member in members.items()):
            raise ValueError('Incomplete runtime dependency tree: ' + name)
        if name in spec.get('project_links', {}):
            links = members.get(root + '.project-links.json')
            if links is None or not links.isfile():
                raise ValueError('Incomplete live project link inventory: ' + name)
        required = list(spec.get('required', {}).get(name, []))
        executable = {'go': 'bin/go', 'node': 'bin/node'}.get(name)
        if executable:
            required.append(executable)
        for relative in required:
            member = members.get(root + '/' + relative)
            seen = set()
            while member is not None and (member.issym() or member.islnk()):
                if member.name in seen:
                    member = None
                    break
                seen.add(member.name)
                target = member.linkname if member.islnk() else posixpath.join(
                    posixpath.dirname(member.name), member.linkname)
                member = members.get(posixpath.normpath(target))
            if member is None or not member.isfile() or (relative == executable and not member.mode & 0o111):
                raise ValueError('Incomplete runtime dependency file: ' + name + '/' + relative)


def prepare(spec, runtime, scratch, image=None, *, python='python3'):
    from hpc.offline_prep.image_python_runtime import source_image, stage_source, command as image_command, SourceImage
    from harbor_patches.verifier_bundle import digest
    spec = canonical(spec)
    def command(*args):
        argv = image_command(*args)
        if argv[:2] == ['apptainer', 'exec']:
            argv[2:2] = ['--fakeroot', '--ignore-fakeroot-command']
        return argv
    binding = {k: spec[k] for k in ('image_sha256', 'overlay_sha256', 'deferred_sha256') if k in spec}
    source_scratch = Path(tempfile.mkdtemp(prefix='asset-source-', dir=scratch))
    if isinstance(image, SourceImage):
        # Select the pristine bundle beside its SIF, not another preparation
        # component's private overlay copy.
        image = image.sif
    source = stage_source(source_image({'image_python': binding}, image, scratch=source_scratch), source_scratch)
    records = {}
    for name, path in spec['trees'].items():
        target = runtime / 'assets' / name
        target.mkdir(parents=True)
        packed = scratch / ('asset-' + name + '.tar')
        options = []
        if name in spec.get('dereference', []):
            # Check every resolved input in the pristine image. JDK source ZIPs
            # are optional development sources, not runtime dependencies, and
            # distro JRE images may contain dangling links to those ZIPs.
            allowed, excluded = dereference_policy(path)
            check = '''import json,os,pathlib,sys
root=pathlib.Path(sys.argv[1]); allowed=[pathlib.Path(p) for p in json.loads(sys.argv[2])]
excluded=set(json.loads(sys.argv[3]))
for directory,dirs,files in os.walk(root,followlinks=True):
 dirs[:]=[name for name in dirs if str((pathlib.Path(directory)/name).relative_to(root)) not in excluded]
 real=pathlib.Path(directory).resolve(strict=True)
 parent=pathlib.Path(directory).parent
 while parent==root or root in parent.parents:
  assert parent.resolve(strict=True)!=real, ('Cyclic dependency directory',directory)
  parent=parent.parent
 for entry in [directory]+[os.path.join(directory,n) for n in dirs+files]:
  if str(pathlib.Path(entry).relative_to(root)) in excluded: continue
  target=pathlib.Path(entry).resolve(strict=False)
  assert any(target==base or base in target.parents for base in allowed), ('External SDK input',entry,target)
  if pathlib.Path(entry).is_symlink() and not pathlib.Path(entry).exists():
   excluded.add(str(pathlib.Path(entry).relative_to(root)))
print(json.dumps(sorted(excluded)))
'''
            inspected = subprocess.run(command(source, scratch, python, '-I', '-c', check, path,
                                   json.dumps(allowed), json.dumps(excluded)),
                                   capture_output=True, text=True)
            if inspected.returncode:
                raise ValueError('Invalid pristine SDK symlink closure: ' + inspected.stderr[-4000:])
            # Preserve absence: a dangling SDK link is not an available input
            # in the pristine image either. Never follow it into the build host.
            excluded = json.loads(inspected.stdout)
            options = ['--dereference', *['--exclude=./' + p for p in excluded]]
        # Overlay whiteouts describe the merged filesystem; they are not SDK
        # files and may be exposed as unreadable entries by Apptainer's FUSE.
        subprocess.run(command(source, scratch, 'tar', *options, '-cf', packed,
                               '--exclude=.wh.*', '-C', path, '.'), check=True)
        # No external links are followed by host extraction. Resolve the entire
        # member graph before extracting, including hardlinks and duplicates.
        with tarfile.open(packed) as archive:
            names = set()
            selected = []
            for member in archive:
                member_path = PurePosixPath(member.name)
                if (member_path.is_absolute() or '..' in member_path.parts
                        or str(member_path) in names or not (member.isfile() or member.isdir()
                                                            or member.issym() or member.islnk())):
                    raise ValueError('Unsafe dependency tree member: ' + member.name)
                names.add(str(member_path))
                selected.append(member)
            if name in spec.get('project_links', {}):
                selected, links = split_project_links(selected, path, spec['project_links'][name])
                (runtime / 'assets' / (name + '.project-links.json')).write_text(json.dumps(links, sort_keys=True) + '\n')
            for member in selected:
                tarfile.data_filter(member, str(target))
            archive.extractall(target, members=selected, filter='data')
        for entry in target.rglob('*'):
            if entry.is_symlink():
                try:
                    entry.resolve(strict=True).relative_to(target.resolve())
                except (OSError, ValueError) as error:
                    raise ValueError('Dependency symlink leaves its tree: ' + str(entry)) from error
        for relative in spec.get('required', {}).get(name, []):
            if not (target / relative).is_file():
                raise ValueError('Missing required dependency file: ' + name + '/' + relative)
        records[name] = {'source': path, 'source_archive_sha256': digest(packed),
                         'source_archive_bytes': packed.stat().st_size}
        if name in spec.get('dereference', []):
            records[name]['omitted_unavailable_or_development_links'] = excluded
    for name, path in spec.get('files', {}).items():
        target = runtime / 'assets' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(command(source, scratch, '/bin/cp', '-L', '--preserve=mode', path, target), check=True)
        if not target.is_file() or target.is_symlink() or target.stat().st_size > 4 * 1024 * 1024:
            raise ValueError('Invalid standalone SDK resource: ' + name)
        records[name] = {'source': path, 'sha256': digest(target), 'bytes': target.stat().st_size}
    return records
