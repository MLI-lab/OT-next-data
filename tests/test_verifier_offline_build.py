import json

from harbor_patches.verifier_bundle import spec_id
from hpc.offline_prep import verifier_offline
from hpc.offline_prep.verifier_offline import catalog, plan


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
