"""Shared preparation helpers for datasource patchers, with no downloads or builds."""
import json
import re
import shlex

from harbor_patches.verifier_bundle import FORMAT, REFERENCE, TASK_REFERENCE, spec_id

NAME = re.compile(r'[A-Za-z0-9_][A-Za-z0-9_.-]*\Z')
PIN = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]*(?:\[[A-Za-z0-9_,.-]+\])?==[0-9][A-Za-z0-9_.+!-]*\Z')


def requirement_source(value):
    """A checksum-pinned distribution URL, preserving its actual source identity."""
    from urllib.parse import urlsplit, unquote
    match = re.fullmatch(r'([A-Za-z0-9][A-Za-z0-9_.-]*(?:\[[A-Za-z0-9_,.-]+\])?) @ (https://\S+)', value)
    if not match:
        return None
    url = urlsplit(match[2])
    filename = unquote(url.path.rsplit('/', 1)[-1])
    if (not url.hostname or url.username or url.password or url.query
            or not re.fullmatch(r'sha256=[0-9a-f]{64}', url.fragment)
            or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.+-]*', filename)):
        return None
    return {'name': match[1], 'url': match[2], 'sha256': url.fragment.removeprefix('sha256='), 'filename': filename}


def runtime_spec(*, python, requirements, tools=(), architecture='x86_64', libc='gnu', image_python=None, install_no_deps=False,
                 build_requirements=(), install_no_build_isolation=False, python_headers=False):
    """Declare a relocatable installed runtime; identical declarations share a cache key."""
    if not re.fullmatch(r'3\.\d+\.\d+', python):
        raise ValueError('Pin Python to an exact patch version')
    if architecture not in ('x86_64', 'aarch64') or libc not in ('gnu', 'musl'):
        raise ValueError('Unsupported runtime platform')
    if any(not PIN.fullmatch(r) and requirement_source(r) is None for r in requirements):
        raise ValueError('Verifier requirements must use exact package==version pins or SHA256-pinned HTTPS sources')
    if any(not NAME.fullmatch(t) for t in tools):
        raise ValueError('Tool names must be executable basenames')
    spec = {'python': python, 'architecture': architecture, 'libc': libc,
            'requirements': sorted(set(requirements)), 'tools': sorted(set(tools)),
            'runtime_layout': 2}
    if not isinstance(install_no_deps, bool):
        raise ValueError('install_no_deps must be boolean')
    if install_no_deps:
        spec['install_no_deps'] = True
    if not isinstance(install_no_build_isolation, bool):
        raise ValueError('install_no_build_isolation must be boolean')
    if build_requirements or install_no_build_isolation:
        if image_python is None or not build_requirements or not install_no_build_isolation:
            raise ValueError('Build bootstrap requires image Python, pinned build requirements and no build isolation')
        if isinstance(build_requirements, (str, bytes)) or any(not PIN.fullmatch(r) for r in build_requirements):
            raise ValueError('Build requirements must be an exact wheel-only bootstrap lock')
        spec['build_requirements'] = sorted(set(build_requirements))
        spec['install_no_build_isolation'] = True
    if not isinstance(python_headers, bool):
        raise ValueError('python_headers must be boolean')
    if python_headers:
        # Verifiers that rebuild C/Cython extensions after agent edits need the
        # image interpreter's exact headers. Identity-bearing; absent keeps old IDs.
        if image_python is None:
            raise ValueError('python_headers requires image Python')
        spec['python_headers'] = True
    if image_python is not None:
        fields = set(image_python)
        base_fields = {'image_sha256', 'executable'}
        overlay_fields = {'overlay_sha256', 'deferred_sha256'}
        if (fields not in (base_fields, base_fields | overlay_fields)
                or not re.fullmatch(r'[0-9a-f]{64}', image_python['image_sha256'])
                or any(not re.fullmatch(r'[0-9a-f]{64}', image_python[k]) for k in fields & overlay_fields)
                or not re.fullmatch(r'/(?:[A-Za-z0-9_.-]+/)*python(?:3(?:\.\d+)?)?', image_python['executable'])
                or '..' in image_python['executable'].split('/')):
            raise ValueError('Image Python requires a checksum and absolute interpreter path')
        spec['image_python'] = dict(image_python)
    return spec


