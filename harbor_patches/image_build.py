"""Build deferred layers on native scratch, then freeze the ext3 cache artifact."""
from pathlib import Path
import os
import re
import shutil
import tempfile
import subprocess
import signal
import threading
import time
from collections import deque
import base64
import json
import fcntl
import shlex


def image_tmp_build_mounts(run):
    """Keep generated definition %post cleanup in the image's temp directories.

    Apptainer build mounts host /tmp and /var/tmp in %post, has no --no-mount
    flag, and strips that environment setting from its nested exec. Explicit
    binds override those defaults. %setup locates the newly unpacked rootfs;
    binding its own directories preserves both %files inputs and RUN outputs.
    """
    def execute(command, *args, **kwargs):
        offset = 2 if command[:2] == ['unshare', '-r'] else 0
        inner = command[offset:]
        if not (len(inner) >= 4 and Path(inner[0]).name in ('apptainer', 'singularity')
                and inner[1] == 'build' and str(inner[-1]).endswith('.def')):
            return run(command, *args, **kwargs)
        with tempfile.TemporaryDirectory(prefix='image-tmp-mounts-') as temporary:
            directory = Path(temporary)
            definition = directory / 'build.def'
            # Do not follow an image's absolute symlink in the host namespace.
            # Such images can still use the isolated deferred-overlay builder.
            setup = ('\n%setup\n'
                     '    test ! -L "$APPTAINER_ROOTFS/var"\n')
            binds = []
            for name in ('tmp', 'var/tmp'):
                link = directory / name.replace('/', '-')
                setup += f'    test ! -L "$APPTAINER_ROOTFS/{name}"\n'
                setup += f'    mkdir -p "$APPTAINER_ROOTFS/{name}"\n'
                setup += f'    ln -sfn "$APPTAINER_ROOTFS/{name}" {shlex.quote(str(link))}\n'
                binds += ['--bind', f'{link}:/{name}']
            definition.write_text(Path(inner[-1]).read_text() + setup)
            return run([*command[:offset + 2], *binds, *command[offset + 2:-1],
                        str(definition)], *args, **kwargs)
    return execute


