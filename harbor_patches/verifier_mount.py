"""Private mount namespaces for prepared verifier runtimes, never chmod isolation."""
from pathlib import Path
import re
import shlex

MODES = ('readonly', 'writable')


def mode(reference):
    value = reference.get('mount_mode', 'writable')
    if value not in MODES:
        raise ValueError('Verifier mount_mode must be readonly or writable')
    return value


def command(bundle, shell_command):
    if bundle is None:
        return shell_command
    root = bundle['directory']
    # Overlayfs options use commas/colons as separators. Stage generates this
    # path; callers cannot inject an arbitrary mount source or option.
    if not re.fullmatch(r'/tests/\.ot-verifier-runtime-[A-Za-z0-9]+', root):
        raise ValueError('Invalid staged verifier runtime directory')
    selected = mode(bundle)
    namespace = bundle.get('namespace_mode', 'user-mount')
    if namespace not in ('mount', 'user-mount'):
        raise ValueError('Invalid verifier namespace mode')
    user_flags = ['--user', '--map-current-user', '--keep-caps'] if namespace == 'user-mount' else []
    return shlex.join([
        'unshare', *user_flags, '--mount',
        '--fork', '--kill-child', '--propagation', 'private',
        '/bin/sh', root + '/mount.sh', root, selected,
        '/bin/bash', '--noprofile', '--norc', '-c', shell_command,
    ])


async def prepare(environment, bundle, *, user=None, script_staged=False):
    root = bundle['directory']
    if not script_staged:
        await environment.upload_file(source_path=Path(__file__).with_name('verifier_mount.sh'),
                                      target_path=root + '/mount.sh')
    # One container call retains both capability detection and the actual mount
    # probe under the configured verifier user. A failed probe never falls back
    # to unprotected execution.
    probe = 'export OT_VERIFIER_PYTHON=' + shlex.quote(bundle['python']) + '; test -x "$OT_VERIFIER_PYTHON"'
    branches = []
    for selected in ('mount', 'user-mount'):
        mounted = command({**bundle, 'namespace_mode': selected}, probe)
        branches.append(mounted + ' && printf "OT_VERIFIER_NAMESPACE=' + selected + '\\n"')
    script = ('if unshare --mount --fork --propagation private true; then '
              + branches[0] + '; else ' + branches[1] + '; fi')
    result = await environment.exec(script, timeout_sec=90, user=user)
    selected = (result.stdout or '').strip().splitlines()
    namespace = selected[-1].removeprefix('OT_VERIFIER_NAMESPACE=') if selected else ''
    if result.return_code or namespace not in ('mount', 'user-mount'):
        raise RuntimeError('Verifier runtime mount is unavailable; refusing writable fallback: '
                           + (result.stderr or result.stdout or '')[-1500:])
    bundle['namespace_mode'] = namespace


async def release(environment, bundle):
    if bundle is None:
        return
    # Validate the generated path before removing only this private staging tree.
    command(bundle, 'true')
    root = shlex.quote(bundle['directory'])
    # Overlayfs creates mode-000 work directories. Walk without following links,
    # restoring directory traversal before descending; rm must not touch targets
    # of symlinks a setup script may have put in its private upper layer.
    cleanup = (f'if [ -d {root} ] && [ ! -L {root} ]; then '
               f'find -P {root} -type d ! -perm -0700 -exec chmod u+rwx {{}} \\; || exit 1; fi; '
               f'rm -rf -- {root}')
    result = await environment.exec(cleanup,
                                    user='root', timeout_sec=30)
    if result.return_code:
        raise RuntimeError('Could not discard private verifier runtime: '
                           + (result.stderr or result.stdout or '')[-1000:])
