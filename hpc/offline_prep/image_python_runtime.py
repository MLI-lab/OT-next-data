"""Copy an exact Python from a pristine image, excluding all installed packages.

The artifact remains image-compatible, not a portable interpreter for arbitrary
hosts. Source image identity is part of the runtime spec and checked on every build.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
from dataclasses import dataclass

from harbor_patches.verifier_bundle import digest


COPY = r'''
import json, pathlib, platform, shutil, sys, sysconfig
target = pathlib.Path(sys.argv[1])
expected = json.loads(sys.argv[2])
assert platform.python_version() == expected['python'], (platform.python_version(), expected['python'])
assert platform.machine() == expected['architecture'], platform.machine()
assert ('musl' if 'musl' in platform.libc_ver()[0] else 'gnu') == expected['libc']
assert sys.prefix == sys.base_prefix, 'Image interpreter is a virtual environment'
stdlib = pathlib.Path(sysconfig.get_path('stdlib'))
assert stdlib.is_dir(), stdlib
target.mkdir()
(target / 'bin').mkdir()
(target / 'lib').mkdir()
shutil.copy2(pathlib.Path(sys.executable).resolve(), target / 'bin/python3')
(target / ('bin/python' + str(sys.version_info.major) + '.' + str(sys.version_info.minor))).symlink_to('python3')
(target / 'bin/python').symlink_to('python3')
# Apptainer may expose overlay whiteout metadata in the merged stdlib.
# These are filesystem control entries, not interpreter modules or data.
shutil.copytree(stdlib, target / 'lib' / stdlib.name,
               ignore=shutil.ignore_patterns('site-packages', 'dist-packages', '__pycache__', '*.pyc', '.wh.*'), symlinks=False)
# Source builds use this interpreter's exact headers, not host headers or an
# unrelated image Python. Only bootstrap recipes and python_headers request these files.
if expected.get('build_requirements') or expected.get('python_headers'):
    prefix = pathlib.Path(sys.base_prefix).resolve()
    for name in ('include', 'platinclude'):
        source = pathlib.Path(sysconfig.get_path(name)).resolve()
        relative = source.relative_to(prefix)
        destination = target / relative
        if not destination.exists():
            shutil.copytree(source, destination, symlinks=False,
                           ignore=shutil.ignore_patterns('.wh.*'))
    assert (target / pathlib.Path(sysconfig.get_path('include')).resolve().relative_to(prefix) / 'Python.h').is_file()
# Shared libpython belongs to the interpreter, not the task's installed packages.
libdir = pathlib.Path(sysconfig.get_config_var('LIBDIR'))
for source in libdir.glob('libpython' + str(sys.version_info.major) + '.' + str(sys.version_info.minor) + '*.so*'):
    shutil.copy2(source, target / 'lib' / source.name, follow_symlinks=True)
# Conda owns native dependencies beside libpython, unlike a distro interpreter.
# ldd resolves the complete dependency closure in this pristine source image.
# Copy only Conda-prefix libraries; platform libraries stay image requirements.
if (pathlib.Path(sys.prefix) / 'conda-meta').is_dir():
    import subprocess, re
    prefix_lib = (pathlib.Path(sys.prefix) / 'lib').resolve()
    candidates = [target / 'bin/python3'] + list((target / 'lib').rglob('*.so*'))
    libraries = {}
    for candidate in candidates:
        if not candidate.is_file():
            continue
        with candidate.open('rb') as stream:
            if stream.read(4) != b'\x7fELF':
                continue
        # Resolve against source paths, before relocation has removed needed libs.
        if candidate == target / 'bin/python3':
            source_binary = pathlib.Path(sys.executable).resolve()
        else:
            source_binary = pathlib.Path(sys.prefix) / candidate.relative_to(target)
        output = subprocess.check_output(['ldd', str(source_binary)], universal_newlines=True)
        if '=> not found' in output:
            raise ValueError('Pristine Conda native dependency is missing: ' + str(source_binary))
        for line in output.splitlines():
            match = re.search(r'=> (/[^ ]+)', line)
            if not match:
                continue
            dependency = pathlib.Path(match.group(1))
            resolved = dependency.resolve()
            try:
                resolved.relative_to(prefix_lib)
            except ValueError:
                continue
            previous = libraries.setdefault(dependency.name, resolved)
            if previous != resolved:
                raise ValueError('Conflicting Conda library basename: ' + dependency.name)
    for name, source in libraries.items():
        destination = target / 'lib' / name
        if not destination.exists():
            shutil.copy2(source, destination)
(target / 'lib' / stdlib.name / 'site-packages').mkdir(exist_ok=True)
print(json.dumps(dict(version=platform.python_version(), executable=sys.executable, stdlib=str(stdlib))))
'''


@dataclass(frozen=True)
class SourceImage:
    sif: Path
    overlay: Path
    overlay_sha256: str
    private: bool = False


def matches_source_bundle(manifest, name, binding):
    """Shared SIF bytes do not identify the baked dependency overlay."""
    files = manifest.get('files', {})
    for suffix, field in (('.sif', 'image_sha256'), ('.overlay.img', 'overlay_sha256'),
                          ('.deferred.json', 'deferred_sha256')):
        if field in binding:
            filename = str(Path(name).with_suffix(suffix))
            if files.get(filename, {}).get('sha256') != binding[field]:
                return False
    return True


def source_image(spec, image=None, *, scratch=None):
    expected = spec['image_python']['image_sha256']
    if image is None:
        root = os.environ.get('OT_RUNTIME_IMAGE_CACHE')
        if root:
            image = Path(root) / (expected + '.sif')
        else:
            # A normal online run has already prepared its pristine image. Find
            # it by content identity, never by an agent-controlled container path.
            workspace = os.environ.get('OT_WORKSPACE')
            if workspace:
                cache = Path(workspace) / 'images' / 'bundles-v1'
                for manifest_path in sorted(cache.glob('*.sif/manifest.json')):
                    try:
                        manifest = json.loads(manifest_path.read_text())
                    except (OSError, ValueError):
                        continue
                    name = manifest_path.parent.name
                    if matches_source_bundle(manifest, name, spec['image_python']):
                        image = manifest_path.parent / name
                        break
                if image is None:
                    import tarfile
                    import tempfile
                    from hpc.offline_prep.image_cache import bundle_manifest, bundle_name, restore
                    for packed in sorted(cache.glob('*.sif.tar')):
                        try:
                            manifest = bundle_manifest(packed)
                        except (OSError, ValueError, KeyError, tarfile.TarError):
                            continue
                        name = bundle_name(packed)
                        if matches_source_bundle(manifest, name, spec['image_python']):
                            private = Path(tempfile.mkdtemp(prefix='runtime-source-', dir=scratch))
                            image = restore(packed, private)
                            break
            if image is None and not os.environ.get('OT_RUNTIME_IMAGE_SOURCE'):
                raise ValueError('Pristine runtime source image is unavailable; prepare it in '
                                 'OT_WORKSPACE/images or set OT_RUNTIME_IMAGE_CACHE')
        if (image is None or not Path(image).exists()) and os.environ.get('OT_RUNTIME_IMAGE_SOURCE'):
            # Shared recipes may use a sibling image that a normal task run
            # has not downloaded. Fetch it privately, with its declared digest,
            # rather than depending on another account's preparation cache.
            import urllib.request
            from data.utils.verifier_runtime import runtime_image_source
            if scratch is None or 'overlay_sha256' in spec['image_python']:
                raise ValueError('Runtime image download requires private scratch and a single SIF')
            url = runtime_image_source(os.environ['OT_RUNTIME_IMAGE_SOURCE'])
            image = Path(scratch) / (expected + '.sif')
            with urllib.request.urlopen(url, timeout=180) as source, image.open('xb') as target:
                shutil.copyfileobj(source, target, 8 * 1024 * 1024)
    # Sidecars belong beside the selected SIF path, including checksum-named
    # cache links. Resolving the SIF symlink first can select unrelated sidecars.
    image = Path(os.path.abspath(image))
    if digest(image) != expected:
        raise ValueError('Source Python image checksum mismatch')
    binding = spec['image_python']
    if 'overlay_sha256' in binding:
        for suffix, field in (('.overlay.img', 'overlay_sha256'), ('.deferred.json', 'deferred_sha256')):
            if digest(image.with_suffix(suffix)) != binding[field]:
                raise ValueError('Source Python bundle checksum mismatch: ' + field)
        return SourceImage(image, image.with_suffix('.overlay.img'), binding['overlay_sha256'])
    return image


def stage_source(image, scratch):
    """Never let filesystem journal handling touch a shared ext3 source file."""
    if not isinstance(image, SourceImage) or image.private:
        return image
    from hpc.offline_prep.image_cache import copy_image
    target = Path(scratch) / 'source.overlay.img'
    if target.exists():
        raise FileExistsError(target)
    copy_image(image.overlay, target)
    if digest(target) != image.overlay_sha256:
        raise ValueError('Private source overlay checksum mismatch')
    return SourceImage(image.sif, target, image.overlay_sha256, private=True)


def command(image, scratch, *args, binds=()):
    if isinstance(image, SourceImage) and not image.private:
        raise ValueError('Stage source overlay into private build scratch before execution')
    overlay = ['--overlay', str(image.overlay) + ':ro'] if isinstance(image, SourceImage) else []
    sif = image.sif if isinstance(image, SourceImage) else image
    return ['apptainer', 'exec', '--cleanenv', '--containall', '--no-home', '--pwd', '/',
            '--env', f'TMPDIR={scratch}',
            '--bind', f'{scratch}:{scratch}',
            *[arg for bind in binds for arg in ('--bind', bind)], *overlay, str(sif), *map(str, args)]


def proxy_environment():
    """Forward only controller proxy settings through Apptainer's cleanenv.

    Keep values out of command arguments and provenance logs.
    """
    env = {k: v for k, v in os.environ.items() if not k.startswith(('APPTAINERENV_', 'SINGULARITYENV_'))}
    for name in ('http_proxy', 'https_proxy', 'no_proxy', 'HTTP_PROXY', 'HTTPS_PROXY', 'NO_PROXY'):
        if name in os.environ:
            env['APPTAINERENV_' + name] = os.environ[name]
    return env


def normalize_scripts(runtime):
    """Expose Debian posix_local entrypoints without changing its import scheme.

    The relocated image interpreter keeps packages under local/lib/dist-packages.
    Move only scripts to the common bin directory, where the bundle builder
    rewrites their build-time shebangs into relocatable launchers.
    """
    scripts = runtime / 'local/bin'
    if scripts.is_dir():
        entries = list(scripts.iterdir())
        for source in entries:
            destination = runtime / 'bin' / source.name
            if destination.exists() or destination.is_symlink():
                raise ValueError('Package executable conflicts with interpreter: ' + source.name)
        for source in entries:
            source.rename(runtime / 'bin' / source.name)


def prepare(spec, runtime, scratch, image, uv):
    probe = scratch / 'copy-image-python.py'
    probe.write_text(COPY)
    subprocess.run(command(image, scratch, spec['image_python']['executable'], '-I', probe, runtime,
                           json.dumps(spec)), check=True)
    if spec['requirements'] or spec.get('build_requirements'):
        trusted_uv = shutil.which(uv)
        if trusted_uv is None:
            raise ValueError('Trusted host uv executable is missing')
        binds = [str(Path(trusted_uv).resolve()) + ':/ot-preparation-uv:ro']
        inputs = []
        wheelhouse = os.environ.get('OT_RUNTIME_WHEELHOUSE')
        if wheelhouse:
            wheelhouse = Path(wheelhouse).resolve(strict=True)
            if not wheelhouse.is_dir():
                raise ValueError('OT_RUNTIME_WHEELHOUSE must be a directory')
            binds.append(str(wheelhouse) + ':/ot-preparation-wheels:ro')
            inputs += ['--no-index', '--find-links', '/ot-preparation-wheels']
        def install(requirements, options):
            subprocess.run(command(image, scratch, '/ot-preparation-uv', 'pip', 'install',
                                   '--python', runtime / 'bin/python3', '--link-mode=copy', '--no-cache',
                                   '--break-system-packages', *inputs, *options, *requirements,
                                   binds=binds), check=True, env=proxy_environment())
        if spec.get('build_requirements'):
            # The bootstrap lock is complete and wheel-only: compiling a build
            # prerequisite with an implicit environment would reintroduce the
            # ABI ambiguity this recipe is intended to prevent.
            install(spec['build_requirements'], ['--no-deps', '--only-binary=:all:'])
        options = ['--no-deps'] if spec.get('install_no_deps', False) else []
        if spec.get('install_no_build_isolation'):
            options.append('--no-build-isolation')
        image_packages = spec['image_python'].get('packages', {})
        install_requirements = [pin for pin in spec['requirements'] if pin not in image_packages]
        if install_requirements:
            install(install_requirements, options)
        normalize_scripts(runtime)
