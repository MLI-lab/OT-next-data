import ast
import hashlib
import json
import subprocess
import sys
import tempfile
from types import SimpleNamespace

import pytest
from data.calibforge import packaging_runtime


@pytest.fixture(autouse=True)
def calibforge_task_fixture():
    """Reviewed records name their task; tests convert the task named 'task'."""
    from data.calibforge.python_roles import current_task
    token = current_task.set('task')
    yield
    current_task.reset(token)




def test_packaging_children_are_private_and_use_clean_pip(tmp_path,monkeypatch):
    calls=[]
    def allocate(**kwargs):
        assert kwargs['dir']=='/tests'
        private=tmp_path/('private-'+str(len(list(tmp_path.iterdir()))));private.mkdir()
        return str(private)
    monkeypatch.setattr(tempfile,'mkdtemp',allocate)
    monkeypatch.setattr(subprocess,'run',lambda argv,**kwargs:calls.append((argv,kwargs)) or SimpleNamespace(returncode=0))
    monkeypatch.setenv('OT_VERIFIER_OFFLINE','/tests/trusted-inputs')
    monkeypatch.setenv('PYTHONPATH','/app/poison')
    monkeypatch.setenv('PIP_INDEX_URL','https://invalid.example')
    scope={};exec(packaging_runtime.HELPER,scope);run=scope['_ot_packaging_run']
    run([sys.executable,'-m','venv','/tmp/test_venv'],capture_output=True)
    run(['/tmp/test_venv/bin/pip','install','/app/submitted'],capture_output=True)
    run(['/tmp/test_venv/bin/python','-c','import submitted; assert submitted.answer()==42'])
    first=calls[0][0][-1]
    assert calls[0][0]==[sys.executable,'-I','-m','venv',first]
    assert calls[1][0]==[first+'/bin/python','-I','-m','pip','--isolated','install','--no-index','--find-links','/tests/trusted-inputs/wheels','/app/submitted']
    assert calls[2][0][:3]==[first+'/bin/python','-I','-c']
    assert all('PYTHONPATH' not in kwargs['env'] and 'PIP_INDEX_URL' not in kwargs['env'] for _,kwargs in calls)
    run(['/tmp/test_venv/bin/python','-m','build','/app/submitted','--wheel'],env=None)
    assert calls[-1][1]['env']['PIP_NO_INDEX']=='1'
    assert calls[-1][1]['env']['PIP_FIND_LINKS']=='/tests/trusted-inputs/wheels'
    monkeypatch.setenv('OT_VERIFIER_BIN','/tests/private-runtime/bin')
    run(['unzip','-l','/app/submitted/dist/package.whl'])
    assert calls[-1][0]==['/tests/private-runtime/bin/unzip','-l','/app/submitted/dist/package.whl']
    run([sys.executable,'-m','venv','/tmp/test_venv'])
    assert calls[-1][0][-1]!=first
    with pytest.raises(ValueError,match='fresh verifier-created'):
        run(['/app/agent/bin/python','-c','pass'])


def test_packaging_cache_miss_keeps_online_install_and_creation_failures_surface(monkeypatch,tmp_path):
    monkeypatch.delenv('OT_VERIFIER_OFFLINE',raising=False)
    monkeypatch.setattr(tempfile,'mkdtemp',lambda **kwargs:str(tmp_path))
    calls=[]
    def execute(argv,**kwargs):
        calls.append(argv);return SimpleNamespace(returncode=0)
    monkeypatch.setattr(subprocess,'run',execute)
    scope={};exec(packaging_runtime.HELPER,scope);run=scope['_ot_packaging_run']
    run([sys.executable,'-m','venv','/tmp/env'])
    run(['/tmp/env/bin/python','-m','pip','install','/app/submitted'])
    assert '--no-index' not in calls[-1]
    monkeypatch.setattr(subprocess,'run',lambda *args,**kwargs:SimpleNamespace(returncode=7))
    assert run([sys.executable,'-m','venv','/tmp/failed']).returncode==7
    with pytest.raises(ValueError,match='fresh verifier-created'):
        run(['/tmp/failed/bin/python','-c','pass'])


