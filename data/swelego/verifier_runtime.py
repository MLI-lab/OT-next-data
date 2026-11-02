"""Plan clean grading tools from an observed SWE-Lego dependency installation."""
import json
import re

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

from data.utils.verifier_runtime import runtime_spec


# This compiled itables image supplies a pytest plugin through Shiny's local
# 0.0.0 distribution. That version is not available from PyPI. Copy only its
# named package and metadata from these exact pristine bytes; do not upgrade it.
IMAGE_PACKAGES = {
    ('04f6f19ff350d7a406452892ac4c5e45ac32b8ff4ab58d6601a12b184d225fdc',
     '78168749d31230f17215dc8d7bda09b0250537991a349804f3892723de3289fd',
     '4a1a2e301bbc7cddd3bebfa5088a2779495041bbb86179f69abe26abb996e204'):
        {'shiny==0.0.0': ['shiny']},
}


PROBE = r'''import json, os, platform, sys, sysconfig
packages = []
plugins = []
try:
    import importlib.metadata as metadata
except ImportError:
    try:
        import importlib_metadata as metadata
    except ImportError:
        metadata = None
if metadata is None:
    import pkg_resources
    import email
    for dist in pkg_resources.working_set:
        metadata_name = 'METADATA' if dist.has_metadata('METADATA') else 'PKG-INFO'
        document = email.message_from_string(dist.get_metadata(metadata_name))
        requires = document.get_all('Requires-Dist') or []
        if dist.has_metadata('requires.txt'):
            raise ValueError('Legacy egg dependency metadata needs explicit review: ' + dist.project_name)
        packages.append(dict(name=dist.project_name, version=dist.version, location=dist.location,
                             requires=requires))
        for name, ep in dist.get_entry_map('pytest11').items():
            plugins.append(dict(name=name, module=ep.module_name, distribution=dist.project_name))
else:
    for dist in metadata.distributions():
        packages.append(dict(name=dist.metadata['Name'], version=dist.version,
                             location=str(dist.locate_file('')), requires=dist.requires or []))
        for ep in dist.entry_points:
            if ep.group == 'pytest11':
                plugins.append(dict(name=ep.name, module=ep.value.split(':', 1)[0],
                                    distribution=dist.metadata['Name']))
environment = dict(os_name=os.name, sys_platform=sys.platform, platform_machine=platform.machine(),
    platform_python_implementation=platform.python_implementation(), platform_release=platform.release(),
    platform_system=platform.system(), platform_version=platform.version(),
    python_version='.'.join(platform.python_version_tuple()[:2]), python_full_version=platform.python_version(),
    implementation_name=sys.implementation.name, implementation_version=platform.python_version())
print(json.dumps(dict(python=platform.python_version(), executable=sys.executable, prefix=sys.prefix,
    packages=packages, pytest_plugins=plugins, marker_environment=environment)))
'''


def plan(measured, image_bundle):
    """Keep the recorded Python and the installed pytest/plugin dependency closure.

    Missing metadata is an incomplete preparation, never permission to upgrade
    packages or resolve a replacement tool environment.
    """
    version, prefix = measured['python'], measured['prefix']
    if (not re.fullmatch(r'3\.\d+\.\d+', version)
            or prefix not in ('/opt/conda/envs/testbed', '/opt/miniconda3/envs/testbed')
            or measured['executable'] not in (prefix+'/bin/python', prefix+'/bin/python3')):
        raise ValueError('Unreviewed SWE-Lego interpreter')
    expected_site = prefix + '/lib/python' + '.'.join(version.split('.')[:2]) + '/site-packages'
    packages = {}
    for package in measured['packages']:
        # pkg_resources can expose setuptools' private vendored distributions
        # while inspecting metadata. They are not separately installed tools.
        if package['location'].rstrip('/') != expected_site:
            continue
        name = canonicalize_name(package['name'])
        if name in packages and packages[name] != package:
            raise ValueError('Conflicting installed package metadata: ' + name)
        packages[name] = package
    plugins = []
    pending = [('pytest', frozenset())]
    for plugin in measured['pytest_plugins']:
        name = canonicalize_name(plugin['distribution'])
        if name not in packages:
            raise ValueError('Plugin is outside the recorded dependency environment: ' + name)
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*', plugin['module']):
            raise ValueError('Unreviewed pytest plugin entrypoint')
        plugins.append(plugin['module'])
        pending.append((name, frozenset()))
    seen, selected = set(), {}
    while pending:
        name, extras = pending.pop()
        state = (name, extras)
        if state in seen:
            continue
        seen.add(state)
        if name not in packages:
            raise ValueError('Missing recorded verifier dependency: ' + name)
        package = packages[name]
        selected[name] = name + '==' + package['version']
        for text in package['requires']:
            requirement = Requirement(text)
            if requirement.url:
                raise ValueError('Verifier source dependency needs a checksum-pinned recipe')
            if requirement.marker and not any(requirement.marker.evaluate({
                    **measured['marker_environment'], 'extra': extra}) for extra in ('', *sorted(extras))):
                continue
            # --no-deps in task setup preserves the installed versions even when
            # metadata constraints disagree. Do not silently resolve new versions.
            pending.append((canonicalize_name(requirement.name), frozenset(requirement.extras)))
    name = image_bundle['name']
    binding = {'image_sha256': image_bundle['files'][name]['sha256'],
               'executable': measured['executable']}
    for suffix, key in [('.overlay.img', 'overlay_sha256'), ('.deferred.json', 'deferred_sha256')]:
        path = name.replace('.sif', suffix)
        if path in image_bundle['files']:
            binding[key] = image_bundle['files'][path]['sha256']
    image_packages = IMAGE_PACKAGES.get(tuple(binding.get(key) for key in
        ('image_sha256', 'overlay_sha256', 'deferred_sha256')), {})
    copied = {pin: modules for pin, modules in image_packages.items() if pin in selected.values()}
    if copied:
        binding['packages'] = copied
    spec = runtime_spec(python=version, requirements=list(selected.values()), tools=['pytest'],
                        install_no_deps=True, image_python=binding)
    return {'spec': spec, 'plugins': list(dict.fromkeys(plugins)), 'prefix': prefix, 'python': version}


def prepared_files(files, spec, catalog, *, observation, image_bundle):
    from validation.contract import files_digest
    from data.swelego.patch import _prepared_verifier_files
    if (observation.get('kind') != 'swelego-dependencies-v1'
            or observation.get('returncode') != 0 or not observation.get('source_bundle_unchanged')
            or observation.get('source_content_sha256') != files_digest(files.items())):
        raise ValueError('Missing matching successful dependency preparation observation')
    measured = json.loads(observation['stdout'].splitlines()[-1])
    reviewed = plan(measured, image_bundle)
    metadata = json.loads(files['setup_files/swelego.json'])
    repository = metadata.get('repo', '')
    subject = canonicalize_name(repository.rsplit('/', 1)[-1])
    grading_distributions = {canonicalize_name(pin.split('==', 1)[0])
                             for pin in reviewed['spec']['requirements']}
    from data.swelego.subject_imports import reviewed as reviewed_subject
    subject_import = reviewed_subject(metadata)
    if subject in grading_distributions and not subject_import:
        raise ValueError('Task changes a grading dependency; subject-specific import handling is required: ' + repository)
    if spec != reviewed['spec']:
        raise ValueError('Runtime differs from observed SWE-Lego grading tools')
    return _prepared_verifier_files(files, spec, catalog, python_version=reviewed['python'],
                                    prefix=reviewed['prefix'], plugins=reviewed['plugins'])
