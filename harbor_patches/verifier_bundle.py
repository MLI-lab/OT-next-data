"""Content-addressed, offline verifier runtimes staged only after the agent runs."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import tarfile
import time
from collections import OrderedDict
from threading import RLock

REFERENCE = 'ot-verifier-runtime.json'
TASK_REFERENCE = 'ot-task-runtime.json'
TASK_OFFLINE_REFERENCE = 'ot-task-offline.json'
FORMAT = 'ot-verifier-runtime-v1'
# Verifier-only packages (local apt repository, wheelhouse, uv) that a test.sh installs
# after the agent. The task image stays shared and untouched; the agent never sees them.
OFFLINE_REFERENCE = 'ot-verifier-offline.json'
OFFLINE_FORMAT = 'ot-verifier-offline-v1'
# cran/: local CRAN source repository. http/: recorded download responses served by
# http/bin/curl (harbor_patches/http_replay.sh) to the original setup command.
OFFLINE_ROOTS = ('repo', 'wheels', 'bin', 'cran', 'http', 'sources')
OFFLINE_REQUEST_KEYS = ('apt', 'python', 'uv', 'cran', 'http', 'sources', 'apt_source_packages', 'git_sources')
# Exact non-Python toolchain copied from the task's pristine image (hpc/offline_prep/image_toolchain.py),
# staged read-only after the agent so agent changes to the image's toolchain cannot grade.
TOOLCHAIN_REFERENCE = 'ot-verifier-toolchain.json'
TOOLCHAIN_FORMAT = 'ot-image-toolchain-v1'


def spec_id(spec):
    return hashlib.sha256(json.dumps(spec, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def cache_root():
    value = os.environ.get('OT_RUNTIME_CACHE') or os.environ.get('OT_VERIFIER_BUNDLE_CACHE')
    if not value:
        workspace = os.environ.get('OT_WORKSPACE')
        if not workspace:
            raise ValueError('Verifier bundle cache is not configured')
        value = str(Path(workspace) / 'runtimes')
    return Path(value)


def reference(context, name=REFERENCE, format=FORMAT):
    path = Path(context) / name
    if not path.is_file():
        return None
    data = json.loads(path.read_text())
    if (data.get('format') != format or not re.fullmatch(r'[0-9a-f]{64}', data.get('id', ''))
            or ('spec' in data and data['id'] != spec_id(data['spec']))):
        raise ValueError(f'Invalid verifier bundle reference: {path}')
    if format == FORMAT:
        from harbor_patches.verifier_mount import mode
        mode(data)
    return data


def task_references(context):
    """Read the standard task reference, retaining legacy package-cache files.

    A task can name cached installation inputs in ot-task-runtime.json using
    their existing format. Python runtimes and package inputs remain distinct
    archive types, with their respective validators and staging behavior.
    """
    path = Path(context) / TASK_REFERENCE
    fmt = json.loads(path.read_text()).get('format') if path.is_file() else FORMAT
    if fmt == OFFLINE_FORMAT:
        if (Path(context) / TASK_OFFLINE_REFERENCE).exists():
            raise ValueError('Task declares package inputs in both runtime reference files')
        return None, reference(context, TASK_REFERENCE, OFFLINE_FORMAT)
    return reference(context, TASK_REFERENCE), reference(context, TASK_OFFLINE_REFERENCE, OFFLINE_FORMAT)


def verify(ref, root=None, format=FORMAT):
    explicit_root = root is not None
    root = Path(root) if explicit_root else cache_root()
    key = ref['id']
    if not re.fullmatch(r'[0-9a-f]{64}', key):
        raise ValueError('Invalid verifier bundle identifier')
    expected = ref.get('archive_sha256')
    if not expected or not re.fullmatch(r'[0-9a-f]{64}', expected):
        raise ValueError(f'Unpinned verifier bundle {key}; prepare and bind it before validation')
    # Read legacy cache entries without moving or rewriting them. Explicit roots
    # and either environment override never search another directory.
    if (not explicit_root and not os.environ.get('OT_RUNTIME_CACHE')
            and not os.environ.get('OT_VERIFIER_BUNDLE_CACHE')
            and not (root / (key + '.json')).exists()
            and not (root / (key + '.tar.gz')).exists()):
        legacy = root.parent / 'verifier-bundles'
        if (legacy / (key + '.json')).is_file():
            root = legacy
    archive = root / (key + '.tar.gz')
    manifest = json.loads((root / (key + '.json')).read_text())
    if (manifest.get('format') != format or spec_id(manifest.get('spec')) != key
            or ('spec' in ref and manifest.get('spec') != ref['spec'])
            or manifest.get('id') != key or manifest.get('archive_sha256') != expected
            or manifest.get('status') != 'ready' or digest(archive) != expected):
        raise ValueError(f'Missing or corrupt verifier bundle {key}')
    return archive


_validated_archives = OrderedDict()
_validation_lock = RLock()


def validate_archive(path, roots=('python',), required='python/bin/python3'):
    """Reject unsafe members; memoize only checksums freshly read from disk."""
    checksum = digest(path)
    key = (checksum, tuple(roots), required)
    with _validation_lock:
        if key in _validated_archives:
            _validated_archives.move_to_end(key)
            return
    with tarfile.open(path) as archive:
        names = set()
        for member in archive:
            p = PurePosixPath(member.name)
            if not p.parts or p.is_absolute() or '..' in p.parts or p.parts[0] not in roots or member.name in names:
                raise ValueError(f'Unsafe verifier bundle member: {member.name}')
            names.add(member.name)
            tarfile.data_filter(member, '/ot-verifier-bundle-validation')
        if required and required not in names:
            raise ValueError('Verifier bundle does not contain its interpreter')
    if digest(path) != checksum:
        raise ValueError('Verifier bundle changed during structural validation')
    with _validation_lock:
        _validated_archives[key] = None
        _validated_archives.move_to_end(key)
        while len(_validated_archives) > 128:
            _validated_archives.popitem(last=False)


def lookup(ref, format=FORMAT, validate=validate_archive, root=None):
    """(archive, None) for a usable cached archive, else (None, reason).

    A missing, incomplete or corrupt entry is never used; the task's scripts then
    install online as the original task did."""
    try:
        archive = verify(ref, root, format)
        validate(archive)
        if format == OFFLINE_FORMAT:
            manifest = json.loads(archive.with_suffix('').with_suffix('.json').read_text())
            validate_http_archive(manifest['spec'], archive)
        if format == FORMAT:
            manifest = json.loads(archive.with_suffix('').with_suffix('.json').read_text())
            environments = [('python', manifest['spec'])]
            auxiliary = manifest['spec'].get('auxiliary')
            if auxiliary is not None:
                from hpc.offline_prep.auxiliary_verifier_runtimes import canonical
                auxiliary = canonical(auxiliary, architecture=manifest['spec']['architecture'], libc=manifest['spec']['libc'])
                environments.extend(('python/aux/' + name, spec) for name, spec in auxiliary.items())
            with tarfile.open(archive) as stream:
                members = {m.name: m for m in stream}
            for prefix, environment in environments:
                if environment.get('image_tools', {}).get('shared_libraries'):
                    from hpc.offline_prep.image_tool_libraries import validate_members as validate_libraries
                    validate_libraries(archive, environment['image_tools'], members, prefix)
                if environment.get('image_assets'):
                    from hpc.offline_prep.image_runtime_assets import validate_members
                    validate_members(environment['image_assets'], members, prefix)
                tools = environment.get('tools', [])
                from data.utils.verifier_runtime import NAME
                if any(not NAME.fullmatch(tool) for tool in tools):
                    raise ValueError('Invalid runtime executable name')
                for name in ['python3', *tools]:
                    member = members.get(prefix + '/bin/' + name)
                    seen = set()
                    while member is not None and (member.issym() or member.islnk()):
                        import posixpath
                        if member.name in seen:
                            member = None
                            break
                        seen.add(member.name)
                        target = (member.linkname if member.islnk() else
                                  posixpath.join(posixpath.dirname(member.name), member.linkname))
                        member = members.get(posixpath.normpath(target))
                    if member is None or not member.isfile() or not member.mode & 0o111:
                        raise ValueError('Incomplete runtime executable: ' + (name if prefix == 'python' else prefix + '/bin/' + name))
    except (OSError, ValueError, tarfile.TarError) as error:
        return None, str(error)
    return archive, None


def cached(ref, format=FORMAT, validate=validate_archive, root=None):
    return lookup(ref, format, validate, root)[0]


def upload_measured(upload):
    """Ordinary transfer measurements or a worker-cache-copy acknowledgement (worker timings only)."""
    return isinstance(upload, dict) and ('timings_seconds' in upload or upload.get('transport') == 'worker-cache-copy')


def validate_offline_archive(path):
    validate_archive(path, OFFLINE_ROOTS, 'repo/Packages')


def validate_http_archive(spec, archive):
    """Reject incomplete download caches so normal setup can install instead."""
    if not spec.get('http'):
        return
    with tarfile.open(archive) as contents:
        members = {member.name: member for member in contents}
    helper = members.get('http/bin/curl')
    responses = [name for name in members if name.startswith('http/responses/') and name.endswith('/url')]
    if not helper or not helper.isfile() or not helper.mode & 0o111 or not responses:
        raise ValueError('Incomplete HTTP archive: replay helper and recorded responses are required')


def validate_toolchain_archive(path):
    validate_archive(path, ('toolchain',), 'toolchain/bin/go')


# Validation's repository-leak repair (validation/rules/dynamic/environment/repair.py) moves the
# task's own setup.sh to ot-task-setup.sh and calls it from a generated wrapper.
REPAIR_SETUP_MARKER = '# ot-validation: repository-leak repair after task setup (generated)'
REPAIR_TASK_SETUP = 'ot-task-setup.sh'


def setup_script(context):
    """The datasource's own setup script, also inside a repaired validation copy."""
    path = Path(context) / 'setup.sh'
    wrapped = Path(context) / REPAIR_TASK_SETUP
    if wrapped.is_file() and path.is_file() and REPAIR_SETUP_MARKER in path.read_text().splitlines()[:3]:
        return wrapped
    return path


