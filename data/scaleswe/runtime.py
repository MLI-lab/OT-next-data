"""Prepare Scale SWE grading tools from observations of pristine task images.

Task checkouts and their installed dependencies remain the code under test. Only
the interpreter, pytest, its plugins and their dependency closure are prepared.
An observation is required for every image; repository names are not evidence
that two task images contain the same interpreter or packages.
"""
import json
import re

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

from data.utils.verifier_runtime import preparation_command, runtime_reference, runtime_spec
from validation.contract import files_digest


# Executed only in the pristine image, with the original test.sh PATH and cwd.
PROBE = r'''
import ast, configparser, importlib.util, json, os, pathlib, platform, re, sys, sysconfig
try:
    from importlib import metadata
except ImportError:
    import importlib_metadata as metadata
packages = []
for dist in metadata.distributions():
    direct = dist.read_text('direct_url.json')
    packages.append(dict(name=dist.metadata['Name'], version=dist.version,
        requires=list(dist.requires or []), extras=list(dist.metadata.get_all('Provides-Extra') or []),
        location=str(dist.locate_file('')),
        direct_url=json.loads(direct) if direct else None,
        metadata_path=str(dist._path),
        plugin_origins={p.value.split(':')[0]: getattr(importlib.util.find_spec(p.value.split(':')[0]), 'origin', None)
                        for p in dist.entry_points if p.group == 'pytest11'},
        plugins=[dict(name=p.name, value=p.value) for p in dist.entry_points if p.group == 'pytest11']))
cwd = pathlib.Path.cwd()
project_names = {cwd.name}
for dist in metadata.distributions(path=[str(cwd), str(cwd / 'src')]):
    if dist.metadata['Name']:
        project_names.add(dist.metadata['Name'])
config = configparser.ConfigParser(interpolation=None, strict=False)
config.read(str(cwd / 'setup.cfg'))
if config.has_option('metadata', 'name'):
    project_names.add(config.get('metadata', 'name'))
if (cwd / 'pyproject.toml').is_file():
    section = None
    for line in (cwd / 'pyproject.toml').read_text().splitlines():
        if line.strip().startswith('['):
            section = line.strip()
        match = re.fullmatch(r"\s*name\s*=\s*['\"]([^'\"]+)['\"]\s*(?:#.*)?", line)
        if section in ('[project]', '[tool.poetry]') and match:
            project_names.add(match.group(1))
if (cwd / 'setup.py').is_file():
    try:
        tree = ast.parse((cwd / 'setup.py').read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and (getattr(node.func, 'id', None) == 'setup' or getattr(node.func, 'attr', None) == 'setup'):
                for kw in node.keywords:
                    if kw.arg == 'name' and isinstance(kw.value, ast.Str):
                        project_names.add(kw.value.s)
    except (SyntaxError, UnicodeError):
        pass
print(json.dumps(dict(format='scaleswe-image-observation-v1',
    python=platform.python_version(), implementation=platform.python_implementation(), executable=sys.executable,
    base_executable=getattr(sys, '_base_executable', sys.executable),
    prefix=sys.prefix, base_prefix=sys.base_prefix, sys_path=sys.path,
    architecture=platform.machine(), libc='musl' if 'musl' in platform.libc_ver()[0] else 'gnu',
    cwd=os.getcwd(), project_names=sorted(project_names), packages=packages)))
'''


def entrypoint(files):
    """Recognize the existing scorer invocation without rewriting grading logic."""
    script = files['tests/test.sh'].decode()
    command = 'python /tests/score.py /tests/test_ids.json'
    if script.count(command) != 1:
        raise ValueError('Scale SWE scorer invocation requires review')
    matches = re.findall(r'^cd (/workspace/[A-Za-z0-9_.+-]+) \|\| exit 1$', script, re.M)
    if len(matches) != 1:
        raise ValueError('Scale SWE working directory requires review')
    if re.search(r'/setup_files|\bactivate\b', script):
        raise ValueError('Scale SWE grading uses an agent setup entrypoint')
    remaining = script.replace(command, '')
    if re.search(r'(^|[;&|]\s*)(?:python[0-9.]*|pytest|pip[0-9.]*)\s', remaining, re.M):
        raise ValueError('Additional Scale SWE grading executable requires review')
    return matches[0], command


