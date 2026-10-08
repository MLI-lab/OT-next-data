"""Build deferred layers on native scratch, then freeze the ext3 cache artifact."""
from pathlib import Path
import os
import re
import shutil
import tempfile


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
            result = run_unshared(command[:2] + ['--userns', '--containall', '--no-home',
                '--no-mount', 'hostfs,bind-paths,cwd'] + command[2:], timeout=timeout)
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