def runtime_reference(spec, catalog=None, *, mount_mode='writable'):
    """Return task-file updates. An unbound preparation declaration fails runtime preflight."""
    from harbor_patches.verifier_mount import mode
    mode({'mount_mode': mount_mode})
    ref = {'format': FORMAT, 'id': spec_id(spec), 'spec': spec, 'mount_mode': mount_mode}
    if catalog is not None:
        record = catalog[ref['id']]
        if (record.get('format') != FORMAT or record.get('id') != ref['id']
                or record.get('spec') != spec or record.get('status') != 'ready'
                or not re.fullmatch(r'[0-9a-f]{64}', record.get('archive_sha256', ''))):
            raise ValueError('Verifier bundle catalog does not contain a matching ready build')
        ref['archive_sha256'] = record['archive_sha256']
        ref.pop('spec')
    return {'tests/' + REFERENCE: (json.dumps(ref, indent=2) + '\n').encode()}


def tool_command(tool, args=()):
    """Run a prepared executable directly, never uvx's runtime resolver/installer."""
    if not NAME.fullmatch(tool):
        raise ValueError('Tool names must be executable basenames')
    return '"${OT_VERIFIER_BIN:?prepared verifier runtime required}/' + tool + '"' + (
        ' ' + shlex.join(args) if args else '')


def task_runtime_reference(spec, catalog=None):
    """Declare pre-agent dependencies. Same cached bytes, always a private writable copy."""
    value = json.loads(runtime_reference(spec, catalog)['tests/' + REFERENCE])
    value.pop('mount_mode')
    return {'setup_files/' + TASK_REFERENCE: (json.dumps(value, indent=2) + '\n').encode()}


def toolchain_reference(spec, record):
    """Bind a ready pristine-image toolchain (hpc/offline_prep/image_toolchain.py) for the verifier.

    Returns task-file updates: tests/ot-verifier-toolchain.json. Declare the same spec in
    tests/setup.sh with toolchain_recipe_comment(spec) to allow a trusted rebuild from the
    checksum-verified pristine image when the cached artifact is missing or corrupt.
    """
    from harbor_patches.verifier_bundle import TOOLCHAIN_FORMAT, TOOLCHAIN_REFERENCE
    from hpc.offline_prep.image_toolchain import canonical
    canonical(spec)
    key = spec_id(spec)
    if (record.get('format') != TOOLCHAIN_FORMAT or record.get('id') != key or record.get('spec') != spec
            or record.get('status') != 'ready' or not re.fullmatch(r'[0-9a-f]{64}', record.get('archive_sha256', ''))):
        raise ValueError('Toolchain record does not contain a matching ready build')
    ref = {'format': TOOLCHAIN_FORMAT, 'id': key, 'archive_sha256': record['archive_sha256']}
    return {'tests/' + TOOLCHAIN_REFERENCE: (json.dumps(ref, indent=2) + '\n').encode()}


def toolchain_recipe_comment(spec):
    """One canonical tests/setup.sh line naming the toolchain spec for trusted rebuilds."""
    from hpc.offline_prep.image_toolchain import canonical
    return '# OT_TOOLCHAIN_RECIPE: ' + json.dumps(canonical(spec), sort_keys=True, separators=(',', ':'))


# For test.sh: the staged exact toolchain first on PATH. GOTOOLCHAIN/GOFLAGS/module
# settings stay the task's own decision (go.mod toolchain directives may select another).
GO_ACTIVATION = ('export GOROOT="${OT_VERIFIER_GOROOT:?prepared verifier toolchain required}" '
                 'PATH="$OT_VERIFIER_GOROOT/bin:$PATH"')


