"""Uploaded under /tests; restore only declared pristine verifier dependencies."""
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import sys


def destination(path):
    target = Path(path)
    if not target.is_absolute() or '..' in target.parts:
        raise ValueError('Dependency destination must be absolute')
    for parent in reversed(target.parents):
        if parent.is_symlink():
            raise ValueError('Dependency parent was replaced by a symlink: ' + str(parent))
    return target


def remove(target):
    if target.is_symlink() or target.is_file():
        target.unlink()
    elif target.exists():
        shutil.rmtree(target)


def restore_global_node_bins(modules, bindir, wrappers):
    """Recreate global package entrypoints from the freshly restored packages."""
    packages = []
    for path in modules.iterdir():
        packages.extend(path.iterdir() if path.name.startswith('@') and path.is_dir() else [path])
    for package in packages:
        metadata = package/'package.json'
        if not metadata.is_file():
            continue
        info = json.loads(metadata.read_text())
        bins = info.get('bin', {})
        if isinstance(bins, str):
            bins = {info['name'].split('/')[-1]: bins}
        for name, relative in bins.items():
            if not re.fullmatch(r'[A-Za-z0-9_.+-]+', name) or name in ('.', '..'):
                raise ValueError('Invalid global Node entrypoint')
            target = package/relative
            if not target.resolve().is_relative_to(package.resolve()) or not target.is_file():
                raise ValueError('Global Node entrypoint is outside its clean package')
            for parent in (bindir, wrappers):
                link = destination(str(parent/name))
                remove(link)
                link.parent.mkdir(parents=True, exist_ok=True)
                link.symlink_to(target)


