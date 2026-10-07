"""Run concurrent bridge work with cluster-specific container hooks.

Under Slurm, each environment gets a worker before build/start, using CPU
and memory from task.toml's effective environment config (including overrides).
trial_step.py owns that worker; configure_worker() installs the container hooks.
"""
import glob
import base64
import hashlib
import json
import re
import os
import shutil
import socket
import subprocess
import sys
import time
import threading
import tempfile
from pathlib import Path
from functools import partial
from harbor.environments.apptainer import worker

_original_start = worker.ApptainerInstance.start
_original_stop = worker.ApptainerInstance.stop
_original_build_sif = worker.ApptainerInstance._build_sif
_original_parse_copies = worker._parse_copies
_original_pool_key = worker._pool_key_from_payload
STALL_BUDGET = float(os.environ.get('BRIDGE_STALL_BUDGET', '300'))


class ContainerStartGate:
    """Bound startup concurrency and optionally space out admissions."""

    def __init__(self, concurrency, interval=0, clock=time.monotonic, sleep=time.sleep):
        if concurrency < 1 or not 0 <= interval < float('inf'):
            raise ValueError('startup concurrency must be positive and interval finite/nonnegative')
        self.semaphore = threading.BoundedSemaphore(concurrency)
        self.lock = threading.Lock()
        self.interval, self.clock, self.sleep = interval, clock, sleep
        self.last_start = None

    def __enter__(self):
        self.semaphore.acquire()
        try:
            with self.lock:
                if self.last_start is not None:
                    delay = self.last_start + self.interval - self.clock()
                    if delay > 0:
                        self.sleep(delay)
                self.last_start = self.clock()
        except BaseException:
            self.semaphore.release()
            raise
        return self

    def __exit__(self, *exc):
        self.semaphore.release()


def parse_copies_docker_semantics(dockerfile_path):
    """Expand directory COPY sources: Docker copies contents, Apptainer the directory.

    Keep a trailing slash so each child lands inside the destination directory.
    """
    context = os.path.dirname(os.path.abspath(dockerfile_path))
    expanded = []
    for sources, dest in _original_parse_copies(dockerfile_path):
        for src in sources:
            abs_src = os.path.normpath(os.path.join(context, src))
            if os.path.isdir(abs_src):
                target = dest.rstrip('/') + '/'
                for child in sorted(os.listdir(abs_src)):
                    expanded.append(([os.path.join(src, child)], target))
            else:
                expanded.append(([src], dest))
    return expanded


def stop_anchor(instance):
    proc = getattr(instance, '_helma_tmux_anchor', None)
    if proc is not None and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
    log = getattr(instance, '_helma_tmux_log', None)
    if log is not None:
        log.close()


def trial_step_prefix(payload, env_id):
    """srun wrapper giving the trial its own Slurm step cgroup, or [] outside Slurm."""
    if os.environ.get('OT_TRIAL_SRUN', '1') == '0' or not os.environ.get('SLURM_JOB_ID'):
        return []
    cfg = payload.get('task_env_config') or {}
    cpus = int(cfg.get('cpus') or os.environ.get('OT_TRIAL_CPUS', '1'))
    mem = f"{int(cfg['memory_mb'])}M" if cfg.get('memory_mb') else os.environ.get('OT_TRIAL_MEM', '4G')
    # GPU reservations belong to serving, not task steps (--gres=none).
    # Remote coordinators must pin steps to their node: images/staging are local.
    # Only coordinators use --overlap; tasks reserve distinct CPU sets.
    nested = []
    if os.environ.get('SLURM_STEP_ID'):
        node = os.environ.get('SLURMD_NODENAME') or socket.gethostname().split('.')[0]
        # --cpu-bind=none: the parent step already holds a core mask, and an
        # inherited binding makes every nested step fight over the same core.
        nested = ['--nodes=1', '-w', node, '--cpu-bind=none']
    elif int(os.environ.get('SLURM_JOB_NUM_NODES') or 1) > 1:
        # A model served over several nodes: Slurm would otherwise place a trial step
        # on any of them, where this node's staging directory does not exist (934839).
        nested = ['--nodes=1', '-w', os.environ.get('SLURMD_NODENAME') or socket.gethostname().split('.')[0]]
    return ['srun', '--quiet', '--exact', '-n1', *nested,
            f'-c{cpus}', f'--mem={mem}', '--gres=none', '--job-name', f'trial-{env_id}']