def offline_package_reference(spec, catalog=None, *, phase='task'):
    """Bind image-specific cached apt/wheel inputs built by hpc/offline_prep/verifier_offline.py."""
    from harbor_patches.verifier_bundle import OFFLINE_FORMAT, OFFLINE_REFERENCE, TASK_OFFLINE_REFERENCE
    if phase not in ('task', 'verifier'):
        raise ValueError('phase must be task or verifier')
    value = {'format': OFFLINE_FORMAT, 'id': spec_id(spec), 'spec': spec}
    if catalog is not None:
        record = catalog[value['id']]
        if (record.get('format') != OFFLINE_FORMAT or record.get('id') != value['id']
                or record.get('spec') != spec or record.get('status') != 'ready'
                or not re.fullmatch(r'[0-9a-f]{64}', record.get('archive_sha256', ''))):
            raise ValueError('Offline package catalog does not contain a matching ready build')
        value['archive_sha256'] = record['archive_sha256']
        value.pop('spec')
    name = 'setup_files/' + TASK_OFFLINE_REFERENCE if phase == 'task' else 'tests/' + OFFLINE_REFERENCE
    return {name: (json.dumps(value, indent=2) + '\n').encode()}


CRAN_PACKAGE = re.compile(r'[A-Za-z][A-Za-z0-9.]*[A-Za-z0-9]\Z')


def offline_request(*, cran=None, http=None):
    """Download inputs that original setup commands fetch, prepared per task image.

    cran: {'packages': [...], 'repos': URL, 'type': 'source'} for install.packages.
    http: {'commands': [shell text], 'home': dir, 'workdir': dir}; the commands run
    once with a recording curl during preparation and, offline, with a replaying one.
    """
    request = {'apt': [], 'python': [], 'uv': False}
    if cran is not None:
        packages = sorted(set(cran['packages']))
        if not packages or any(not CRAN_PACKAGE.fullmatch(p) for p in packages):
            raise ValueError('CRAN requests need literal package names')
        if not re.fullmatch(r'https://[A-Za-z0-9.-]+(?:/[A-Za-z0-9._/-]*)?', cran['repos']):
            raise ValueError('CRAN repository must be a literal https URL')
        request['cran'] = {'packages': packages, 'repos': cran['repos'], 'type': cran.get('type', 'source')}
    if http is not None:
        if not http['commands'] or not all(isinstance(c, str) and c for c in http['commands']):
            raise ValueError('HTTP recipes need the literal setup commands')
        if not all(re.fullmatch(r'/[A-Za-z0-9._/-]*', http[k]) for k in ('home', 'workdir')):
            raise ValueError('HTTP recipes need absolute home and workdir paths')
        request['http'] = {'commands': list(http['commands']), 'home': http['home'], 'workdir': http['workdir']}
    if 'cran' not in request and 'http' not in request:
        raise ValueError('Empty offline download request')
    return request


def offline_recipe(request):
    """Setup comment naming the request; it enables the online branch as fallback."""
    return '# OT_OFFLINE_RECIPE: ' + json.dumps(request, sort_keys=True, separators=(',', ':'))


def task_offline_reference(request, record):
    """Compact ot-task-offline.json bound to a ready build of request for one image."""
    from harbor_patches.verifier_bundle import OFFLINE_FORMAT, TASK_OFFLINE_REFERENCE
    spec = record.get('spec') or {}
    if (record.get('format') != OFFLINE_FORMAT or record.get('status') != 'ready'
            or record.get('id') != spec_id(spec)
            or {k: v for k, v in spec.items() if k in request} != request
            or not re.fullmatch(r'[0-9a-f]{64}', record.get('archive_sha256', ''))):
        raise ValueError('Offline package catalog does not contain a matching ready build')
    value = {'format': OFFLINE_FORMAT, 'id': record['id'], 'archive_sha256': record['archive_sha256'],
             'request_id': spec_id(request)}
    return {'setup_files/' + TASK_OFFLINE_REFERENCE: (json.dumps(value, indent=2) + '\n').encode()}


