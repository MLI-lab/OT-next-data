import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

import pytest

from data.utils.verifier_runtime import prepare_uvx, runtime_reference, runtime_spec, tool_command
from harbor_patches.verifier_bundle import FORMAT, spec_id
from hpc.offline_prep.verifier_bundles import relocate_scripts


def test_uvx_package_and_command_can_differ_and_arguments_remain_literal():
    spec, command = prepare_uvx(
        ['uvx', '--python=3.10', '--from', 'httpie==3.2.4', '--with', 'requests==2.32.3',
         'http', 'https://example.org', 'x=$(touch /tmp/not-executed)'], python='3.10.19')
    assert spec['tools'] == ['http']
    assert spec['requirements'] == ['httpie==3.2.4', 'requests==2.32.3']
    assert "'x=$(touch /tmp/not-executed)'" in command


def test_uv_tool_alias_and_reordered_requirements_share_a_bundle():
    first, command = prepare_uvx(['uvx', '--with', 'pluggy==1.6.0', 'pytest@8.4.1', '-q'], python='3.10.19')
    second, _ = prepare_uvx(['uv', 'tool', 'run', '--from=pytest==8.4.1',
                            '-w', 'pluggy==1.6.0', 'pytest'], python='3.10.19')
    assert spec_id(first) == spec_id(second)
    assert command == tool_command('pytest', ['-q'])


@pytest.mark.parametrize('argv', [
    ['uvx', 'pytest'], ['uvx', 'pytest@latest'], ['uvx', '--from', 'pytest>=8', 'pytest'],
    ['uvx', '--with', 'pluggy', 'pytest@8.4.1'], ['uvx', '--python', '3.11', 'pytest@8.4.1'],
    ['uvx', '--index-url', 'https://example.org', 'pytest@8.4.1'], ['uv', 'run', 'test.py'],
    ['uvx', '--from', 'pytest==8.4.1', '../pytest'],
])
def test_ambiguous_or_unpinned_uvx_commands_fail_closed(argv):
    with pytest.raises(ValueError):
        prepare_uvx(argv, python='3.10.19')


def test_manifest_binds_only_matching_ready_artifacts():
    spec = runtime_spec(python='3.10.19', requirements=['pytest==8.4.1'], tools=['pytest'])
    key = spec_id(spec)
    record = dict(format=FORMAT, id=key, spec=spec, status='ready', archive_sha256='a' * 64)
    updates = runtime_reference(spec, {key: record})
    assert json.loads(updates['tests/ot-verifier-runtime.json'])['archive_sha256'] == 'a' * 64
    record['spec'] = {**spec, 'python': '3.11.14'}
    with pytest.raises(ValueError, match='matching ready'):
        runtime_reference(spec, {key: record})


@pytest.mark.parametrize('header', [
    '#!/nonexistent/build/python\n',
    '#!/bin/sh\n\'\'\'exec\' /nonexistent/build/python "$0" "$@"\n\' \'\'\'\n',
])
@pytest.mark.parametrize('tool_name', ['tool', '_test_python_toolbox'])
def test_relocated_console_tool_ignores_agent_pythonpath_and_preserves_exit_code(tmp_path, header, tool_name):
    original = tmp_path / 'build'
    bin_dir = original / 'bin'
    bin_dir.mkdir(parents=True)
    (bin_dir / 'python3').symlink_to(sys.executable)
    script = bin_dir / tool_name
    script.write_text(header + 'import json, sys\n'
                      'print(json.dumps([sys.flags.isolated, sys.argv[1:]]))\nsys.exit(7)\n')
    script.chmod(0o755)
    native = bin_dir / 'native'
    native.write_bytes(b'\x7fELF-native-tool')
    relocate_scripts(original)
    moved = tmp_path / 'runtime with spaces'
    original.rename(moved)
    poison = tmp_path / 'agent'
    poison.mkdir()
    (poison / 'json.py').write_text('raise RuntimeError("agent package imported")')
    env = {**os.environ, 'PYTHONPATH': str(poison), 'OT_VERIFIER_BIN': str(moved / 'bin')}
    result = subprocess.run(['bash', '-c', tool_command(tool_name, ['hello world', '$(false)'])],
                            cwd=poison, env=env, capture_output=True, text=True)
    assert result.returncode == 7, result.stderr
    assert json.loads(result.stdout) == [1, ['hello world', '$(false)']]
    assert (moved / 'bin/native').read_bytes() == b'\x7fELF-native-tool'


def test_setup_and_grading_receive_prepared_tool_paths(tmp_path, monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from harbor_patches import verifier_bundle, verifier_setup

    root = '/tests/.ot-verifier-runtime-123456789abc'
    bundle = {'python': root + '/active/bin/python3',
              'bin': root + '/active/bin', 'directory': root, 'mount_mode': 'readonly'}
    async def stage(*args):
        return bundle
    async def offline(*args):
        return None
    monkeypatch.setattr(verifier_bundle, 'stage', stage)
    monkeypatch.setattr(verifier_bundle, 'stage_offline', offline)
    calls = []
    class Environment:
        async def exec(self, command, **kwargs):
            calls.append(command)
            return SimpleNamespace(return_code=0, stdout='', stderr='')
        async def upload_file(self, **kwargs):
            pass
        async def upload_dir(self, **kwargs):
            pass
    verifier = SimpleNamespace(
        _resolve_tests=lambda: (None, tmp_path, None),
        trial_paths=SimpleNamespace(verifier_dir=tmp_path / 'evidence'))
    wrapped = verifier_setup.SetupEnvironment(Environment(), 'grade', verifier)
    asyncio.run(wrapped.exec('grade'))
    wrapped_calls = [shlex.split(c)[-1] for c in calls if c.startswith('unshare ')]
    setup = next(c for c in wrapped_calls if c.endswith(verifier_setup.SETUP_COMMAND))
    grade = next(c for c in wrapped_calls if c.endswith('; grade'))
    for command in (setup, grade):
        assert 'export OT_VERIFIER_PYTHON=' + root + '/active/bin/python3;' in command
        assert 'export OT_VERIFIER_BIN=' + root + '/active/bin;' in command


@pytest.mark.parametrize('name',['../_tool','_tool/path','-option','_tool;false'])
def test_underscore_support_does_not_allow_paths_or_shell_syntax(name):
    with pytest.raises(ValueError):
        runtime_spec(python='3.8.20',requirements=[],tools=[name])


def test_safe_leading_underscore_tool_is_a_declared_basename():
    assert runtime_spec(python='3.8.20',requirements=[],tools=['_test_python_toolbox'])['tools']==['_test_python_toolbox']