def anchor_log_tail(env, lines=8):
    """The last lines the anchor wrote, so the failure says why instead of where."""
    path = Path(env.staging_dir) / 'tmux-anchor.log'
    try:
        env._helma_tmux_log.flush()
    except Exception:  # noqa: BLE001
        pass
    try:
        text = path.read_text(errors='replace').strip().splitlines()[-lines:]
    except OSError as e:
        return f'(no tmux-anchor.log: {e})'
    return ' | '.join(text) or '(tmux-anchor.log is empty)'


def content_keyed_payload(payload):
    """Hash the actual build payload; pinned Harbor sends a Dockerfile-only key."""
    files = payload.get('files_b64') or {}
    if 'Dockerfile' not in files:
        return payload
    h = hashlib.sha256()
    for name in sorted(files, key=Path):
        rel = name.encode()
        content = base64.b64decode(files[name], validate=True)
        h.update(len(rel).to_bytes(4, 'big')); h.update(rel)
        h.update(len(content).to_bytes(4, 'big')); h.update(content)
    key = h.hexdigest()[:12]
    result = dict(payload, dockerfile_hash=key)
    old = payload.get('dockerfile_hash')
    path = payload.get('sif_path') or ''
    if old and Path(path).name.startswith('build_') and path.endswith('-' + old + '.sif'):
        result['sif_path'] = path[:-len(old + '.sif')] + key + '.sif'
    return result


_trial = threading.local()


def dependency_archives():
    """Read the optional dependency archive configuration for this run."""
    text = os.environ.get('OT_DEPENDENCY_ARCHIVES')
    return json.loads(text) if text else None


def dependency_archive(payload):
    """Host path of the task's archive; the file need not exist yet."""
    spec, name = dependency_archives(), payload.get('task_name') or ''
    if not spec or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', name):
        return None
    return Path(spec['directory']) / (name + '.tar')


SAVE_TIMEOUT = 1800


def sweep_partial_archives(now=None):
    """Remove half-written archives of workers that were killed while saving.

    Several jobs may share the directory, so a file another worker is still writing must stay:
    only files untouched for twice the save timeout go."""
    spec = dependency_archives()
    if not spec:
        return 0
    now, removed = time.time() if now is None else now, 0
    for path in glob.glob(os.path.join(glob.escape(spec['directory']), '*.partial')):
        try:
            if now - os.path.getmtime(path) > 2 * SAVE_TIMEOUT:
                os.unlink(path)
                removed += 1
        except OSError:
            pass
    if removed:
        print(f'[worker] Removed {removed} half-written dependency archives', flush=True)
    return removed


def trial_reward(staging_dir):
    logs = Path(staging_dir) / 'logs/verifier'
    try:
        return float((logs / 'reward.txt').read_text().strip())
    except (OSError, ValueError):
        pass
    try:
        return float(json.loads((logs / 'reward.json').read_text())['reward'])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def save_dependency_archive(self):
    """Save a passed oracle trial's dependencies; log archive-write failures."""
    spec, archive = dependency_archives(), getattr(self, '_helma_dependency_archive', None)
    if not (spec and archive and getattr(self, '_helma_save_dependencies', False) and self.started):
        return None
    if trial_reward(self.staging_dir) != 1:
        return None
    partial = archive.with_name(f'{archive.name}.{self.env_id}.partial')
    cmd = ([worker.APPTAINER, 'exec', f'instance://{self.instance_name}', 'tar', '-cf', '-', '-C', spec['folder'],
            '--anchored', '--no-wildcards-match-slash'] + ['--exclude=' + e for e in spec.get('exclude') or []] + ['.'])
    try:
        archive.parent.mkdir(parents=True, exist_ok=True)
        with open(partial, 'wb') as out:
            done = subprocess.run(cmd, stdout=out, stderr=subprocess.PIPE, timeout=SAVE_TIMEOUT)
        if done.returncode:
            raise RuntimeError(done.stderr.decode(errors='replace').strip()[-300:])
        size = partial.stat().st_size
        os.replace(partial, archive)
        print(f'[{self.env_id}] Saved dependency archive {archive.name} ({size / 1e6:.0f} MB)', flush=True)
        return archive
    except Exception as e:
        try:
            partial.unlink()
        except OSError:
            pass
        print(f'[{self.env_id}] Dependency archive {archive.name} not saved: {e}', flush=True)
        return None


