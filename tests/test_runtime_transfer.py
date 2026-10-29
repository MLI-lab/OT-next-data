import asyncio
import base64
import hashlib
import io
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace
import pytest
from harbor_patches import runtime_transfer as transfer
from harbor_patches import upload_runtime

TARGET='/tests/.ot-verifier-runtime-Ab123/runtime.tar.gz'

def fixture(tmp_path,monkeypatch):
    cache=tmp_path/'cache';cache.mkdir()
    monkeypatch.setenv('OT_RUNTIME_CACHE',str(cache));monkeypatch.setenv('OT_RUNTIME_CACHE_TRANSFER','1')
    data=b'original cached artifact'*1000
    ref={'id':'a'*64,'archive_sha256':hashlib.sha256(data).hexdigest()}
    source=cache/(ref['id']+'.tar.gz');source.write_bytes(data)
    staging=tmp_path/'staging';(staging/'workspace').mkdir(parents=True)
    instance=SimpleNamespace(staging_dir=str(staging),instance_name='test')
    calls=[]
    def run(command,**kwargs):
        calls.append(command)
        script=command[-1].replace('/workspace/',str(staging/'workspace')+'/').replace('/tests/',str(tmp_path/'tests')+'/')
        return subprocess.run(['/bin/bash','-c',script],**kwargs)
    worker=SimpleNamespace(APPTAINER='apptainer',subprocess=SimpleNamespace(run=run))
    return ref,source,instance,worker,calls

@pytest.mark.parametrize('state',['ready','missing','corrupt','symlink','fifo','disabled'])
def test_worker_fresh_copy_or_safe_miss(tmp_path,monkeypatch,state):
    ref,source,instance,worker,calls=fixture(tmp_path,monkeypatch)
    if state=='missing':source.unlink()
    if state=='corrupt':source.write_bytes(b'bad bytes')
    if state=='symlink':
        other=source.with_suffix('.outside');source.rename(other);source.symlink_to(other)
    if state=='fifo':source.unlink();os.mkfifo(source)
    if state=='disabled':monkeypatch.delenv('OT_RUNTIME_CACHE_TRANSFER')
    result=transfer.receive(instance,{'runtime_cache_upload':ref,'target_path':TARGET},worker)
    if state=='ready':
        dest=tmp_path/'tests/.ot-verifier-runtime-Ab123/runtime.tar.gz'
        assert dest.read_bytes()==source.read_bytes()
        assert result['archive_sha256']==ref['archive_sha256'] and len(calls)==1
        dest.write_bytes(b'agent mutation')
        assert hashlib.sha256(source.read_bytes()).hexdigest()==ref['archive_sha256']
    else:
        assert result['runtime_cache_miss'] is True and calls==[]
    assert not list((Path(instance.staging_dir)/'workspace').iterdir())

@pytest.mark.parametrize('ref,target',[
 ({'id':'../etc/passwd','archive_sha256':'a'*64},TARGET),
 ({'id':'a'*64,'archive_sha256':'bad'},TARGET),
 ({'id':'a'*64,'archive_sha256':'b'*64},'/etc/passwd'),
 ({'id':'a'*64,'archive_sha256':'b'*64},'/tests/.ot-verifier-runtime-X/../../escape')])
def test_reject_unbound_paths(ref,target):
    with pytest.raises(ValueError):transfer.identity(ref,target)

@pytest.mark.parametrize('server,worker_state',[('old','ready'),('new','old'),('new','missing'),('new','ready')])
def test_protocol_ack_and_ordinary_fallback(tmp_path,monkeypatch,server,worker_state):
    ref,source,receiver,worker,calls=fixture(tmp_path,monkeypatch)
    results={};requests=[];fallback=[]
    async def get(url):return {'runtime_cache_upload_protocol':1} if server=='new' else {}
    async def post(url,payload):
        requests.append(payload)
        if worker_state=='old':result={'return_code':0}
        elif worker_state=='missing':result={'return_code':0,'runtime_cache_upload_protocol':1,'runtime_cache_miss':True}
        else:result=transfer.receive(receiver,payload,worker)
        results['job']=result;return {'job_id':'job'}
    async def poll(*args,**kwargs):return results['job']
    bridge=SimpleNamespace(_async_http_get=get,_async_http_post=post,_poll_job=poll)
    class Environment:
        _bridge_url='http://bridge';_env_id='env'
        async def upload_runtime_archive(self,ref,target):return await transfer.request(bridge,self,ref,target)
        async def upload_file(self,**kwargs):fallback.append(kwargs);return {'ordinary':True}
        def _check_transfer_result(self,op,result):assert result['return_code']==0
    result=asyncio.run(transfer.upload(Environment(),source,ref,TARGET))
    if server=='new' and worker_state=='ready':assert result['transport']=='worker-cache-copy' and not fallback
    else:assert result=={'ordinary':True} and len(fallback)==1
    assert len(requests)==int(server=='new')


def test_default_never_requests_worker_cache(tmp_path,monkeypatch):
    monkeypatch.delenv('OT_RUNTIME_CACHE_TRANSFER',raising=False)
    class Environment:
        async def upload_runtime_archive(self,*args):pytest.fail('Opt-in required')
        async def upload_file(self,**kwargs):return kwargs
    assert asyncio.run(transfer.upload(Environment(),'archive',{},'target'))=={'source_path':'archive','target_path':'target'}


def test_existing_spool_never_removed_or_overwritten(tmp_path,monkeypatch):
    ref,source,instance,worker,calls=fixture(tmp_path,monkeypatch)
    monkeypatch.setattr(transfer.uuid,'uuid4',lambda:SimpleNamespace(hex='fixed'))
    path=Path(instance.staging_dir)/'workspace/.upload-fixed';path.write_bytes(b'existing bytes')
    with pytest.raises(FileExistsError):
        transfer.receive(instance,{'runtime_cache_upload':ref,'target_path':TARGET},worker)
    assert path.read_bytes()==b'existing bytes' and not calls


def test_install_failure_not_misreported_as_cache_miss(tmp_path,monkeypatch):
    ref,source,instance,worker,calls=fixture(tmp_path,monkeypatch)
    worker.subprocess.run=lambda *a,**k:SimpleNamespace(returncode=41,stdout='',stderr='failed install')
    result=transfer.receive(instance,{'runtime_cache_upload':ref,'target_path':TARGET},worker)
    assert result['return_code']==41 and not result.get('runtime_cache_miss')
    assert not list((Path(instance.staging_dir)/'workspace').iterdir())
