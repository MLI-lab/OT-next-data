import os
from pathlib import Path
import shlex
import subprocess
from types import SimpleNamespace

import pytest

from harbor_patches import exec_environment as patch


def worker():
    class Instance:
        def _proxy_command_prefix(self):
            return 'export PATH=' + patch.DEFAULT_PATH + '; export PROXY_PRESERVED=yes; '
        def exec(self, payload):
            cmd = ['apptainer', 'exec', '--env', 'PATH=' + patch.DEFAULT_PATH]
            for key, value in payload.get('env_vars', {}).items():
                cmd.extend(['--env', key + '=' + shlex.quote(value)])
            return patch.command_arguments(cmd + ['instance://test', '/bin/bash', '-lc',
                self._proxy_command_prefix() + payload['command']])
    value = SimpleNamespace(ApptainerInstance=Instance)
    patch.install_worker(value)
    return value


def test_image_python_wins_over_system_and_runtime_tools(tmp_path, monkeypatch):
    image = tmp_path / 'venv/bin'
    image.mkdir(parents=True)
    for name in ['python', 'pip']:
        exe = image / name
        exe.write_text('#!/bin/sh\necho image-' + name + '\n')
        exe.chmod(0o755)
    instance = worker().ApptainerInstance()
    cmd = instance.exec({'command': 'python; pip; printf "%s" "$PROXY_PRESERVED"'})
    assert 'PATH=' + patch.DEFAULT_PATH not in cmd[:-1]
    result = subprocess.run(cmd[-3:], env={**os.environ, 'PATH': str(image) + ':/usr/bin:/bin'},
                            capture_output=True, text=True, check=True)
    assert result.stdout == 'image-python\nimage-pip\nyes'


def test_explicit_override_and_requested_login_shell_are_preserved():
    instance = worker().ApptainerInstance()
    cmd = instance.exec({'env_vars': {'PATH': '/chosen/bin'}, 'command': 'bash -lc "echo requested"'})
    assert cmd[2:4] == ['--env', 'PATH=/chosen/bin']
    assert cmd[-2] == '-c' and cmd[-1].endswith('bash -lc "echo requested"')
    assert 'case ' not in cmd[-1]  # Explicit overrides are not augmented.


def test_deferred_env_reaches_exec_and_payload_wins():
    instance = worker().ApptainerInstance()
    instance._deferred_spec = {'env': {'PATH': '/opt/venv/bin:/usr/bin', 'VALUE': 'image'}}
    cmd = instance.exec({'env_vars': {'VALUE': 'payload'}, 'command': 'true'})
    assert 'PATH=/opt/venv/bin:/usr/bin' in cmd and 'VALUE=payload' in cmd
    assert 'VALUE=image' not in cmd


def test_start_keeps_image_path_and_does_not_change_unrelated_commands():
    command = ['apptainer', 'instance', 'start', '--env', 'PATH=' + patch.DEFAULT_PATH,
               '--env', 'PATH=/opt/venv/bin:/usr/bin', '/image.sif', 'test']
    assert patch.command_arguments(command) == command[:3] + command[5:]
    command = ['apptainer', 'exec', '--env', 'PATH=' + patch.DEFAULT_PATH,
               'instance://test', '/bin/bash', '-lc', 'user command']
    assert patch.command_arguments(command) == command


def test_path_fallback_only_when_unset():
    instance = worker().ApptainerInstance()
    cmd = instance.exec({'command': 'printf "%s" "$PATH"'})
    result = subprocess.run(cmd[-3:], env={}, capture_output=True, text=True, check=True)
    assert '/usr/bin' in result.stdout


@pytest.mark.parametrize('selection', ['export', 'activate', 'absolute', 'payload'])
def test_dataset_interpreter_choice_overrides_image(tmp_path, selection):
    # Model the explicit selections used by Conda converters, FACET,
    # BugsInPy and the Multi-SWE grader, with a conflicting image interpreter.
    for name in ['image', 'chosen']:
        directory = tmp_path / name / 'bin'
        directory.mkdir(parents=True)
        executable = directory / 'python'
        executable.write_text('#!/bin/sh\necho ' + name + '\n')
        executable.chmod(0o755)
    chosen = str(tmp_path / 'chosen/bin')
    activation = tmp_path / 'chosen/bin/activate'
    activation.write_text('export PATH=' + shlex.quote(chosen) + ':$PATH\n')
    commands = {
        'export': 'export PATH=' + shlex.quote(chosen) + ':$PATH; python',
        'activate': '. ' + shlex.quote(str(activation)) + '; python',
        'absolute': shlex.quote(chosen + '/python'),
        'payload': 'python',
    }
    payload = {'command': commands[selection]}
    if selection == 'payload':
        payload['env_vars'] = {'PATH': chosen}
    cmd = worker().ApptainerInstance().exec(payload)
    environment = {'PATH': str(tmp_path / 'image/bin') + ':/usr/bin:/bin'}
    boundary = cmd.index('instance://test')
    for index in range(2, boundary - 1):
        if cmd[index] == '--env':
            key, value = cmd[index + 1].split('=', 1)
            environment[key] = shlex.split(value)[0] if value else ''
    result = subprocess.run(cmd[boundary + 1:], env=environment,
                            capture_output=True, text=True, check=True)
    assert result.stdout == 'chosen\n'


def test_installed_harbor_exec_preserves_image_python(tmp_path, monkeypatch):
    worker_module = pytest.importorskip('harbor.environments.apptainer.worker')
    class Instance(worker_module.ApptainerInstance):
        pass
    patch.install_worker(SimpleNamespace(ApptainerInstance=Instance))
    instance = Instance.__new__(Instance)
    instance.instance_name = 'test'
    instance._dockerfile_workdir = '/tmp'
    instance._deferred_spec = None
    instance.max_exec_output_chars = 10000
    monkeypatch.delenv('BRIDGE_AGENT_TOOLS', raising=False)
    monkeypatch.delenv('BRIDGE_PROXY_HOST', raising=False)
    monkeypatch.setattr(worker_module, 'APPTAINER', 'apptainer')
    image = tmp_path / 'venv/bin'
    image.mkdir(parents=True)
    for name in ['python', 'pip']:
        executable = image / name
        executable.write_text('#!/bin/sh\necho actual-image-' + name + '\n')
        executable.chmod(0o755)
    real_run = subprocess.run
    def simulated_apptainer(cmd, **kwargs):
        cmd = patch.command_arguments(cmd)
        assert cmd[-2] == '-c'
        environment = {'PATH': str(image) + ':/usr/bin:/bin'}
        boundary = cmd.index('instance://test')
        for index in range(2, boundary - 1):
            if cmd[index] == '--env':
                key, value = cmd[index + 1].split('=', 1)
                environment[key] = shlex.split(value)[0] if value else ''
        return real_run(cmd[boundary + 1:], env=environment, **kwargs)
    monkeypatch.setattr(worker_module, 'subprocess', SimpleNamespace(run=simulated_apptainer,
                        TimeoutExpired=subprocess.TimeoutExpired))
    result = instance.exec({'command': 'python; pip'})
    assert result['return_code'] == 0
    assert result['stdout'] == 'actual-image-python\nactual-image-pip\n'