def plan(files, observation, *, interpreter='image'):
    """Pin the original grading dependency closure, preserving editable plugins."""
    if (observation.get('returncode') != 0 or not observation.get('source_unchanged')
            or observation.get('source_content_sha256') != files_digest(files.items())):
        raise ValueError('Missing matching pristine Scale SWE observation')
    measured = json.loads(observation['stdout'])
    if interpreter not in ('image', 'standalone'):
        raise ValueError('Unknown interpreter preparation strategy')
    if measured.get('format') != 'scaleswe-image-observation-v1':
        raise ValueError('Unsupported Scale SWE observation')
    if measured.get('implementation', 'CPython') != 'CPython':
        raise ValueError('Original verifier requires a non-CPython runtime')
    cwd, _ = entrypoint(files)
    if measured['cwd'] != cwd:
        raise ValueError('Observed interpreter used a different working directory')
    packages = {}
    project_metadata_variants = {}
    project_names = {canonicalize_name(name) for name in measured.get('project_names', [])}
    def is_project(package):
        direct = package.get('direct_url') or {}
        return (canonicalize_name(package['name']) in project_names
                or package['location'].startswith(cwd + '/') or package['location'] == cwd
                or direct.get('dir_info', {}).get('editable', False))

    shadowed = {}
    nameless = []
    project_plugins = []
    for package in measured['packages']:
        if not package.get('name'):
            # Broken metadata without a Name cannot be selected by name. pluggy would
            # still load its entry points, so only plugin-free ones are ignored.
            if package.get('plugins'):
                origins = package.get('plugin_origins', {})
                metadata_path = package.get('metadata_path', '')
                if (not metadata_path.startswith(package['location'] + '/')
                        or not metadata_path.endswith(('.dist-info', '.egg-info'))
                        or not all(isinstance(origins.get(p['value'].split(':')[0]), str)
                                   and origins[p['value'].split(':')[0]].startswith(cwd + '/')
                                   for p in package['plugins'])):
                    raise ValueError('Nameless installed distribution registers pytest plugins')
                # These plugins are the editable project under test. Preserve their
                # original metadata and imports, rather than installing a guessed package.
                project_plugins.extend(dict(p, distribution=None, metadata_path=metadata_path)
                                       for p in package['plugins'])
            nameless.append(dict(version=package.get('version'), location=package.get('location')))
            continue
        name = canonicalize_name(package['name'])
        if name in packages and packages[name] != package:
            previous = packages[name]
            # The checkout may expose older egg-info than an installed copy of
            # the task package. Preserve both in the task environment and keep
            # metadata's original first-match precedence, never reinstall either
            # as a grading tool. Conflicting plugin entry points need review.
            if is_project(previous) and is_project(package):
                if previous['plugins'] != package['plugins']:
                    raise ValueError('Ambiguous installed distribution: ' + name)
                variants = project_metadata_variants.setdefault(name, [
                    dict(version=previous['version'], location=previous['location'])])
                variants.append(dict(version=package['version'], location=package['location']))
                continue
            # The probe lists distributions in sys.path order, so the first copy is
            # the one Python imports and metadata resolves. A later copy stays in the
            # task environment; it only blocks preparation if grading needs this
            # distribution (checked below) or if pluggy would load its entry points.
            if package['plugins']:
                raise ValueError('Ambiguous installed distribution: ' + name)
            shadowed.setdefault(name, []).append(dict(version=package['version'], location=package['location']))
            continue
        packages[name] = package
    preserved = set()
    for name, package in packages.items():
        if is_project(package):
            preserved.add(name)
    plugins = [dict(p, distribution=name) for name, package in packages.items() for p in package['plugins']] + project_plugins
    roots = {'pytest'} | {name for name, package in packages.items() if package['plugins']}
    pending = [(name, set()) for name in sorted(roots - preserved)]
    selected = set()
    unsatisfied = []
    processed_extras = {}
    marker_environment = dict(python_version='.'.join(measured['python'].split('.')[:2]),
        python_full_version=measured['python'], sys_platform='linux', platform_system='Linux',
        platform_machine=measured['architecture'], implementation_name='cpython',
        implementation_version=measured['python'], platform_python_implementation='CPython', extra='')
    while pending:
        name, extras = pending.pop()
        if name in preserved:
            continue
        package = packages.get(name)
        if package is None:
            raise ValueError('Missing original grading dependency: ' + name)
        if not extras.issubset(set(package.get('extras', []))):
            raise ValueError('Unknown grading dependency extras: ' + name)
        if name in processed_extras and extras.issubset(processed_extras[name]):
            continue
        processed_extras.setdefault(name, set()).update(extras)
        selected.add(name)
        for value in package['requires']:
            requirement = Requirement(value)
            if requirement.marker and not any(requirement.marker.evaluate({**marker_environment, 'extra': extra})
                    for extra in [''] + sorted(processed_extras[name])):
                continue
            dependency = canonicalize_name(requirement.name)
            installed = packages.get(dependency)
            if installed is None:
                raise ValueError('Inconsistent original grading dependency: ' + value)
            if not requirement.specifier.contains(installed['version'], prereleases=True):
                # Upstream shipped this combination. Install exactly the observed
                # versions without dependency resolution (install_no_deps) and let
                # the reference/no-op stages show whether grading works with it.
                unsatisfied.append(dict(distribution=name, requirement=value,
                                        installed=dependency + '==' + installed['version']))
            if requirement.url:
                raise ValueError('Grading dependency URL requires explicit review: ' + value)
            pending.append((dependency, set(requirement.extras)))
    if 'pytest' in preserved:
        raise ValueError('Task modifies pytest itself; separate grading design required')
    def vendored_later(name):
        # e.g. setuptools appends site-packages/setuptools/_vendor to sys.path: its
        # packaging copy comes after site-packages, so `import packaging` and metadata
        # both resolve to the first copy.
        order = measured['sys_path']
        first = packages[name]['location']
        return first in order and all(
            '_vendor' in copy['location'].split('/') and copy['location'] in order
            and order.index(copy['location']) > order.index(first) for copy in shadowed[name])
    ambiguous = sorted(name for name in selected & set(shadowed) if not vendored_later(name))
    if ambiguous:
        raise ValueError('Ambiguous installed distribution: ' + ambiguous[0])
    requirements = []
    for name in sorted(selected):
        pin = name + '==' + packages[name]['version']
        if (packages[name].get('direct_url') or {}).get('url'):
            pin = vcs_requirement(name, packages[name])
        requirements.append(pin)
    image = {'image_sha256': observation['image_sha256'],
             'executable': measured['base_executable']}
    for key in ('overlay_sha256', 'deferred_sha256'):
        if key in observation:
            image[key] = observation[key]
    # Legacy Matplotlib is already installed in the pristine image. Reuse its
    # exact modules and metadata instead of rebuilding old sources on the host.
    if 'matplotlib' in selected and packages['matplotlib']['version'] in ('3.1.3', '3.2.2'):
        image['packages'] = {'matplotlib==' + packages['matplotlib']['version']:
                             ['matplotlib', 'mpl_toolkits', 'pylab']}
        interpreter = 'image'
    spec = runtime_spec(python=measured['python'], architecture=measured['architecture'],
        libc=measured['libc'], requirements=requirements,
        tools=['pytest'], image_python=image if interpreter == 'image' else None, install_no_deps=True)
    paths = list(dict.fromkeys([cwd + '/src', cwd] + [p for p in measured['sys_path']
        if p and (p.endswith(('/site-packages', '/dist-packages')) or p.startswith(cwd + '/'))]))
    if any(not p.startswith('/') or '..' in p.split('/') for p in paths):
        raise ValueError('Unsafe project import path')
    if any(not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_.]*(?::[A-Za-z_][A-Za-z0-9_.]*)?', p['value']) for p in plugins):
        raise ValueError('Unrecognized pytest plugin entry point')
    if len({p['name'] for p in plugins}) != len(plugins):
        raise ValueError('Conflicting original pytest plugin names')
    return dict(spec=spec, paths=paths, plugins=plugins,
                project_distributions=sorted(preserved), grading_distributions=sorted(selected),
                project_metadata_variants=project_metadata_variants,
                shadowed_distributions=shadowed, unsatisfied_original_requirements=unsatisfied,
                nameless_distributions=nameless)


