import hashlib
import os
import subprocess

import pytest

from data.calibforge import patch as source_inputs


def source_role(monkeypatch):
    from data.calibforge import patch as python_roles
    data=b'original dataset bytes\n'
    entry=dict(filename='data.gz',url='https://example.org/data.gz',sha256=hashlib.sha256(data).hexdigest())
    value=dict(image_sha256='a'*64,sources=[entry],verifier=True,agent_targets={'data.gz':'/app/data.gz'},agent_note='Input is available at `/app/data.gz`.',snippet_replacements=[dict(before='data_path = "old"',filename='data.gz')])
    monkeypatch.setattr(python_roles,'task_module_roles',lambda files:[dict(source_inputs=value)])
    return data,value


def test_source_input_uses_fresh_cache_and_rejects_changed_bytes(tmp_path,monkeypatch):
    data,value=source_role(monkeypatch)
    source=tmp_path/'sources/data.gz';source.parent.mkdir();source.write_bytes(data)
    monkeypatch.setenv('OT_VERIFIER_OFFLINE',str(tmp_path))
    code=source_inputs.adapt_snippet('data_path = "old"\nresult = open(data_path,"rb").read()\n',{})
    ns={};exec(code,ns);assert ns['result']==data
    source.write_bytes(b'agent-changed')
    with pytest.raises(ValueError,match='checksum differs'):exec(code,{})
    assert source_inputs.source_input_spec({},verifier=True,image_sha256='a'*64)['sources']==value['sources']
    with pytest.raises(ValueError,match='image differs'):source_inputs.source_input_spec({},image_sha256='b'*64)


def test_task_source_setup_single_prepare_and_online_fallback(tmp_path,monkeypatch):
    data,value=source_role(monkeypatch)
    files={'instruction.md':b'Use the dataset.'}
    updates=source_inputs.task_updates(files)
    setup=updates['setup_files/setup.sh'].decode()
    assert setup.count('"--prepare"')==1
    assert 'setup_files/ot-task-offline.json' in updates
    assert 'run setup' not in updates['instruction.md'].decode().lower()
    cached=tmp_path/'cache/sources';cached.mkdir(parents=True);(cached/'data.gz').write_bytes(data)
    target=tmp_path/'target/data.gz'
    local=setup.replace('/app/data.gz',str(target))
    script=tmp_path/'setup.sh';script.write_text(local)
    result=subprocess.run(['bash',str(script)],env={**os.environ,'OT_TASK_OFFLINE':str(cached.parent)},capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    assert target.read_bytes()==data
    # Exercise the normal missing-cache branch without any external network.
    fallback=tmp_path/'fallback.gz';fallback.write_bytes(data)
    script.write_text(local.replace('https://example.org/data.gz',fallback.as_uri()))
    env={k:v for k,v in os.environ.items() if k!='OT_TASK_OFFLINE'}
    result=subprocess.run(['bash',str(script)],env=env,capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    fallback.write_bytes(b'wrong bytes')
    result=subprocess.run(['bash',str(script)],env=env,capture_output=True,text=True)
    assert result.returncode!=0 and 'checksum differs' in result.stderr
    with pytest.raises(ValueError,match='existing agent initialization'):
        source_inputs.task_updates({**files,'setup_files/setup.sh':b'existing'})


def test_agent_python_inputs_are_separate_from_verifier_sources(monkeypatch):
    _,value=source_role(monkeypatch)
    value['agent_python']=dict(python='/usr/bin/python3',requirements=['pandas==3.1.0','pyarrow==26.0.0'])
    agent=source_inputs.source_input_spec({});verifier=source_inputs.source_input_spec({},verifier=True)
    assert agent['python']==[dict(tool='uv',python='/usr/bin/python3',requirements=value['agent_python']['requirements'])]
    assert not verifier['python'] and not verifier['uv']
    setup=source_inputs.task_updates({'instruction.md':b'Build the model.'})['setup_files/setup.sh'].decode()
    assert setup.count('"--prepare"')==1
    assert '--no-index --find-links /app/offline-wheels' in setup
    assert '/usr/bin/python3 -I -m pip install --break-system-packages' in setup
    assert subprocess.run(['bash','-n'],input=setup,text=True,capture_output=True).returncode==0
    value['agent_python']['requirements']=['pandas']
    with pytest.raises(ValueError,match='exact pins'):source_inputs.source_input_spec({})