def start_with_anchor(self, payload):
    payload = content_keyed_payload(payload)
    archive = dependency_archive(payload)
    self._helma_dependency_archive = archive
    self._helma_save_dependencies = bool(archive and (payload.get('task_env_config') or {}).get('save_dependencies'))
    _trial.archive = archive if archive and archive.is_file() and not self._helma_save_dependencies else None
    _trial.workspace_seeded = set()
    try:
        result = _original_start(self, payload)
        if archive and isinstance(result, dict):
            result['dependency_archive'] = ('saved after reward 1' if self._helma_save_dependencies
                                            else 'mounted' if _trial.archive else 'none for this task')
    finally:
        _trial.archive = None
        _trial.workspace_seeded = None
    try:
        start_anchor(self)
    except BaseException:
        stop_anchor(self)
        _original_stop(self, {})
        raise
    return result


def start_anchor(self):
    """Keep tmux alive in the container for commands that reconnect later."""
    cmd = [worker.APPTAINER, 'exec', '--pwd', '/tmp',
           f'instance://{self.instance_name}', '/bin/bash', '-c']
    from harbor_patches.tmux_runtime import ensure_tmux
    ensure_tmux(cmd, subprocess.run)
    # The container has a private /tmp. Fakeroot may expose uid 0 to the shell
    # while tmux uses the host uid, so prepare both possible socket directories.
    uid = os.getuid()
    subprocess.run(cmd + [f'mkdir -p -m 700 /tmp/tmux-{uid} /tmp/tmux-0'],
                   capture_output=True, timeout=60)
    self._helma_tmux_log = open(Path(self.staging_dir) / 'tmux-anchor.log', 'w')
    self._helma_tmux_anchor = subprocess.Popen(
        cmd + ['tmux new-session -d -s _pilot_anchor && exec sleep infinity'],
        stdin=subprocess.DEVNULL, stdout=self._helma_tmux_log,
        stderr=subprocess.STDOUT, start_new_session=True)
    # The lifecycle worker already owns the Slurm step; tmux inherits it.
    deadline = time.monotonic() + max(15.0, STALL_BUDGET)
    while time.monotonic() < deadline:
        if self._helma_tmux_anchor.poll() is not None:
            raise RuntimeError('Persistent tmux owner exited: ' + anchor_log_tail(self))
        try:
            check = subprocess.run(cmd + ['tmux has-session -t _pilot_anchor'],
                                   capture_output=True, timeout=10)
        except subprocess.TimeoutExpired:
            if time.monotonic() >= deadline:
                raise
            continue
        if check.returncode == 0:
            print(f'[{self.env_id}] Helma persistent tmux owner ready', flush=True)
            return
        time.sleep(0.5)
    raise RuntimeError('Persistent tmux owner readiness timed out')


def only_creates_directories(cmd):
    """`apptainer exec ... -c 'mkdir -p <paths>'` and nothing else, so it can safely run twice."""
    return (isinstance(cmd, list) and len(cmd) > 3 and cmd[1] == 'exec' and cmd[-2] == '-c'
            and re.fullmatch(r'mkdir -p [^;&|<>$`\n]+', str(cmd[-1])) is not None)


def stop_with_anchor(self, payload):
    # Checked before the instance is stopped: stopping it kills the owner, which is normal.
    # An owner that is gone earlier means the tmux server lost its fakeroot session mid-trial.
    proc = getattr(self, '_helma_tmux_anchor', None)
    if proc is not None and proc.poll() is not None:
        print(f'[{self.env_id}] tmux owner had exited with {proc.returncode} before stop: {anchor_log_tail(self)}', flush=True)
    try:
        save_dependency_archive(self)
        return _original_stop(self, payload)
    finally:
        stop_anchor(self)


_baked_tests = {}


def image_bakes_tests(sif):
    """Does this SIF already contain /tests/test.sh? Probed once per image, then cached."""
    if sif not in _baked_tests:
        apptainer = worker.APPTAINER or worker.detect_apptainer()
        probe = subprocess.run([apptainer, 'exec', sif, 'test', '-f', '/tests/test.sh'],
                               capture_output=True, timeout=120)
        _baked_tests[sif] = probe.returncode == 0
        if _baked_tests[sif]:
            print(f'[worker] {os.path.basename(sif)} bakes /tests; not masking it with a bind',
                  flush=True)
    return _baked_tests[sif]


