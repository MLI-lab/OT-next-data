import hashlib
import json

import pytest

from data.utils.verifier_runtime import runtime_spec
from hpc.offline_prep.image_python_runtime import command, source_image


def spec(checksum):
    return runtime_spec(python='3.10.22', requirements=['pytest==8.4.1'], image_python={
        'image_sha256': checksum, 'executable': '/usr/local/bin/python3.10'})


def test_image_source_changes_identity_and_requires_exact_bytes(tmp_path, monkeypatch):
    from harbor_patches.verifier_bundle import spec_id
    data = b'pristine source image'
    checksum = hashlib.sha256(data).hexdigest()
    selected = spec(checksum)
    image = tmp_path / (checksum + '.sif')
    image.write_bytes(data)
    monkeypatch.setenv('OT_RUNTIME_IMAGE_CACHE', str(tmp_path))
    assert source_image(selected) == image
    assert spec_id(selected) != spec_id({k: v for k, v in selected.items() if k != 'image_python'})
    image.write_bytes(b'changed')
    with pytest.raises(ValueError, match='checksum mismatch'):
        source_image(selected)


@pytest.mark.parametrize('executable', ['python3', '/usr/../bin/python3', '/usr/bin/python3;false'])
def test_image_source_rejects_unsafe_interpreter(executable):
    with pytest.raises(ValueError):
        runtime_spec(python='3.10.22', requirements=[], image_python={
            'image_sha256': 'a' * 64, 'executable': executable})


def test_image_commands_use_private_scratch_and_clean_environment(tmp_path):
    argv = command('/pristine.sif', tmp_path, '/python', '-I')
    assert '--cleanenv' in argv and '--containall' in argv and '--no-home' in argv
    assert argv[argv.index('--env') + 1] == f'TMPDIR={tmp_path}'
    assert argv[-3:] == ['/pristine.sif', '/python', '-I']


def test_image_install_can_use_readonly_local_wheels(tmp_path, monkeypatch):
    from hpc.offline_prep import image_python_runtime
    wheelhouse = tmp_path / 'wheels'
    wheelhouse.mkdir()
    monkeypatch.setenv('OT_RUNTIME_WHEELHOUSE', str(wheelhouse))
    monkeypatch.setattr(image_python_runtime.shutil, 'which', lambda _: '/trusted/uv')
    calls = []
    monkeypatch.setattr(image_python_runtime.subprocess, 'run', lambda argv, **kwargs: calls.append(argv))
    image_python_runtime.prepare(spec('a' * 64), tmp_path / 'python', tmp_path, '/pristine.sif', 'uv')
    install = calls[1]
    assert '--no-index' in install
    assert install[install.index('--find-links') + 1] == '/ot-preparation-wheels'
    assert str(wheelhouse) + ':/ot-preparation-wheels:ro' in install
    assert '/trusted/uv:/ot-preparation-uv:ro' in install


def test_cleanenv_forwards_proxy_without_command_line_values(monkeypatch):
    from hpc.offline_prep.image_python_runtime import proxy_environment
    monkeypatch.setenv('https_proxy', 'http://approved-proxy:80')
    monkeypatch.setenv('APPTAINERENV_PYTHONPATH', '/unwanted')
    env = proxy_environment()
    assert env['APPTAINERENV_https_proxy'] == 'http://approved-proxy:80'
    assert 'APPTAINERENV_PYTHONPATH' not in env


def test_debian_scripts_move_without_changing_package_layout(tmp_path):
    from hpc.offline_prep.image_python_runtime import normalize_scripts
    (tmp_path / 'bin').mkdir()
    scripts = tmp_path / 'local/bin'
    scripts.mkdir(parents=True)
    (scripts / 'pytest').write_text('#!/build/python\n')
    package = tmp_path / 'local/lib/python3.10/dist-packages/pytest'
    package.mkdir(parents=True)
    normalize_scripts(tmp_path)
    assert (tmp_path / 'bin/pytest').read_text() == '#!/build/python\n'
    assert package.is_dir()
    assert not (scripts / 'pytest').exists()


