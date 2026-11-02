"""Source-image SDK recipes for Multi-SWE's clean verifier runtime.

Only dependency roots are eligible. Repository files remain live test subjects.
The image inspection is performed during preparation, never against a trial.
"""
import json
from pathlib import Path, PurePosixPath
import re

from data.utils.verifier_runtime import runtime_spec
from hpc.offline_prep.image_runtime_assets import canonical


PREINSTALLED_NODE = {'insomnia', 'github-readme-stats', 'async', 'trpc', 'redux',
                     'material-ui', 'dayjs', 'react-router'}


def candidates(repo, tools):
    """Return explicit dependency candidates to inspect in the pristine image."""
    if not re.fullmatch(r'[A-Za-z0-9_.-]+', repo):
        raise ValueError('Invalid repository basename')
    trees, files, dereference = {}, {}, []
    if tools.get('go', {}).get('resolved', '').startswith('/usr/local/go/'):
        files['go-environment'] = '/root/.config/go/env'
    if 'node' in tools:
        files.update({'node-config-'+str(i): path for i, path in enumerate(
            ('/root/.npmrc', '/root/.yarnrc', '/root/.yarnrc.yml', '/etc/npmrc', '/etc/yarnrc', '/etc/yarnrc.yml'))})
        for name, path in [('node-local', '/usr/local/lib/node_modules'),
                           ('node-system', '/usr/lib/node_modules'),
                           ('node-distro', '/usr/share/nodejs')]:
            trees[name] = path
        yarn = tools.get('yarn', {}).get('resolved', '')
        match = re.match(r'(/opt/yarn-v[0-9.]+)/', yarn)
        if match:
            trees['yarn'] = match[1]
        if repo in PREINSTALLED_NODE:
            trees['project-modules'] = '/home/' + repo + '/node_modules'
    java = tools.get('java', {}).get('resolved', '')
    if java:
        java_root = PurePosixPath(java).parent.parent
        if java_root.name == 'jre':
            java_root = java_root.parent
        trees['jdk'] = str(java_root)
        dereference.append('jdk')
        for name, path in [('maven', '/usr/share/maven'),
                           ('maven-inputs', '/opt/multiswe-maven'),
                           ('legacy-maven-inputs', '/opt/multiswe-legacy-maven'),
                           ('maven-distributions', '/opt/multiswe-maven-user/wrapper/dists'),
                           ('gradle-inputs', '/opt/multiswe-gradle/caches/modules-2'),
                           ('gradle-distributions', '/opt/multiswe-gradle/wrapper/dists')]:
            trees[name] = path
        dereference.append('maven')
        files.update({'maven-settings': '/opt/multiswe-maven-settings.xml',
                 'junit-local': '/opt/multiswe-junit-local.gradle',
                 'grgit-init': '/opt/multiswe-gradle/init.d/multiswe-grgit.gradle',
                 'gradle-properties': '/opt/multiswe-gradle/gradle.properties'})
    if 'ruby' in tools:
        trees.update({'ruby-stdlib': '/usr/lib/ruby',
                      'ruby-native': '/usr/lib/x86_64-linux-gnu/ruby',
                      'ruby-gems': '/usr/share/rubygems-integration',
                      'ruby-native-gems': '/usr/lib/x86_64-linux-gnu/rubygems-integration'})
        match = re.search(r'ruby ([0-9]+\.[0-9]+)', tools['ruby']['version'])
        if match:
            trees['ruby-installed-gems'] = '/var/lib/gems/' + match[1] + '.0'
        dereference.extend(name for name in trees if name.startswith('ruby-'))
    if 'cmake' in tools:
        match = re.search(r'cmake version ([0-9]+\.[0-9]+)', tools['cmake']['version'])
        if match:
            trees['cmake-data'] = '/usr/share/cmake-' + match[1]
    gcc = tools.get('gcc', {}).get('version', '')
    match = re.search(r'\) ([0-9]+)(?:\.[0-9]+)*', gcc)
    if match:
        trees['gcc-sdk'] = '/usr/lib/gcc/x86_64-linux-gnu/' + match[1]
        dereference.append('gcc-sdk')
    return {'trees': trees, 'files': files, 'dereference': dereference,
            'project': '/home/' + repo if repo in PREINSTALLED_NODE else None,
            'compiler': tools.get('gcc', {}).get('executable'), 'autotools': repo == 'jq'}


