import hashlib
import io
import tarfile
import pytest
from hpc.offline_prep.offline_sources import prepare,validate,verify_staged
from harbor_patches.verifier_bundle import validate_offline_archive


def source(data=b'source archive'):
    return dict(url='https://example.org/source.tar.gz',filename='source.tar.gz',sha256=hashlib.sha256(data).hexdigest())


def test_local_input_requires_exact_bytes_without_network(tmp_path,monkeypatch):
    local=tmp_path/'local';local.mkdir();(local/'source.tar.gz').write_bytes(b'source archive')
    monkeypatch.setenv('OT_RUNTIME_SOURCE_FILES',str(local))
    records=prepare([source()],tmp_path/'out',opener=lambda *a,**k:pytest.fail('network'))
    assert records==[{**source(),'bytes':14,'verified':True}]
    verify_staged(records,tmp_path/'out')
    (tmp_path/'out/sources/source.tar.gz').write_bytes(b'changed')
    with pytest.raises(ValueError,match='Staged'):verify_staged(records,tmp_path/'out')
    (local/'source.tar.gz').write_bytes(b'bad')
    with pytest.raises(ValueError,match='checksum'):prepare([source()],tmp_path/'bad')


def test_fetches_original_url_and_checks_download_before_use(tmp_path,monkeypatch):
    monkeypatch.delenv('OT_RUNTIME_SOURCE_FILES',raising=False)
    calls=[]
    def fetch(url,**kwargs):calls.append(url);return io.BytesIO(b'source archive')
    prepare([source()],tmp_path,opener=fetch)
    assert calls==[source()['url']]
    with pytest.raises(FileExistsError):prepare([source()],tmp_path,opener=fetch)


@pytest.mark.parametrize('changes',[{'filename':'../bad'},{'url':'http://example.org/file'},{'url':'https://user:secret@example.org/file'},{'sha256':'bad'}])
def test_rejects_unsafe_sources(changes):
    with pytest.raises(ValueError):validate([{**source(),**changes}])


def test_duplicate_names_and_source_archive_layout(tmp_path):
    with pytest.raises(ValueError):validate([source(),source()])
    archive=tmp_path/'inputs.tar.gz'
    with tarfile.open(archive,'w:gz') as output:
        for name in ('repo/Packages','sources/source.tar.gz'):
            entry=tarfile.TarInfo(name);entry.size=1;output.addfile(entry,io.BytesIO(b'x'))
    validate_offline_archive(archive)