def test_debian_script_conflicts_fail_before_any_move(tmp_path):
    from hpc.offline_prep.image_python_runtime import normalize_scripts
    (tmp_path / 'bin').mkdir()
    scripts = tmp_path / 'local/bin'
    scripts.mkdir(parents=True)
    (tmp_path / 'bin/python3').write_text('interpreter')
    (scripts / 'pytest').write_text('pytest')
    (scripts / 'python3').write_text('conflict')
    with pytest.raises(ValueError, match='conflicts with interpreter'):
        normalize_scripts(tmp_path)
    assert (scripts / 'pytest').exists()
    assert (tmp_path / 'bin/python3').read_text() == 'interpreter'


def test_full_bundle_bound_before_readonly_overlay_execution(tmp_path, monkeypatch):
    from hpc.offline_prep.image_python_runtime import SourceImage, stage_source
    from harbor_patches.verifier_bundle import spec_id
    paths={suffix:tmp_path/('image'+suffix) for suffix in ('.sif','.overlay.img','.deferred.json')}
    for suffix,path in paths.items():path.write_bytes(suffix.encode())
    hashes={suffix:hashlib.sha256(path.read_bytes()).hexdigest() for suffix,path in paths.items()}
    binding=dict(image_sha256=hashes['.sif'],overlay_sha256=hashes['.overlay.img'],
                 deferred_sha256=hashes['.deferred.json'],executable='/opt/conda/envs/testbed/bin/python')
    recipe=runtime_spec(python='3.9.21',requirements=[],image_python=binding)
    selected=source_image(recipe,paths['.sif'])
    assert isinstance(selected,SourceImage)
    with pytest.raises(ValueError,match='private build scratch'):
        command(selected,tmp_path,'true')
    selected=stage_source(selected,tmp_path)
    argv=command(selected,tmp_path,'/opt/conda/envs/testbed/bin/python','-I')
    assert argv[argv.index('--overlay')+1]==str(tmp_path/'source.overlay.img')+':ro'
    assert (tmp_path/'source.overlay.img').read_bytes()==paths['.overlay.img'].read_bytes()
    assert str(paths['.sif']) in argv
    changed=runtime_spec(python='3.9.21',requirements=[],image_python={**binding,'overlay_sha256':'f'*64})
    assert spec_id(recipe)!=spec_id(changed)
    paths['.deferred.json'].write_bytes(b'changed')
    with pytest.raises(ValueError,match='bundle checksum'):source_image(recipe,paths['.sif'])


@pytest.mark.parametrize('field',['overlay_sha256','deferred_sha256'])
def test_partial_bundle_bindings_rejected(field):
    with pytest.raises(ValueError):
        runtime_spec(python='3.9.21',requirements=[],image_python={
            'image_sha256':'a'*64,'executable':'/opt/bin/python',field:'b'*64})


@pytest.mark.parametrize('header_mode', [None, 'build_requirements', 'python_headers'])
def test_pristine_copy_skips_overlay_whiteouts_and_task_packages(tmp_path, monkeypatch, header_mode):
    import platform
    import sys
    import sysconfig
    from hpc.offline_prep.image_python_runtime import COPY
    source = tmp_path / 'python3.12'
    source.mkdir()
    (source / 'os.py').write_text('stdlib body')
    for name in ('.wh..wh..opq', '.wh.deleted'):
        # A dangling link represents a control entry which cannot be copied as
        # ordinary data. Omitting it must not omit adjacent actual modules.
        (source / name).symlink_to('missing-control-target')
    packages = source / 'site-packages'; packages.mkdir()
    (packages / 'agent.py').write_text('untrusted')
    headers = tmp_path / 'include/python3.12'
    headers.mkdir(parents=True)
    (headers / 'Python.h').write_text('exact interpreter header')
    monkeypatch.setattr(sysconfig, 'get_path', lambda key: str(source if key == 'stdlib' else headers))
    monkeypatch.setattr(sysconfig, 'get_config_var', lambda key: str(tmp_path / 'empty-lib'))
    target = tmp_path / 'target'
    monkeypatch.setattr(sys, 'argv', ['copy', str(target), json.dumps(dict(
        python=platform.python_version(),architecture=platform.machine(),libc='gnu',
        build_requirements=['wheel==0.37.1'] if header_mode == 'build_requirements' else [],
        **({'python_headers': True} if header_mode == 'python_headers' else {})))])
    monkeypatch.setattr(sys, 'prefix', str(tmp_path))
    monkeypatch.setattr(sys, 'base_prefix', sys.prefix)
    exec(compile(COPY, '<copy-image-python>', 'exec'), {})
    copied = target / 'lib' / source.name
    assert (copied / 'os.py').read_text() == 'stdlib body'
    assert not list(copied.glob('.wh.*'))
    assert not (copied / 'site-packages/agent.py').exists()
    assert (target / 'include/python3.12/Python.h').exists() == (header_mode is not None)


