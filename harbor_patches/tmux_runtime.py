"""Require image-provided tmux; never install or repair it at task startup."""

LEGACY_INSTALL = (
    "if ! command -v tmux >/dev/null 2>&1; then "
    "  apt-get update -qq && apt-get install -y -qq tmux 2>/dev/null || true; "
    "fi; "
)


def without_runtime_install(script):
    """Remove only the pinned Harbor startup installer, leaving other setup intact."""
    return script.replace(LEGACY_INSTALL, '')


def ensure_tmux(cmd, run):
    """A broken image is an infrastructure error, not a runtime repair request."""
    check = run(cmd + ['tmux -V'], capture_output=True, text=True, timeout=30)
    if check.returncode:
        error = (check.stderr or '') + (check.stdout or '')
        raise RuntimeError('Task image must provide working tmux. Install tmux and its '
                           'dependencies during image preparation, then rebuild the image. '
                           'Runtime installation and repair are disabled. ' + error[-2000:])
