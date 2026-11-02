import json
import pytest

from harbor_patches.verifier_bundle import spec_id
from hpc.offline_prep import verifier_offline
from hpc.offline_prep.verifier_offline import catalog, plan


def test_missing_http_recorder_fails_before_running_download_commands(tmp_path):
    import subprocess
    import shlex
    work = tmp_path / 'work'
    commands = work / 'http-commands'
    commands.mkdir(parents=True)
    marker = tmp_path / 'unexpected-download'
    (commands / '0.sh').write_text('touch ' + shlex.quote(str(marker)))
    block = verifier_offline.BUILD.split('# Original setup commands', 1)[1]
    block = block[block.index('if [ -d'):]
    block = block.replace('/ot-replay/http_replay.sh', str(tmp_path / 'missing-recorder'))
    result = subprocess.run(['sh', '-eu', '-c',
        'W=' + shlex.quote(str(work)) + '; O=' + shlex.quote(str(tmp_path / 'out')) +
        '; OT_HTTP_WORKDIR=' + shlex.quote(str(tmp_path)) + '; OT_HTTP_HOME=$W\n' + block],
        capture_output=True, text=True)
    assert result.returncode != 0
    assert not marker.exists()


def test_http_archive_requires_executable_replay_and_recorded_download(tmp_path):
    import tarfile
    import io
    path = tmp_path / 'archive.tar.gz'
    spec = {'http': {'commands': ['curl https://example.test/input']}}
    for mode, response, valid in ((0o755, False, False), (0o644, True, False), (0o755, True, True)):
        with tarfile.open(path, 'w:gz') as archive:
            helper = tarfile.TarInfo('http/bin/curl'); helper.mode = mode
            archive.addfile(helper, io.BytesIO())
            if response:
                archive.addfile(tarfile.TarInfo('http/responses/key/url'), io.BytesIO())
        if valid:
            verifier_offline.validate_http_archive(spec, path)
        else:
            with pytest.raises(ValueError, match='Incomplete HTTP archive'):
                verifier_offline.validate_http_archive(spec, path)


def test_python_apt_prerequisites_are_bound_to_cached_packages():
    spec = {'apt': ['python3-pip', 'libxml2-dev'], 'python': [
        {'tool': 'pip3', 'requirements': ['PyYAML'], 'apt_prerequisites': ['python3-pip']},
        {'tool': 'pip3', 'requirements': ['lxml'], 'apt_prerequisites': ['python3-pip', 'libxml2-dev']}]}
    assert verifier_offline.python_apt_prerequisites(spec) == 'python3-pip\npython3-pip libxml2-dev\n'
    spec['python'][0]['apt_prerequisites'] = ['curl']
    with pytest.raises(ValueError, match='cached apt recipe'):
        verifier_offline.python_apt_prerequisites(spec)


def test_tasks_share_a_package_set_only_with_the_same_image_and_request():
    request = {'apt': ['curl', 'tree'], 'python': [], 'uv': False}
    other = {'apt': ['curl'], 'python': [], 'uv': False}
    groups = plan({'a': request, 'b': request, 'c': request, 'd': other}, {'a': 'k1', 'b': 'k1', 'c': 'k2', 'd': 'k1'})
    assert sorted(g['tasks'] for g in groups) == [['a', 'b'], ['c'], ['d']]
    assert {g['spec']['image_key'] for g in groups} == {'k1', 'k2'}


def test_rebuilt_image_changes_the_content_address_but_not_the_task_request(tmp_path):
    request = {'apt': ['tree'], 'python': [], 'uv': False}
    (old,) = plan({'a': request}, {'a': 'k1'})
    (new,) = plan({'a': request}, {'a': 'k1'}, {'image_sha256': 'f' * 64})
    assert spec_id(old['spec']) != spec_id(new['spec'])
    (tmp_path / 'shard-0000.json').write_text(json.dumps({spec_id(new['spec']): {
        **new, 'result': {'id': spec_id(new['spec']), 'spec': new['spec'], 'status': 'ready', 'archive_sha256': '0' * 64}}}))
    tasks = catalog(tmp_path, tmp_path / 'catalog.json')
    assert tasks['a']['request_id'] == spec_id(request) and tasks['a']['spec']['image_sha256'] == 'f' * 64


