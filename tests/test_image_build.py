from pathlib import Path

import pytest

from harbor_patches.image_build import directory_overlay_builds, file_backed_build_scripts


@pytest.mark.parametrize('fail', [False, True])
def test_definition_build_prepares_ca_mountpoints_without_baking_host_ca(tmp_path, monkeypatch, fail):
    from harbor_patches.image_build import certificate_build_mountpoints
    definition = tmp_path / 'original.def'
    original = 'Bootstrap: docker\nFrom: ubuntu\n%files\n    input /input\n%post\n    true\n'
    definition.write_text(original)
    monkeypatch.setenv('APPTAINER_BINDPATH', '/host/ca:/run/ot-certificates/ssl_cert_file.pem:ro,/data:/data:ro')
    temporary = []
    def execute(command, **kwargs):
        assert kwargs == {'timeout': 42}
        generated = Path(command[-1]);temporary.append(generated)
        content = generated.read_text()
        assert content.startswith(original)
        source, target = content.split('%files\n')[-1].strip().split()
        assert target == '/run/ot-certificates/ssl_cert_file.pem'
        assert Path(source).read_bytes() == b''
        assert '/host/ca' not in content
        if fail:
            raise RuntimeError('build error')
        return 'complete'
    run = certificate_build_mountpoints(execute)
    if fail:
        with pytest.raises(RuntimeError, match='build error'):
            run(['apptainer', 'build', 'output.sif', str(definition)], timeout=42)
    else:
        assert run(['apptainer', 'build', 'output.sif', str(definition)], timeout=42) == 'complete'
    assert definition.read_text() == original
    assert all(not path.exists() for path in temporary)


def test_ca_mountpoint_adapter_leaves_runtime_exec_unchanged(monkeypatch):
    from harbor_patches.image_build import certificate_build_mountpoints
    monkeypatch.setenv('APPTAINER_BINDPATH', '/ca:/run/ot-certificates/ssl_cert_file.pem:ro')
    command = ['apptainer', 'exec', 'image.sif', 'true']
    calls = []
    certificate_build_mountpoints(lambda cmd: calls.append(cmd))(command)
    assert calls == [command]


@pytest.mark.parametrize('fail', [False, True])
def test_large_build_script_is_bound_and_cleaned_up(tmp_path, fail):
    import subprocess
    script = 'set -e\n#' + 'x' * 200000 + '\nprintf done'
    staged_paths = []

    def execute(command, timeout):
        assert timeout == 123
        assert max(len(arg.encode()) for arg in command) < 65536
        binding = command[command.index('--bind') + 1]
        host, target, mode = binding.split(':')
        staged_paths.append(Path(host))
        assert mode == 'ro'
        assert Path(host).read_text() == script
        result = subprocess.run(['bash', '-lc', command[-1].replace(target, host)],
                                check=True, capture_output=True, text=True)
        assert result.stdout == 'done'
        if fail:
            raise RuntimeError('build failed')
        return result.stdout

    run = file_backed_build_scripts(execute, 'apptainer')
    command = ['apptainer', 'exec', '--overlay', 'image.overlay', 'base.sif', 'bash', '-lc', script]
    if fail:
        with pytest.raises(RuntimeError, match='build failed'):
            run(command, timeout=123)
    else:
        assert run(command, timeout=123) == 'done'
    assert all(not path.exists() for path in staged_paths)
    assert command[-1] == script


def test_small_and_unrelated_commands_are_unchanged():
    calls = []
    run = file_backed_build_scripts(lambda command, timeout: calls.append((command, timeout)), 'apptainer')
    for command in [['apptainer', 'exec', 'base.sif', 'bash', '-lc', 'true'], ['mkfs.ext3', 'overlay']]:
        run(command, timeout=12)
        assert calls[-1] == (command, 12)


