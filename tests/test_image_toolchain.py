import io
import json
from pathlib import Path
import tarfile
from types import SimpleNamespace
import pytest
from hpc.offline_prep import image_toolchain as tool
from harbor_patches.verifier_bundle import digest,spec_id


def spec(**kwargs):
    return tool.toolchain_spec(**dict(kind='go',version='1.26.3',root='/opt/go-1.26',image_sha256='a'*64,**kwargs))

@pytest.mark.parametrize('change',[{'kind':'python'},{'version':'1.26'},{'version':'latest'},
 {'root':'/etc'},{'root':'/opt/../etc'},{'root':'/root/go'},{'architecture':'unknown'},
 {'overlay_sha256':'b'*64},{'image_sha256':'bad'}])
def test_reject_nonexact_or_unbound_spec(change):
    args=dict(kind='go',version='1.26.3',root='/opt/go-1.26',image_sha256='a'*64);args.update(change)
    with pytest.raises(ValueError):tool.toolchain_spec(**args)


def test_canonical_exact_version_and_complete_sidecars():
    value=spec(overlay_sha256='b'*64,deferred_sha256='c'*64)
    assert tool.canonical(value)==value
    assert tool.expected_version(value)=='go version go1.26.3 linux/amd64'
    with pytest.raises(ValueError):tool.canonical(dict(value,python='3.12'))
    with pytest.raises(ValueError):tool.canonical(dict(value,layout=2))

@pytest.mark.parametrize('bad',['none','version','external-link'])
def test_builder_source_binding_relocation_and_exclusive_cache(tmp_path,monkeypatch,bad):
    from hpc.offline_prep import image_python_runtime as image
    source=tmp_path/'source.sif';source.write_bytes(b'pristine image')
    value=tool.toolchain_spec(kind='go',version='1.26.3',root='/opt/go-1.26',image_sha256=digest(source))
    monkeypatch.setenv('TMPDIR',str(tmp_path))
    monkeypatch.setattr(image,'command',lambda image,scratch,*args:list(args))
    calls=[]
    def run(command,**kw):
        calls.append(command)
        if command[0]=='readlink':text='/opt/go-1.26'
        elif command[0]=='tar':
            with tarfile.open(command[2],'w') as tf:
                item=tarfile.TarInfo('bin/go');item.mode=0o755;item.size=5;tf.addfile(item,io.BytesIO(b'tool!'))
                if bad=='external-link':
                    link=tarfile.TarInfo('escape');link.type=tarfile.SYMTYPE;link.linkname='/etc/passwd';tf.addfile(link)
            text=''
        else:text='go version go1.26.2 linux/amd64' if bad=='version' else 'go version go1.26.3 linux/amd64'
        return SimpleNamespace(returncode=0,stdout=text,stderr='')
    monkeypatch.setattr(tool.subprocess,'run',run)
    cache=tmp_path/'cache'
    if bad!='none':
        with pytest.raises((ValueError,tarfile.TarError)):tool.build(value,source,cache)
        assert not list(cache.iterdir())
        return
    record=tool.build(value,source,cache)
    assert record['format']==tool.FORMAT and record['relocated_version']==record['observed_version']
    archive=cache/(record['id']+'.tar.gz')
    assert digest(archive)==record['archive_sha256']
    count=len(calls)
    assert tool.build(value,source,cache)==record and len(calls)==count
    # Corrupt existing bytes cannot be overwritten through a second preparation.
    archive.write_bytes(b'conflicting')
    with pytest.raises(ValueError):tool.build(value,source,cache)
    assert archive.read_bytes()==b'conflicting'
