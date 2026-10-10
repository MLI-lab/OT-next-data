"""Give every task container a working tmux: use the image's copy, otherwise install one at start.

Harbor keeps the agent terminal in a tmux session. Upstream Harbor installs tmux in its
startup bootstrap when an image lacks it; datasets such as Scale-SWE reference upstream
Docker Hub images that ship without it. The bridge performs that installation itself,
before the persistent tmux owner starts, so the image stays byte-identical to upstream
and no per-task image rebuild is needed. Only tmux is installed, nothing is repaired.
"""

LEGACY_INSTALL = (
    "if ! command -v tmux >/dev/null 2>&1; then "
    "  apt-get update -qq && apt-get install -y -qq tmux 2>/dev/null || true; "
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


def without_runtime_install(script):
    """Remove Harbor's own startup installer: the bridge already installed tmux before the bootstrap."""
    return script.replace(LEGACY_INSTALL, '')


def ensure_tmux(cmd, run):
    """Check for tmux; install it in the running container when the image does not provide it."""
    check = run(cmd + ['tmux -V'], capture_output=True, text=True, timeout=30)
    if not check.returncode:
        return
    install = run(cmd + [INSTALL], capture_output=True, text=True, timeout=600)
    check = run(cmd + ['tmux -V'], capture_output=True, text=True, timeout=30)
    if check.returncode:
        error = ((install.stderr or '') + (install.stdout or '') + (check.stderr or '') + (check.stdout or ''))
        raise RuntimeError('Task image lacks tmux and installing it at task start failed. '
                           'Provide tmux in the image or make its package manager reachable. ' + error[-2000:])
