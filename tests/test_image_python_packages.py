import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from data.utils.verifier_runtime import runtime_spec
from hpc.offline_prep.image_python_packages import COPY


def test_image_package_identity_requires_declared_pins_and_modules():
    image=dict(image_sha256='a'*64,executable='/usr/bin/python3.12')
    baseline=runtime_spec(python='3.12.3',requirements=['pandas==2.1.4+dfsg'],image_python=image,install_no_deps=True)
    copied=runtime_spec(python='3.12.3',requirements=['pandas==2.1.4+dfsg'],
        image_python={**image,'packages':{'pandas==2.1.4+dfsg':['pandas']}},install_no_deps=True)
    assert copied!=baseline
    for packages in ({'pandas==2.1.4+dfsg':['../task']},{'numpy==1.26.4':['numpy']}):
        with pytest.raises(ValueError,match='Image packages'):
            runtime_spec(python='3.12.3',requirements=['pandas==2.1.4+dfsg'],image_python={**image,'packages':packages},install_no_deps=True)


def test_copy_selects_only_declared_distribution_and_rejects_external_links(tmp_path,monkeypatch):
    import importlib.metadata
    import subprocess
    import sys
    source=tmp_path/'source';source.mkdir()
    module=source/'demo';module.mkdir();(module/'__init__.py').write_text('version="1.0+distro"\n')
    metadata=source/'demo-1.0+distro.egg-info';metadata.mkdir();(metadata/'PKG-INFO').write_text('Name: demo\nVersion: 1.0+distro\n')
    (source/'agent_hook.pth').write_text('import attacker\n')
    (source/'unrelated.py').write_text('raise Exception()\n')
    runtime=tmp_path/'runtime';runtime.mkdir();site=runtime/'site'
    monkeypatch.setattr(importlib.metadata,'distribution',lambda name:SimpleNamespace(version='1.0+distro',_path=metadata))
    monkeypatch.setattr(subprocess,'check_output',lambda *args,**kwargs:str(site)+'\n')
    monkeypatch.setattr(sys,'argv',['copy',str(runtime),json.dumps({'demo==1.0+distro':['demo']})])
    exec(compile(COPY,'copy-image-packages','exec'),{})
    assert (site/'demo/__init__.py').read_bytes()==(module/'__init__.py').read_bytes()
    assert set(p.name for p in site.iterdir())=={'demo','demo-1.0+distro.egg-info'}
    outside=tmp_path/'outside';outside.write_text('not a package file')
    (module/'external').symlink_to(outside)
    second=tmp_path/'second';second.mkdir()
    monkeypatch.setattr(sys,'argv',['copy',str(second),json.dumps({'demo==1.0+distro':['demo']})])
    monkeypatch.setattr(subprocess,'check_output',lambda *args,**kwargs:str(second/'site')+'\n')
    with pytest.raises(ValueError):exec(compile(COPY,'copy-image-packages','exec'),{})


def test_copy_supports_single_file_distro_egg_info(tmp_path,monkeypatch):
    import importlib.metadata
    import subprocess
    import sys
    source=tmp_path/'source';source.mkdir()
    (source/'demo.py').write_text('answer=42\n')
    metadata=source/'demo-1.0.egg-info';metadata.write_text('Name: demo\nVersion: 1.0\n')
    runtime=tmp_path/'runtime';runtime.mkdir();site=runtime/'site'
    monkeypatch.setattr(importlib.metadata,'distribution',lambda name:SimpleNamespace(version='1.0',_path=metadata))
    monkeypatch.setattr(subprocess,'check_output',lambda *args,**kwargs:str(site)+'\n')
    monkeypatch.setattr(sys,'argv',['copy',str(runtime),json.dumps({'demo==1.0':['demo']})])
    exec(compile(COPY,'copy-image-packages','exec'),{})
    assert (site/'demo-1.0.egg-info').read_bytes()==metadata.read_bytes()
    assert (site/'demo.py').read_text()=='answer=42\n'


def test_unversioned_distro_modules_are_image_bound_and_copied_selectively(tmp_path,monkeypatch):
    import subprocess,sys
    from hpc.offline_prep.image_python_packages import COPY_MODULES
    image=dict(image_sha256='a'*64,executable='/usr/bin/python3.12',modules={'demo':'/usr/lib/python3/dist-packages/demo'})
    spec=runtime_spec(python='3.12.3',requirements=[],image_python=image,install_no_deps=True)
    assert spec['image_python']['modules']==image['modules']
    with pytest.raises(ValueError,match='Image modules'):
        runtime_spec(python='3.12.3',requirements=[],image_python={**image,'modules':{'demo':'/app/demo'}},install_no_deps=True)
    source=tmp_path/'dist-packages';source.mkdir();module=source/'demo';module.mkdir()
    (module/'__init__.py').write_text('answer=42\n');(source/'unrelated.py').write_text('raise Exception()')
    runtime=tmp_path/'runtime';runtime.mkdir();site=runtime/'site'
    monkeypatch.setattr(subprocess,'check_output',lambda *args,**kwargs:str(site)+'\n')
    monkeypatch.setattr(sys,'argv',['copy',str(runtime),json.dumps({'demo':str(module)})])
    exec(compile(COPY_MODULES,'copy-modules','exec'),{})
    assert (site/'demo/__init__.py').read_bytes()==(module/'__init__.py').read_bytes()
    assert set(p.name for p in site.iterdir())=={'demo'}
    with pytest.raises(AssertionError,match='Conflicting'):
        exec(compile(COPY_MODULES,'copy-modules','exec'),{})


def test_module_resource_symlinks_require_explicit_files(tmp_path,monkeypatch):
    import subprocess,sys
    from hpc.offline_prep.image_python_packages import COPY_MODULES
    source=tmp_path/'dist-packages';source.mkdir();module=source/'demo';module.mkdir()
    resource=tmp_path/'font.ttf';resource.write_bytes(b'pristine font bytes')
    (module/'font.ttf').symlink_to(resource)
    runtime=tmp_path/'runtime';runtime.mkdir();site=runtime/'site'
    monkeypatch.setattr(subprocess,'check_output',lambda *args,**kwargs:str(site)+'\n')
    monkeypatch.setattr(sys,'argv',['copy',str(runtime),json.dumps({'demo':str(module)})])
    with pytest.raises(AssertionError,match='Undeclared'):
        exec(compile(COPY_MODULES,'copy-modules','exec'),{})
    monkeypatch.setattr(sys,'argv',['copy',str(runtime),json.dumps({'demo':str(module)}),json.dumps([str(resource)])])
    exec(compile(COPY_MODULES,'copy-modules','exec'),{})
    assert (site/'demo/font.ttf').read_bytes()==resource.read_bytes()
    assert not (site/'demo/font.ttf').is_symlink()
