import subprocess

import pytest

from harbor_patches.tmux_runtime import LEGACY_INSTALL, ensure_tmux, without_runtime_install


@pytest.mark.parametrize('error', ['', 'tmux: command not found',
    'error while loading shared libraries: libutempter.so.0: missing'])
def test_tmux_guard_never_installs_or_repairs(error):
    calls = []
    def run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, bool(error), '', error)
    if error:
        with pytest.raises(RuntimeError, match='Runtime installation and repair are disabled'):
            ensure_tmux(['container', 'bash', '-c'], run)
    else:
        ensure_tmux(['container', 'bash', '-c'], run)
    assert calls == [['container', 'bash', '-c', 'tmux -V']]


def test_pinned_harbor_startup_installer_is_removed():
    pytest.importorskip('harbor')
    from harbor.environments.apptainer import worker
    constants = worker.ApptainerInstance.start.__code__.co_consts
    script = next(c for c in constants if isinstance(c, str) and LEGACY_INSTALL in c)
    changed = without_runtime_install(script)
    assert 'apt-get install' not in changed
    assert 'ln -sf /usr/bin/tmux' in changed


def test_unrelated_script_is_unchanged():
    script = 'apt-get install custom-package; tmux -V'
    assert without_runtime_install(script) == script