@pytest.mark.parametrize('failure', ['run', 'freeze', None])
def test_directory_build_only_leaves_artifact_after_complete_success(tmp_path, failure):
    overlay = tmp_path / 'image.overlay.tmp'
    calls = []

    def execute(command, timeout):
        calls.append(command)
        if command[0] == 'apptainer':
            assert '--userns' in command
            assert '--containall' in command and '--no-home' in command
            assert command[command.index('--no-mount') + 1] == 'hostfs,bind-paths,cwd,tmp'
            (overlay / 'upper').mkdir()
            (overlay / 'upper/package').write_text('installed')
            if failure == 'run':
                raise RuntimeError('installation failed')
        else:
            tree = Path(command[command.index('-d') + 1])
            assert (tree / 'upper/package').read_text() == 'installed'
            if failure == 'freeze':
                raise RuntimeError('image full')
            overlay.write_bytes(b'complete image')

    run = directory_overlay_builds(execute, 'apptainer')
    run(['apptainer', 'overlay', 'create', '--sparse', '--size', '64', str(overlay)])
    command = ['apptainer', 'exec', '--overlay', str(overlay), '--cleanenv', 'base.sif', 'sh', '-c', 'install']
    if failure:
        with pytest.raises(RuntimeError):
            run(command)
        assert not overlay.exists()
    else:
        run(command)
        assert overlay.read_bytes() == b'complete image'
    assert not overlay.with_name(overlay.name + '.tree').exists()
    assert len(calls) == (1 if failure == 'run' else 2)


def test_deferred_build_streams_full_failure_output_and_preserves_deadline():
    import subprocess
    from harbor_patches.image_build import logged_build_commands
    calls = []
    def execute(command, **kwargs):
        calls.append((command, kwargs))
        raise subprocess.CalledProcessError(100, command)
    with pytest.raises(subprocess.CalledProcessError):
        logged_build_commands(execute)(['apptainer', 'exec', 'base.sif', 'install'], timeout=91)
    assert calls == [(['unshare', '-r', 'apptainer', 'exec', 'base.sif', 'install'],
                      {'check': True, 'timeout': 91})]


def test_deferred_build_preserves_image_tmp_inputs_and_outputs(tmp_path):
    """Exercise real mount semantics and reopen the frozen overlay, without downloads."""
    import shutil
    import subprocess
    import sys
    import shlex

    for tool in ('apptainer', 'unshare', 'ldd', 'mkfs.ext3'):
        if not shutil.which(tool):
            pytest.skip(f'{tool} is required for the mount integration test')
    probe = subprocess.run(['unshare', '-r', 'true'], capture_output=True)
    if probe.returncode:
        pytest.skip('user namespaces are unavailable')

    root = tmp_path / 'root'
    for directory in ('bin', 'usr/bin', 'etc', 'dev', 'proc', 'sys', 'tmp',
                      'var/tmp', 'opt', 'home', 'root', 'run'):
        (root / directory).mkdir(parents=True, exist_ok=True)
    for executable in ('/bin/sh', '/bin/cat'):
        shutil.copy2(executable, root / executable.lstrip('/'))
        for word in subprocess.check_output(['ldd', executable], text=True).split():
            if word.startswith('/'):
                target = root / word.lstrip('/')
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(word, target)
    # COPY inputs in both temporary directories must behave like ordinary inputs.
    for directory in ('tmp', 'var/tmp', 'opt'):
        (root / directory / 'input').write_text(directory + '\n')
    host_only = tmp_path / 'host-only'
    host_only.write_text('must remain outside the build')
    overlay = tmp_path / 'built.overlay'

    def execute(command, timeout):
        result = subprocess.run(['unshare', '-r', *command],
                                capture_output=True, text=True, timeout=timeout)
        assert result.returncode == 0, result.stdout + result.stderr
        return result

    script = ('for d in /tmp /var/tmp /opt; do cat "$d/input" > "$d/output"; done; '
              f'test ! -e {shlex.quote(str(host_only))}')
    # The production builder also runs under namespace-root, which can remove
    # the kernel overlay's mode-000 work directory during cleanup.
    builder = '''
import subprocess, sys
from harbor_patches.image_build import directory_overlay_builds
root, overlay, script = sys.argv[1:]
def execute(command, timeout):
    return subprocess.run(command, check=True, timeout=timeout)
run = directory_overlay_builds(execute, 'apptainer')
run(['apptainer', 'overlay', 'create', '--size', '16', overlay])
run(['apptainer', 'exec', '--overlay', overlay, '--cleanenv', '--pwd', '/',
     root, '/bin/sh', '-ec', script], timeout=30)
'''
    execute([sys.executable, '-c', builder, str(root), str(overlay), script], timeout=40)
    result = execute(['apptainer', 'exec', '--userns', '--containall', '--no-home',
                      '--no-mount', 'hostfs,bind-paths,cwd,tmp', '--overlay', str(overlay) + ':ro',
                      '--cleanenv', '--pwd', '/', str(root), '/bin/cat',
                      '/tmp/output', '/var/tmp/output', '/opt/output'], timeout=30)
    assert result.stdout == 'tmp\nvar/tmp\nopt\n'
    assert not (root / 'tmp/output').exists()