def setup_recipe(context, ref):
    """Read the datasource's canonical setup definition, never agent-side files."""
    path = setup_script(context)
    if not path.is_file():
        return None
    matches = re.findall(r'^# OT_RUNTIME_RECIPE: (.+)$', path.read_text(), re.M)
    if len(matches) != 1:
        return None
    spec = json.loads(matches[0])
    from data.utils.verifier_runtime import runtime_spec
    if (runtime_spec(**{k: v for k, v in spec.items() if k != 'runtime_layout'}) != spec
            or spec_id(spec) != ref['id']):
        raise ValueError('Setup runtime recipe does not match its reference')
    return spec


def offline_setup_recipe(context, ref):
    """Package request declared by task setup next to its explicit online branch."""
    path = setup_script(context)
    if not path.is_file() or not re.fullmatch(r'[0-9a-f]{64}', ref.get('request_id', '')):
        return None
    matches = re.findall(r'^# OT_OFFLINE_RECIPE: (.+)$', path.read_text(), re.M)
    if len(matches) != 1:
        return None
    request = json.loads(matches[0])
    if spec_id(request) != ref['request_id']:
        raise ValueError('Setup offline recipe does not match its reference')
    return request


def toolchain_setup_recipe(context, ref):
    """Toolchain specification declared in the datasource's tests/setup.sh."""
    path = setup_script(context)
    if not path.is_file():
        return None
    matches = re.findall(r'^# OT_TOOLCHAIN_RECIPE: (.+)$', path.read_text(), re.M)
    if len(matches) != 1:
        return None
    from hpc.offline_prep.image_toolchain import canonical
    spec = canonical(json.loads(matches[0]))
    if spec_id(spec) != ref['id']:
        raise ValueError('Setup toolchain recipe does not match its reference')
    return spec