def inspection_script(request):
    """Read availability, never run repository initialization or grading."""
    return ('import json, pathlib, shutil, os, subprocess\nrequest = ' + repr(request) + '\n'
            'out = {kind: {name: path for name,path in request[kind].items() '
            'if getattr(pathlib.Path(path), "is_dir" if kind == "trees" else "is_file")()} '
            'for kind in ("trees", "files")}\n'
            'out["trees"] = {name:path for name,path in out["trees"].items() '
            'if any(not p.name.startswith(".wh.") and p.is_file() for p in pathlib.Path(path).rglob("*"))}\n'
            'out["executables"] = {}\n'
            'out["node_distro_dereference"] = False\n'
            'if "node-distro" in out["trees"]:\n'
            ' node_root = pathlib.Path(out["trees"]["node-distro"])\n'
            ' out["node_distro_dereference"] = any(p.is_symlink() and not p.resolve().is_relative_to(node_root) for p in node_root.rglob("*"))\n'
            'for name in ("cc", "c++", "gfortran", "ar", "as", "ld", "ranlib", "strip", "nm", "objdump", "pkg-config"):\n'
            ' path = shutil.which(name)\n'
            ' if path and pathlib.Path(path).read_bytes()[:4] == b"\\x7fELF": out["executables"][name] = path\n'
            'out["workspace_modules"] = []\n'
            'out["autotools"] = {"trees": {}, "files": {}}\n'
            'if request["autotools"]:\n'
            ' for name in ("perl", "m4"):\n'
            '  path = shutil.which(name)\n'
            '  if not path: raise ValueError("Missing pristine autotools executable: " + name)\n'
            '  out["executables"][name] = path\n'
            ' includes = subprocess.check_output([out["executables"]["perl"], "-e", \'print join("\\\\n", @INC)\'], text=True).splitlines()\n'
            ' for index,path in enumerate(includes):\n'
            '  if pathlib.Path(path).is_dir() and any(p.is_file() for p in pathlib.Path(path).rglob("*")): out["autotools"]["trees"]["perl-"+str(index)] = path\n'
            ' for pattern in ("autoconf", "automake-*", "aclocal*", "libtool"):\n'
            '  for path in pathlib.Path("/usr/share").glob(pattern):\n'
            '   if path.is_dir(): out["autotools"]["trees"]["auto-"+path.name] = str(path)\n'
            ' for name in ("autoreconf", "autoconf", "autoheader", "automake", "aclocal", "libtoolize"):\n'
            '  path = "/usr/bin/"+name\n'
            '  if pathlib.Path(path).is_file(): out["autotools"]["files"]["tool-"+name] = path\n'
            'out["compiler_trees"] = {}\n'
            'if request["compiler"]:\n'
            ' sdk = pathlib.Path(subprocess.check_output([request["compiler"], "-print-libgcc-file-name"], text=True).strip()).resolve().parent\n'
            ' out["compiler_trees"]["gcc-sdk"] = str(sdk)\n'
            ' prefix = "/usr/local" if str(sdk).startswith("/usr/local/") else "/usr"\n'
            ' executables = pathlib.Path(prefix)/"libexec/gcc"/sdk.parent.name/sdk.name\n'
            ' if executables.is_dir(): out["compiler_trees"]["gcc-executables"] = str(executables)\n'
            ' for name,path in [("cxx-headers", prefix+"/include/c++/"+sdk.name), ("cxx-native-headers", prefix+"/include/x86_64-linux-gnu/c++/"+sdk.name)]:\n'
            '  if pathlib.Path(path).is_dir(): out["compiler_trees"][name] = path\n'
            'if request["project"]:\n'
            ' for parent, dirs, files in os.walk(request["project"], followlinks=False):\n'
            '  dirs[:] = [d for d in dirs if d != ".git"]\n'
            '  if "node_modules" in dirs:\n'
            '   out["workspace_modules"].append(str(pathlib.Path(parent)/"node_modules"))\n'
            '   dirs.remove("node_modules")\n'
            'print(json.dumps(out))\n')


def inspection_command(image, scratch, request):
    from hpc.offline_prep.image_python_runtime import command
    argv = command(image, scratch, '/opt/multiswe-python/bin/python3', '-I', '-c', inspection_script(request))
    if argv[:2] == ['apptainer', 'exec']:
        # Gradle caches can be root-readable even outside /root. The host's
        # fakeroot interposer requires a newer libc than some source images;
        # use the root user namespace without injecting that interposer.
        argv[2:2] = ['--fakeroot', '--ignore-fakeroot-command']
    return argv


