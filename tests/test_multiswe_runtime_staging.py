"""Large package inputs use private node-local storage and strict cleanup paths."""
import asyncio
from types import SimpleNamespace
import pytest
from harbor_patches.runtime_transfer import identity
from harbor_patches import task_runtime


def test_private_node_local_archive_transfer_identity():
    ref={'id':'a'*64,'archive_sha256':'b'*64}
    identity(ref,'/tmp/.ot-task-offline-Abc123/packages.tar.gz')
    for target in ('/tmp/packages.tar.gz','/tmp/.ot-task-offline-Abc123/../../outside',
                   '/tmp/.ot-task-offline-Abc123/runtime.tar.gz'):
        with pytest.raises(ValueError):identity(ref,target)


def test_node_local_input_cleanup_still_stops_environment():
    calls=[]
    class Environment:
        async def exec(self,command,**kwargs):
            calls.append(command)
            return SimpleNamespace(return_code=0,stdout='',stderr='')
        async def stop(self,**kwargs):calls.append('stop')
    env=Environment();task_runtime.ensure_cleanup(env)
    task_runtime.retain(env,'/tmp/.ot-task-offline-Abc123')
    asyncio.run(env.stop())
    assert calls[-1]=='stop'
    assert 'rm -rf -- /tmp/.ot-task-offline-Abc123' in calls[0]
    assert 'find -P' in calls[0]