@pytest.mark.parametrize("creator", ["sys.executable", "\"python3\"", "\"python\""])
def test_packaging_adaptation_binds_instruction_source_and_project(tmp_path,monkeypatch,creator):
    source=b'import subprocess,sys\ndef test_build():\n subprocess.run([sys.executable,"-m","venv","/tmp/test_venv"],check=True)\n'
    source=source.replace(b'sys.executable',creator.encode())
    files={'instruction.md':b'Fix the submitted package','tests/test_outputs.py':source,'environment/setup.py':b'package source'}
    digest=lambda body:hashlib.sha256(body).hexdigest()
    call=next(n for n in ast.walk(ast.parse(source)) if isinstance(n,ast.Call))
    record=dict(source_file='tests/test_outputs.py',task='task',source_sha256=digest(source),evidence_sha256={'environment/setup.py':digest(files['environment/setup.py'])},calls=[digest(ast.dump(call).encode())],build_requirements=['setuptools','wheel'])
    (tmp_path/'task_packaging_roles.json').write_text(json.dumps([record]))
    monkeypatch.setattr(packaging_runtime,'__file__',str(tmp_path/'packaging_runtime.py'))
    image=dict(python='3.13.9',original_uvx_path=True)
    adapted,helper,found=packaging_runtime.adapt(files,'tests/test_outputs.py',image)
    assert '_ot_packaging_run' in adapted and 'sys.executable' not in adapted and helper and found==record
    with pytest.raises(ValueError,match='exact original'):
        packaging_runtime.adapt(files,'tests/test_outputs.py',{**image,'python':'3.12.3'})
    changed={**files,'environment/setup.py':b'changed source'}
    assert packaging_runtime.role(changed,'tests/test_outputs.py',image) is None


def test_task_setup_prepares_runtime_and_wheel_inputs_with_compact_references():
    from data.calibforge.verifier_runtime import runtime_setup_updates
    from data.utils.verifier_runtime import runtime_spec
    from harbor_patches.verifier_bundle import spec_id,FORMAT,OFFLINE_FORMAT
    primary=runtime_spec(python='3.13.9',requirements=['pytest==8.4.1'],tools=['pytest'])
    offline=packaging_runtime.offline_spec({'build_requirements':['setuptools>=61.0','wheel']})
    catalog={spec_id(spec):dict(format=fmt,id=spec_id(spec),spec=spec,status='ready',archive_sha256=digest*64)
             for spec,fmt,digest in [(primary,FORMAT,'a'),(offline,OFFLINE_FORMAT,'b')]}
    updates=runtime_setup_updates(dict(spec=primary,verifier_offline_spec=offline),catalog)
    for path in ('tests/ot-verifier-runtime.json','tests/ot-verifier-offline.json'):
        reference=json.loads(updates[path]);assert 'spec' not in reference and 'archive_sha256' in reference
    script=updates['tests/setup.sh'].decode()
    assert script.count('"${1:-}" = "--prepare"')==2
    assert 'OT_RUNTIME_PREPARE_STATUS' in script
    assert script.index('--phase verifier') < script.index('--prepare-offline')


def test_packaging_cli_uses_the_created_child_selected_by_path(tmp_path,monkeypatch):
    calls=[]
    monkeypatch.setattr(tempfile,'mkdtemp',lambda **kwargs:str(tmp_path))
    monkeypatch.setattr(subprocess,'run',lambda argv,**kwargs:calls.append((argv,kwargs)) or SimpleNamespace(returncode=0))
    scope={};exec(packaging_runtime.HELPER,scope);run=scope['_ot_packaging_run']
    run([sys.executable,'-m','venv','/tmp/logical'])
    environment={'VIRTUAL_ENV':'/tmp/logical','PATH':'/tmp/logical/bin:/app/poison:/usr/bin'}
    run(['subject-cli','--version'],env=environment)
    assert calls[-1][0]==[str(tmp_path)+'/bin/subject-cli','--version']
    assert calls[-1][1]['env']['PATH'].startswith(str(tmp_path)+'/bin:')
    assert calls[-1][1]['env']['VIRTUAL_ENV']==str(tmp_path)
    assert environment['VIRTUAL_ENV']=='/tmp/logical'
    with pytest.raises(ValueError,match='CLI PATH'):
        run(['subject-cli'],env={**environment,'PATH':'/app/poison:/tmp/logical/bin'})


