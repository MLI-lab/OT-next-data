import subprocess

import pytest

from harbor_patches.tmux_runtime import INSTALL, LEGACY_INSTALL, LEGACY_PIP_INSTALL, ensure_tmux, without_runtime_install


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
        ensure_tmux(['container', 'bash', '-c'], run, environ={})


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


def test_pinned_harbor_startup_adds_no_python_packages_to_the_task_image():
    pytest.importorskip('harbor')
    from harbor.environments.apptainer import worker
    constants = worker.ApptainerInstance.start.__code__.co_consts
    pip = next(c for c in constants if isinstance(c, str) and 'pip install' in c)
    assert pip == LEGACY_PIP_INSTALL
    script = "echo 'precedence ::ffff:0:0/96 100'; " + LEGACY_INSTALL + pip + 'true'
    assert 'pip' not in without_runtime_install(script, environ={})
    # Kept only where Harbor intends it: a cluster that really routes containers through proxychains.
    proxied = without_runtime_install(script, environ={'BRIDGE_PROXY_HOST': 'login1', 'BRIDGE_PROXY_PORT': '1080'})
    assert pip in proxied and LEGACY_INSTALL not in proxied


def test_offline_failure_copies_only_the_configured_static_tmux(tmp_path):
    from harbor_patches.tmux_runtime import STATIC_COPY, STATIC_TARGET
    binary = tmp_path / 'tmux'
    binary.write_bytes(b'\x7fELF static')
    calls = []
    def run(cmd, **kwargs):
        calls.append((cmd[-1], kwargs.get('input')))
        if cmd[-1] == 'tmux -V':
            return subprocess.CompletedProcess(cmd, 127, '', 'tmux: command not found')
        if cmd[-1] == INSTALL:
            return subprocess.CompletedProcess(cmd, 100, '', 'E: Unable to locate package tmux')
        return subprocess.CompletedProcess(cmd, 0, 'tmux 3.4', b'' if cmd[-1] == STATIC_COPY else '')
    ensure_tmux(['container', 'bash', '-c'], run, environ={'OT_STATIC_TMUX': str(binary)})
    assert calls == [('tmux -V', None), (INSTALL, None), ('tmux -V', None),
                     (STATIC_COPY, b'\x7fELF static'), (STATIC_TARGET + ' -V', None)]


def test_without_static_tmux_the_offline_failure_stays_an_error(tmp_path):
    def run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, '', 'E: Unable to locate package tmux')
    with pytest.raises(RuntimeError, match='installing it at task start failed'):
        ensure_tmux(['container', 'bash', '-c'], run, environ={'OT_WORKSPACE': str(tmp_path)})
