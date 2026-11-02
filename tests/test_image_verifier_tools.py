import os
from pathlib import Path

import pytest

from data.utils.verifier_runtime import runtime_spec
from hpc.offline_prep import image_verifier_tools as tools


def test_image_tools_keep_task_python_resolution_and_copy_only_declared_files(tmp_path, monkeypatch):
    from hpc.offline_prep import image_python_runtime
    source = tmp_path / 'pristine-tool'
    source.write_bytes(b'\x7fELFexample')
    source.chmod(0o755)
    runtime = tmp_path / 'runtime'
    (runtime / 'bin').mkdir(parents=True)
    monkeypatch.setattr(image_python_runtime, 'source_image', lambda spec, **kwargs: tmp_path / 'image.sif')
    monkeypatch.setattr(image_python_runtime, 'stage_source', lambda image, scratch: image)
    monkeypatch.setattr(image_python_runtime, 'command',
                        lambda image, scratch, *args: [*args[:-2], str(source), args[-1]])
    value = dict(image_sha256='a'*64, executables={'pngcheck': '/usr/bin/pngcheck'})
    tools.prepare(value, runtime, tmp_path)
    assert (runtime / 'bin/pngcheck').read_bytes() == source.read_bytes()
    assert os.access(runtime / 'native-tools/pngcheck', os.X_OK)
    assert (runtime / 'native-tools/pngcheck').resolve() == runtime / 'bin/pngcheck'
    assert not (runtime / 'native-tools/python3').exists()
    with pytest.raises(ValueError, match='conflicts'):
        # Another attempt cannot replace a tool already installed by Python.
        other = tmp_path / 'other'
        (other / 'bin').mkdir(parents=True)
        (other / 'bin/pngcheck').write_text('installed console entrypoint')
        tools.prepare(value, other, tmp_path)


def test_image_tools_require_declared_names_and_pinned_source():
    kwargs = dict(python='3.13.9', requirements=['pytest==8.4.1'], tools=['pytest'])
    value = dict(image_sha256='a'*64, executables={'pngcheck': '/usr/bin/pngcheck'})
    with pytest.raises(ValueError, match='declared'):
        runtime_spec(**kwargs, image_tools=value)
    spec = runtime_spec(**{**kwargs, 'tools': ['pytest', 'pngcheck']}, image_tools=value)
    assert spec['image_tools'] == value
    for path in ('/app/pngcheck', '/usr/bin/../bin/pngcheck', '/setup_files/check.sh'):
        with pytest.raises(ValueError):
            tools.canonical({**value, 'executables': {'pngcheck': path}})


def test_native_magic_resource_is_bound_to_file_and_never_task_data(tmp_path,monkeypatch):
    from hpc.offline_prep import image_python_runtime
    binary=tmp_path/'file';binary.write_bytes(b'\x7fELFnative-file');binary.chmod(0o755)
    magic=tmp_path/'magic.mgc';magic.write_bytes(b'original magic database')
    runtime=tmp_path/'runtime';(runtime/'bin').mkdir(parents=True)
    monkeypatch.setattr(image_python_runtime,'source_image',lambda spec,**kwargs:tmp_path/'image.sif')
    monkeypatch.setattr(image_python_runtime,'stage_source',lambda image,scratch:image)
    def command(image,scratch,*args):
        source=magic if args[-2].endswith('magic.mgc') else binary
        return [*args[:-2],str(source),args[-1]]
    monkeypatch.setattr(image_python_runtime,'command',command)
    spec=dict(image_sha256='a'*64,executables={'file':'/usr/bin/file'},resources={'magic.mgc':'/usr/share/misc/magic.mgc'})
    tools.prepare(spec,runtime,tmp_path)
    assert (runtime/'native-resources/magic.mgc').read_bytes()==magic.read_bytes()
    for resources in [{'magic.mgc':'/app/magic.mgc'},{'other':'/etc/passwd'}]:
        with pytest.raises(ValueError,match='resources'):
            tools.canonical({**spec,'resources':resources})


