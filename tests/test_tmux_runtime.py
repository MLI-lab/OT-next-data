import subprocess

import pytest

from harbor_patches.tmux_runtime import INSTALL, LEGACY_INSTALL, ensure_tmux, without_runtime_install


def test_image_tmux_is_used_without_installing():
    calls = []
    def run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, 'tmux 3.3a', '')
    ensure_tmux(['container', 'bash', '-c'], run)
    assert calls == [['container', 'bash', '-c', 'tmux -V']]


def test_missing_tmux_is_installed_at_task_start_then_rechecked():
    calls = []
    def run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[-1] == 'tmux -V':
            return subprocess.CompletedProcess(cmd, 0 if len(calls) > 2 else 127, '', 'tmux: command not found')
        return subprocess.CompletedProcess(cmd, 0, '', '')
    ensure_tmux(['container', 'bash', '-c'], run)
    assert [c[-1] for c in calls] == ['tmux -V', INSTALL, 'tmux -V']
    assert 'apt-get' in INSTALL and 'apk add' in INSTALL and 'dnf install' in INSTALL


@pytest.mark.parametrize('error', ['tmux: command not found',
    'error while loading shared libraries: libutempter.so.0: missing'])
def test_failed_installation_is_an_infrastructure_error(error):
    def run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, '', error if cmd[-1] == 'tmux -V' else 'E: Unable to locate package tmux')
    with pytest.raises(RuntimeError, match='installing it at task start failed.*Unable to locate package'):
        ensure_tmux(['container', 'bash', '-c'], run)


def test_pinned_harbor_startup_installer_is_removed_because_the_bridge_installs_first():
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