def instance_start_command(cmd, net_flags):
    """Add isolation/archive mounts and avoid masking tests baked into an image."""
    flags = list(net_flags)
    if '--contain' not in cmd and '--containall' not in cmd:
        flags.append('--contain')
    archive = getattr(_trial, 'archive', None)
    if archive:
        flags += ['--bind', f'{archive}:{dependency_archives()["target"]}:ro']
    sif = next((c for c in cmd if isinstance(c, str) and c.endswith('.sif')), '')
    if sif and image_bakes_tests(sif):
        # Separate verifier images COPY their tests into /tests. An empty
        # staging bind would hide them; ordinary uploaded tests keep the bind.
        args, kept = iter(cmd[3:]), []
        for arg in args:
            if arg == '--bind':
                mount = next(args)
                if not mount.endswith(':/tests:rw'):
                    kept.extend([arg, mount])
            else:
                kept.append(arg)
        return cmd[:3] + flags + kept
    return cmd[:3] + flags + cmd[3:]


def merge_workspace(source, destination):
    """Overlay task files without following image or task directory symlinks."""
    for src in source.iterdir():
        dst = destination / src.name
        if src.is_dir() and not src.is_symlink() and dst.is_dir() and not dst.is_symlink():
            dst.chmod(dst.stat().st_mode | 0o700)
            merge_workspace(src, dst)
            src.rmdir()
        else:
            if dst.is_symlink() or dst.is_file():
                dst.unlink()
            elif dst.exists():
                shutil.rmtree(dst)
            shutil.move(str(src), str(dst))


def seed_image_workspace(cmd, original_run):
    """Seed each fresh staging workspace before its bind can mask image files.

    Read the effective image, including deferred build layers, without any of
    the trial's masking binds. Task uploads/COPY destinations take precedence.
    The thread-local scope is one start(), so fallback attempts copy only once.
    """
    seeded = getattr(_trial, 'workspace_seeded', None)
    if seeded is None:
        return
    mounts = [cmd[i + 1] for i, arg in enumerate(cmd[:-1]) if arg == '--bind']
    workspace = next((Path(m[:-len(':/workspace:rw')]) for m in mounts
                      if m.endswith(':/workspace:rw')), None)
    if workspace is None or workspace in seeded:
        return
    overlays = []
    for i, arg in enumerate(cmd[:-1]):
        if arg == '--overlay' and cmd[i + 1].endswith(':ro'):
            overlays += ['--overlay', cmd[i + 1]]
    with tempfile.TemporaryDirectory(prefix='workspace-image-', dir=workspace.parent) as temp:
        image_workspace = Path(temp) / 'contents'
        image_workspace.mkdir()
        # A read-only-only overlay can expose fuse-overlayfs whiteout files
        # through kernel OverlayFS on Helma. A disposable writable upper layer
        # makes Apptainer apply the whiteouts before we copy the merged view.
        # Never copy or filter raw layer entries: that could resurrect deletions.
        probe = [cmd[0], 'exec', '--containall', '--cleanenv', '--no-home',
                 '--writable-tmpfs', '--pwd', '/',
                 '--bind', f'{image_workspace}:/_ot_workspace_init:rw', *overlays,
                 cmd[-2], 'sh', '-ec',
                 'if [ -d /workspace ]; then '
                 # cp -a also copies filesystem ACLs, which can fail between
                 # fuse-overlayfs and the bind destination. Tar preserves the
                 # actual files, links and mode bits without copying those ACLs.
                 'archive=$(mktemp); trap \'rm -f "$archive"\' EXIT; '
                 'tar -C /workspace -cpf "$archive" .; '
                 'tar --no-same-owner -xpf "$archive" -C /_ot_workspace_init; '
                 'elif [ -e /workspace ] || [ -L /workspace ]; then '
                 'echo "Image /workspace is not a readable directory" >&2; exit 1; fi']
        result = original_run(probe, capture_output=True, text=True, timeout=300)
        if result.returncode:
            raise RuntimeError('Cannot preserve image /workspace: ' + (result.stderr or '')[-2000:])
        image_workspace.chmod(image_workspace.stat().st_mode | 0o700)
        merge_workspace(workspace, image_workspace)
        workspace.rmdir()
        image_workspace.rename(workspace)
    seeded.add(workspace)
    print(f'[worker] Preserved image /workspace in {workspace}', flush=True)