def specification(base, image, repo, tools, inspected):
    """Bind fresh native tools and SDK dependencies to the exact task image."""
    request = candidates(repo, tools)
    for kind in ('trees', 'files'):
        if any(request[kind].get(name) != path for name, path in inspected[kind].items()):
            raise ValueError('Image inspection returned an undeclared dependency')
    executables = {name: info['executable'] for name, info in tools.items()
                   if name in ('git', 'node', 'ruby', 'cmake', 'ctest', 'make', 'gcc', 'g++')}
    extra = inspected.get('executables', {})
    allowed_extra = {'cc', 'c++', 'gfortran', 'ar', 'as', 'ld', 'ranlib', 'strip', 'nm', 'objdump', 'pkg-config'}
    if repo == 'jq':
        allowed_extra.update(('perl', 'm4'))
    if not set(extra) <= allowed_extra:
        raise ValueError('Unreviewed compiler support executable')
    executables.update(extra)
    if 'git' not in executables:
        raise ValueError('Multi-SWE patch extraction requires clean Git')
    if repo in PREINSTALLED_NODE and 'project-modules' not in inspected['trees']:
        raise ValueError('Missing preinstalled project dependencies')
    trees = dict(inspected['trees'])
    extra_files = {}
    autotools = inspected.get('autotools', {})
    if any(autotools.values()):
        if repo != 'jq':
            raise ValueError('Unexpected autotools dependency roots')
        trees.update(autotools.get('trees', {}))
        extra_files = autotools.get('files', {})
    compiler = inspected.get('compiler_trees', {})
    if not set(compiler) <= {'gcc-sdk', 'gcc-executables', 'cxx-headers', 'cxx-native-headers'}:
        raise ValueError('Unreviewed compiler dependency tree')
    trees.update(compiler)
    for index, path in enumerate(sorted(set(inspected.get('workspace_modules', [])))):
        if repo not in PREINSTALLED_NODE or not path.startswith('/home/' + repo + '/'):
            raise ValueError('Workspace dependencies must belong to the declared repository')
        if path not in trees.values():
            trees['project-modules-' + str(index)] = path
    assets = None
    if trees or inspected['files'] or extra_files:
        assets = {**image, 'trees': trees}
        if inspected['files'] or extra_files:
            assets['files'] = {**inspected['files'], **extra_files}
        dereference = [name for name in request['dereference'] if name in trees]
        if inspected.get('node_distro_dereference') and 'node-distro' in trees:
            dereference.append('node-distro')
        if 'gcc-sdk' in trees and 'gcc-sdk' not in dereference:
            dereference.append('gcc-sdk')
        if 'gcc-executables' in trees:
            dereference.append('gcc-executables')
        dereference.extend(name for name in trees if name.startswith(('perl-', 'auto-')))
        if dereference:
            assets['dereference'] = dereference
        required = {name: [relative] for name, relative in [('jdk', 'bin/java'), ('maven', 'bin/mvn')]
                    if name in inspected['trees']}
        for manager in ('npm', 'yarn', 'pnpm'):
            path = tools.get(manager, {}).get('resolved')
            if path:
                roots = [(name, root) for name, root in trees.items() if path.startswith(root + '/')]
                if len(roots) != 1:
                    raise ValueError('Package manager has no unique clean SDK tree: ' + manager)
                name, root = roots[0]
                required.setdefault(name, []).append(path[len(root)+1:])
        if required:
            assets['required'] = required
        if 'project-modules' in trees:
            assets['project_links'] = {name: '/home/' + repo for name in trees if name.startswith('project-modules')}
        assets = canonical(assets)
    kwargs = {k: v for k, v in base.items() if k != 'runtime_layout'}
    kwargs['image_python'] = {**image, 'executable': base['image_python']['executable']}
    kwargs.update(image_tools={**image, 'executables': executables, 'shared_libraries': True},
                  tools=sorted(executables), image_assets=assets)
    # Image probes establish the same exact grader interpreter and package pins.
    # Use this task image for all components so regeneration needs only its image.
    return runtime_spec(**kwargs)