def cran_offline_or_online(command, repos):
    """Run a literal install.packages command from the staged local CRAN repository.

    Only the repository URL changes; without staged packages the original command
    runs unchanged. install.packages keeps its own failure semantics either way.
    """
    if repos not in command:
        raise ValueError('Command does not name the declared CRAN repository')
    local = '"file://$OT_TASK_OFFLINE/cran"'.join(shlex.quote(part) for part in command.split(repos))
    return ('if [ -n "${OT_TASK_OFFLINE:-}" ] && [ -f "$OT_TASK_OFFLINE/cran/src/contrib/PACKAGES" ]; then\n'
            '  /bin/sh -c ' + local + '\nelse\n  /bin/sh -c ' + shlex.quote(command) + '\nfi')


def http_replay_or_online(command):
    """Run a literal setup command with recorded downloads when they are staged."""
    return ('if [ -n "${OT_TASK_OFFLINE:-}" ] && [ -x "$OT_TASK_OFFLINE/http/bin/curl" ]; then\n'
            '  PATH="$OT_TASK_OFFLINE/http/bin:$PATH" /bin/sh -c ' + shlex.quote(command) + '\nelse\n'
            '  /bin/sh -c ' + shlex.quote(command) + '\nfi')


def python_install_or_prepared(requirements, *, phase='task', python='python3'):
    """Explicit setup/test script branch: validate a supplied runtime, else use pip.

    Task cache misses use the normal installation branch. Verifier cache misses
    require a trusted controller rebuild before reaching this script. Incomplete
    prepared runtimes are repaired privately. Checks cover direct pins, not extras.
    """
    if phase not in ('task', 'verifier'):
        raise ValueError('phase must be task or verifier')
    requirements = list(requirements)
    if not requirements or any(not PIN.fullmatch(r) or '[' in r for r in requirements):
        raise ValueError('Use exact package==version pins without extras for this install helper')
    variable = 'OT_TASK_PYTHON' if phase == 'task' else 'OT_VERIFIER_PYTHON'
    offline = 'OT_TASK_OFFLINE' if phase == 'task' else 'OT_VERIFIER_OFFLINE'
    pins = [r.split('==', 1) for r in requirements]
    check = ('from importlib.metadata import version; import sys; pins = ' + repr(pins)
             + '; bad = [(name, want, version(name)) for name, want in pins if version(name) != want]'
             + '; sys.exit("Prepared runtime package mismatch: " + repr(bad) if bad else 0)')
    install = shlex.join([python, '-m', 'pip', 'install', *requirements])
    prepared_install = (f'"${variable}" -I -m pip install --force-reinstall --break-system-packages '
                        + shlex.join(requirements))
    # A fresh prepared runtime is trusted; repairing it never changes the base
    # archive. A failed repair is the final command and propagates through set -e.
    fallback = (f'elif [ -n "${{{offline}:-}}" ]; then\n'
                f'  PIP_NO_INDEX=1 PIP_FIND_LINKS="${offline}/wheels" ' + install + '\n'
                'else\n  ' + install + '\nfi')
    if phase == 'verifier':
        fallback = ('else\n  echo "Trusted prepared verifier runtime required" >&2\n  exit 1\nfi')
    return (f'if [ -n "${{{variable}:-}}" ]; then\n'
            f'  if ! "${variable}" -I -c {shlex.quote(check)}; then\n'
            f'    "${variable}" -I -m pip --version >/dev/null 2>&1 || '
            f'"${variable}" -I -m ensurepip --upgrade || exit $?\n'
            f'    if [ -n "${{{offline}:-}}" ]; then\n'
            f'      PIP_NO_INDEX=1 PIP_FIND_LINKS="${offline}/wheels" {prepared_install}\n'
            f'    else\n      {prepared_install}\n    fi\n  fi\n'
            + fallback)