def run_image_build(original_run, cmd, args, kwargs, clock=time.monotonic, sleep=time.sleep):
    """Bound squashfs caches inside small Slurm steps; retry transient OCI fetches."""
    cmd = list(cmd)
    compression = os.environ.get('OT_IMAGE_COMPRESSION_ARGS')
    if compression and not any(str(arg).startswith('--mksquashfs-args') for arg in cmd):
        cmd[2:2] = ['--mksquashfs-args', compression]
    # Only retry registry imports into the worker's temporary image. Never
    # rerun arbitrary Dockerfile RUN steps or overwrite a published image.
    registry_import = cmd[-1].startswith('docker://') and cmd[-2].endswith('.tmp')
    deadline = clock() + kwargs['timeout'] if kwargs.get('timeout') is not None else None
    for attempt in range(3):
        options = dict(kwargs)
        if deadline is not None:
            options['timeout'] = max(0.001, deadline - clock())
        result = original_run(cmd, *args, **options)
        error = result.stderr or ''
        if isinstance(error, bytes):
            error = error.decode(errors='replace')
        transient = ('conveyor failed to get' in error and any(message in error for message in
                     ('unexpected end of JSON input', 'connection reset by peer', 'TLS handshake timeout')))
        if not registry_import or result.returncode == 0 or not transient or attempt == 2:
            return result
        delay = 2 ** (attempt + 1)
        if deadline is not None and deadline - clock() <= delay:
            return result
        print(f'[worker] Transient OCI import failure; retry {attempt + 1}/2 in {delay}s', flush=True)
        sleep(delay)
        if '--force' not in cmd:
            cmd.insert(2, '--force')


def compatible_fakeroot_command(cmd, original_run, cache):
    """Keep root mapping when the host fakeroot helper cannot run in an older image."""
    if '--fakeroot' not in cmd or '--ignore-fakeroot-command' in cmd:
        return cmd
    sif = next((c for c in cmd if isinstance(c, str) and c.endswith('.sif')), '')
    if not sif or not os.path.isfile(sif):
        return cmd
    stat = os.stat(sif)
    key = (os.path.realpath(sif), stat.st_size, stat.st_mtime_ns)
    if key not in cache:
        probe_cmd = [cmd[0], 'exec', '--fakeroot', '--contain', '--no-home',
                     '--pwd', '/', sif, '/usr/bin/id', '-u']
        probe = original_run(probe_cmd, capture_output=True, text=True, timeout=30)
        error = probe.stderr or ''
        helper_error = ('/.singularity.d/libs/fakeroot' in error
                        or ('faked' in error and 'GLIBC_' in error))
        fallback = False
        if probe.returncode and helper_error:
            retry = original_run(probe_cmd[:2] + ['--ignore-fakeroot-command'] + probe_cmd[2:],
                                 capture_output=True, text=True, timeout=30)
            fallback = retry.returncode == 0 and retry.stdout.strip() == '0'
            if fallback:
                print(f'[worker] {sif}: host fakeroot helper incompatible; '
                      'using verified root-mapped namespace without the helper', flush=True)
        cache[key] = fallback
    return cmd[:3] + ['--ignore-fakeroot-command'] + cmd[3:] if cache[key] else cmd


def run_container_commands(original_run, net_flags=(), clock=time.monotonic):
    """Adapt instance startup and retry safe mkdir stalls."""
    fakeroot_cache = {}
    def run(cmd, *args, **kwargs):
        if (getattr(_trial, 'workspace_seeded', None) is not None
                and isinstance(cmd, list) and len(cmd) > 4 and cmd[1] == 'exec'
                and cmd[-2] == '-c' and 'precedence ::ffff:0:0/96 100' in cmd[-1]):
            # Only Harbor's startup bootstrap, never task/agent commands.
            from harbor_patches.tmux_runtime import without_runtime_install
            cmd = [*cmd[:-1], without_runtime_install(cmd[-1])]
        if (isinstance(cmd, list) and len(cmd) >= 4 and cmd[1] == 'build'
                and os.path.basename(cmd[0]) in ('apptainer', 'singularity')):
            return run_image_build(original_run, cmd, args, kwargs)
        instance_start = isinstance(cmd, list) and cmd[1:3] == ['instance', 'start']
        if instance_start:
            seed_image_workspace(cmd, original_run)
            cmd = instance_start_command(cmd, net_flags)
            cmd = compatible_fakeroot_command(cmd, original_run, fakeroot_cache)
        start = clock()
        while True:
            try:
                return original_run(cmd, *args, **kwargs)
            except subprocess.TimeoutExpired:
                if not only_creates_directories(cmd) or clock() - start > STALL_BUDGET:
                    raise
                print(f'[worker] {cmd[-1]!r} timed out; trying again', flush=True)
    return run


