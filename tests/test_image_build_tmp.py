"""Definition builds must not expose another job's temporary files."""
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile

import pytest

from harbor_patches.image_build import image_tmp_build_mounts


@pytest.mark.parametrize('prefix', [[], ['unshare', '-r']])
@pytest.mark.parametrize('fail', [False, True])
def test_definition_tmp_mounts_preserve_recipe_and_cleanup(tmp_path, prefix, fail):
    definition = tmp_path / 'original.def'
    recipe = 'Bootstrap: localimage\nFrom: base.sif\n%post\n    true\n'
    definition.write_text(recipe)
    rootfs = tmp_path / 'rootfs'
    generated = []

    def execute(command, **kwargs):
        assert kwargs == {'timeout': 42, 'check': True}
        adapted = Path(command[-1])
        generated.append(adapted)
        content = adapted.read_text()
        assert content.startswith(recipe)
        subprocess.run(['sh', '-ec', content.split('%setup\n')[1]], check=True,
                       env={**os.environ, 'APPTAINER_ROOTFS': str(rootfs)})
        binds = [command[i + 1] for i, arg in enumerate(command) if arg == '--bind']
        for bind, name in zip(binds[:2], ('tmp', 'var/tmp')):
            source, target = bind.split(':')
            assert Path(source).resolve() == rootfs / name
            assert target == '/' + name
        assert binds[2] == '/artifact:/run/artifact:ro'
        assert '--fakeroot' in command
        if fail:
            raise RuntimeError('post failed')
        return 'built'

    command = [*prefix, '/usr/bin/apptainer', 'build', '--fakeroot', '--bind',
               '/artifact:/run/artifact:ro', 'output.sif', str(definition)]
    original = command.copy()
    run = image_tmp_build_mounts(execute)
    if fail:
        with pytest.raises(RuntimeError, match='post failed'):
            run(command, timeout=42, check=True)
    else:
        assert run(command, timeout=42, check=True) == 'built'
    assert command == original
    assert definition.read_text() == recipe
    assert generated and all(not path.parent.exists() for path in generated)


@pytest.mark.parametrize('command', [
    ['apptainer', 'build', 'base.sif', 'docker://ubuntu'],
    ['apptainer', 'exec', 'base.sif', 'true'],
    ['unshare', '-r', 'apptainer', 'exec', '--overlay', 'layer', 'base.sif', 'true'],
    ['echo', 'ordinary'],
])
def test_non_definition_commands_unchanged(command):
    calls = []
    image_tmp_build_mounts(lambda cmd, **kw: calls.append((cmd, kw)))(command, timeout=7)
    assert calls == [(command, {'timeout': 7})]


@pytest.mark.parametrize('name', ['tmp', 'var', 'var/tmp'])
def test_setup_rejects_host_resolved_image_symlinks(tmp_path, name):
    root = tmp_path / 'rootfs'
    link = root / name
    link.parent.mkdir(parents=True)
    host = tmp_path / 'host'
    host.mkdir()
    link.symlink_to(host, target_is_directory=True)
    definition = tmp_path / 'image.def'
    definition.write_text('Bootstrap: localimage\nFrom: base.sif\n%post\n    true\n')

    def execute(command):
        setup = Path(command[-1]).read_text().split('%setup\n')[1]
        result = subprocess.run(['sh', '-ec', setup],
                                env={**os.environ, 'APPTAINER_ROOTFS': str(root)})
        assert result.returncode != 0
        assert not list(host.iterdir())

    image_tmp_build_mounts(execute)(['apptainer', 'build', 'output.sif', str(definition)])


@pytest.mark.parametrize('fakeroot', [False, True])
def test_definition_build_cleanup_isolated_and_image_tmp_persists(tmp_path, fakeroot):
    """No downloads: real %post cleanup, COPY inputs and persisted outputs.

    Only uniquely named files created by this test are ever removed in %post,
    so running this regression against the old builder is safe on shared hosts.
    """
    for tool in ('apptainer', 'unshare', 'ldd'):
        if not shutil.which(tool):
            pytest.skip(f'{tool} is required for the build integration test')
    if subprocess.run(['unshare', '-r', 'true'], capture_output=True).returncode:
        pytest.skip('user namespaces are unavailable')

    root = tmp_path / 'base'
    for name in ('bin', 'usr/bin', 'etc', 'dev', 'proc', 'sys', 'tmp', 'var/tmp', 'opt', 'root', 'run'):
        (root / name).mkdir(parents=True, exist_ok=True)
    # The host fakeroot helper is a shell script using cut, sed and getopt.
    for executable in ('/bin/sh', '/usr/bin/sh', '/bin/cat', '/bin/rm',
                       '/usr/bin/cut', '/usr/bin/sed', '/usr/bin/getopt'):
        shutil.copy2(executable, root / executable.lstrip('/'))
        for word in subprocess.check_output(['ldd', executable], text=True).split():
            if word.startswith('/'):
                target = root / word.lstrip('/')
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(word, target)
    payload = tmp_path / 'input'
    payload.write_text('copied input\n')
    output = tmp_path / 'built'
    definition = tmp_path / 'image.def'
    with tempfile.NamedTemporaryFile(prefix='image-build-regression-', dir='/tmp') as host_tmp, \
            tempfile.NamedTemporaryFile(prefix='image-build-regression-', dir='/var/tmp') as host_var:
        sentinels = [Path(host_tmp.name), Path(host_var.name)]
        # Give the image same-named files, to prove cleanup actually runs there.
        for path in sentinels:
            path.write_text('other job scratch')
            (root / str(path).lstrip('/')).write_text('image scratch')
        input_name = sentinels[0].name + '-input'
        output_name = sentinels[0].name + '-output'
        definition.write_text(
            f'Bootstrap: localimage\nFrom: {root}\n\n%files\n'
            f'    {payload} /tmp/{input_name}\n    {payload} /var/tmp/{input_name}\n'
            f'    {payload} /opt/{input_name}\n\n%post\n'
            '    rm -f ' + ' '.join(shlex.quote(str(p)) for p in sentinels) + '\n'
            f'    for d in /tmp /var/tmp /opt; do test -f "$d/{input_name}"; '
            f'cat "$d/{input_name}" > "$d/{output_name}"; done\n')
        # A sandbox avoids compression and lets us inspect the actual artifact.
        result = image_tmp_build_mounts(subprocess.run)(
            [*([] if fakeroot else ['unshare', '-r']), shutil.which('apptainer'), 'build',
             *(['--fakeroot'] if fakeroot else []), '--sandbox',
             str(output), str(definition)], capture_output=True, text=True, timeout=60)
        assert all(p.exists() for p in sentinels), result.stdout + result.stderr
        assert result.returncode == 0, result.stdout + result.stderr
        for name in ('tmp', 'var/tmp', 'opt'):
            assert (output / name / output_name).read_text() == 'copied input\n'
            assert not (root / name / output_name).exists()
        for path in sentinels:
            assert path.read_text() == 'other job scratch'
            assert not (output / str(path).lstrip('/')).exists()