def prepare_uvx(argv, *, python, architecture='x86_64', libc='gnu'):
    """Translate an explicitly selected literal uvx invocation into a spec and command.

    Supports --from, --with/-w, --python/-p and tool@version. Unknown options,
    unpinned packages, project execution and shell syntax are not guessed. Call
    once per environment: independent uvx environments must not be merged blindly.
    """
    args = list(argv)
    if args[:1] == ['uvx']:
        args = args[1:]
    elif args[:3] == ['uv', 'tool', 'run']:
        args = args[3:]
    else:
        raise ValueError('Expected uvx or uv tool run')
    package, requirements = None, []
    while args and args[0].startswith('-'):
        option = args.pop(0)
        if option == '--':
            break
        option, equals, value = option.partition('=')
        if option not in ('--from', '--with', '-w', '--python', '-p'):
            raise ValueError('Unsupported uvx option: ' + option)
        if not equals:
            if not args:
                raise ValueError('Missing value for ' + option)
            value = args.pop(0)
        if option in ('--python', '-p'):
            if value not in (python, '.'.join(python.split('.')[:2])):
                raise ValueError('uvx Python selection disagrees with the prepared runtime')
        elif option == '--from':
            if package is not None:
                raise ValueError('Repeated --from')
            package = value
        else:
            requirements.append(value)
    if not args:
        raise ValueError('Missing uvx tool')
    tool, at, version = args.pop(0).partition('@')
    if at:
        if package is not None:
            raise ValueError('Use either --from or tool@version')
        package = tool + '==' + version
    if package is None:
        # Common TerminalBench form: uvx --with pytest==X pytest ...
        normalize = lambda s: re.sub(r'[-_.]+', '-', s).lower()
        candidates = [r for r in requirements if normalize(r.split('==')[0]) == normalize(tool)]
        if len(candidates) != 1:
            raise ValueError('Pin the tool with --from package==version or tool@version')
        package = candidates[0]
    requirements.append(package)
    spec = runtime_spec(python=python, requirements=requirements, tools=[tool],
                        architecture=architecture, libc=libc)
    return spec, tool_command(tool, args)


def preparation_command(spec, *, phase='task', python='python3'):
    """Setup prefix: --prepare saves dependencies then exits before initialization.

    Run preparation on a trusted host with this repository on PYTHONPATH and uv
    available. The recipe remains in setup, while bound task references contain
    only identity/checksum/options. Normal container execution needs no repo import.
    """
    if phase not in ('task', 'verifier'):
        raise ValueError('phase must be task or verifier')
    if runtime_spec(**{k: v for k, v in spec.items() if k != 'runtime_layout'}) != spec:
        raise ValueError('Preparation requires a canonical layout-2 runtime spec')
    name = TASK_REFERENCE if phase == 'task' else REFERENCE
    recipe = json.dumps(spec, sort_keys=True, separators=(',', ':'))
    command = shlex.join([python, '-m', 'data.utils.verifier_runtime', '--prepare', '--phase', phase])
    return (f'# OT_RUNTIME_RECIPE: {recipe}\n'
            'if [ "${1:-}" = "--prepare" ]; then\n'
            f'  {command} --reference "$(dirname -- "$0")/{name}" <<\'OT_RUNTIME_RECIPE_JSON\'\n'
            + recipe + '\nOT_RUNTIME_RECIPE_JSON\n'
            '  exit $?\nfi')