# Grading distributions installed from a VCS commit in the original image, with the
# GitHub archive of exactly that commit pinned by sha256 (downloaded twice, checked
# that the packaged version matches the observation, 2026-11-10).
VCS_SOURCES = {
    ('https://github.com/syrusakbary/snapshottest.git', '54117098bd756c0fa6936f3bafab4bf85c58e943'): dict(
        version='0.5.0', url='https://github.com/syrusakbary/snapshottest/archive/'
        '54117098bd756c0fa6936f3bafab4bf85c58e943.tar.gz',
        sha256='ae8bf43e05b263dea25b5fa4612bda7ed84aad54a890237d85832dbcd833ff97'),
    ('https://github.com/Teemu/pytest-sugar.git', 'ee02ada200026f4bfabf2252c6c59b08f5564255'): dict(
        version='0.9.4', url='https://github.com/Teemu/pytest-sugar/archive/'
        'ee02ada200026f4bfabf2252c6c59b08f5564255.tar.gz',
        sha256='c25915aa706df66eec825af9376dfbef2f4dee96a96dbd58ebe537295f8734e9'),
}


def vcs_requirement(name, package):
    """Install a VCS-installed grading distribution from its pinned commit archive."""
    direct = package.get('direct_url') or {}
    vcs = direct.get('vcs_info') or {}
    source = VCS_SOURCES.get((direct.get('url'), vcs.get('commit_id')))
    if vcs.get('vcs') != 'git' or source is None or source['version'] != package['version']:
        raise ValueError('Non-index grading distribution needs a pinned source: ' + name)
    return name + ' @ ' + source['url'] + '#sha256=' + source['sha256']


