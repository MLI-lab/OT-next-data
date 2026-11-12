"""Give every task container a working tmux: use the image's copy, otherwise install one at start.

Harbor keeps the agent terminal in a tmux session. Upstream Harbor installs tmux in its
startup bootstrap when an image lacks it; datasets such as Scale-SWE reference upstream
Docker Hub images that ship without it. The bridge performs that installation itself,
before the persistent tmux owner starts, so the image stays byte-identical to upstream
and no per-task image rebuild is needed. Only tmux is installed: no Python package is
added and nothing is repaired, so the task runs in the published image.
"""

LEGACY_INSTALL = (
    "if ! command -v tmux >/dev/null 2>&1; then "
    "  apt-get update -qq && apt-get install -y -qq tmux 2>/dev/null || true; "
    "fi; "
)

# Harbor's proxychains workaround for another benchmark. Our prefix for the tmux socket makes Harbor
# treat every run as proxied, so without this removal each container would get these packages.
LEGACY_PIP_INSTALL = (
    "pip install chardet 2>/dev/null || "
    "python3 -m pip install chardet 2>/dev/null || true; "
    "if ! command -v uv >/dev/null 2>&1; then "
    "  pip install uv 2>/dev/null || "
    "  python3 -m pip install uv 2>/dev/null || true; "
    "fi; "
)

INSTALL = (
    'if ! command -v tmux >/dev/null 2>&1; then '
    'if command -v apt-get >/dev/null 2>&1; then '
    'apt-get update -qq && apt-get install -y -qq --no-install-recommends tmux; '
    'elif command -v apk >/dev/null 2>&1; then apk add --no-cache tmux; '
    'elif command -v dnf >/dev/null 2>&1; then dnf install -y -q tmux; '
    'elif command -v yum >/dev/null 2>&1; then yum install -y -q tmux; '
    'else echo "no supported package manager to install tmux" >&2; exit 1; fi; fi'
)


def proxychains_configured(environ=None):
    """Harbor's own condition for its proxy workaround: a proxychains host and port are set."""
    import os
    environ = os.environ if environ is None else environ
    return bool(environ.get('BRIDGE_PROXY_HOST') and environ.get('BRIDGE_PROXY_PORT'))


def without_runtime_install(script, environ=None):
    """Remove Harbor's own startup installers: the bridge already installed tmux, and nothing else is added.

    The pip workaround stays only where Harbor intends it, on a cluster that really uses proxychains.
    """
    script = script.replace(LEGACY_INSTALL, '')
    return script if proxychains_configured(environ) else script.replace(LEGACY_PIP_INSTALL, '')


STATIC_TARGET = '/usr/local/bin/tmux'
STATIC_COPY = (f'mkdir -p /usr/local/bin && cat > {STATIC_TARGET}.ot-partial && '
               f'chmod 755 {STATIC_TARGET}.ot-partial && mv -f {STATIC_TARGET}.ot-partial {STATIC_TARGET}')


def static_tmux(environ=None):
    """A statically linked tmux on the host for offline clusters, or None.

    OT_STATIC_TMUX names it explicitly; otherwise $OT_WORKSPACE/tools/tmux-static/tmux is used if present.
    """
    import os
    from pathlib import Path
    environ = os.environ if environ is None else environ
    if environ.get('OT_STATIC_TMUX'):
        path = Path(environ['OT_STATIC_TMUX'])
        if not path.is_file():
            raise RuntimeError(f'OT_STATIC_TMUX={path} is not a file')
        return path
    workspace = environ.get('OT_WORKSPACE')
    path = Path(workspace) / 'tools' / 'tmux-static' / 'tmux' if workspace else None
    return path if path is not None and path.is_file() else None


def ensure_tmux(cmd, run, environ=None):
    """Check for tmux; install it in the running container when the image does not provide it.

    Offline, the package manager cannot reach its mirrors. If a static tmux is configured,
    only that one binary is copied to /usr/local/bin/tmux (where Harbor links tmux anyway):
    the container gains tmux and nothing else, as with the online install.
    """
    check = run(cmd + ['tmux -V'], capture_output=True, text=True, timeout=30)
    if not check.returncode:
        return
    install = run(cmd + [INSTALL], capture_output=True, text=True, timeout=600)
    check = run(cmd + ['tmux -V'], capture_output=True, text=True, timeout=30)
    static = None if not check.returncode else static_tmux(environ)
    if static is not None:
        copy = run(cmd + [STATIC_COPY], input=static.read_bytes(), capture_output=True, timeout=120)
        check = run(cmd + [STATIC_TARGET + ' -V'], capture_output=True, text=True, timeout=30)
        if not check.returncode:
            print(f'tmux: image lacks it and the package manager failed; copied static {static} '
                  f'to {STATIC_TARGET}: {(check.stdout or "").strip()}', flush=True)
            return
        install.stderr = (install.stderr or '') + ' static tmux copy failed: ' + (
            copy.stderr.decode(errors='replace') if isinstance(copy.stderr, bytes) else (copy.stderr or ''))
    if check.returncode:
        error = ((install.stderr or '') + (install.stdout or '') + (check.stderr or '') + (check.stdout or ''))
        raise RuntimeError('Task image lacks tmux and installing it at task start failed. '
                           'Provide tmux in the image or make its package manager reachable. ' + error[-2000:])