def adapt(files, record, tools):
    from data.utils.verifier_runtime import preparation_command, runtime_reference
    from harbor_patches.verifier_bundle import spec_id
    setup = files['tests/setup.sh'].decode()
    match = re.search(r'^# OT_RUNTIME_RECIPE: (.+)$', setup, re.M)
    if match is None:
        raise ValueError('Missing existing Python runtime recipe')
    previous = json.loads(match[1])
    spec = record['spec']
    if any(previous.get(k) != spec.get(k) for k in ('python', 'requirements', 'architecture', 'libc')):
        raise ValueError('SDK adaptation must preserve the grader interpreter and dependency lock')
    if spec_id(spec) != record['id']:
        raise ValueError('SDK record differs from its identity')
    before = preparation_command(previous, phase='verifier')
    if before not in setup:
        before = preparation_command(previous, phase='verifier', exit_after=False)
        after = preparation_command(spec, phase='verifier', exit_after=False)
    else:
        after = preparation_command(spec, phase='verifier')
    if setup.count(before) != 1:
        raise ValueError('Unexpected SDK preparation entrypoint')
    result = {**files, **runtime_reference(spec, {record['id']: record})}
    result['tests/setup.sh'] = setup.replace(before, after, 1).encode()
    result['tests/restore_sdk.py'] = Path(__file__).with_name('restore_sdk.py').read_bytes()
    result['tests/sdk.json'] = (json.dumps({'spec': spec, 'tools': tools}, sort_keys=True)+'\n').encode()
    test = files['tests/test.sh'].decode()
    anchor = re.search(r'^export PATH=.+$', test, re.M)
    if anchor is None or '/tests/restore_sdk.py' in test:
        raise ValueError('Unexpected native verifier activation')
    activation = ('\n"${OT_VERIFIER_PYTHON:?clean runtime required}" -I /tests/restore_sdk.py '
                  '/tests/sdk.json /tests/sdk-env.sh\nsource /tests/sdk-env.sh')
    result['tests/test.sh'] = (test[:anchor.end()] + activation + test[anchor.end():]).encode()
    return result


def adapt_inputs(files, value, record):
    from data.multiswe.setup_runtime import canonical as input_canonical, paths, install_or_cached
    from data.utils.verifier_runtime import offline_package_reference
    input_canonical(value)
    result = {**files, **offline_package_reference(record['spec'], {record['id']: record}, phase='verifier')}
    setup = files['tests/setup.sh'].decode()
    anchor = 'set -euo pipefail\n'
    if setup.count(anchor) != 1:
        raise ValueError('Unreviewed verifier setup')
    payload = json.dumps(value, sort_keys=True, separators=(',', ':'))
    prepare = ('if [ "${1:-}" = "--prepare" ]; then\n'
               '  python3 -m data.multiswe.setup_runtime --verifier --reference '
               '"$(dirname -- "$0")/ot-verifier-offline.json" <<\'OT_MULTISWE_VERIFIER_INPUTS\'\n'
               + payload + '\nOT_MULTISWE_VERIFIER_INPUTS\nfi\n')
    result['tests/setup.sh'] = setup.replace(anchor, anchor + prepare, 1).encode()
    result['tests/verifier-inputs.json'] = (json.dumps({**value, 'paths': paths(value)})+'\n').encode()
    result['tests/restore_sdk.py'] = Path(__file__).with_name('restore_sdk.py').read_bytes()
    rebuild = ('#!/bin/bash\nset -euo pipefail\n'
               '"${OT_VERIFIER_PYTHON:?clean runtime required}" -I /tests/restore_sdk.py '
               '--inputs /tests/verifier-inputs.json\n')
    if value['installer'] == 'go':
        rebuild += 'unset GOENV\nexport GOCACHE="$(mktemp -d /tmp/multiswe-go-cache-XXXXXXXX)"\n'
    install = install_or_cached(value).replace('OT_TASK_OFFLINE', 'OT_VERIFIER_OFFLINE') + '\n'
    if value['installer'] == 'go':
        install = 'if [ -f go.mod ] && [ ! -d vendor ]; then\n' + install + 'fi\n'
    rebuild += install
    result['tests/rebuild_dependencies.sh'] = rebuild.encode()
    test = files['tests/test.sh'].decode()
    matches = list(re.finditer(r'^bash /tests/extract_fix_patch.sh .+$', test, re.M))
    if len(matches) != 1:
        raise ValueError('Cannot identify the baseline dependency installation point')
    end = matches[0].end()
    result['tests/test.sh'] = (test[:end] + '\nsource /tests/rebuild_dependencies.sh' + test[end:]).encode()
    return result