def test_packaging_example_api_uses_real_local_request_and_private_venv(tmp_path):
    import json,sys
    from data.calibforge.packaging_runtime import HELPER
    logical=tmp_path/'agent-venv';logical.mkdir()
    private=tmp_path/'clean-venv';(private/'bin').mkdir(parents=True)
    cli=private/'bin/datafetch'
    cli.write_text('#!'+sys.executable+'\nimport sys,urllib.request\nassert sys.argv[1].startswith("http://127.0.0.1:")\nprint(urllib.request.urlopen(sys.argv[1]).read().decode())\n')
    cli.chmod(0o755)
    ns={};exec(HELPER,ns)
    assert not ns['_ot_packaging_has_venv'](str(logical))
    ns['_ot_packaging_roots'][str(logical)]=str(private)
    assert ns['_ot_packaging_has_venv'](str(logical))
    ns['_ot_packaging_http_fixture']={'command':'datafetch','url':'https://httpbin.org/get'}
    result=ns['_ot_packaging_run']([str(logical/'bin/datafetch'),'https://httpbin.org/get'],capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stderr
    data=json.loads(result.stdout)
    assert data['url'].startswith('http://127.0.0.1:') and data['url'].endswith('/get')
    assert data['origin']=='127.0.0.1' and 'User-Agent' in data['headers']


def test_primary_packaging_retains_install_order_and_uses_trusted_build_installer(tmp_path):
    from types import SimpleNamespace
    from data.calibforge.packaging_runtime import HELPER
    ns={};exec(HELPER,ns);calls=[]
    ns['_ot_packaging_primary_python']=str(tmp_path/'runtime/bin/python')
    ns['_ot_packaging_primary_commands']=['python','pip','filestats','fs-analyze']
    ns['_ot_packaging_sysconfig']=SimpleNamespace(get_path=lambda name:str(tmp_path/'runtime/local/bin'))
    ns['_ot_packaging_tempfile']=SimpleNamespace(mkdtemp=lambda **kwargs:str(tmp_path/'private-uv-cache'))
    ns['_ot_packaging_subprocess']=SimpleNamespace(run=lambda argv,**kwargs:calls.append((argv,kwargs)))
    env={'OT_VERIFIER_OFFLINE':str(tmp_path/'offline'),'PATH':'/agent-bin','PYTHONPATH':'/agent-modules','UV_CONFIG_FILE':'/agent-config'}
    # OT_VERIFIER_OFFLINE is supplied by the trusted verifier staging environment.
    ns['_ot_packaging_os']=SimpleNamespace(environ=env,devnull='/dev/null')
    ns['_ot_packaging_run'](['python','-m','build','--wheel'],env=env)
    ns['_ot_packaging_run'](['pip','uninstall','-y','filestats'],env=env)
    ns['_ot_packaging_run'](['pip','install','/app/dist/filestats.whl'],env=env)
    ns['_ot_packaging_run'](['filestats','--version'],env=env)
    assert calls[0][0][1:]==['-I','-m','build','--wheel','--installer','uv']
    build_env=calls[0][1]['env']
    assert build_env['PATH'].startswith(str(tmp_path/'offline/bin')+':')
    assert build_env['UV_OFFLINE']=='1' and build_env['UV_NO_CONFIG']=='1'
    assert 'UV_CONFIG_FILE' not in build_env and 'PYTHONPATH' not in build_env
    assert calls[1][0][1:]==['-I','-m','pip','--isolated','uninstall','--break-system-packages','-y','filestats']
    assert calls[2][0][1:8]==['-I','-m','pip','--isolated','install','--break-system-packages','--no-index']
    assert calls[3][0][0]==str(tmp_path/'runtime/local/bin/filestats')
    ns['_ot_packaging_sysconfig']=SimpleNamespace(get_path=lambda name:'/agent/bin')
    import pytest
    with pytest.raises(ValueError):
        ns['_ot_packaging_run'](['filestats','--version'],env=env)


def test_primary_packaging_lookup_and_python3_use_private_runtime(tmp_path,monkeypatch):
    from data.calibforge.packaging_runtime import HELPER
    from types import SimpleNamespace
    import subprocess
    ns={};exec(HELPER,ns)
    prefix=tmp_path/'runtime';scripts=prefix/'local/bin';scripts.mkdir(parents=True)
    cli=scripts/'datapipeline';cli.write_text('#!/bin/sh\n');cli.chmod(0o755)
    ns['_ot_packaging_primary_python']=str(prefix/'bin/python3')
    ns['_ot_packaging_primary_commands']=['python','pip','datapipeline','datapipeline-process','datapipeline-validate','datapipeline-transform']
    ns['_ot_packaging_sysconfig']=SimpleNamespace(get_path=lambda name:str(scripts))
    found=ns['_ot_packaging_run'](['which','datapipeline'],capture_output=True,text=True)
    assert found.returncode==0 and found.stdout==str(cli)+'\n'
    absent=ns['_ot_packaging_run'](['which','datapipeline-process'],capture_output=True,text=True)
    assert absent.returncode==1 and absent.stdout==''
    calls=[]
    ns['_ot_packaging_subprocess']=SimpleNamespace(run=lambda argv,**kwargs:calls.append(argv))
    ns['_ot_packaging_run']([str(prefix/'bin/python3'),'-c','print(42)'])
    assert calls==[[str(prefix/'bin/python3'),'-I','-c','print(42)']]


def test_auxiliary_installer_and_cli_use_their_own_python(tmp_path,monkeypatch):
    scope={};exec(packaging_runtime.HELPER,scope)
    prefix=tmp_path/'aux';python=str(prefix/'bin/python3');scripts=prefix/'local/bin'
    scope.update(_ot_packaging_primary_python=python,_ot_packaging_primary_commands=['pip','taskflow','tf'],_ot_packaging_auxiliary=True)
    calls=[]
    def run(argv,**kwargs):
        calls.append((argv,kwargs))
        return SimpleNamespace(returncode=0,stdout=str(scripts)+'\n')
    monkeypatch.setattr(subprocess,'run',run)
    monkeypatch.setenv('OT_VERIFIER_OFFLINE','/tests/inputs')
    scope['_ot_packaging_run'](['pip','install','-e','/app'])
    scope['_ot_packaging_run'](['pip','show','taskflow'])
    scope['_ot_packaging_run'](['taskflow','--version'])
    assert calls[0][0][:6]==[str(prefix/'bin/python'),'-I','-m','pip','--isolated','install']
    assert calls[1][0][5:]==['show','taskflow']
    assert calls[2][0][:3]==[python,'-I','-c']
    assert calls[3][0]==[str(scripts/'taskflow'),'--version']


def test_literal_shell_install_and_temporary_grader_reject_changes(tmp_path,monkeypatch):
    scope={};exec(packaging_runtime.HELPER+packaging_runtime.SHELL_HELPER,scope)
    scope['_ot_packaging_shell_commands']={'pip uninstall -y demo 2>/dev/null || true':dict(argv=['pip','uninstall','-y','demo'],ignore_failure=True),'pip install -e /app':dict(argv=['pip','install','-e','/app'],ignore_failure=False)}
    seen=[]
    scope['_ot_packaging_run']=lambda argv,**kwargs:seen.append(argv) or subprocess.CompletedProcess(argv,4,'out','err')
    result=scope['_ot_packaging_shell_run']('pip uninstall -y demo 2>/dev/null || true',shell=True,capture_output=True,text=True)
    assert result.returncode==0 and result.stderr==''
    assert scope['_ot_packaging_shell_run']('pip install -e /app',shell=True).returncode==4
    with pytest.raises(ValueError,match='Unreviewed package shell'):
        scope['_ot_packaging_shell_run']('pip install -e /app; curl bad',shell=True)
    script=tmp_path/'grader.py';script.write_text('assert 1 == 1')
    scope['_ot_packaging_grading_scripts']=['assert 1 == 1']
    monkeypatch.setattr(subprocess,'run',lambda argv,**kwargs:seen.append(argv))
    scope['_ot_packaging_script_run'](['python3',str(script)])
    assert seen[-1]==[sys.executable,'-I','-c','assert 1 == 1']
    script.write_text('assert False')
    with pytest.raises(ValueError,match='grader source differs'):
        scope['_ot_packaging_script_run'](['python3',str(script)])