def test_build_budget_streams_output_and_overrides_inner_timeout(tmp_path, capsys):
    from harbor_patches.image_build import build_budget_commands
    tool = tmp_path/'apptainer'
    tool.write_text('#!/bin/sh\necho diagnostic-before-wait\nsleep 0.1\necho finished\n')
    tool.chmod(0o755)
    run = build_budget_commands(lambda *a, **kw: pytest.fail('unexpected fallback'), 2)
    result = run([str(tool),'build','output','input.def'], capture_output=True, text=True, timeout=0.01)
    assert result.returncode == 0
    assert 'finished' in result.stdout
    assert 'diagnostic-before-wait' in capsys.readouterr().out


def test_build_budget_timeout_keeps_partial_diagnostics(tmp_path, capsys):
    import subprocess
    from harbor_patches.image_build import build_budget_commands
    tool = tmp_path/'apptainer'
    tool.write_text('#!/bin/sh\necho download-started\nsleep 10\n')
    tool.chmod(0o755)
    run = build_budget_commands(lambda *a, **kw: None, 0.15)
    with pytest.raises(subprocess.TimeoutExpired) as error:
        run([str(tool),'build','out','in.def'], timeout=1800)
    assert 'download-started' in error.value.output
    assert 'download-started' in capsys.readouterr().out


def test_build_artifacts_are_read_only_and_only_mounted_for_build_commands(tmp_path):
    from harbor_patches.image_build import artifact_build_mount
    cache = tmp_path / 'artifacts'
    cache.mkdir()
    commands = []
    def run(command, **kwargs):
        commands.append(command)
        if command[1] == 'build':
            assert '/run/ot-image-artifacts/' in Path(command[-1]).read_text()
        return 0
    execute = artifact_build_mount(run, cache)
    execute(['apptainer', 'exec', 'image.sif', 'true'])
    assert commands[-1][2:4] == ['--bind', f'{cache}:/run/ot-image-artifacts:ro']
    execute(['unshare', '-r', 'apptainer', 'exec', '--overlay', 'layer', 'image.sif', 'true'])
    assert commands[-1][4:6] == ['--bind', f'{cache}:/run/ot-image-artifacts:ro']
    definition = tmp_path/'image.def'
    definition.write_text('Bootstrap: localimage\nFrom: image.sif\n')
    execute(['apptainer', 'build', 'built.sif', str(definition)])
    assert commands[-1][2:4] == ['--bind', f'{cache}:/run/ot-image-artifacts:ro']
    assert definition.read_text() == 'Bootstrap: localimage\nFrom: image.sif\n'
    execute(['echo', 'unrelated'])
    assert commands[-1] == ['echo', 'unrelated']