def offline_preparation_command(request, *, image_sha256, image_key=None, python='python3',
                                overlay_sha256=None, deferred_sha256=None):
    """Prepare task apt/wheel/download inputs before task initialization, on a trusted host."""
    from harbor_patches.verifier_bundle import OFFLINE_REQUEST_KEYS, TASK_OFFLINE_REFERENCE
    if (not set(request).issubset(OFFLINE_REQUEST_KEYS)
            or not all(k in request for k in ('apt', 'python', 'uv'))
            or not re.fullmatch(r'[0-9a-f]{64}', image_sha256)
            or (image_key is not None and not re.fullmatch(r'[0-9a-f]{12,64}', image_key))):
        raise ValueError('Offline preparation requires a package request and pinned image identity')
    payload = {'request': request, 'image_sha256': image_sha256}
    if overlay_sha256 is not None or deferred_sha256 is not None:
        if any(not isinstance(v, str) or not re.fullmatch(r'[0-9a-f]{64}', v)
               for v in (overlay_sha256, deferred_sha256)):
            raise ValueError('Offline bundle preparation requires both sidecar checksums')
        payload.update(overlay_sha256=overlay_sha256, deferred_sha256=deferred_sha256)
    if image_key is not None:
        payload['image_key'] = image_key
    command = shlex.join([python, '-m', 'data.utils.verifier_runtime', '--prepare-offline', '--phase', 'task'])
    return (offline_recipe(request) + '\n'
            'if [ "${1:-}" = "--prepare" ]; then\n'
            f'  {command} --reference "$(dirname -- "$0")/{TASK_OFFLINE_REFERENCE}" <<\'OT_OFFLINE_PREPARATION_JSON\'\n'
            + json.dumps(payload, sort_keys=True) + '\nOT_OFFLINE_PREPARATION_JSON\n'
            '  exit $?\nfi')


def main():
    import argparse
    from pathlib import Path
    import sys
    from harbor_patches.verifier_bundle import cache_root
    from hpc.offline_prep.verifier_bundles import build
    parser = argparse.ArgumentParser(description='Prepare dependencies declared by a task setup script')
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--prepare', action='store_true')
    action.add_argument('--prepare-offline', action='store_true')
    parser.add_argument('--phase', choices=('task', 'verifier'), required=True)
    parser.add_argument('--reference', type=Path, required=True)
    args = parser.parse_args()
    spec = json.load(sys.stdin)
    if args.prepare_offline:
        import os
        import shutil
        from hpc.offline_prep.image_python_runtime import source_image
        from hpc.offline_prep.verifier_offline import build as build_offline
        if args.phase != 'task':
            parser.error('--prepare-offline currently prepares task inputs only')
        payload = spec
        offline_preparation_command(**payload)  # Validate before using image/cache paths.
        spec = {**payload['request'], 'image_sha256': payload['image_sha256']}
        if payload.get('image_key') is not None:
            spec['image_key'] = payload['image_key']
        binding = {k: payload[k] for k in ('image_sha256', 'overlay_sha256', 'deferred_sha256') if k in payload}
        spec.update(binding)
        image = source_image({'image_python': binding}, os.environ.get('OT_RUNTIME_PREPARE_IMAGE'))
        from hpc.offline_prep.image_python_runtime import SourceImage
        if isinstance(image, SourceImage):
            image = image.sif.parent
        uv = shutil.which('uv')
        if uv is None:
            raise ValueError('Trusted preparation requires uv on PATH')
        cache = cache_root()
        cache.mkdir(parents=True, exist_ok=True)
        record = build_offline(spec, image, cache, uv)
        if record.get('status') != 'ready':
            raise RuntimeError('Offline input preparation failed: ' + record.get('error', 'unknown error'))
        args.reference.write_bytes(next(iter(task_offline_reference(payload['request'], record).values())))
        print(json.dumps(record))
        return
    record = build(spec, cache_root())
    catalog = {record['id']: record}
    if args.phase == 'task':
        updates = task_runtime_reference(spec, catalog)
    else:
        # Regeneration updates artifact bytes without changing the task's explicit
        # runtime mount choice. New references retain the writable default.
        previous = json.loads(args.reference.read_text()) if args.reference.is_file() else {}
        updates = runtime_reference(spec, catalog, mount_mode=previous.get('mount_mode', 'writable'))
    args.reference.write_bytes(next(iter(updates.values())))
    print(json.dumps(record))


if __name__ == '__main__':
    main()