def preflight(tasks, root=None):
    release = os.environ.get('OT_RUNTIME_RELEASE_MANIFEST')
    if release:
        from validation.publishing.runtime_release import fetch_for_tasks, load_manifest
        tasks = list(tasks)
        fetch_for_tasks(tasks, load_manifest(release, cache=root), cache=root)
    checked, identities = {}, {}
    kinds = ((REFERENCE, FORMAT, validate_archive), (OFFLINE_REFERENCE, OFFLINE_FORMAT, validate_offline_archive),
             (TOOLCHAIN_REFERENCE, TOOLCHAIN_FORMAT, validate_toolchain_archive))
    for task in tasks:
        contexts = [(context, kinds) for context in [Path(task) / 'tests', *Path(task).glob('steps/*/tests')]]
        contexts.append((Path(task) / 'setup_files', (
            (TASK_REFERENCE, FORMAT, validate_archive),
            (TASK_OFFLINE_REFERENCE, OFFLINE_FORMAT, validate_offline_archive))))
        for context, selected_kinds in contexts:
            for name, format, validate in selected_kinds:
                if name == TASK_REFERENCE:
                    task_ref, package_ref = task_references(context)
                    if task_ref is None and package_ref is not None and (context / TASK_REFERENCE).is_file():
                        format, validate = OFFLINE_FORMAT, validate_offline_archive
                ref = reference(context, name, format)
                if not ref:
                    continue
                key, checksum = ref['id'], ref.get('archive_sha256')
                if key in identities and checksum != identities[key]:
                    raise ValueError(f'Conflicting verifier bundle checksums for {key}')
                identities[key] = checksum
                if key in checked:
                    continue
                archive, reason = lookup(ref, format, validate, root)
                checked[key] = ({'archive': str(archive), 'sha256': checksum, 'format': format} if archive
                                else {'status': 'missing', 'format': format, 'sha256': checksum, 'reason': reason})
    return checked