def restore(root, spec, tools):
    assets = spec.get('image_assets', {})
    for home in ('/opt/multiswe-gradle', '/opt/multiswe-maven-user'):
        if any(path.startswith(home + '/') for path in assets.get('trees', {}).values()):
            # Restore download inputs without retaining agent-side compiled
            # build scripts, daemon state or package-manager configuration.
            remove(destination(home))
    if 'node' in tools:
        for path in ('/root/.npmrc', '/root/.yarnrc', '/root/.yarnrc.yml',
                     '/etc/npmrc', '/etc/yarnrc', '/etc/yarnrc.yml'):
            remove(destination(path))
    if tools.get('go', {}).get('resolved', '').startswith('/usr/local/go/'):
        remove(destination('/root/.config/go/env'))
    for name, path in assets.get('trees', {}).items():
        target = destination(path)
        source = root / 'assets' / name
        if not source.is_dir():
            raise ValueError('Missing private SDK tree: ' + name)
        remove(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, target, symlinks=True)
        project = assets.get('project_links', {}).get(name)
        if project:
            for relative, linked in json.loads((root/'assets'/(name+'.project-links.json')).read_text()).items():
                if (Path(relative).is_absolute() or '..' in Path(relative).parts
                        or Path(linked).is_absolute() or '..' in Path(linked).parts):
                    raise ValueError('Invalid project dependency link')
                link = destination(str(target/relative))
                link.parent.mkdir(parents=True, exist_ok=True)
                link.symlink_to(os.path.relpath(Path(project)/linked, link.parent))
    for name, path in assets.get('files', {}).items():
        target = destination(path)
        remove(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root/'assets'/name, target)
    wrappers = root/'multiswe-tools'
    wrappers.mkdir(exist_ok=True)
    for modules, bindir in (('/usr/local/lib/node_modules', '/usr/local/bin'),
                           ('/usr/lib/node_modules', '/usr/bin')):
        if modules in assets.get('trees', {}).values():
            restore_global_node_bins(Path(modules), Path(bindir), wrappers)
    def wrapper(name, command):
        path = wrappers/name
        remove(path)
        path.write_text('#!/bin/sh\nexec ' + command + ' "$@"\n')
        path.chmod(0o755)
    node = root/'native-tools/node'
    for name in ('npm', 'yarn', 'pnpm'):
        if name in tools:
            resolved = tools[name]['resolved']
            if not any(resolved.startswith(path + '/') for path in assets.get('trees', {}).values()):
                raise ValueError('Package manager is outside the clean SDK: ' + name)
            wrapper(name, shlex.quote(str(node)) + ' ' + shlex.quote(resolved))
    if 'ruby' in tools:
        roots = [path for name, path in assets.get('trees', {}).items() if name.startswith('ruby-')]
        bootstrap = wrappers/'ruby_bootstrap.rb'
        bootstrap.write_text('roots = ' + json.dumps(roots) + '\n'
            'clean = ->(path) { roots.any? { |root| path == root || path.start_with?(root + "/") } }\n'
            '$LOAD_PATH.select! { |path| clean.call(path) }\n'
            'require "rubygems"\n'
            'paths = Gem.path.select { |path| clean.call(path) }\n'
            'raise "No clean Ruby gem paths" if paths.empty?\n'
            'Gem.use_paths(paths.first, paths)\n')
        wrapper('ruby', shlex.quote(str(root/'native-tools/ruby')) + ' --disable-gems -r ' + shlex.quote(str(bootstrap)))
        wrapper('rake', shlex.quote(str(wrappers/'ruby')) + " -rrake -e 'Rake.application.run' --")
    if 'perl' in spec.get('image_tools', {}).get('executables', {}):
        roots = [path for name, path in assets.get('trees', {}).items() if name.startswith('perl-')]
        bootstrap = wrappers/'ot_clean_perl.pm'
        bootstrap.write_text('package ot_clean_perl;\nBEGIN { @INC = (' +
            ','.join("'" + path.replace("'", "\\'") + "'" for path in roots) + '); }\n1;\n')
        wrapper('perl', shlex.quote(str(root/'native-tools/perl')) + ' -I ' + shlex.quote(str(wrappers)) + ' -Mot_clean_perl')
    environment = ['export PATH=' + shlex.quote(str(wrappers) + ':' + str(root/'native-tools')) + ':"$PATH"',
                   'export GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null',
                   'unset NODE_OPTIONS NODE_PATH RUBYOPT RUBYLIB PERL5OPT PERL5LIB JAVA_TOOL_OPTIONS JDK_JAVA_OPTIONS _JAVA_OPTIONS']
    if 'jdk' in assets.get('trees', {}):
        environment.append('export JAVA_HOME=' + shlex.quote(assets['trees']['jdk']))
        environment.append('export PATH="$JAVA_HOME/bin:$PATH"')
    if 'maven' in assets.get('trees', {}):
        wrapper('mvn', shlex.quote(assets['trees']['maven']+'/bin/mvn'))
        environment.append('export MAVEN_SKIP_RC=true')
    # Build-system caches sometimes retain absolute executable paths. Restore
    # those entrypoints too, without resetting repository source/build choices.
    launchers = {path: root/'native-tools'/name for name, path in
                 spec.get('image_tools', {}).get('executables', {}).items()}
    for name in spec.get('image_tools', {}).get('executables', {}):
        if name in tools:
            launchers[tools[name]['resolved']] = root/'native-tools'/name
    if 'ruby' in tools:
        for field in ('executable', 'resolved'):
            launchers[tools['ruby'][field]] = wrappers/'ruby'
    if 'perl' in spec.get('image_tools', {}).get('executables', {}):
        launchers[spec['image_tools']['executables']['perl']] = wrappers/'perl'
    for name in ('npm', 'yarn', 'pnpm', 'mvn', 'rake'):
        if name in tools and (wrappers/name).is_file():
            launchers[tools[name]['executable']] = wrappers/name
    for path, executable in launchers.items():
        target = destination(path)
        remove(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('#!/bin/sh\nexec ' + shlex.quote(str(executable)) + ' "$@"\n')
        target.chmod(0o755)
    return '\n'.join(environment) + '\n'


def restore_inputs(value):
    # This literal inventory is uploaded from the patcher, not imported from
    # /setup_files or from the task's package installation.
    for index, path in enumerate(value['paths']):
        target = destination(path)
        remove(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        offline = os.environ.get('OT_VERIFIER_OFFLINE')
        source = Path(offline)/'sources'/('cache-'+str(index)) if offline else None
        if source is not None and source.is_dir():
            shutil.copytree(source, target, symlinks=True)
        else:
            target.mkdir()
    if value['installer'] != 'go':
        project = destination('/home/' + value['repo'])
        # These ignored dependency installations are rebuilt at the baseline,
        # before the trusted harness applies the submitted source patch.
        modules = list(project.rglob('node_modules'))
        for path in sorted(modules, key=lambda p: len(p.parts), reverse=True):
            if '.git' not in path.relative_to(project).parts:
                remove(destination(str(path)))


if __name__ == '__main__':
    if sys.argv[1] == '--inputs':
        restore_inputs(json.loads(Path(sys.argv[2]).read_text()))
    else:
        plan = json.loads(Path(sys.argv[1]).read_text())
        root = Path(os.environ['OT_VERIFIER_PYTHON']).parent.parent
        environment = restore(root, plan['spec'], plan['tools'])
        output = destination(sys.argv[2])
        remove(output)
        with output.open('x') as stream:
            stream.write(environment)
