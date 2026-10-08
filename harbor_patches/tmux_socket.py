"""Keep the container's terminal socket outside task temporary directories."""
from functools import wraps

SOCKET_ROOT = '/run/ot-harbor-tmux'
SOCKET_ENV = f'export TMUX_TMPDIR={SOCKET_ROOT}; '
FALLBACK_PATH = ('export PATH=/root/.local/bin:/testbed/.venv/bin:/usr/local/sbin:'
                 '/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin; ')


def prepare_socket_directory(uid):
    # Fakeroot can report uid 0 while tmux sees the host uid. Both directories
    # live in this instance's writable filesystem, never a shared host /run.
    return (f'mkdir -p -m 700 {SOCKET_ROOT} {SOCKET_ROOT}/tmux-0 '
            f'{SOCKET_ROOT}/tmux-{int(uid)}')


def install_worker(worker):
    instance = worker.ApptainerInstance
    if getattr(instance, '_ot_private_tmux_socket', False):
        return
    original = instance._proxy_command_prefix

    @wraps(original)
    def prefix(self):
        # Preserve Harbor's no-proxy PATH fallback, which exec otherwise applies
        # only when this method returns an empty string. Set after login profiles.
        return (original(self) or FALLBACK_PATH) + SOCKET_ENV

    instance._proxy_command_prefix = prefix
    instance._ot_private_tmux_socket = True