NET_FLAGS = ['--net', '--network', 'none']


def network_isolation_available(sif_cache):
    """Probe isolation and visibly record any fallback; host access is not offline."""
    requested = os.environ.get('OT_NET_ISOLATION', '1') != '0'
    available, reason = False, 'host networking explicitly requested'
    if requested:
        sifs = sorted(glob.glob(os.path.join(sif_cache or '', '*.sif')))
        reason = 'no cached SIF available to probe a network namespace'
        if sifs:
            apptainer = worker.APPTAINER or worker.detect_apptainer()
            try:
                probe = subprocess.run([apptainer, 'exec'] + NET_FLAGS + [sifs[0], 'true'],
                                       capture_output=True, text=True, timeout=120)
                available = probe.returncode == 0
                lines = [re.sub(r'\x1b\[[0-9;]*m', '', l).strip() for l in (probe.stderr or '').splitlines()]
                reason = 'namespace probe succeeded' if available else next(
                    (l for l in lines if 'ERROR' in l), next((l for l in reversed(lines) if l), 'namespace probe failed'))
            except (OSError, subprocess.TimeoutExpired) as exc:
                reason = str(exc)
    status = {'requested': 'isolated' if requested else 'host',
              'effective': 'network-none' if available else 'host', 'reason': reason}
    print('[worker] network status: ' + json.dumps(status), flush=True)
    if requested and not available:
        print('WARNING: could not enable a separate network namespace; falling back to the HOST network. '
              'Containers share host loopback and may access the internet. This run is NOT certified offline. '
              'Reason: ' + reason, file=sys.stderr, flush=True)
    path = os.environ.get('OT_NETWORK_STATUS_PATH')
    if path:
        Path(path).write_text(json.dumps(status, indent=2) + '\n')
    return available


def configure_loopback_hosts(staging_base):
    """Supply standard loopback names when site bind mounts are disabled."""
    binds = [p for p in os.environ.get('APPTAINER_BINDPATH', '').split(',') if p]
    if any((p.split(':') + [''])[1] == '/etc/hosts' or p == '/etc/hosts' for p in binds):
        return  # Respect an explicitly supplied hosts file.
    directory = Path(staging_base or os.path.join(os.environ.get('TMPDIR', '/tmp'), 'harbor-bridge'))
    directory.mkdir(parents=True, exist_ok=True)
    hosts = directory / 'container-hosts'
    # Parent creates this before starting children. Do not truncate a file that
    # running instances may already have mounted.
    if not hosts.exists():
        with tempfile.NamedTemporaryFile(mode='w', dir=directory, delete=False) as temporary:
            temporary.write('127.0.0.1 localhost\n::1 localhost ip6-localhost ip6-loopback\n')
            pending = Path(temporary.name)
        pending.chmod(0o644)
        pending.replace(hosts)
    binds.append(f'{hosts.resolve()}:/etc/hosts:ro')
    os.environ['APPTAINER_BINDPATH'] = ','.join(binds)


def configure_explicit_host_network(force=False):
    """Give explicit or fallback host networking the same DNS/proxy setup."""
    if not force and os.environ.get('OT_NET_ISOLATION', '1') != '0':
        return
    dns = '/etc/resolv.conf:/etc/resolv.conf:ro'
    binds = [p for p in os.environ.get('APPTAINER_BINDPATH', '').split(',') if p]
    if dns not in binds:
        binds.append(dns)
    os.environ['APPTAINER_BINDPATH'] = ','.join(binds)
    for name in ('http_proxy', 'https_proxy', 'HTTP_PROXY', 'HTTPS_PROXY', 'no_proxy', 'NO_PROXY'):
        if os.environ.get(name):
            os.environ['APPTAINERENV_' + name] = os.environ[name]