def test_directory_bundle_gets_one_scratch_copy_per_slot(tmp_path):
    bundle = tmp_path / 'cache' / 'build_task-k.sif'
    bundle.mkdir(parents=True)
    (bundle / 'build_task-k.overlay.img').write_bytes(b'ext3')
    scratch = tmp_path / 'scratch'
    first, temporary = verifier_offline.sif_for('k', {'k': str(bundle)}, scratch, 0)
    second, _ = verifier_offline.sif_for('k', {'k': str(bundle)}, scratch, 1)
    assert temporary and first != second
    assert first.parent.parent == second.parent.parent == scratch / 'images' / 'k'
    assert (second / 'build_task-k.overlay.img').read_bytes() == b'ext3'


def test_offline_bundle_execution_never_mounts_source_overlay(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from pathlib import Path
    source=tmp_path/'source';source.mkdir()
    (source/'image.sif').write_bytes(b'sif')
    original=source/'image.overlay.img';original.write_bytes(b'pristine')
    seen=[]
    def execute(command,**kwargs):
        overlay=Path(command[command.index('--overlay')+1].removesuffix(':ro'))
        assert overlay!=original and overlay.read_bytes()==b'pristine'
        overlay.write_bytes(b'mount metadata changed')
        seen.append(overlay)
        return SimpleNamespace(returncode=0,stdout='',stderr='')
    monkeypatch.setattr(verifier_offline.subprocess,'run',execute)
    # copy_image uses a subprocess too; replace only that copy with a byte copy.
    from hpc.offline_prep import image_cache
    monkeypatch.setattr(image_cache,'copy_image',lambda a,b:Path(b).write_bytes(Path(a).read_bytes()))
    result=verifier_offline.apptainer(source,[],{},'true',10)
    assert result.returncode==0 and len(seen)==1
    assert original.read_bytes()==b'pristine'


def test_task_wheel_no_deps_is_identity_bound_and_explicit():
    import pytest
    base={'apt':[],'uv':True,'python':[{'tool':'uv','requirements':['pytest==8.4.1','pluggy==1.3.0']}]}
    strict={**base,'python':[{**base['python'][0],'no_deps':True}]}
    assert spec_id(base)!=spec_id(strict)
    assert verifier_offline.python_requests(base).startswith('uv\t0\t')
    assert verifier_offline.python_requests(strict).startswith('uv\t1\t')
    with pytest.raises(ValueError,match='boolean'):
        verifier_offline.python_requests({**base,'python':[{**base['python'][0],'no_deps':'yes'}]})


def test_bound_offline_bundle_requires_every_exact_sidecar(tmp_path):
    import pytest
    from harbor_patches.verifier_bundle import digest
    image = tmp_path / 'image.sif'
    image.write_bytes(b'sif')
    overlay = image.with_suffix('.overlay.img'); overlay.write_bytes(b'overlay')
    deferred = image.with_suffix('.deferred.json'); deferred.write_bytes(b'{}')
    spec = dict(image_sha256=digest(image), overlay_sha256=digest(overlay), deferred_sha256=digest(deferred))
    verifier_offline.validate_source(spec, tmp_path)
    with pytest.raises(ValueError):
        verifier_offline.validate_source({'image_sha256': spec['image_sha256']}, tmp_path)
    with pytest.raises(ValueError):
        verifier_offline.validate_source({k:v for k,v in spec.items() if k != 'deferred_sha256'}, tmp_path)
    overlay.write_bytes(b'changed')
    with pytest.raises(ValueError, match='overlay_sha256'):
        verifier_offline.validate_source(spec, tmp_path)
    overlay.write_bytes(b'overlay')
    (tmp_path/'extra.overlay.img').write_bytes(b'extra')
    with pytest.raises(ValueError):
        verifier_offline.validate_source(spec, tmp_path)


def test_offline_recipe_binds_both_sidecars_without_changing_default():
    import pytest
    from data.utils.verifier_runtime import offline_preparation_command
    request = {'apt': [], 'python': [], 'uv': False}
    base = offline_preparation_command(request, image_sha256='a'*64)
    assert 'overlay_sha256' not in base
    bound = offline_preparation_command(request, image_sha256='a'*64,
        overlay_sha256='b'*64, deferred_sha256='c'*64)
    assert '"overlay_sha256": "'+'b'*64+'"' in bound
    assert '"deferred_sha256": "'+'c'*64+'"' in bound
    with pytest.raises(ValueError):
        offline_preparation_command(request, image_sha256='a'*64, overlay_sha256='b'*64)


def test_trusted_wheelhouse_is_readonly_local_only_and_byte_recorded(tmp_path, monkeypatch):
    import pytest
    from harbor_patches.verifier_bundle import digest
    monkeypatch.delenv('OT_RUNTIME_WHEELHOUSE', raising=False)
    assert verifier_offline.wheelhouse_inputs() == ([], {}, [])
    monkeypatch.setenv('OT_RUNTIME_WHEELHOUSE', str(tmp_path))
    with pytest.raises(ValueError, match='no wheels'):
        verifier_offline.wheelhouse_inputs()
    wheel = tmp_path/'example-1-py3-none-any.whl';wheel.write_bytes(b'wheel bytes')
    binds, env, records = verifier_offline.wheelhouse_inputs()
    assert binds == [(tmp_path, '/ot-preparation-wheels', 'ro')]
    assert env == {'PIP_NO_INDEX':'1','PIP_FIND_LINKS':'/ot-preparation-wheels',
                   'UV_NO_INDEX':'1','UV_FIND_LINKS':'/ot-preparation-wheels'}
    assert records == [{'filename':wheel.name,'sha256':digest(wheel),'bytes':11}]
    wheel.write_bytes(b'mutated')
    assert records != verifier_offline.wheelhouse_inputs()[2]
    monkeypatch.setenv('OT_RUNTIME_WHEELHOUSE', str(wheel))
    with pytest.raises(ValueError, match='directory'):
        verifier_offline.wheelhouse_inputs()


def test_wheel_inputs_bind_the_selected_image_python():
    from hpc.offline_prep.verifier_offline import python_requests
    request={'python':[{'tool':'uv','python':'/opt/verifier/bin/python','requirements':['wheel==0.45.1']}]}
    assert python_requests(request)=='uv@/opt/verifier/bin/python\t0\twheel==0.45.1\n'
    for value in ('python3','/app/../bin/python','/usr/bin/python3;echo unsafe'):
        request['python'][0]['python']=value
        with pytest.raises(ValueError,match='pristine-image interpreter'):
            python_requests(request)


@pytest.mark.parametrize('phase', ['build', 'check'])
@pytest.mark.parametrize('index_url', [None, 'https://download.pytorch.org/whl/cpu'])
def test_wheel_shell_preserves_requirements_and_selected_python(tmp_path, phase, index_url):
    import subprocess
    work=tmp_path/'work';work.mkdir()
    output=tmp_path/'out';output.mkdir()
    tools=tmp_path/'tools';tools.mkdir()
    request={'python':[{'tool':'uv','python':'/opt/verifier/bin/python',
                       'requirements':['setuptools>=69', 'wheel; python_version >= "3.10"']}]}
    if index_url:
        request['python'][0]['index_url'] = index_url
    (work/'python-requests.tsv').write_text(verifier_offline.python_requests(request))
    # Exercise the actual shell loop with a recorder instead of downloading wheels.
    recorder='''#!/bin/bash
set -eu
printf '%s\\n' "$@" >> "$RECORD/args"
while [ "$#" -gt 0 ]; do
 if [ "$1" = -r ]; then cat "$2" >> "$RECORD/requirements"; fi
 shift
done
'''
    uv=tools/'uv';uv.write_text(recorder);uv.chmod(0o755)
    venv=work/'venv1/bin';venv.mkdir(parents=True)
    (venv/'python').write_text(recorder);(venv/'python').chmod(0o755)
    (output/'bin').mkdir();(output/'bin/uv').write_text(recorder);(output/'bin/uv').chmod(0o755)
    script=verifier_offline.BUILD if phase=='build' else verifier_offline.CHECK.format(setup=':')
    loop=script[script.index('i=0\n'):script.index('done < $W/python-requests.tsv')+len('done < $W/python-requests.tsv')]
    import os,shlex
    subprocess.run(['bash','-eu','-c', 'W='+shlex.quote(str(work))+'; O='+shlex.quote(str(output))+'; T=$O\n'+loop.replace('/ot-uv/uv',shlex.quote(str(uv)))],
                   check=True,env={**os.environ,'RECORD':str(tmp_path)})
    assert '/opt/verifier/bin/python' in (tmp_path/'args').read_text().splitlines()
    expected = (['--index-url ' + index_url] if index_url else []) + request['python'][0]['requirements']
    assert (tmp_path/'requirements').read_text().splitlines() == expected
    for bad in ('wheel\n--index-url https://invalid', '--extra-index-url=x'):
        with pytest.raises(ValueError,match='individual requirement'):
            verifier_offline.python_requests({'python':[{'tool':'uv','requirements':[bad]}]})
    for bad in ('https://user:secret@example.org/simple', 'https://example.org/\n--extra-index-url=x'):
        with pytest.raises(ValueError, match='credential-free HTTPS URL'):
            verifier_offline.python_requests({'python':[{'tool':'pip','requirements':['wheel'], 'index_url':bad}]})