def test_downloaded_native_libraries_follow_only_internal_links(tmp_path,monkeypatch):
    import io
    import tarfile
    from hpc.offline_prep import offline_sources
    source=dict(url='https://example.org/lib.deb',filename='lib.deb',sha256='b'*64)
    library='/usr/lib/x86_64-linux-gnu/libexample.so.1'
    spec=dict(image_sha256='a'*64,executables={},debian=dict(sources=[source],libraries=[library]))
    assert tools.canonical(spec)==spec
    def archive_bytes(link):
        output=io.BytesIO()
        with tarfile.open(fileobj=output,mode='w') as archive:
            member=tarfile.TarInfo('./usr/lib/x86_64-linux-gnu/libexample.so.1.0')
            payload=b'\x7fELFclean-library';member.size=len(payload)
            archive.addfile(member,io.BytesIO(payload))
            member=tarfile.TarInfo('.'+library);member.type=tarfile.SYMTYPE;member.linkname=link
            archive.addfile(member)
            member=tarfile.TarInfo('./etc/should-not-be-copied');member.size=3
            archive.addfile(member,io.BytesIO(b'bad'))
        return output.getvalue()
    monkeypatch.setattr(offline_sources,'prepare',lambda sources,output:None)
    monkeypatch.setattr(tools.subprocess,'check_output',lambda args:archive_bytes('libexample.so.1.0'))
    runtime=tmp_path/'runtime'
    tools.prepare_debian(spec['debian'],{},runtime,tmp_path)
    assert (runtime/'native-libs/libexample.so.1').read_bytes()==b'\x7fELFclean-library'
    assert sorted(p.name for p in runtime.iterdir())==['native-libs']
    monkeypatch.setattr(tools.subprocess,'check_output',lambda args:archive_bytes('/app/poison.so'))
    with pytest.raises(ValueError,match='escapes'):
        tools.prepare_debian(spec['debian'],{},tmp_path/'other',tmp_path)
    with pytest.raises(ValueError,match='explicit system libraries'):
        tools.canonical({**spec,'debian':{**spec['debian'],'libraries':['/app/libexample.so']}})


def test_clean_node_package_comes_only_from_the_pinned_image(tmp_path,monkeypatch):
    import json
    from hpc.offline_prep import image_python_runtime
    binary=tmp_path/'node';binary.write_bytes(b'\x7fELFnode');binary.chmod(0o755)
    package=tmp_path/'ws';package.mkdir();(package/'package.json').write_text(json.dumps(dict(name='ws',version='8.22.0')))
    (package/'index.js').write_text('module.exports = "clean ws";\n')
    monkeypatch.setattr(image_python_runtime,'source_image',lambda spec,**kwargs:tmp_path/'image.sif')
    monkeypatch.setattr(image_python_runtime,'stage_source',lambda image,scratch:image)
    def command(image,scratch,*args):
        source=package if args[-2].endswith('/ws') else binary
        return [*args[:-2],str(source),args[-1]]
    monkeypatch.setattr(image_python_runtime,'command',command)
    spec=dict(image_sha256='a'*64,executables={'node':'/usr/bin/node'},node_modules={'ws':{'path':'/usr/local/lib/node_modules/ws','version':'8.22.0'}})
    runtime=tmp_path/'runtime';(runtime/'bin').mkdir(parents=True)
    tools.prepare(spec,runtime,tmp_path)
    assert (runtime/'native-node/ws/index.js').read_text()=='module.exports = "clean ws";\n'
    assert (runtime/'native-tools/node').resolve()==runtime/'bin/node'
    (package/'outside').symlink_to(tmp_path/'node')
    other=tmp_path/'other';(other/'bin').mkdir(parents=True)
    with pytest.raises(ValueError,match='external'):
        tools.prepare(spec,other,tmp_path)
    with pytest.raises(ValueError,match='Node modules'):
        tools.canonical({**spec,'node_modules':{'ws':{'path':'/app/node_modules/ws','version':'8.22.0'}}})


def test_valgrind_resource_inventory_requires_exact_pristine_memcheck_files():
    from hpc.offline_prep.image_verifier_tools import canonical
    spec={'image_sha256':'a'*64,'executables':{'valgrind':'/usr/bin/valgrind.bin'},
          'resources':{'valgrind/'+name:'/usr/libexec/valgrind/'+name for name in (
              'memcheck-amd64-linux','vgpreload_core-amd64-linux.so','vgpreload_memcheck-amd64-linux.so','default.supp')}}
    assert canonical(spec)==spec
    with pytest.raises(ValueError,match='reviewed'):
        canonical({**spec,'resources':{**spec['resources'],'valgrind/task.py':'/app/task.py'}})