def cleanup_own_staging_only(hostname, staging_base):
    if os.path.isdir(staging_base):
        for entry in os.listdir(staging_base):
            if entry.startswith('apt_env-'):
                shutil.rmtree(os.path.join(staging_base, entry), ignore_errors=True)
                print(f'[{hostname}] Cleaned stale staging: {entry}', flush=True)


def keep_instance_records_off_home(staging_base):
    """Give apptainer a per-job directory for its instance registry and logs.

    Without --staging-base the worker stages in /tmp, which is shared, so the default is kept."""
    if staging_base and 'APPTAINER_CONFIGDIR' not in os.environ:
        os.environ['APPTAINER_CONFIGDIR'] = os.path.join(staging_base, 'apptainer-config')
    if os.environ.get('APPTAINER_CONFIGDIR'):
        os.makedirs(os.environ['APPTAINER_CONFIGDIR'], exist_ok=True)


def configure_container_certificates():
    """Expose host-selected CA files read-only at stable container paths."""
    binds = [p for p in os.environ.get('APPTAINER_BINDPATH', '').split(',') if p]
    for name in ('SSL_CERT_FILE', 'REQUESTS_CA_BUNDLE', 'CURL_CA_BUNDLE'):
        source = os.environ.get(name)
        if not source:
            continue
        source = str(Path(source).resolve(strict=True))
        target = '/run/ot-certificates/' + name.lower() + '.pem'
        bind = f'{source}:{target}:ro'
        if bind not in binds:
            binds.append(bind)
        os.environ['APPTAINERENV_' + name] = target
    if binds:
        os.environ['APPTAINER_BINDPATH'] = ','.join(binds)


def prepared_image_only(self, dockerfile, output):
    if os.environ.get('OT_IMAGES_PREPARED') == '1':
        raise RuntimeError(f'Image unavailable after separate preparation: {output}; '
                           'see image-preparation.json and image-build-logs. '
                           'Refusing to rebuild within task memory/time limits.')
    return _original_build_sif(self, dockerfile, output)


def configure_worker(staging_base, sif_cache, child=False):
    configure_container_certificates()
    configure_loopback_hosts(staging_base)
    # Slurm's host TMPDIR is not mounted inside isolated task containers.
    # APPTAINERENV changes only the container environment, not worker staging.
    for name in ('TMPDIR', 'TMP', 'TEMP'):
        os.environ[f'APPTAINERENV_{name}'] = '/tmp'
    worker._INSTANCE_START_SEM = ContainerStartGate(
        int(os.environ.get('BRIDGE_START_CONCURRENCY', '8')),
        float(os.environ.get('BRIDGE_START_INTERVAL', '0')))
    worker._cleanup_stale_instances = cleanup_own_staging_only
    keep_instance_records_off_home(staging_base)
    if not child:
        sweep_partial_archives()
    if child:
        net_flags = json.loads(os.environ.get('OT_TRIAL_NET_FLAGS', '[]'))
    else:
        net_flags = NET_FLAGS if network_isolation_available(sif_cache) else []
        os.environ['OT_TRIAL_NET_FLAGS'] = json.dumps(net_flags)
    if not net_flags:
        # A failed namespace probe uses host networking even when isolation
        # was requested. It needs the same DNS bind and proxy forwarding.
        configure_explicit_host_network(force=True)
    if not child and os.environ.get('SLURM_JOB_ID') and os.environ.get('OT_TRIAL_SRUN', '1') != '0':
        from trial_step import SlurmInstance, REGISTRY
        with worker._instances_lock:
            REGISTRY.update(worker._instances)
            worker._instances = REGISTRY
        worker.ApptainerInstance = partial(
            SlurmInstance, step_prefix=trial_step_prefix, start_gate=worker._INSTANCE_START_SEM)
        return  # Container hooks belong to the child, not the shared dispatcher.
    worker._pool_key_from_payload = lambda payload: _original_pool_key(content_keyed_payload(payload))
    worker.ApptainerInstance.start = start_with_anchor
    worker.ApptainerInstance.stop = stop_with_anchor
    worker.ApptainerInstance._build_sif = prepared_image_only
    worker._parse_copies = parse_copies_docker_semantics
    worker.subprocess.run = run_container_commands(worker.subprocess.run, net_flags)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--staging-base', default='')
    parser.add_argument('--sif-cache', default='')
    args, _ = parser.parse_known_args()
    configure_worker(args.staging_base, args.sif_cache)
    worker.main()