def plugin_loader_module(plugins):
    """Install the plugin guard after pluggy loads, never during early .pth setup."""
    return ('''import sys
from importlib.machinery import PathFinder

PLUGINS = ''' + repr(plugins) + '''

def install(pluggy):
    try:
        from importlib import metadata
    except ImportError:
        import importlib_metadata as metadata
    try:
        from pluggy._manager import DistFacade
    except ImportError:
        from pluggy.manager import DistFacade
    original_load = pluggy.PluginManager.load_setuptools_entrypoints

    def load(self, group, name=None):
        if group != 'pytest11':
            return original_load(self, group, name)
        count = 0
        for entry in PLUGINS:
            key = entry['name']
            if ((name is not None and name != key) or self.get_plugin(key) or self.is_blocked(key)):
                continue
            plugin = metadata.EntryPoint(name=key, value=entry['value'], group=group).load()
            self.register(plugin, name=key)
            self._plugin_distinfo.append((plugin, DistFacade(metadata.distribution(entry['distribution']) if entry['distribution'] is not None
                                                       else metadata.PathDistribution(__import__('pathlib').Path(entry['metadata_path'])))))
            count += 1
        return count

    pluggy.PluginManager.load_setuptools_entrypoints = load

class Loader:
    def __init__(self, original):
        self.original = original
    def create_module(self, spec):
        return self.original.create_module(spec) if hasattr(self.original, 'create_module') else None
    def exec_module(self, module):
        self.original.exec_module(module)
        install(module)

class Finder:
    @staticmethod
    def find_spec(name, path=None, target=None):
        if name != 'pluggy':
            return None
        spec = PathFinder.find_spec(name, path)
        if spec is None:
            return None
        sys.meta_path.remove(Finder)
        spec.loader = Loader(spec.loader)
        return spec

if 'pluggy' in sys.modules:
    install(sys.modules['pluggy'])
else:
    sys.meta_path.insert(0, Finder)
''')


def prepared_files(files, spec, catalog, *, observation, image_source=None):
    from data.scaleswe.patch import VERIFIER_VENV, VERIFIER_VENV_SETUP, project_paths_module
    reviewed = plan(files, observation, interpreter='image' if 'image_python' in spec else 'standalone')
    if spec != reviewed['spec']:
        raise ValueError('Runtime differs from observed grading dependency closure')
    if any(k in files for k in ('tests/setup.sh', 'tests/ot-verifier-runtime.json',
                                'tests/ot_project_paths.py', 'tests/ot_plugin_allowlist.py')):
        raise ValueError('Existing Scale SWE runtime setup requires composition review')
    _, command = entrypoint(files)
    result = dict(files)
    result.update(runtime_reference(spec, catalog, image_source=image_source))
    result['tests/setup.sh'] = ('#!/bin/bash\nset -euo pipefail\n' +
        preparation_command(spec, phase='verifier', image_source=image_source) + '\n' +
        ': "${OT_VERIFIER_PYTHON:?trusted controller runtime required}"\n' + VERIFIER_VENV_SETUP +
        'cp /tests/ot_plugin_allowlist.py "$ot_site/ot_plugin_allowlist.py"\n' +
        'cat > ' + VERIFIER_VENV + '/bin/pytest <<\'OT_CLEAN_PYTEST\'\n'
        '#!/bin/sh\nexec ' + VERIFIER_VENV + '/bin/python -I -m pytest "$@"\nOT_CLEAN_PYTEST\n' +
        'chmod 755 ' + VERIFIER_VENV + '/bin/pytest\n' +
        'ln -s pytest ' + VERIFIER_VENV + '/bin/py.test\n').encode()
    # Do not discover additional pytest plugins from the agent's site-packages.
    # Explicit original plugins include editable task plugins, which are tested
    # from their submitted project paths rather than copied into the runtime.
    invocation = ('PATH=' + VERIFIER_VENV + '/bin:"${OT_VERIFIER_BIN:?trusted runtime required}:$PATH" '
        'PYTEST_DISABLE_PLUGIN_AUTOLOAD= PYTEST_PLUGINS= ' + VERIFIER_VENV +
        '/bin/python -I /tests/score.py /tests/test_ids.json')
    result['tests/test.sh'] = files['tests/test.sh'].decode().replace(command, invocation).encode()
    result['tests/ot_project_paths.py'] = (project_paths_module(reviewed['paths']) + '\nimport ot_plugin_allowlist\n').encode()
    result['tests/ot_plugin_allowlist.py'] = plugin_loader_module(reviewed['plugins']).encode()
    return result
