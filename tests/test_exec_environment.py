import json
import os
import shlex
import subprocess
import sys
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

def test_worker_patch_handles_binary_output_through_real_exec(monkeypatch):
    import subprocess
    from types import SimpleNamespace

    from harbor.environments.apptainer import worker


    original_run = subprocess.run
    emit = 'import os; os.write(1, b"%PDF-1.4\\n%\\xe2\\xe3\\xcf\\xd3\\n"); os.write(2, b"bad \\x81"); raise SystemExit(7)'

    def run(command, *args, **kwargs):
        if any(str(arg).startswith("instance://") for arg in command):
            command = [sys.executable, "-c", emit]
        return original_run(command, *args, **kwargs)

    monkeypatch.setattr(worker, "subprocess", SimpleNamespace(**{**vars(subprocess), "run": run}))
    monkeypatch.setattr(worker, "_ot_exec_utf8_replace", False, raising=False)
    instance = object.__new__(worker.ApptainerInstance)
    instance.instance_name = "test-instance"
    instance.max_exec_output_chars = 1000
    instance._proxy_command_prefix = lambda: ""
    with pytest.raises(UnicodeDecodeError):
        instance.exec({"command": "cat document.pdf"})
    patch.install_output_decoding(worker)
    patched_run = worker.subprocess.run
    patch.install_output_decoding(worker)
    assert worker.subprocess.run is patched_run
    result = instance.exec({"command": "cat document.pdf"})
    assert result["stdout"] == "%PDF-1.4\n%\ufffd\ufffd\ufffd\ufffd\n"
    assert result["stderr"] == "bad \ufffd"
    assert result["return_code"] == 7
    assert subprocess.run is original_run
    # Binary transfers and explicit decoding choices remain intact.
    command = ["apptainer", "exec", "instance://test-instance", "anything"]
    binary = worker.subprocess.run(command, capture_output=True)
    assert binary.stdout == b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"
    with pytest.raises(UnicodeDecodeError):
        worker.subprocess.run(command, capture_output=True, text=True, errors="strict")
    # Non-container subprocess calls retain their original strict behavior.
    with pytest.raises(UnicodeDecodeError):
        worker.subprocess.run([sys.executable, "-c", emit], capture_output=True, text=True)


def test_worker_patch_preserves_timeouts(monkeypatch):
    import subprocess
    from types import SimpleNamespace

    from harbor.environments.apptainer import worker


    def run(command, *args, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(worker, "subprocess", SimpleNamespace(**{**vars(subprocess), "run": run}))
    monkeypatch.setattr(worker, "_ot_exec_utf8_replace", False, raising=False)
    patch.install_output_decoding(worker)
    instance = object.__new__(worker.ApptainerInstance)
    instance.instance_name = "test-instance"
    instance.max_exec_output_chars = 1000
    instance._proxy_command_prefix = lambda: ""
    result = instance.exec({"command": "sleep 100", "timeout_sec": 1})
    assert result["return_code"] == -1
    assert "timed out" in result["stderr"]


@pytest.mark.parametrize('command', [
    '\x00', 'echo before\x00echo after',
    # JSON tool arguments turn this escape into an actual NUL on decoding.
    json.loads(r'{"command": "python3 -c \"data = \u0000\""}')['command'],
])
def test_nul_command_returns_tool_error_without_execution(command):
    calls = []

    class Instance:
        def _proxy_command_prefix(self):
            raise AssertionError('Invalid command must not reach shell setup')

        def exec(self, payload):
            calls.append(payload)
            return {'stdout': 'recovered', 'stderr': '', 'return_code': 0}

    module = SimpleNamespace(ApptainerInstance=Instance)
    patch.install_worker(module)
    installed_exec = Instance.exec
    patch.install_worker(module)
    assert Instance.exec is installed_exec
    instance = Instance()
    payload = {'command': command, 'cwd': '/app', 'timeout_sec': 30,
               'env_vars': {'VALUE': 'unchanged'}}
    original_payload = {**payload, 'env_vars': dict(payload['env_vars'])}
    result = instance.exec(payload)
    assert result['stdout'] == ''
    assert result['return_code'] == 2
    assert 'NUL (0x00)' in result['stderr']
    assert 'Command not executed' in result['stderr']
    assert result['stdout_truncated'] is False
    assert result['stderr_truncated'] is False
    assert calls == []
    assert payload == original_payload
    # Rejecting one action leaves the same instance usable for the next action.
    assert instance.exec({'command': 'echo recovered'})['stdout'] == 'recovered'
    assert len(calls) == 1


def test_nul_guard_preserves_other_exec_failures():
    failure = ValueError('unrelated execution failure')

    class Instance:
        def _proxy_command_prefix(self):
            return ''

        def exec(self, payload):
            raise failure

    patch.install_worker(SimpleNamespace(ApptainerInstance=Instance))
    with pytest.raises(ValueError) as caught:
        Instance().exec({'command': 'true'})
    assert caught.value is failure
    assert patch._execution.get() is None


def test_nul_guard_through_real_harbor_exec_preserves_binary_and_options(monkeypatch):
    from harbor.environments.apptainer import worker as worker_module

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
    calls = []
    real_run = subprocess.run

    def simulated_apptainer(command, **kwargs):
        calls.append((command, kwargs))
        command = patch.command_arguments(command)
        boundary = command.index('instance://test')
        return real_run(command[boundary + 1:], **kwargs)

    monkeypatch.setattr(worker_module, 'subprocess', SimpleNamespace(
        run=simulated_apptainer, TimeoutExpired=subprocess.TimeoutExpired))
    assert instance.exec({'command': 'echo bad\x00'})['return_code'] == 2
    assert calls == []
    assert patch._execution.get() is None
    # Literal backslash escapes are valid shell text and may produce NUL output.
    command = r"printf '\000'; printf 'error' >&2; exit 7"
    payload = {'command': command, 'cwd': '/tmp', 'env_vars': {'VALUE': 'kept'},
               'timeout_sec': 13}
    result = instance.exec(payload)
    assert result['stdout'] == '\x00'
    assert result['stderr'] == 'error'
    assert result['return_code'] == 7
    assert len(calls) == 1
    argv, options = calls[0]
    assert command in argv[-1]
    assert 'VALUE=kept' in argv
    assert argv[argv.index('--pwd') + 1] == '/tmp'
    assert options['timeout'] == 13
    assert payload['command'] == command
    assert patch._execution.get() is None