def test_python_headers_option_is_identity_bearing_and_requires_image_python():
    from harbor_patches.verifier_bundle import spec_id
    from data.utils.verifier_runtime import preparation_command
    base = spec('a' * 64)
    kwargs = {k: v for k, v in base.items() if k != 'runtime_layout'}
    assert runtime_spec(**kwargs, python_headers=False) == base
    selected = runtime_spec(**kwargs, python_headers=True)
    assert selected['python_headers'] is True and spec_id(selected) != spec_id(base)
    assert runtime_spec(**{k: v for k, v in selected.items() if k != 'runtime_layout'}) == selected
    assert '"python_headers":true' in preparation_command(selected, phase='verifier')
    with pytest.raises(ValueError, match='image Python'):
        runtime_spec(python='3.12.1', requirements=[], python_headers=True)
    with pytest.raises(ValueError, match='boolean'):
        runtime_spec(**kwargs, python_headers='yes')


def test_conda_copy_includes_only_prefix_owned_native_dependencies(tmp_path, monkeypatch):
    import platform
    import subprocess
    import sys
    import sysconfig
    from hpc.offline_prep.image_python_runtime import COPY
    prefix=tmp_path/'conda';(prefix/'conda-meta').mkdir(parents=True)
    lib=prefix/'lib';source=lib/'python3.12';source.mkdir(parents=True)
    (source/'os.py').write_text('stdlib')
    owned=lib/'libssl.so.1.1';owned.write_bytes(b'owned SSL')
    external=tmp_path/'libc.so.6';external.write_bytes(b'system libc')
    monkeypatch.setattr(sys,'prefix',str(prefix));monkeypatch.setattr(sys,'base_prefix',str(prefix))
    monkeypatch.setattr(sysconfig,'get_path',lambda key:str(source))
    monkeypatch.setattr(sysconfig,'get_config_var',lambda key:str(lib))
    monkeypatch.setattr(subprocess,'check_output',lambda *a,**kw:
        f'libssl.so.1.1 => {owned} (0x1)\nlibc.so.6 => {external} (0x2)\n')
    target=tmp_path/'target'
    monkeypatch.setattr(sys,'argv',['copy',str(target),json.dumps(dict(
        python=platform.python_version(),architecture=platform.machine(),libc='gnu'))])
    exec(compile(COPY,'<copy-image-python>','exec'),{})
    assert (target/'lib/libssl.so.1.1').read_bytes()==b'owned SSL'
    assert not (target/'lib/libc.so.6').exists()


@pytest.mark.parametrize('no_deps',[False,True])
def test_exact_lock_install_mode_is_explicit_and_replayed(tmp_path,monkeypatch,no_deps):
    from hpc.offline_prep import image_python_runtime
    from harbor_patches.verifier_bundle import spec_id
    from data.utils.verifier_runtime import preparation_command
    base=spec('a'*64)
    selected=runtime_spec(**{k:v for k,v in base.items() if k!='runtime_layout'},install_no_deps=no_deps)
    assert ('install_no_deps' in selected)==no_deps
    assert (spec_id(selected)!=spec_id(base))==no_deps
    assert runtime_spec(**{k:v for k,v in selected.items() if k!='runtime_layout'})==selected
    assert ('install_no_deps' in preparation_command(selected,phase='verifier'))==no_deps
    wheelhouse=tmp_path/'wheels';wheelhouse.mkdir()
    monkeypatch.setenv('OT_RUNTIME_WHEELHOUSE',str(wheelhouse))
    monkeypatch.setattr(image_python_runtime.shutil,'which',lambda _: '/trusted/uv')
    calls=[]
    monkeypatch.setattr(image_python_runtime.subprocess,'run',lambda argv,**kw:calls.append(argv))
    image_python_runtime.prepare(selected,tmp_path/'runtime',tmp_path,'/pristine.sif','uv')
    install=calls[1]
    assert ('--no-deps' in install)==no_deps
    assert '--no-index' in install