async def stage(environment, context, evidence_dir, *, user=None):
    ref = reference(context)
    if ref is None:
        return None
    archive, reason = lookup(ref)
    if archive is None:
        return await install_and_stage(environment, context, ref, evidence_dir, reason, user=user)
    return await _stage(environment, archive, ref, evidence_dir, user=user)


async def install_and_stage(environment, context, ref, evidence_dir, reason, *, user=None):
    """Install with trusted host tools into a disposable cache on a cache miss.

    Read recipes from the controller's task, never the agent's container. Neither
    rebuilt bytes nor their new checksum replace the published cache/reference.
    """
    import asyncio
    import sys
    import tempfile
    spec = setup_recipe(context, ref)
    if spec is None:
        raise RuntimeError('Verifier runtime unavailable and no matching setup recipe: ' + str(reason))
    evidence = Path(evidence_dir)
    evidence.mkdir(parents=True, exist_ok=True)
    record = {'id': ref['id'], 'requested_sha256': ref.get('archive_sha256'),
              'reason': reason, 'cache_writeback': False, 'status': 'installation-required'}
    isolated = '1' in (os.environ.get('OT_NET_ISOLATION'), os.environ.get('OT_NET_SIMULATED'))
    record['installation_network'] = 'isolated' if isolated else 'host'
    report = evidence / 'verifier-runtime-fallback.json'
    report.write_text(json.dumps(record, indent=2) + '\n')
    with tempfile.TemporaryDirectory(prefix='ot-verifier-fallback-') as directory:
        root = Path(directory)
        specs = root / 'specs.json'
        specs.write_text(json.dumps([spec]))
        builder = Path(__file__).resolve().parents[1] / 'hpc/offline_prep/verifier_bundles.py'
        command = [sys.executable, str(builder), '--specs', str(specs), '--cache', str(root)]
        env = os.environ.copy()
        if ref.get('image_source') is not None:
            from data.utils.verifier_runtime import runtime_image_source
            env['OT_RUNTIME_IMAGE_SOURCE'] = runtime_image_source(ref['image_source'])
        if isolated:
            # Container isolation must not be bypassed by a host-side cache
            # miss. Kernel isolation also covers URL inputs and native-image
            # installers, which do not all inherit uv's offline environment.
            command = ['unshare', '--user', '--map-current-user', '--net', '--', *command]
            env['UV_OFFLINE'] = '1'
        try:
            with (evidence / 'verifier-runtime-install.log').open('wb') as log:
                process = await asyncio.create_subprocess_exec(
                    *command, env=env, stdout=log, stderr=log, start_new_session=True)
                try:
                    status = await process.wait()
                except BaseException:
                    import signal
                    try:
                        os.killpg(process.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                    try:
                        await asyncio.wait_for(process.wait(), timeout=10)
                    except asyncio.TimeoutError:
                        os.killpg(process.pid, signal.SIGKILL)
                        await process.wait()
                    raise
            if status:
                raise RuntimeError(f'Trusted verifier runtime installation failed (exit {status}); see {log.name}')
            built = json.loads((root / (ref['id'] + '.json')).read_text())
            actual = {**ref, 'archive_sha256': built['archive_sha256']}
            archive = verify(actual, root)
            validate_archive(archive)
            result = await _stage(environment, archive, actual, evidence_dir, user=user)
            record.update(status='installed', actual_sha256=actual['archive_sha256'])
            return result
        except BaseException as error:
            record.update(status='failed', error=str(error))
            raise
        finally:
            report.write_text(json.dumps(record, indent=2) + '\n')

async def _stage(environment, archive, ref, evidence_dir, *, user=None):
    timings = {}
    started = time.monotonic()
    tools = json.loads(archive.with_suffix('').with_suffix('.json').read_text())['spec'].get('tools', [])
    from data.utils.verifier_runtime import NAME
    if any(not NAME.fullmatch(tool) for tool in tools):
        raise ValueError('Invalid verifier tool name')
    # /tests is backed by job-local disk in the bridge. Apptainer's default
    # /tmp session filesystem is too small for complete Python runtimes.
    result = await environment.exec('mktemp -d /tests/.ot-verifier-runtime-XXXXXXXXXXXX', user='root')
    target = (result.stdout or '').strip()
    if result.return_code or not re.fullmatch(r'/tests/\.ot-verifier-runtime-[A-Za-z0-9]+', target):
        raise RuntimeError('Could not allocate a fresh verifier runtime directory')
    timings['allocate'] = time.monotonic() - started
    from harbor_patches.verifier_mount import release
    try:
        started = time.monotonic()
        remote = target + '/runtime.tar.gz'
        from harbor_patches.runtime_transfer import upload as upload_runtime
        upload = await upload_runtime(environment, archive, ref, remote)
        timings['archive_upload'] = time.monotonic() - started
        started = time.monotonic()
        command = (f'cd {shlex.quote(target)} && '
                   f'printf "%s  runtime.tar.gz\\n" {shlex.quote(ref["archive_sha256"])} | sha256sum -c - && '
                   'tar --no-same-owner -xzf runtime.tar.gz && '
                   'test -x python/bin/python3 && '
                   + ''.join('test -x ' + shlex.quote('python/bin/' + tool) + ' && ' for tool in tools)
                   + 'rm -f runtime.tar.gz && chmod a+rx . && chmod -R a+rX python && '
                   'mkdir active upper work && chmod 1777 upper work && '
                   + 'printf %s ' + shlex.quote(Path(__file__).with_name('verifier_mount.sh').read_text())
                   + ' > mount.sh && chmod a+r mount.sh')
        result = await environment.exec(command, user='root', timeout_sec=180)
        timings['checksum_extract_permissions'] = time.monotonic() - started
        if result.return_code:
            raise RuntimeError('Verifier runtime staging failed: ' + (result.stderr or result.stdout or '')[-1000:])
    except BaseException:
        await release(environment, {'directory': target})
        raise
    from harbor_patches.verifier_mount import mode, prepare, release
    record = {'format': FORMAT, 'id': ref['id'], 'archive_sha256': ref['archive_sha256'],
              'python': target + '/active/bin/python3', 'directory': target,
              'bin': target + '/active/bin', 'mount_mode': mode(ref),
              'phase': 'after-agent-before-verifier', 'network_downloads': False}
    try:
        started = time.monotonic()
        await prepare(environment, record, user=user, script_staged=True)
        timings['mount_preparation'] = time.monotonic() - started
        record['timings_seconds'] = timings
        if upload_measured(upload):
            record['archive_upload'] = upload
        Path(evidence_dir).mkdir(parents=True, exist_ok=True)
        (Path(evidence_dir) / 'verifier-runtime.json').write_text(json.dumps(record, indent=2) + '\n')
    except BaseException:
        await release(environment, record)
        raise
    return record


def offline_setup_command(target):
    """apt reads only the staged flat repository and keeps its own lists, so the
    agent's apt state and sources are untouched; pip/uv read the wheelhouse.

    APT_CONFIG is read before apt.conf.d, so it redirects the parts directory to a
    copy of the container's own settings (taken now, after the agent) followed by
    ours, which are read last and win."""
    t = shlex.quote(target)
    settings = (f'Dir::Etc::SourceList "{target}/apt/sources.list";\\n'
                f'Dir::Etc::SourceParts "{target}/apt/sources.d";\\n'
                f'Dir::State::Lists "{target}/apt/lists";\\nDir::Cache "{target}/apt/cache";\\n'
                f'Dir::Cache::Archives "{target}/apt/archives";\\nAcquire::Languages "none";\\n')
    return (f'mkdir -p {t}/apt/lists/partial {t}/apt/sources.d {t}/apt/archives/partial {t}/apt/cache {t}/apt/conf.d && '
            f'{{ cp -p /etc/apt/apt.conf.d/* {t}/apt/conf.d/ 2>/dev/null || true; }} && '
            f'{{ ! [ -f /etc/apt/apt.conf ] || cp /etc/apt/apt.conf {t}/apt/conf.d/000-ot-main-apt.conf; }} && '
            f'printf "deb [trusted=yes] file:%s/repo ./\\n" {t} > {t}/apt/sources.list && '
            f"printf '{settings}' > {t}/apt/conf.d/zzzz-ot-verifier-offline && "
            f"printf 'Dir::Etc::Parts \"{target}/apt/conf.d\";\\nDir::Etc::Main \"/dev/null\";\\n{settings}' > {t}/apt.conf && "
            f'mkdir -p {t}/wheels && chmod -R a+rX {t}')


async def stage_offline(environment, context, evidence_dir, *, task=False):
    ref = task_references(context)[1] if task else reference(context, OFFLINE_REFERENCE, OFFLINE_FORMAT)
    if ref is None:
        return None
    timings = {}
    started = time.monotonic()
    archive = cached(ref, OFFLINE_FORMAT, validate_offline_archive)
    if archive is None:
        return None
    timings['verify_structure'] = time.monotonic() - started
    started = time.monotonic()
    # Apptainer binds /tmp to private node-local storage. Large package inputs
    # must not pass through the writable image layer under /setup_files.
    prefix = '/tmp/.ot-task-offline-' if task else '/tests/.ot-verifier-offline-'
    result = await environment.exec('mktemp -d ' + prefix + 'XXXXXXXXXXXX', user='root')
    target = (result.stdout or '').strip()
    if result.return_code or not re.fullmatch(re.escape(prefix) + r'[A-Za-z0-9]+', target):
        raise RuntimeError('Could not allocate a fresh verifier package directory')
    timings['allocate'] = time.monotonic() - started
    started = time.monotonic()
    remote = target + '/packages.tar.gz'
    from harbor_patches.runtime_transfer import upload as upload_runtime
    upload = await upload_runtime(environment, archive, ref, remote)
    timings['archive_upload'] = time.monotonic() - started
    started = time.monotonic()
    from harbor_patches.upload_runtime import extraction_command
    command = (f'cd {shlex.quote(target)} && '
               f'printf "%s  packages.tar.gz\\n" {shlex.quote(ref["archive_sha256"])} | sha256sum -c - && '
               + extraction_command('packages.tar.gz', '.')
               + ' && rm -f packages.tar.gz && test -f repo/Packages && '
               + offline_setup_command(target))
    result = await environment.exec(command, user='root', timeout_sec=300)
    timings['checksum_extract_configuration'] = time.monotonic() - started
    if result.return_code:
        raise RuntimeError('Verifier package staging failed: ' + (result.stderr or result.stdout or '')[-1000:])
    record = {'format': OFFLINE_FORMAT, 'id': ref['id'], 'archive_sha256': ref['archive_sha256'],
              'directory': target, 'phase': 'before-agent' if task else 'after-agent-before-verifier', 'network_downloads': False, 'timings_seconds': timings}
    if upload_measured(upload):
        record['archive_upload'] = upload
    if evidence_dir is not None:
        Path(evidence_dir).mkdir(parents=True, exist_ok=True)
        name = 'task-offline-packages.json' if task else 'verifier-offline-packages.json'
        (Path(evidence_dir) / name).write_text(json.dumps(record, indent=2) + '\n')
    return record


async def stage_toolchain(environment, context, evidence_dir):
    """Stage an exact pristine-image toolchain read-only under a fresh /tests directory."""
    ref = reference(context, TOOLCHAIN_REFERENCE, TOOLCHAIN_FORMAT)
    if ref is None:
        return None
    timings = {}
    started = time.monotonic()
    archive = cached(ref, TOOLCHAIN_FORMAT, validate_toolchain_archive)
    if archive is None:
        return None
    manifest = json.loads(archive.with_suffix('').with_suffix('.json').read_text())
    from hpc.offline_prep.image_toolchain import canonical, expected_version
    expected = expected_version(canonical(manifest['spec']))
    timings['verify_structure'] = time.monotonic() - started
    started = time.monotonic()
    result = await environment.exec('mktemp -d /tests/.ot-verifier-toolchain-XXXXXXXXXXXX', user='root')
    target = (result.stdout or '').strip()
    if result.return_code or not re.fullmatch(r'/tests/\.ot-verifier-toolchain-[A-Za-z0-9]+', target):
        raise RuntimeError('Could not allocate a fresh verifier toolchain directory')
    timings['allocate'] = time.monotonic() - started
    try:
        started = time.monotonic()
        from harbor_patches.runtime_transfer import upload as upload_runtime
        upload = await upload_runtime(environment, archive, ref, target + '/toolchain.tar.gz')
        timings['archive_upload'] = time.monotonic() - started
        started = time.monotonic()
        t = shlex.quote(target)
        go = shlex.quote(target + '/toolchain/bin/go')
        # GOENV/GOTOOLCHAIN only for this identity probe; test.sh decides its own activation.
        command = (f'cd {t} && printf "%s  toolchain.tar.gz\\n" {shlex.quote(ref["archive_sha256"])} | sha256sum -c - && '
                   'tar --no-same-owner -xzf toolchain.tar.gz && rm -f toolchain.tar.gz && '
                   f'test -x {go} && chmod -R a+rX,go-w toolchain && chmod a+rx . && '
                   f'env GOENV=off GOTOOLCHAIN=local GOROOT={shlex.quote(target + "/toolchain")} {go} version')
        result = await environment.exec(command, user='root', timeout_sec=300)
        timings['checksum_extract_version'] = time.monotonic() - started
        observed = (result.stdout or '').strip().splitlines()[-1:] or ['']
        if result.return_code or observed[0] != expected:
            raise RuntimeError('Verifier toolchain staging failed: '
                               + (observed[0] if not result.return_code else (result.stderr or result.stdout or '')[-1000:]))
    except BaseException:
        await environment.exec('rm -rf -- ' + shlex.quote(target), user='root')
        raise
    record = {'format': TOOLCHAIN_FORMAT, 'id': ref['id'], 'archive_sha256': ref['archive_sha256'],
              'kind': manifest['spec']['kind'], 'version': manifest['spec']['version'], 'observed_version': observed[0],
              'directory': target, 'goroot': target + '/toolchain', 'bin': target + '/toolchain/bin',
              'phase': 'after-agent-before-verifier', 'network_downloads': False,
              'timings_seconds': timings}
    if upload_measured(upload):
        record['archive_upload'] = upload
    if evidence_dir is not None:
        Path(evidence_dir).mkdir(parents=True, exist_ok=True)
        (Path(evidence_dir) / 'verifier-toolchain.json').write_text(json.dumps(record, indent=2) + '\n')
    return record
