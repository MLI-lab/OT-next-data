import io
import json
import tarfile

import pytest

from data.utils.verifier_runtime import runtime_spec, runtime_reference
from harbor_patches import verifier_bundle as bundle


def test_auxiliary_versions_are_identity_bearing_and_platform_bound():
    child = runtime_spec(python='3.12.3', requirements=['numpy==1.26.4'])
    base = runtime_spec(python='3.13.9', requirements=['pytest==8.4.1'], tools=['pytest'])
    composite = runtime_spec(**{k:v for k,v in base.items() if k != 'runtime_layout'}, auxiliary={'system':child})
    assert bundle.spec_id(composite) != bundle.spec_id(base)
    with pytest.raises(ValueError,match='platform'):
        runtime_spec(python='3.13.9',requirements=[],auxiliary={'system':{**child,'architecture':'aarch64'}})
    with pytest.raises(ValueError,match='Nested'):
        runtime_spec(python='3.13.9',requirements=[],auxiliary={'system':composite})
    with pytest.raises(ValueError,match='name'):
        runtime_spec(python='3.13.9',requirements=[],auxiliary={'../system':child})


def test_missing_auxiliary_interpreter_invalidates_otherwise_checksum_valid_cache(tmp_path):
    spec = runtime_spec(python='3.13.9', requirements=[], auxiliary={
        'system':runtime_spec(python='3.12.3',requirements=[])})
    key=bundle.spec_id(spec)
    archive=tmp_path/(key+'.tar.gz')
    with tarfile.open(archive,'w:gz') as stream:
        member=tarfile.TarInfo('python/bin/python3');member.mode=0o755;stream.addfile(member)
    record=dict(format=bundle.FORMAT,id=key,spec=spec,status='ready',archive_sha256=bundle.digest(archive))
    (tmp_path/(key+'.json')).write_text(json.dumps(record))
    ref=json.loads(runtime_reference(spec,{key:record})['tests/ot-verifier-runtime.json'])
    assert 'spec' not in ref
    path,reason=bundle.lookup(ref,root=tmp_path, preparation=True)
    assert path is None and 'python/aux/system/bin/python3' in reason


def test_auxiliary_preparation_uses_verified_image_and_relative_subtree(tmp_path,monkeypatch):
    from hpc.offline_prep import auxiliary_verifier_runtimes as auxiliary,verifier_bundles
    child=runtime_spec(python='3.12.3',requirements=[])
    key=bundle.spec_id(child);cache=tmp_path/'cache';cache.mkdir()
    archive=cache/(key+'.tar.gz')
    with tarfile.open(archive,'w:gz') as stream:
        member=tarfile.TarInfo('python/bin/python3');body=b'#!/bin/sh\nexit 0\n';member.mode=0o755;member.size=len(body);stream.addfile(member,io.BytesIO(body))
    record=dict(format=bundle.FORMAT,id=key,spec=child,status='ready',archive_sha256=bundle.digest(archive))
    (cache/(key+'.json')).write_text(json.dumps(record))
    from harbor_patches.runtime_images import build
    image = build(record, cache, processors=1)
    monkeypatch.setattr(verifier_bundles,'build',lambda *args:image)
    archive.unlink()  # Published caches contain only the image and manifest.
    runtime=tmp_path/'runtime';runtime.mkdir();scratch=tmp_path/'scratch';scratch.mkdir()
    calls=[]
    original_run = auxiliary.subprocess.run
    def run(argv, **kwargs):
        if argv[0] == 'unsquashfs':
            return original_run(argv, **kwargs)
        calls.append(argv)
    monkeypatch.setattr(auxiliary.subprocess,'run',run)
    records=auxiliary.prepare({'system':child},runtime,scratch,cache,'uv')
    assert calls[0][:2]==[str(runtime/'aux/system/bin/python3'),'-I']
    assert records['system']['archive_sha256']==record['archive_sha256']
    assert (runtime/'aux/system/bin/python3').read_bytes()==body
    assert (cache/(key+'.json')).read_text()==json.dumps(record)