def authenticated_registry_commands(run):
    """Give root-mapped OCI imports an explicit, temporary Docker login."""
    def execute(command, *args, **kwargs):
        offset = 2 if command[:2] == ['unshare', '-r'] else 0
        inner = command[offset:]
        environment = kwargs.get('env', os.environ)
        username = environment.get('APPTAINER_DOCKER_USERNAME')
        password = environment.get('APPTAINER_DOCKER_PASSWORD')
        if (len(inner) < 2 or Path(inner[0]).name not in ('apptainer', 'singularity')
                or inner[1] not in ('build', 'pull') or not username or not password
                or '--authfile' in inner):
            return run(command, *args, **kwargs)
        with tempfile.TemporaryDirectory(prefix='image-registry-auth-',
                                         dir=environment.get('TMPDIR')) as temporary:
            authfile = Path(temporary) / 'auth.json'
            token = base64.b64encode(f'{username}:{password}'.encode()).decode()
            with os.fdopen(os.open(authfile, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as stream:
                json.dump({'auths': {'https://index.docker.io/v1/': {'auth': token}}}, stream)
            flags = ['--authfile', str(authfile)]
            # --docker-login prompts again even with environment credentials.
            # On unattended jobs EOF replaces the supplied password with empty
            # input; use the private authfile without interactive login.
            interval = float(environment.get('OT_DOCKER_PULL_INTERVAL_SECONDS', '0'))
            if interval > 0:
                # One shared clock bounds aggregate starts across this loop's shards.
                clock_path = Path(environment['OT_DOCKER_PULL_STATE'])
                clock_path.parent.mkdir(parents=True, exist_ok=True)
                with clock_path.open('a+') as clock_file:
                    fcntl.flock(clock_file, fcntl.LOCK_EX)
                    clock_file.seek(0)
                    previous = float(clock_file.read() or '0')
                    time.sleep(max(0.0, previous + interval - time.time()))
                    clock_file.seek(0)
                    clock_file.truncate()
                    clock_file.write(str(time.time()))
                    clock_file.flush()
            return run([*command[:offset + 2], *flags, *command[offset + 2:]], *args, **kwargs)
    return execute


def build_budget_commands(run, seconds, clock=time.monotonic):
    """Stream build diagnostics; zero leaves timing to the Slurm allocation."""
    deadline = clock() + seconds if seconds else None

    def execute(command, *args, **kwargs):
        inner = command[2:] if command[:2] == ['unshare', '-r'] else command
        tool = Path(inner[0]).name if inner else ''
        build = tool in ('apptainer', 'singularity') and inner[1:2] == ['build']
        deferred = command[:2] == ['unshare', '-r'] and (
            tool == 'mkfs.ext3' or (tool in ('apptainer', 'singularity') and '--overlay' in inner))
        if not (build or deferred):
            return run(command, *args, **kwargs)
        remaining = deadline - clock() if deadline is not None else None
        if remaining is not None and remaining <= 0:
            raise subprocess.TimeoutExpired(command, seconds)
        options = dict(kwargs)
        options.pop('timeout', None)
        options.pop('capture_output', None)
        options.pop('text', None)
        options.pop('stdout', None)
        options.pop('stderr', None)
        check = options.pop('check', False)
        tail = deque(maxlen=100)
        if remaining is None:
            print('[build] no per-image timeout; the Slurm allocation sets the deadline', flush=True)
        else:
            print(f'[build] remaining image budget: {remaining:.1f}s', flush=True)
        with subprocess.Popen(command, *args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              text=True, errors='replace', start_new_session=True, **options) as process:
            def stream():
                for line in process.stdout:
                    tail.append(line[-2000:])
                    print(line, end='', flush=True)
            reader = threading.Thread(target=stream, daemon=True)
            reader.start()
            try:
                code = process.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
                reader.join(timeout=5)
                raise subprocess.TimeoutExpired(command, seconds, output=''.join(tail))
            reader.join(timeout=5)
        output = ''.join(tail)
        if check and code:
            raise subprocess.CalledProcessError(code, command, output=output, stderr=output)
        return subprocess.CompletedProcess(command, code, stdout=output, stderr=output if code else '')
    return execute


def definition_build_home(run):
    """Correct Apptainer's missing synthetic root home before Docker RUN steps."""
    def execute(command, *args, **kwargs):
        if not (isinstance(command, list) and len(command) >= 4
                and Path(command[0]).name in ('apptainer', 'singularity')
                and command[1] == 'build' and str(command[-1]).endswith('.def')):
            return run(command, *args, **kwargs)
        content = Path(command[-1]).read_text()
        fixed = re.sub(r'(?m)^(%post[^\n]*\n)',
                       r'\1    if [ "$HOME" = /.root ] && [ ! -d "$HOME" ]; then export HOME=/root; fi\n',
                       content)
        if fixed == content:
            return run(command, *args, **kwargs)
        # Dockerfile ENV exports occur later in %post and keep precedence.
        with tempfile.TemporaryDirectory(prefix='image-build-home-') as temporary:
            definition = Path(temporary) / 'build.def'
            definition.write_text(fixed)
            return run([*command[:-1], str(definition)], *args, **kwargs)
    return execute


def certificate_build_mountpoints(run):
    """Create empty CA bind targets before Apptainer's writable %post mount.

    Runtime overlays can create missing targets; a definition build cannot.
    Copy placeholders, never the host's certificate contents, into the image.
    """
    def execute(command, *args, **kwargs):
        if not (isinstance(command, list) and len(command) >= 4
                and Path(command[0]).name in ('apptainer', 'singularity')
                and command[1] == 'build' and str(command[-1]).endswith('.def')):
            return run(command, *args, **kwargs)
        targets = set()
        for bind in os.environ.get('APPTAINER_BINDPATH', '').split(','):
            fields = bind.split(':')
            if len(fields) >= 2 and re.fullmatch(
                    r'/run/ot-certificates/(ssl_cert_file|requests_ca_bundle|curl_ca_bundle)\.pem', fields[1]):
                targets.add(fields[1])
        if not targets:
            return run(command, *args, **kwargs)
        with tempfile.TemporaryDirectory(prefix='image-cert-targets-') as temporary:
            empty = Path(temporary) / 'empty.pem'
            empty.touch()
            definition = Path(temporary) / 'build.def'
            content = Path(command[-1]).read_text()
            content += '\n%files\n' + ''.join(f'    {empty} {target}\n' for target in sorted(targets))
            definition.write_text(content)
            return run([*command[:-1], str(definition)], *args, **kwargs)
    return execute


def artifact_build_mount(run, directory):
    """Expose an optional build-only artifact store read-only in both build paths."""
    source = Path(directory).resolve(strict=True)
    if not source.is_dir():
        raise ValueError('Image build artifact store must be a directory')
    target = '/run/ot-image-artifacts'

    def execute(command, *args, **kwargs):
        offset = 2 if command[:2] == ['unshare', '-r'] else 0
        inner = command[offset:]
        if not (isinstance(command, list) and len(inner) > 2
                and Path(inner[0]).name in ('apptainer', 'singularity')
                and inner[1] in ('exec', 'build')):
            return run(command, *args, **kwargs)
        adapted = command[:offset + 2] + ['--bind', f'{source}:{target}:ro'] + command[offset + 2:]
        if inner[1] == 'build' and str(command[-1]).endswith('.def'):
            with tempfile.TemporaryDirectory(prefix='image-artifact-target-') as temporary:
                empty = Path(temporary) / 'empty'
                empty.mkdir()
                definition = Path(temporary) / 'build.def'
                definition.write_text(Path(command[-1]).read_text() + f'\n%files\n    {empty}/ {target}/\n')
                return run([*adapted[:-1], str(definition)], *args, **kwargs)
        return run(adapted, *args, **kwargs)
    return execute


def file_backed_build_scripts(run_unshared, apptainer):
    """Keep large deferred RUN scripts out of execve's per-argument limit."""
    def run(command, timeout=3600):
        if (command[:2] != [apptainer, 'exec'] or len(command) < 5
                or command[-3:-1] != ['bash', '-lc']
                or len(command[-1].encode()) < 64 * 1024):
            return run_unshared(command, timeout=timeout)
        with tempfile.TemporaryDirectory(prefix='image-build-script-') as temporary:
            script = Path(temporary) / 'build.sh'
            script.write_text(command[-1])
            target = '/run/ot-image-build.sh'
            staged = (command[:2] + ['--bind', f'{script}:{target}:ro']
                      + command[2:-1] + [f'source {target}'])
            return run_unshared(staged, timeout=timeout)
    return run


def directory_overlay_builds(run_unshared, apptainer):
    """Adapt the upstream two-step overlay builder without changing cache format.

    Opted-in build workers run the same RUN script in a directory overlay. Once
    it succeeds, mkfs populates the final overlay in one pass. Namespace-root
    preserves ownership, xattrs and overlay whiteouts during this conversion.
    """
    pending = {}

    def run(command, timeout=3600):
        if command[:3] == [apptainer, 'overlay', 'create']:
            path = Path(command[-1])
            size = int(command[command.index('--size') + 1])
            path.mkdir()
            pending[str(path)] = size
            return
        if command[:2] != [apptainer, 'exec'] or '--overlay' not in command:
            return run_unshared(command, timeout=timeout)
        path = Path(command[command.index('--overlay') + 1])
        size = pending.pop(str(path), None)
        if size is None:
            return run_unshared(command, timeout=timeout)
        tree = path.with_name(path.name + '.tree')
        try:
            print('[build] using native directory scratch for deferred RUN steps', flush=True)
            # Build against the image filesystem, not site-wide host /home or
            # working-directory mounts. Explicit DNS/certificate binds remain.
            # Containment's empty /tmp and /var/tmp would hide COPY inputs and
            # discard RUN outputs there. Use the image's overlaid directories.
            result = run_unshared(command[:2] + ['--userns', '--containall', '--no-home',
                '--no-mount', 'hostfs,bind-paths,cwd,tmp'] + command[2:], timeout=timeout)
            path.rename(tree)
            path.touch(mode=0o600, exist_ok=False)
            with path.open('wb') as stream:
                stream.truncate(size * 1024 * 1024)
            run_unshared(['mkfs.ext3', '-q', '-F', '-d', str(tree), str(path)], timeout=timeout)
            return result
        except BaseException:
            # Upstream expects a file or a nonexistent path when cleaning up.
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink(missing_ok=True)
            raise
        finally:
            if tree.exists():
                shutil.rmtree(tree)

    return run


def logged_build_commands(run):
    """Stream deferred build output to the build log without dropping the cause.

    Upstream keeps only the final 1500 stderr/300 stdout characters, often just
    dpkg's dependent-package summary. This runner is installed only for builds.
    """
    def execute(command, timeout=3600):
        return run(['unshare', '-r', *command], check=True, timeout=timeout)
    return execute
