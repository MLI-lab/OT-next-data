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
            assert command[command.index('--no-mount') + 1] == 'hostfs,bind-paths,cwd'
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