@pytest.mark.parametrize('value',['true',1,None])
def test_install_mode_rejects_nonboolean_values(value):
    with pytest.raises(ValueError,match='boolean'):
        runtime_spec(python='3.8.20',requirements=[],install_no_deps=value)


def test_build_bootstrap_identity_and_install_order(tmp_path, monkeypatch):
    from hpc.offline_prep import image_python_runtime
    from harbor_patches.verifier_bundle import spec_id
    from data.utils.verifier_runtime import preparation_command
    base = spec('a' * 64)
    kwargs = {k: v for k, v in base.items() if k != 'runtime_layout'}
    assert runtime_spec(**kwargs, build_requirements=(), install_no_build_isolation=False) == base
    selected = runtime_spec(**kwargs, build_requirements=['numpy==1.18.4', 'wheel==0.37.1'],
                            install_no_build_isolation=True)
    assert spec_id(selected) != spec_id(base)
    assert runtime_spec(**{k: v for k, v in selected.items() if k != 'runtime_layout'}) == selected
    assert 'build_requirements' in preparation_command(selected, phase='verifier')
    wheelhouse = tmp_path / 'wheels'
    wheelhouse.mkdir()
    monkeypatch.setenv('OT_RUNTIME_WHEELHOUSE', str(wheelhouse))
    monkeypatch.setattr(image_python_runtime.shutil, 'which', lambda _: '/trusted/uv')
    calls = []
    monkeypatch.setattr(image_python_runtime.subprocess, 'run', lambda argv, **kw: calls.append(argv))
    image_python_runtime.prepare(selected, tmp_path / 'runtime', tmp_path, '/pristine.sif', 'uv')
    assert len(calls) == 3
    bootstrap, final = calls[1:]
    assert bootstrap[-2:] == selected['build_requirements']
    assert '--only-binary=:all:' in bootstrap and '--no-deps' in bootstrap
    assert '--no-build-isolation' not in bootstrap
    assert '--no-build-isolation' in final and '--no-deps' not in final
    assert final[-1] == 'pytest==8.4.1'
    for call in (bootstrap, final):
        assert '--no-cache' in call and '--no-index' in call
        assert str(wheelhouse) + ':/ot-preparation-wheels:ro' in call


@pytest.mark.parametrize('changes', [
    {'image_python': None}, {'build_requirements': []},
    {'build_requirements': ['numpy']}, {'build_requirements': 'numpy==1.18.4'},
    {'install_no_build_isolation': False}, {'install_no_build_isolation': 1},
])
def test_build_bootstrap_incomplete_or_unsupported_rejected(changes):
    kwargs = dict(python='3.8.20', requirements=['fastparquet==0.4.0'],
                  image_python={'image_sha256': 'a' * 64, 'executable': '/usr/local/bin/python'},
                  build_requirements=['numpy==1.18.4'], install_no_build_isolation=True)
    kwargs.update(changes)
    with pytest.raises(ValueError):
        runtime_spec(**kwargs)


def test_failed_bootstrap_does_not_attempt_final_install(tmp_path, monkeypatch):
    import subprocess
    from hpc.offline_prep import image_python_runtime
    selected = runtime_spec(python='3.8.20', requirements=['fastparquet==0.4.0'],
                            image_python={'image_sha256': 'a' * 64, 'executable': '/usr/local/bin/python'},
                            build_requirements=['numpy==1.18.4'], install_no_build_isolation=True)
    monkeypatch.setattr(image_python_runtime.shutil, 'which', lambda _: '/trusted/uv')
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        if '/ot-preparation-uv' in argv:
            raise subprocess.CalledProcessError(41, argv)
    monkeypatch.setattr(image_python_runtime.subprocess, 'run', run)
    with pytest.raises(subprocess.CalledProcessError) as error:
        image_python_runtime.prepare(selected, tmp_path / 'runtime', tmp_path, '/pristine.sif', 'uv')
    assert error.value.returncode == 41
    assert len(calls) == 2
