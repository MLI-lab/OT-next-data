import ast
import copy
import hashlib
import json

import pytest

from data.calibforge import verifier_runtime as runtime
from validation.contract import files_digest


from contextlib import contextmanager


@contextmanager
def as_task(name):
    """Convert another task than the one the reviewed record names."""
    from data.calibforge.python_roles import current_task
    token = current_task.set(name)
    try:
        yield
    finally:
        current_task.reset(token)


@pytest.fixture(autouse=True)
def calibforge_task_fixture():
    """Reviewed records name their task; tests convert the task named 'task'."""
    from data.calibforge.python_roles import current_task
    token = current_task.set('task')
    yield
    current_task.reset(token)



def fixture():
    source='''import sys,subprocess

def select():
    return sys.executable

def test_roles():
    py = select()
    subprocess.run([py,"/app/task.py"])
    subprocess.run([py,"-c","import json; print(1)"])
'''
    files={'tests/test.sh':runtime.SCRIPT,'tests/test_outputs.py':source.encode()}
    definition=next(n for n in ast.parse(source).body if isinstance(n,ast.FunctionDef) and n.name=='select')
    calls=[n for n in ast.walk(ast.parse(source)) if isinstance(n,ast.Call) and ast.unparse(n.func)=='subprocess.run']
    child=dict(kind='python_selector',returncode=0,sha256='a'*64,source_file='tests/test_outputs.py',
        source_sha256=hashlib.sha256(source.encode()).hexdigest(),selector='select',selector_code=ast.get_source_segment(source,definition),
        test_script_sha256=hashlib.sha256(runtime.SCRIPT).hexdigest(),
        uses=[dict(line=c.lineno,column=c.col_offset,role='task' if i==0 else 'grading') for i,c in enumerate(calls)],
        observed=dict(python='3.12.3',executable='/usr/bin/python3',resolved_executable='/usr/bin/python3.12',prefix='/usr',requirements=[],stdlib=['sys','json','os']))
    observed=dict(returncode=0,sha256='a'*64,probe_invocation='auto-opt',auxiliary={'metrics':child},
        observed=dict(python='3.13.9',executable='/opt/verifier/bin/python',resolved_executable='/opt/uv/python/cpython-3.13.9-linux-x86_64-gnu/bin/python3.13',prefix='/opt/verifier',requirements=['pytest==8.4.1'],stdlib=['sys','subprocess','os']))
    binding=dict(content_sha256=files_digest(files.items()),environments=[dict(path='environment',bundle='image',environment_sha256='b'*64)])
    bundle=dict(name='image.sif',files={'image.sif':{'sha256':'a'*64}})
    return files,observed,binding,bundle


def test_mixed_selector_keeps_task_python_but_uses_clean_exact_grader():
    files,observed,binding,bundle=fixture()
    planned=runtime.measured_native_plan(files,observed,binding,bundle)
    assert planned['spec']['python']=='3.13.9'
    assert planned['spec']['auxiliary']['metrics']['python']=='3.12.3'
    assert '/../aux/metrics/bin/python3' in planned['test_source']
    assert '["/usr/bin/python3","/app/task.py"]' in planned['test_source']
    assert ', "-I","-c","import json; print(1)"' in planned['test_source']
    assert 'return sys.executable' not in planned['test_source']
    assert planned['content_sha256']==binding['content_sha256']
    assert [u['role'] for u in planned['auxiliary_evidence']['metrics']['uses']]==['task','grading']


def test_auxiliary_role_binding_rejects_changed_source_and_unreviewed_uses():
    files,observed,binding,bundle=fixture()
    broken=copy.deepcopy(observed);broken['auxiliary']['metrics']['source_sha256']='b'*64
    with pytest.raises(ValueError,match='source binding'):
        runtime.measured_native_plan(files,broken,binding,bundle)
    files['tests/test_outputs.py']+=b'    print(py)\n'
    binding['content_sha256']=files_digest(files.items())
    observed['auxiliary']['metrics']['source_sha256']=hashlib.sha256(files['tests/test_outputs.py']).hexdigest()
    with pytest.raises(ValueError,match='unreviewed auxiliary Python alias'):
        runtime.measured_native_plan(files,observed,binding,bundle)


def test_direct_selector_calls_keep_per_invocation_roles():
    files,observed,binding,bundle=fixture()
    source=files['tests/test_outputs.py'].decode().replace('    py = select()\n','').replace('[py,','[select(),')
    files['tests/test_outputs.py']=source.encode()
    binding['content_sha256']=files_digest(files.items())
    child=observed['auxiliary']['metrics'];child['source_sha256']=hashlib.sha256(source.encode()).hexdigest()
    calls=[n for n in ast.walk(ast.parse(source)) if isinstance(n,ast.Call) and ast.unparse(n.func)=='subprocess.run']
    child['uses']=[dict(line=n.lineno,column=n.col_offset,role='task' if i==0 else 'grading') for i,n in enumerate(calls)]
    planned=runtime.measured_native_plan(files,observed,binding,bundle)
    assert '["/usr/bin/python3","/app/task.py"]' in planned['test_source']
    assert '/../aux/metrics/bin/python3' in planned['test_source']
    child['uses'].pop()
    with pytest.raises(ValueError,match='unreviewed direct'):
        runtime.measured_native_plan(files,observed,binding,bundle)


def test_shell_task_launch_uses_fresh_helper_and_preserves_task_exit(tmp_path):
    import os,subprocess,sys
    from data.calibforge.python_roles import shell_task_launcher
    files={'instruction.md':b'Write /app/recover.py to recover the input.'}
    path,source,command,proof=shell_task_launcher('python3 /app/recover.py --fast',files)
    assert path.startswith('tests/') and 'OT_VERIFIER_PYTHON' in command
    assert proof['task_program']=='/app/recover.py'
    helper=tmp_path/'helper.py';helper.write_text(source)
    tools=tmp_path/'bin';tools.mkdir();task_python=tools/'python3'
    task_python.write_text('#!/bin/sh\nprintf "TASK:%s:%s\\n" "$1" "$2"\nexit 17\n');task_python.chmod(0o755)
    result=subprocess.run([sys.executable,'-I',str(helper)],env={**os.environ,'PATH':str(tools)},capture_output=True,text=True)
    assert result.returncode==17 and result.stdout=='TASK:/app/recover.py:--fast\n'
    assert shell_task_launcher('python3 -c "import pytest"',files) is None
    assert shell_task_launcher('python3 /app/unknown.py',files) is None
    assert shell_task_launcher('python3 /app/recover.py && echo pass',files) is None


def test_uvx_plan_preserves_task_launch_without_container_python_entrypoint():
    files={'instruction.md':b'Write /app/recover.py.',
           'tests/test.sh':b'#!/bin/bash\npython3 /app/recover.py || true\nuvx -p 3.13 -w pytest==8.4.1 pytest /tests/test_outputs.py\n'}
    planned=runtime.uvx_plan(files,python='3.13.9')
    assert '\npython3 ' not in planned['test_script']
    assert ' || true' in planned['test_script']
    assert len(planned['test_sources'])==1
    assert planned['task_shell_calls'][0]['original_command']=='python3 /app/recover.py || true'


def test_background_task_launcher_preserves_subprocess_cwd(tmp_path):
    import ast
    import os
    import subprocess
    import sys
    from data.calibforge.python_roles import shell_task_launcher
    files={'instruction.md':b'Fix /app/app.py.'}
    path,source,command,proof=shell_task_launcher('cd /app && python3 app.py &',files)
    assert command.endswith(' &') and 'python3 app.py' not in command
    assert 'os.chdir(\'/app\')' in source
    assert proof['task_program']=='/app/app.py' and proof['background'] is True
    ast.parse(source)
    assert shell_task_launcher('cd /app && python3 grader.py &',files) is None
    assert shell_task_launcher('cd /app && source /app/venv/bin/activate && python3 app.py &',files) is None
    task_dir=tmp_path/'task';task_dir.mkdir()
    parent_dir=tmp_path/'parent';parent_dir.mkdir()
    helper=tmp_path/'helper.py';helper.write_text(source.replace('/app',str(task_dir)))
    task_python=tmp_path/'python3'
    task_python.write_text('#!/bin/sh\npwd > "$TASK_MARKER"\nexit 17\n');task_python.chmod(0o755)
    run=command.replace('/'+path,str(helper))+'\npid=$!\npwd > "$PARENT_MARKER"\nwait "$pid"\n'
    result=subprocess.run(['bash','-c',run],cwd=parent_dir,env={**os.environ,
        'OT_VERIFIER_PYTHON':sys.executable,'PATH':str(tmp_path)+':'+os.environ['PATH'],
        'TASK_MARKER':str(tmp_path/'task-cwd'),'PARENT_MARKER':str(tmp_path/'parent-cwd')})
    assert result.returncode==17
    assert (tmp_path/'task-cwd').read_text().strip()==str(task_dir)
    assert (tmp_path/'parent-cwd').read_text().strip()==str(parent_dir)


def test_literal_grader_uses_independently_measured_python():
    files,observed,binding,bundle=fixture()
    source='import subprocess\ndef test_value():\n    subprocess.run(["python3","-c","import json; print(json.dumps(42))"])\n'
    files['tests/test_outputs.py']=source.encode();binding['content_sha256']=files_digest(files.items())
    call=next(n for n in ast.walk(ast.parse(source)) if isinstance(n,ast.Call) and ast.unparse(n.func)=='subprocess.run')
    child=observed['auxiliary']['metrics']
    child.update(kind='literal_python',probe_invocation='python3',source_sha256=hashlib.sha256(source.encode()).hexdigest(),
        uses=[dict(line=call.lineno,column=call.col_offset,role='grading',call_sha256=hashlib.sha256(ast.get_source_segment(source,call).encode()).hexdigest())])
    planned=runtime.measured_native_plan(files,observed,binding,bundle)
    assert planned['spec']['python']=='3.13.9'
    assert planned['spec']['auxiliary']['metrics']['python']=='3.12.3'
    assert '/../aux/metrics/bin/python3' in planned['test_source']
    assert ', "-I","-c"' in planned['test_source']
    broken=copy.deepcopy(observed);broken['auxiliary']['metrics']['uses'][0]['call_sha256']='b'*64
    with pytest.raises(ValueError,match='binding differs'):
        runtime.measured_native_plan(files,broken,binding,bundle)


def test_literal_package_presence_probe_is_not_automatically_a_clean_grader():
    from data.calibforge.python_roles import adapt_literal
    source='import subprocess\nsubprocess.run(["python3","-c","import target_package"])\n'
    files={'tests/test.sh':runtime.SCRIPT,'tests/test_outputs.py':source.encode()}
    call=next(n for n in ast.walk(ast.parse(source)) if isinstance(n,ast.Call))
    record=dict(source_file='tests/test_outputs.py',source_sha256=hashlib.sha256(source.encode()).hexdigest(),probe_invocation='python3',
        uses=[dict(line=call.lineno,column=call.col_offset,role='grading',call_sha256=hashlib.sha256(ast.get_source_segment(source,call).encode()).hexdigest())])
    with pytest.raises(ValueError,match='installation check'):
        adapt_literal(files,record,'child',dict(python='3.12.3',executable='/usr/bin/python3',stdlib=[]))
    files['instruction.md']=b'Build and install the `target_package` Python package from source.'
    _,edits,_,modules,_=adapt_literal(files,record,'child',dict(python='3.12.3',executable='/usr/bin/python3',prefix='/usr',stdlib=[]))
    assert any('InstalledTaskFinder' in value for _,_,value in edits)
    assert not modules  # The submitted package must not enter the dependency cache.


def test_installation_role_is_bound_to_task_call_and_interpreter(monkeypatch):
    from data.calibforge import python_roles
    source='import subprocess\nPYTHON="python3"\nsubprocess.run([PYTHON,"-c","import target_package"],check=False)\n'
    call=next(n for n in ast.walk(ast.parse(source)) if isinstance(n,ast.Call))
    files={'instruction.md':b'Fix the target_package installation.'}
    record=dict(task='task',
                call_ast_sha256=hashlib.sha256(ast.dump(call,include_attributes=False).encode()).hexdigest(),
                executables=['python3'],role='Evaluate the task installation')
    monkeypatch.setattr(python_roles,'installation_roles',lambda:[record])
    assert runtime.task_python_calls(source,files=files)[0]==source
    for changed in (source.replace('python3','/opt/other/python3'),
                    source.replace('import target_package','import target_package; assert False'),
                    source.replace('check=False','check=True')):
        with pytest.raises(ValueError,match='literal nested Python'):
            runtime.task_python_calls(changed,files=files)
    with as_task('other'), pytest.raises(ValueError,match='literal nested Python'):
        runtime.task_python_calls(source,files=files)
    # An unrelated child rewrite does not broaden the reviewed call's scope.
    assert runtime.task_python_calls(source+'\n# another reviewed child\n',files=files)[0].startswith(source)


def test_literal_child_versioned_module_without_top_level_metadata_is_copied_once():
    files,observed,binding,bundle=fixture()
    source='import subprocess\ndef test_value():\n    subprocess.run(["python3","-c","import numpy; print(numpy.sum([1,2]))"])\n'
    files['tests/test_outputs.py']=source.encode();binding['content_sha256']=files_digest(files.items())
    call=next(n for n in ast.walk(ast.parse(source)) if isinstance(n,ast.Call) and ast.unparse(n.func)=='subprocess.run')
    child=observed['auxiliary']['metrics']
    child['observed'].update(requirements=['numpy==1.26.4'],import_names=['numpy'],
                            unversioned_module_origins={'numpy':'/usr/lib/python3/dist-packages/numpy'})
    child.update(kind='literal_python',probe_invocation='python3',source_sha256=hashlib.sha256(source.encode()).hexdigest(),
        uses=[dict(line=call.lineno,column=call.col_offset,role='grading',call_sha256=hashlib.sha256(ast.get_source_segment(source,call).encode()).hexdigest())])
    planned=runtime.measured_native_plan(files,observed,binding,bundle)
    image=planned['spec']['auxiliary']['metrics']['image_python']
    assert image['packages']=={'numpy==1.26.4':['numpy']}
    assert 'numpy' not in image.get('modules',{})


def test_literal_child_pythonpath_exposes_only_submitted_module():
    from data.calibforge.python_roles import adapt_literal
    source='import os,subprocess\nsubprocess.run(["python3","-c","import solver; print(solver.solve())"],env={**os.environ,"PYTHONPATH":"/app"})\n'
    files={'instruction.md':b'Fix /app/solver.py.', 'tests/test.sh':runtime.SCRIPT,'tests/test_outputs.py':source.encode()}
    call=next(n for n in ast.walk(ast.parse(source)) if isinstance(n,ast.Call))
    record=dict(source_file='tests/test_outputs.py',source_sha256=hashlib.sha256(source.encode()).hexdigest(),probe_invocation='python3',
        uses=[dict(line=call.lineno,column=call.col_offset,role='grading',call_sha256=hashlib.sha256(ast.get_source_segment(source,call).encode()).hexdigest())])
    measured=dict(python='3.12.3',executable='/usr/bin/python3',prefix='/usr',stdlib=['os'])
    _,edits,_,_,_=adapt_literal(files,record,'child',measured)
    replacement='\n'.join(value for _,_,value in edits)
    assert 'solver' in replacement and 'OT_VERIFIER_BIN' in replacement and '"-I"' in replacement
    changed=source.replace('"PYTHONPATH":"/app"','"LD_PRELOAD":"/app/evil.so"')
    files['tests/test_outputs.py']=changed.encode();record['source_sha256']=hashlib.sha256(changed.encode()).hexdigest()
    with pytest.raises(ValueError,match='environment requires review'):
        adapt_literal(files,record,'child',measured)


def test_dynamic_helper_reviews_every_caller_and_rejects_aliases():
    from data.calibforge.python_roles import adapt_literal
    source='''import subprocess
def run_code(code):
    return subprocess.run(["python3","-c",code],capture_output=True,text=True)
def test_one(value):
    code=f"print({value!r})"
    return run_code(code)
def test_two(value):
    return run_code(f"print('prefix:{value}:suffix')")
'''
    files={'tests/test.sh':runtime.SCRIPT,'tests/test_outputs.py':source.encode()}
    call=next(n for n in ast.walk(ast.parse(source)) if isinstance(n,ast.Call) and ast.unparse(n.func)=='subprocess.run')
    record=dict(source_file='tests/test_outputs.py',source_sha256=hashlib.sha256(source.encode()).hexdigest(),probe_invocation='python3',
        uses=[dict(line=call.lineno,column=call.col_offset,role='grading',call_sha256=hashlib.sha256(ast.get_source_segment(source,call).encode()).hexdigest())])
    measured=dict(python='3.12.3',executable='/usr/bin/python3',prefix='/usr',stdlib=['os'])
    _,edits,helper,_,_=adapt_literal(files,record,'child',measured)
    assert any('_ot_checked_snippet_choices(code' in value for _,_,value in edits)
    namespace={};exec(helper,namespace)
    # Use nodes belonging to the same tree, just as production does.
    tree=ast.parse(source);call=next(n for n in ast.walk(tree) if isinstance(n,ast.Call) and ast.unparse(n.func)=='subprocess.run')
    choices=runtime.grading_helper_templates(call.args[0].elts[2],tree)
    options=[(template,template,holes) for template,holes in choices]
    assert "print({'x': 3})"==namespace['_ot_checked_snippet_choices']("print({'x':3})",options)
    with pytest.raises(ValueError,match='reviewed caller'):
        namespace['_ot_checked_snippet_choices']("print(1); import os",options)
    tree=ast.parse(source+'\nalias=run_code\n');call=next(n for n in ast.walk(tree) if isinstance(n,ast.Call) and ast.unparse(n.func)=='subprocess.run')
    assert runtime.grading_helper_templates(call.args[0].elts[2],tree) is None


def test_service_launch_exception_requires_both_original_source_bindings(monkeypatch):
    from data.calibforge import python_roles
    files={'instruction.md':b'Test the running HTTP service.', 'tests/test.sh':b'python3 /app/service.py &\n'}
    line='python3 /app/service.py &'
    record=dict(line=line,task='task',
                test_script_sha256=hashlib.sha256(files['tests/test.sh']).hexdigest(),role='Task service')
    monkeypatch.setattr(python_roles,'service_roles',lambda:[record])
    result=python_roles.shell_task_launcher(line,files)
    assert result and 'os.execvp' in result[1] and result[3]['service_role']==record
    with as_task('other'):
        assert python_roles.shell_task_launcher(line,files) is None
    assert python_roles.shell_task_launcher(line,{**files,'tests/test.sh':b'Changed script'}) is None


def test_named_task_module_role_requires_its_task_and_unchanged_source(monkeypatch):
    import ast
    import hashlib
    from data.calibforge import python_roles, verifier_runtime
    files={'instruction.md':b'Recover the task repository.', 'environment/files/init.sh':b'create module'}
    record={'task':'task',
            'modules':['recovered'], 'evidence_sha256':{'environment/files/init.sh':hashlib.sha256(files['environment/files/init.sh']).hexdigest()}}
    monkeypatch.setattr(python_roles,'module_roles',lambda:[record])
    source=ast.parse('import recovered')
    image={'stdlib':[],'implicit_task_root':'/app'}
    assert verifier_runtime.review_imports(source,files,image)[1]==['recovered']
    with as_task('other'), pytest.raises(ValueError,match='recovered'):
        verifier_runtime.review_imports(source,files,image)
    with pytest.raises(ValueError,match='recovered'):
        verifier_runtime.review_imports(source,{**files,'environment/files/init.sh':b'changed'},image)
    with pytest.raises(ValueError,match='unrelated'):
        verifier_runtime.review_imports(ast.parse('import unrelated'),files,image)


def test_service_module_launcher_requires_complete_source_binding(monkeypatch):
    import hashlib
    from data.calibforge import python_roles
    line='cd /app && python3 -m service.app &'
    files={'instruction.md':b'Repair service', 'tests/test.sh':line.encode()}
    role={'line':line,'task':'task',
          'test_script_sha256':hashlib.sha256(files['tests/test.sh']).hexdigest()}
    monkeypatch.setattr(python_roles,'service_roles',lambda:[role])
    result=python_roles.shell_task_launcher(line,files)
    assert result and "['python3', '-m', 'service.app']" in result[1]
    assert result[2].endswith(' &')
    with as_task('other'):
        assert python_roles.shell_task_launcher(line,files) is None
    assert python_roles.shell_task_launcher(line.replace('service.app','unrelated'),files) is None


def test_task_initialization_does_not_authorize_other_script_or_task(monkeypatch):
    import hashlib
    from data.calibforge import python_roles
    files={'instruction.md':b'Repair nginx', 'tests/test.sh':b'nginx -g "daemon off;" &'}
    role={'task':'task',
          'test_script_sha256':hashlib.sha256(files['tests/test.sh']).hexdigest(),
          'lines':['nginx -g "daemon off;" &']}
    monkeypatch.setattr(python_roles,'initialization_roles',lambda:[role])
    assert python_roles.task_initialization_roles(files)==[role]
    assert python_roles.task_initialization_roles({**files,'tests/test.sh':files['tests/test.sh']+b'\nextra'})==[]
    with as_task('other'):
        assert python_roles.task_initialization_roles(files)==[]


def test_source_bound_service_virtualenv_uses_environment_without_activate(monkeypatch):
    import builtins
    import hashlib
    from types import SimpleNamespace
    from data.calibforge import python_roles
    line='cd /app && source /app/venv/bin/activate && python /app/app.py &'
    files={'instruction.md':b'Repair Flask app', 'tests/test.sh':line.encode()}
    role={'line':line,'virtualenv':'/app/venv','task':'task',
          'test_script_sha256':hashlib.sha256(files['tests/test.sh']).hexdigest()}
    monkeypatch.setattr(python_roles,'service_roles',lambda:[role])
    result=python_roles.shell_task_launcher(line,files)
    assert result and 'activate' not in result[1] and 'activate' not in result[2]
    captured=[]
    env={'PATH':'/usr/bin','PYTHONHOME':'/poison'}
    fake=SimpleNamespace(environ=env,chdir=lambda cwd:captured.append(cwd),execvp=lambda exe,argv:captured.append((exe,argv)))
    custom={**vars(builtins),'__import__':lambda name,*args,**kw:fake if name=='os' else builtins.__import__(name,*args,**kw)}
    exec(result[1],{'__builtins__':custom})
    assert env['PATH']=='/app/venv/bin:/usr/bin' and env['VIRTUAL_ENV']=='/app/venv' and 'PYTHONHOME' not in env
    assert captured==['/app',('python',['python','/app/app.py'])]
    assert python_roles.shell_task_launcher(line,{**files,'tests/test.sh':b'changed'}) is None


@pytest.mark.parametrize("canonical", [False, True])
def test_global_selector_uses_clean_helper_script_and_task_interpreter(canonical):
    files,observed,binding,bundle=fixture()
    source='''import sys,subprocess

def select():
    return sys.executable

_PYTHON = select()

def test_roles():
    subprocess.run([_PYTHON,"/app/task.py"])
    subprocess.run([_PYTHON,"/tests/golden.py"])
'''
    files['tests/test_outputs.py']=source.encode()
    files['tests/golden.py']=b'import json\nprint(json.dumps({"expected":3}))\n'
    child=observed['auxiliary']['metrics']
    child['source_sha256']=hashlib.sha256(source.encode()).hexdigest()
    calls=[n for n in ast.walk(ast.parse(source)) if isinstance(n,ast.Call) and ast.unparse(n.func)=='subprocess.run']
    child['uses']=[dict(line=c.lineno,column=c.col_offset,role='task' if i==0 else 'grading_script') for i,c in enumerate(calls)]
    binding['content_sha256']=files_digest(files.items())
    if canonical:
        primary=observed['observed']
        primary['requirements']=['pytest==8.4.1','pytest-json-ctrf==0.3.5']
        image=dict(return_code=0,sif_sha256='a'*64,environment_sha256='b'*64,bundle='image',
                   version=primary['python'],python3=['/opt/verifier/bin/python3',primary['resolved_executable']],
                   stdlib=primary['stdlib'])
        probe=dict(returncode=0,sif_sha256='a'*64,stdout='OT_RUNTIME_PROBE='+json.dumps(primary))
        planned=runtime.plan(files,image,probe,binding,bundle,auxiliary=observed['auxiliary'])
    else:
        planned=runtime.measured_native_plan(files,observed,binding,bundle)
    assert '["/usr/bin/python3","/app/task.py"]' in planned['test_source']
    assert ', "-E", "-s","/tests/golden.py"]' in planned['test_source']
    assert 'tests/golden.py' in planned['auxiliary_helpers']
    assert 'json' not in planned['imports']
    files['tests/test_outputs.py']+=b'\ndef another(_PYTHON):\n    return _PYTHON\n'
    child['source_sha256']=hashlib.sha256(files['tests/test_outputs.py']).hexdigest()
    binding['content_sha256']=files_digest(files.items())
    with pytest.raises(ValueError,match='global auxiliary Python alias'):
        runtime.measured_native_plan(files,observed,binding,bundle)


def test_task_installation_check_keeps_original_python_with_full_source_binding(monkeypatch):
    from data.calibforge import python_roles
    source='import sys,subprocess\nsubprocess.run([sys.executable,"-c","import submitted; print(submitted.__version__)"])\n'
    files={'instruction.md':b'Install submitted.', 'tests/test_outputs.py':source.encode()}
    call=next(n for n in ast.walk(ast.parse(source)) if isinstance(n,ast.Call))
    record=dict(task='task',
        source_sha256=hashlib.sha256(source.encode()).hexdigest(),
        call_ast_sha256=hashlib.sha256(ast.dump(call,include_attributes=False).encode()).hexdigest(),
        executables=['sys.executable'])
    monkeypatch.setattr(python_roles,'installation_roles',lambda:[record])
    updated,count=runtime.task_python_calls(source,'/usr/bin/python3.12',files=files,image={'stdlib':['sys','subprocess']})
    assert count==1 and '"/usr/bin/python3.12"' in updated and '"-I"' not in updated
    changed=source+'\nprint("changed")\n'
    with pytest.raises(ValueError,match='package imports'):
        runtime.task_python_calls(changed,'/usr/bin/python3.12',files={**files,'tests/test_outputs.py':changed.encode()},image={'stdlib':['sys','subprocess']})


def test_dynamic_extension_loader_binds_the_entire_directory_search(monkeypatch):
    from data.calibforge import python_roles
    source='import importlib.util,glob\ndef load():\n    paths=glob.glob("/app/solver*.so")\n    return importlib.util.spec_from_file_location("solver",paths[0])\n'
    function=ast.parse(source).body[1]
    files={'instruction.md':b'Build solver in /app.', 'tests/test_outputs.py':source.encode()}
    role=dict(task='task',evidence_sha256={},modules=['solver'],
        extension_loader_functions=[hashlib.sha256(ast.dump(function,include_attributes=False).encode()).hexdigest()])
    monkeypatch.setattr(python_roles,'module_roles',lambda:[role])
    runtime.review_imports(ast.parse(source),files,{'stdlib':['importlib','glob']})
    with pytest.raises(ValueError,match='dynamic task-module'):
        runtime.review_imports(ast.parse(source.replace('/app/solver','/tmp/solver')),files,{'stdlib':['importlib','glob']})


def test_selector_dynamic_child_grader_checks_ast_before_launch(monkeypatch):
    from data.calibforge.python_roles import adapt_selector
    from types import SimpleNamespace
    import subprocess
    source='''import sys,subprocess

def select():
    return sys.executable

def run(code):
    py=select()
    return subprocess.run([py,'-c',code])

def exercise(value):
    return run(f'import json; print({value!r})')
'''
    tree=ast.parse(source);definition=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='select')
    call=next(n for n in ast.walk(tree) if isinstance(n,ast.Call) and ast.unparse(n.func)=='subprocess.run')
    files={'tests/test_outputs.py':source.encode()}
    record=dict(source_file='tests/test_outputs.py',source_sha256=hashlib.sha256(source.encode()).hexdigest(),
                selector='select',selector_code=ast.get_source_segment(source,definition),uses=[dict(line=call.lineno,column=call.col_offset,role='grading')])
    measured=dict(python='3.12.3',prefix='/usr',executable='/usr/bin/python3',stdlib=['sys','json','subprocess','os'])
    _,adapted,_=adapt_selector(files,record,'metrics',measured)
    calls=[]
    monkeypatch.setenv('OT_VERIFIER_BIN','/tests/private/bin')
    monkeypatch.setattr(subprocess,'run',lambda argv:calls.append(argv) or SimpleNamespace(returncode=0))
    namespace={};exec(adapted,namespace)
    namespace['exercise']({'safe':[1,2]})
    assert len(calls)==1 and calls[0][:3]==['/tests/private/bin/../aux/metrics/bin/python3','-I','-c']
    with pytest.raises(ValueError):namespace['run']('import os; print(3)')
    assert len(calls)==1


def test_dynamic_submitted_import_is_source_bound_and_keeps_dependencies_clean(tmp_path, monkeypatch):
    import ast
    import hashlib
    import subprocess
    import sys
    from data.calibforge import python_roles as roles
    source = 'import importlib\nname="submitted"\nmod=importlib.import_module(name)\nassert mod.answer==42\n'
    files = {'instruction.md': b'Create the ctypes binding in /app'}
    call = next(n for n in ast.walk(ast.parse(source)) if isinstance(n,ast.Call))
    record = dict(task='task',
                  evidence_sha256={}, modules=[],test_source_sha256=hashlib.sha256(source.encode()).hexdigest(),
                  dynamic_import_calls=[hashlib.sha256(ast.dump(call,include_attributes=False).encode()).hexdigest()])
    monkeypatch.setattr(roles,'module_roles',lambda:[record])
    adapted, helper = roles.adapt_dynamic_task_imports(source,files)
    assert 'mod=_ot_import_submitted_module(name)' in adapted
    assert roles.adapt_dynamic_task_imports(source+'\n',files)==(source+'\n','')
    with as_task('other'):
        assert roles.adapt_dynamic_task_imports(source,files)==(source,'')
    (tmp_path/'submitted.py').write_text('import json\nanswer=json.loads("42")\n')
    (tmp_path/'json.py').write_text('raise RuntimeError("agent dependency poison")\n')
    helper=helper.replace("'/app'",repr(str(tmp_path)))
    result=subprocess.run([sys.executable,'-I','-c',helper+'\n'+adapted],capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    (tmp_path/'submitted.py').unlink()
    (tmp_path/'submitted.py').symlink_to(tmp_path.parent/'outside.py')
    (tmp_path.parent/'outside.py').write_text('raise RuntimeError("outside executed")\n')
    result=subprocess.run([sys.executable,'-I','-c',helper+'\n'+adapted],capture_output=True,text=True)
    assert result.returncode and 'escaped /app' in result.stderr and 'outside executed' not in result.stderr


def test_generated_grader_uses_clean_python_and_checks_written_content(tmp_path, monkeypatch):
    import ast
    import hashlib
    import subprocess
    import sys
    from data.calibforge import python_roles as roles
    source = '''import tempfile, subprocess
from pathlib import Path

def run_script(content, timeout=10):
    with tempfile.NamedTemporaryFile(mode='w',suffix='.py',delete=False) as f:
        f.write(content)
        path=f.name
    try:
        return subprocess.run(['python3',path],capture_output=True,text=True,timeout=timeout)
    finally:
        Path(path).unlink()
def test_script():
    result=run_script("import json; assert json.loads('42')==42; print('passed')",timeout=10)
    assert result.returncode==0,result.stderr
    assert result.stdout.strip()=='passed'
'''
    tree=ast.parse(source)
    digest=lambda n:hashlib.sha256(ast.dump(n,include_attributes=False).encode()).hexdigest()
    writes=[digest(n) for n in ast.walk(tree) if isinstance(n,ast.Call) and ast.unparse(n.func)=='f.write']
    runs=[digest(n) for n in ast.walk(tree) if isinstance(n,ast.Call) and ast.unparse(n.func)=='subprocess.run']
    files={'instruction.md':b'Repair the worker pool'}
    record=dict(task='task',evidence_sha256={},
                test_source_sha256=hashlib.sha256(source.encode()).hexdigest(),modules=[],
                generated_script_write_calls=writes,generated_script_run_calls=runs,generated_script_cwd='/app')
    monkeypatch.setattr(roles,'module_roles',lambda:[record])
    adapted,helper=roles.adapt_generated_scripts(source,files,{'stdlib':['json']})
    (tmp_path/'python3').write_text('#!/bin/sh\nexit 97\n');(tmp_path/'python3').chmod(0o755)
    (tmp_path/'json.py').write_text('raise RuntimeError("poisoned json")\n')
    result=subprocess.run([sys.executable,'-I','-c',adapted+helper+'\ntest_script()\n'],
                          env={'PATH':str(tmp_path),'PYTHONPATH':str(tmp_path)},capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    script=tmp_path/'changed.py';script.write_text('print("unreviewed")\n')
    result=subprocess.run([sys.executable,'-I','-c',helper+'\n_ot_generated_run(["ot-clean-python",'+repr(str(script))+'])'],capture_output=True,text=True)
    assert result.returncode and 'content changed' in result.stderr and 'unreviewed' not in result.stdout
    assert roles.adapt_generated_scripts(source+'\n',files,{'stdlib':['json']})==(source+'\n','')


def test_project_suite_is_restored_to_private_tests_and_uses_clean_pytest(tmp_path, monkeypatch):
    import subprocess
    import sys
    from data.calibforge import python_roles as roles
    source="import subprocess\nresult=subprocess.run(['python3','-m','pytest','/app/test_project.py','-q'],capture_output=True,text=True)\n"
    body=b'import json\ndef test_correct(): assert json.loads("42")==42\n'
    files={'instruction.md':b'Fix the library','environment/files/test_project.py':body}
    call=next(n for n in ast.walk(ast.parse(source)) if isinstance(n,ast.Call))
    record=dict(task='task',
                test_source_sha256=hashlib.sha256(source.encode()).hexdigest(),modules=[],
                evidence_sha256={'environment/files/test_project.py':hashlib.sha256(body).hexdigest()},
                project_test_calls=[hashlib.sha256(ast.dump(call,include_attributes=False).encode()).hexdigest()],
                project_test_sources={'/app/test_project.py':dict(source_file='environment/files/test_project.py',import_root='/app')})
    monkeypatch.setattr(roles,'module_roles',lambda:[record])
    adapted,helper=roles.adapt_project_tests(source,files,{'stdlib':['json']})
    helper=helper.replace("dir='/tests'",'dir='+repr(str(tmp_path)))
    (tmp_path/'json.py').write_text('raise RuntimeError("agent json")\n')
    (tmp_path/'conftest.py').write_text('raise RuntimeError("agent conftest")\n')
    script=helper+'\n'+adapted+'\nassert result.returncode==0,result.stderr+result.stdout\n'
    result=subprocess.run([sys.executable,'-I','-c',script],cwd=tmp_path,capture_output=True,text=True,
                          env={**__import__('os').environ,'PYTHONPATH':str(tmp_path),'PYTEST_ADDOPTS':'--invalid-agent-option','PYTEST_PLUGINS':'agent_plugin'})
    assert result.returncode==0,result.stderr
    assert not list(tmp_path.glob('.ot-project-tests-*'))
    changed={**files,'environment/files/test_project.py':b'assert False'}
    assert roles.adapt_project_tests(source,changed,{'stdlib':['json']})==(source,'')


def test_literal_extension_environment_is_bound_to_source_image_and_python(monkeypatch):
    from data.calibforge import python_roles as roles
    source='import os,subprocess\nenv={**os.environ,"LD_LIBRARY_PATH":"/app/local/lib","PYTHONPATH":"/app"}\nsubprocess.run(["/app/venv/bin/python","-c","import solver; assert solver.answer==42"],env=env)\n'
    files={'instruction.md':b'Build /app/solver.py','tests/test_outputs.py':source.encode(),'tests/test.sh':runtime.SCRIPT}
    call=next(n for n in ast.walk(ast.parse(source)) if isinstance(n,ast.Call))
    record=dict(sha256='a'*64,source_file='tests/test_outputs.py',source_sha256=hashlib.sha256(source.encode()).hexdigest(),probe_invocation='/app/venv/bin/python',
                uses=[dict(line=call.lineno,column=call.col_offset,role='grading',call_sha256=hashlib.sha256(ast.get_source_segment(source,call).encode()).hexdigest())])
    role=dict(task='task',test_source_sha256=record['source_sha256'],evidence_sha256={},modules=['solver'],
              literal_environment=dict(image_sha256='a'*64,python='3.12.3',executable='/app/venv/bin/python',variable='env',task_root='/app',modules=['solver'],roots=['/app','/app/venv/lib/python3.12/site-packages']))
    monkeypatch.setattr(roles,'module_roles',lambda:[role])
    measured=dict(python='3.12.3',executable='/app/venv/bin/python',prefix='/app/venv',stdlib=[])
    _,edits,_,_,_=roles.adapt_literal(files,record,'child',measured)
    replacements='\n'.join(value for _,_,value in edits)
    assert 'solver' in replacements and '/app/venv/lib/python3.12/site-packages' in replacements and '"-I"' in replacements
    with pytest.raises(ValueError,match='environment requires review'):
        roles.adapt_literal(files,{**record,'sha256':'b'*64},'child',measured)
    with pytest.raises(ValueError,match='environment requires review'):
        roles.adapt_literal(files,record,'child',{**measured,'python':'3.13.9'})


def test_selector_task_package_install_remains_separate_from_clean_assertions():
    files,observed,binding,bundle=fixture()
    source=files['tests/test_outputs.py'].decode().replace('[py,"/app/task.py"]','[py,"-m","pip","install","/app/project"]')
    files['tests/test_outputs.py']=source.encode()
    files['environment/files/project/setup.py']=b'from setuptools import setup\nsetup(name="subject")\n'
    binding['content_sha256']=files_digest(files.items())
    child=observed['auxiliary']['metrics'];child['source_sha256']=hashlib.sha256(source.encode()).hexdigest()
    child['uses'][0]['role']='task_package'
    planned=runtime.measured_native_plan(files,observed,binding,bundle)
    assert '["/usr/bin/python3","-m","pip","install","/app/project"]' in planned['test_source']
    assert '/../aux/metrics/bin/python3' in planned['test_source']
    changed=source.replace('"/app/project"','"pytest"');files['tests/test_outputs.py']=changed.encode()
    binding['content_sha256']=files_digest(files.items());child['source_sha256']=hashlib.sha256(changed.encode()).hexdigest()
    with pytest.raises(ValueError,match='task package command'):
        runtime.measured_native_plan(files,observed,binding,bundle)


def test_copied_helpers_restore_only_bound_bytes_and_preserve_clean_imports(monkeypatch):
    from data.calibforge import python_roles as roles, verifier_runtime as runtime
    source=b'import subprocess\nresult=subprocess.run(["python3", "/tests/worker_check.py"])\n'
    helper=b'import json\nassert json.loads("42")==42\n'
    files={'instruction.md':b'Repair worker', 'tests/test_outputs.py':source,
           'environment/files/worker_check.py':helper}
    record=dict(task='task',
                evidence_sha256={'environment/files/worker_check.py':hashlib.sha256(helper).hexdigest()},
                modules=[],verifier_helper_sources={'tests/worker_check.py':'environment/files/worker_check.py'})
    monkeypatch.setattr(roles,'module_roles',lambda:[record])
    reviewed=runtime.review_test_sources(files,{'stdlib':['json','subprocess','sys'],
                                               'original_native_invocation':'python3'})
    assert reviewed['test_sources']['tests/worker_check.py']==helper.decode()
    assert 'sys.executable, "-E", "-s"' in reviewed['test_source']
    assert 'tests/worker_check.py' not in files
    changed={**files,'environment/files/worker_check.py':b'raise RuntimeError("changed")'}
    assert 'tests/worker_check.py' not in roles.restore_verifier_helpers(changed)
    with pytest.raises(ValueError,match='conflicts'):
        roles.restore_verifier_helpers({**files,'tests/worker_check.py':b'changed'})


def test_valgrind_child_is_reviewed_as_clean_grading_python(monkeypatch):
    from data.calibforge import python_roles as roles, verifier_runtime as runtime
    text='import subprocess, sys\nsubprocess.run(["valgrind", "--leak-check=full", "--error-exitcode=99", sys.executable, "-c", "import json; assert json.loads(\'42\')==42"], capture_output=True)\n'
    call=next(n for n in ast.walk(ast.parse(text)) if isinstance(n,ast.Call))
    files={'instruction.md':b'Fix memory management','tests/test_outputs.py':text.encode()}
    role=dict(task='task',evidence_sha256={},modules=[],
              test_source_sha256=hashlib.sha256(text.encode()).hexdigest(),valgrind_call_ast_sha256=hashlib.sha256(ast.dump(call).encode()).hexdigest())
    monkeypatch.setattr(roles,'module_roles',lambda:[role])
    adapted,helper=roles.adapt_wrapped_grading(text,files)
    reviewed,calls=runtime.task_python_calls(adapted,files=files,image={'stdlib':['json']})
    assert calls==1 and 'sys.executable, "-I",' in reviewed
    assert 'VALGRIND_LIB' in helper and '--error-exitcode=99' in helper
    assert roles.adapt_wrapped_grading(text+'\n',files)==(text+'\n','')
    altered=dict(role,valgrind_call_ast_sha256='0'*64)
    monkeypatch.setattr(roles,'module_roles',lambda:[altered])
    with pytest.raises(ValueError,match='binding differs'):roles.adapt_wrapped_grading(text,files)


def test_mixed_stdin_grader_reviews_each_literal_caller(monkeypatch):
    from data.calibforge import python_roles as roles
    text='''import sys,subprocess
def run_python_script(script_path,timeout=30,input_data=None):
    return subprocess.run([sys.executable,script_path],input=input_data,capture_output=True,text=True,timeout=timeout)
def test_literal():
    code='import json; assert json.loads("42")==42'
    return run_python_script('/dev/stdin',input_data=code)
def test_file():
    return run_python_script('/app/stress.py')
'''
    function=next(n for n in ast.parse(text).body if isinstance(n,ast.FunctionDef))
    record={'modules':[],'test_source_sha256':hashlib.sha256(text.encode()).hexdigest(),
            'stdin_grading_helper':{'function':'run_python_script','function_ast_sha256':hashlib.sha256(ast.dump(function).encode()).hexdigest(),
                                    'scripts':{'/app/stress.py':'/tests/stress.py'}}}
    monkeypatch.setattr(roles,'task_module_roles',lambda files:[record])
    files={'tests/stress.py':b'assert True'}
    adapted,helper=roles.adapt_stdin_grading(text,files,{'original_uvx_path':True,'stdlib':['json']})
    namespace={};exec(adapted+helper,namespace)
    result=namespace['test_literal']()
    assert result.returncode==0,result.stderr
    with pytest.raises(KeyError):namespace['run_python_script']('/dev/stdin',input_data='raise RuntimeError("injected")')
    with pytest.raises(ValueError,match='fresh supplied'):
        roles.adapt_stdin_grading(text,{}, {'original_uvx_path':True,'stdlib':['json']})
    assert '/tests/stress.py' in helper


def test_pristine_package_suite_uses_fresh_relative_helpers_and_submitted_package(tmp_path,monkeypatch):
    import os,site
    from data.calibforge import python_roles as roles
    source='import sys,subprocess\ndef grade():\n return subprocess.run([sys.executable,"-m","pytest","/app/widget/widget/tests","-v"],capture_output=True,text=True)\n'
    call=next(n for n in ast.walk(ast.parse(source)) if isinstance(n,ast.Call))
    original={'widget/tests/__init__.py':'','widget/tests/checks.py':'from .. import answer\ndef check(): assert answer==42\n',
              'widget/tests/test_value.py':'import importlib.resources\nfrom .checks import check\ndef test_value():\n check()\n ref=importlib.resources.files("widget") / "test_images" / "data.fits.fz"\n assert ref.read_bytes()==b"fixture"\n'}
    record={'modules':['widget'],'test_source_sha256':hashlib.sha256(source.encode()).hexdigest(),
            'pristine_suite':{'package':'widget','image_sha256':'a'*64,
                 'files':{name:dict(text=body,bytes=len(body.encode()),sha256=hashlib.sha256(body.encode()).hexdigest()) for name,body in original.items()},
                 'targets':['/app/widget/widget/tests'],'call_ast_sha256':hashlib.sha256(ast.dump(call).encode()).hexdigest()}}
    monkeypatch.setattr(roles,'task_module_roles',lambda files:[record])
    import base64
    record['pristine_suite']['files']['widget/test_images/data.fits.fz']=dict(
        base64=base64.b64encode(b'fixture').decode(),bytes=7,sha256=hashlib.sha256(b'fixture').hexdigest())
    prefix=tmp_path/'installed';package=__import__('pathlib').Path(site.getsitepackages([str(prefix)])[0])/'widget';package.mkdir(parents=True)
    (package/'__init__.py').write_text('answer=42\n')
    (package/'tests').mkdir();(package/'tests/__init__.py').write_text('raise RuntimeError("agent tests must not load")\n')
    files={'tests/test_outputs.py':source.encode()}
    assets,updated,helper=roles.pristine_project_suite(files,'a'*64,{'prefix':str(prefix),'stdlib':['importlib']})
    for name,body in assets.items():
        target=tmp_path/name;target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes(body.replace(b'/tests/ot_pristine_widget',str(tmp_path/'tests/ot_pristine_widget').encode()))
    helper=helper.replace('/tests/ot_pristine_widget',str(tmp_path/'tests/ot_pristine_widget'))
    namespace={};exec(helper,namespace)
    argv=['ot-clean-python','-m','pytest','/app/widget/widget/tests','-v']
    kwargs=dict(capture_output=True,text=True,cwd=str(tmp_path),env={**os.environ,'PYTEST_DISABLE_PLUGIN_AUTOLOAD':'1'})
    result=namespace['_ot_pristine_suite_run'](argv,**kwargs)
    assert result.returncode==0,result.stdout+result.stderr
    (package/'__init__.py').write_text('answer=420\n')
    result=namespace['_ot_pristine_suite_run'](argv,**kwargs)
    assert result.returncode!=0 and '1 failed' in result.stdout
    with pytest.raises(ValueError,match='image binding'):
        roles.pristine_project_suite(files,'b'*64,{'prefix':str(prefix),'stdlib':[]})


def test_embedded_project_group_requires_image_and_runs_every_member(tmp_path, monkeypatch):
    import subprocess, sys
    from data.calibforge import python_roles as roles
    source="import subprocess\nresult=subprocess.run(['python','-m','pytest','tests/','-q'],capture_output=True,text=True)\n"
    files={'instruction.md':b'Merge both features'}
    call=next(n for n in ast.walk(ast.parse(source)) if isinstance(n,ast.Call))
    bodies={'tests/test_one.py':'def test_one(): assert True\n','tests/test_two.py':'def test_two(): assert False, "second feature checked"\n'}
    record=dict(task='task',test_source_sha256=hashlib.sha256(source.encode()).hexdigest(),evidence_sha256={},modules=[],project_image_sha256='a'*64,
                project_test_calls=[hashlib.sha256(ast.dump(call).encode()).hexdigest()],project_test_groups={'tests/':list(bodies)},
                project_test_sources={name:dict(text=body,sha256=hashlib.sha256(body.encode()).hexdigest(),import_root='/app') for name,body in bodies.items()})
    monkeypatch.setattr(roles,'module_roles',lambda:[record])
    with pytest.raises(ValueError,match='image or source'):
        roles.adapt_project_tests(source,files,{'stdlib':[],'source_image_sha256':'b'*64})
    adapted,helper=roles.adapt_project_tests(source,files,{'stdlib':[],'source_image_sha256':'a'*64})
    helper=helper.replace("dir='/tests'",'dir='+repr(str(tmp_path)))
    run=subprocess.run([sys.executable,'-I','-c',helper+'\n'+adapted+'\nassert result.returncode==1\nassert "second feature checked" in result.stdout\n'],capture_output=True,text=True)
    assert run.returncode==0,run.stderr
    record['project_test_sources']['tests/test_two.py']['text']='def test_two(): pass\n'
    with pytest.raises(ValueError,match='image or source'):
        roles.adapt_project_tests(source,files,{'stdlib':[],'source_image_sha256':'a'*64})


def test_restored_shell_initialization_rejects_changed_source(monkeypatch):
    from data.calibforge import python_roles as roles
    body=b'#!/bin/bash\nsetfattr -n user.example -v value /app/data\n'
    item=dict(source='environment/files/init.sh',target='tests/ot_init.sh',command='bash /app/init.sh',sha256=hashlib.sha256(body).hexdigest())
    monkeypatch.setattr(roles,'task_initialization_roles',lambda files:[dict(lines=['bash /app/init.sh'],restored_shell_helpers=[item])])
    assets,commands=roles.restored_shell_helpers({item['source']:body})
    assert assets=={'tests/ot_init.sh':body} and commands=={'bash /app/init.sh':'bash /tests/ot_init.sh'}
    with pytest.raises(ValueError,match='source differs'):
        roles.restored_shell_helpers({item['source']:body+b'echo changed\n'})


def test_shell_project_installation_uses_private_python_and_cached_inputs(monkeypatch, tmp_path):
    from data.calibforge import python_roles as roles
    import subprocess,sys,os
    command='cd /app && pip3 install --break-system-packages -e . 2>/dev/null || pip install --break-system-packages -e . 2>/dev/null || true'
    files={'tests/test.sh':(command+'\n').encode()}
    item=dict(image_sha256='a'*64,test_script_sha256=hashlib.sha256(files['tests/test.sh']).hexdigest(),command=command,argv=['install','--break-system-packages','-e','.'])
    monkeypatch.setattr(roles,'task_module_roles',lambda files:[dict(shell_package_installation=item)])
    assets,commands=roles.shell_package_installation(files,'a'*64)
    assert 'pip3' not in commands[command] and commands[command].count('OT_VERIFIER_PYTHON')==2
    calls=[]
    monkeypatch.setattr(subprocess,'call',lambda argv,**kwargs:calls.append((argv,kwargs)) or 17)
    monkeypatch.setenv('OT_VERIFIER_OFFLINE',str(tmp_path/'inputs'))
    monkeypatch.setenv('PYTHONPATH','/agent-code')
    with pytest.raises(SystemExit) as failure:exec(assets['tests/ot_install_submitted_package.py'])
    assert failure.value.code==17
    argv,kwargs=calls[0]
    assert argv[:6]==[sys.executable,'-I','-m','pip','--isolated','install']
    assert '--no-index' in argv and argv[-2:]==['-e','.']
    assert kwargs['env']['PIP_NO_INDEX']=='1' and 'PYTHONPATH' not in kwargs['env']
    with pytest.raises(ValueError,match='installation differs'):
        roles.shell_package_installation(files,'b'*64)


def test_shell_regression_keeps_separate_exact_python_and_fresh_source():
    source=b'import json\ndef test_original(): assert json.loads("42")==42\n'
    command='python -m pytest /app/tests/test_original.py -v --tb=short'
    script=('uv pip install --system pytest\n'+command+'\nORIGINAL_EXIT_CODE=$?\nuvx -p 3.13 -w pytest==8.4.1 pytest /tests/test_outputs.py\nSECURITY_EXIT_CODE=$?\nif [ $ORIGINAL_EXIT_CODE -eq 0 ] && [ $SECURITY_EXIT_CODE -eq 0 ]; then\necho 1 > /logs/verifier/reward.txt\nelse\necho 0 > /logs/verifier/reward.txt\nfi\n').encode()
    files={'tests/test.sh':script,'environment/files/tests/test_original.py':source}
    use=dict(source='environment/files/tests/test_original.py',target='tests/ot_original.py',source_sha256=hashlib.sha256(source).hexdigest(),command=command,original_path='/app/tests/test_original.py',installer='uv pip install --system pytest')
    record=dict(kind='shell_pytest',returncode=0,sha256='a'*64,test_script_sha256=hashlib.sha256(script).hexdigest(),uses=[use],observed=dict(python='3.12.3',executable='/usr/bin/python3',resolved_executable='/usr/bin/python3.12',prefix='/usr',requirements=['pytest==8.4.1'],stdlib=['json']))
    plan=runtime.uvx_plan(files,python='3.13.9',source_image_sha256='a'*64,auxiliary={'original':record})
    assert plan['auxiliary_specs']['original']['python']=='3.12.3'
    assert '/../aux/original/bin/python3' in plan['test_script']
    assert 'ORIGINAL_EXIT_CODE=$?' in plan['test_script']
    assert 'uv pip install' not in plan['test_script']
    assert plan['test_assets']['tests/ot_original.py']==source
    with pytest.raises(ValueError,match='source or command differs'):
        runtime.uvx_plan({**files,'environment/files/tests/test_original.py':source+b'assert False\n'},python='3.13.9',source_image_sha256='a'*64,auxiliary={'original':record})


def test_submitted_distribution_metadata_does_not_import_package(tmp_path, monkeypatch):
    import site
    from data.calibforge import python_roles as roles
    from data.calibforge import verifier_runtime
    monkeypatch.setattr(verifier_runtime, 'task_distributions', lambda files: {'submitted'})
    monkeypatch.setattr(roles, 'task_module_roles', lambda files: [])
    prefix = tmp_path / 'original'
    root = __import__('pathlib').Path(site.getsitepackages([str(prefix)])[0])
    root.mkdir(parents=True)
    package = root / 'submitted'
    package.mkdir()
    (package / '__init__.py').write_text("raise RuntimeError('must not import task code')")
    metadata = root / 'submitted-1.2.dist-info'
    metadata.mkdir()
    (metadata / 'METADATA').write_text('Name: submitted\nVersion: 1.2\n')
    (root / 'poison.pth').write_text("import sys; raise RuntimeError('must not execute pth')\n")
    source = "import importlib.metadata\nresult = importlib.metadata.version('submitted')\n"
    adapted = roles.preserve_task_metadata(source, {}, {'prefix': str(prefix)})
    scope = {}
    exec(adapted, scope)
    assert scope['result'] == '1.2'
    (metadata / 'METADATA').write_text('Name: submitted\nVersion: 2.0\n')
    exec(adapted, scope)
    assert scope['result'] == '2.0'
    unknown = "importlib.metadata.version('unreviewed')"
    assert roles.preserve_task_metadata(unknown, {}, {}) == unknown
    with __import__('pytest').raises(ValueError, match='prefix requires review'):
        roles.preserve_task_metadata(source, {}, {'prefix': '/tmp/../bad'})


def test_composite_package_alias_keeps_clean_install_and_show(monkeypatch):
    import subprocess
    from data.calibforge.python_roles import adapt_selector
    source='''import subprocess

def choose():
    return '/opt/original/bin/python'

def check():
    python=choose()
    pip_args=[python,'-m','pip']
    first=subprocess.run(pip_args+['install','--break-system-packages','/app/pkg'])
    if first.returncode != 0:
        subprocess.run(pip_args+['install','/app/pkg'])
    return subprocess.run(pip_args+['show','submitted'])
'''
    tree=ast.parse(source)
    calls=[n for n in ast.walk(tree) if isinstance(n,ast.Call) and ast.unparse(n.func)=='subprocess.run']
    record=dict(source_file='tests/test_outputs.py',source_sha256=hashlib.sha256(source.encode()).hexdigest(),
                selector='choose',selector_code="def choose():\n    return '/opt/original/bin/python'",uses=[],
                package_installation=dict(alias='pip_args',calls=[hashlib.sha256(ast.dump(n).encode()).hexdigest() for n in calls]))
    measured=dict(python='3.12.3',prefix='/opt/original',executable='/opt/original/bin/python')
    files={'tests/test_outputs.py':source.encode()}
    _,adapted,_=adapt_selector(files,record,'installer',measured)
    monkeypatch.setenv('OT_VERIFIER_BIN','/tests/private/bin')
    monkeypatch.setenv('OT_VERIFIER_OFFLINE','/tests/inputs')
    monkeypatch.setenv('PIP_INDEX_URL','https://invalid.invalid')
    seen=[]
    def run(argv,**kwargs):
        seen.append((argv,kwargs))
        return subprocess.CompletedProcess(argv,1 if len(seen)==1 else 0)
    monkeypatch.setattr(subprocess,'run',run)
    scope={};exec(adapted,scope);scope['check']()
    assert len(seen)==3
    for argv,kwargs in seen:
        assert argv[:5]==['/tests/private/bin/../aux/installer/bin/python3','-I','-m','pip','--isolated']
        assert 'PIP_INDEX_URL' not in kwargs['env']
    assert seen[0][0][5:9]==['install','--no-index','--find-links','/tests/inputs/wheels']
    assert seen[-1][0][5:]==['show','submitted']
    broken=copy.deepcopy(record);broken['package_installation']['calls'].pop()
    with pytest.raises(ValueError,match='Composite package call'):
        adapt_selector(files,broken,'installer',measured)
    changed=source.replace("    first=subprocess.run", "    pip_args.append('unsafe')\n    first=subprocess.run")
    changed_record={**record,'source_sha256':hashlib.sha256(changed.encode()).hexdigest()}
    with pytest.raises(ValueError,match='Composite package alias use'):
        adapt_selector({'tests/test_outputs.py':changed.encode()},changed_record,'installer',measured)


def test_system_numpy_install_keeps_original_point_and_cache_branch(monkeypatch):
    from data.calibforge import python_roles as roles
    block='apt-get update -qq 2>/dev/null || true\napt-get install -y -qq python3-numpy 2>/dev/null || true'
    script=('#!/bin/bash\n'+block+'\n').encode()
    record=dict(shell_dependency_installation=dict(block=block,test_script_sha256=hashlib.sha256(script).hexdigest(),spec=dict(apt=['python3-numpy'],python=[],uv=False,image_sha256='a'*64)))
    monkeypatch.setattr(roles,'task_module_roles',lambda files:[record])
    files={'tests/test.sh':script}
    assets,commands=roles.shell_dependency_installation(files,'a'*64)
    helper=assets['tests/ot_system_numpy.sh'].decode()
    assert commands=={block:'bash /tests/ot_system_numpy.sh'}
    assert helper.endswith(block+'\n') and 'APT_CONFIG="$OT_VERIFIER_OFFLINE/apt.conf"' in helper
    assert '"$_ot_uid_count" = 1' in helper and 'APT::Sandbox::User' in helper
    import subprocess
    assert subprocess.run(['bash','-n'],input=helper,text=True,capture_output=True).returncode==0
    with pytest.raises(ValueError,match='binding differs'):
        roles.shell_dependency_installation(files,'b'*64)
    with pytest.raises(ValueError,match='binding differs'):
        roles.shell_dependency_installation({'tests/test.sh':script.replace(b'python3-numpy',b'other')},'a'*64)


def test_background_initializer_restores_bound_source_and_exact_python():
    files,observed,_,_=fixture()
    line='/usr/bin/python3 /app/backend_server.py --bind 127.0.0.3 8081 &'
    files['tests/test.sh']=line.encode()
    files['environment/files/backend_server.py']=b'import json\nprint(json.dumps({"backend": True}))\n'
    record=copy.deepcopy(observed['auxiliary']['metrics'])
    record.update(kind='background_helper',probe_invocation='/usr/bin/python3',test_script_sha256=hashlib.sha256(files['tests/test.sh']).hexdigest(),uses=[dict(source='environment/files/backend_server.py',source_sha256=hashlib.sha256(files['environment/files/backend_server.py']).hexdigest(),target='tests/ot_backend_server.py',original_path='/app/backend_server.py',line=line)])
    plan=runtime.auxiliary_helper_plan(files,{'backend':record},'a'*64)
    assert plan['specs']['backend']['python']=='3.12.3'
    assert plan['sources']['tests/ot_backend_server.py']==files['environment/files/backend_server.py'].decode()
    assert plan['replacements'][line].endswith('-I /tests/ot_backend_server.py --bind 127.0.0.3 8081 &')
    with pytest.raises(ValueError,match='Background initializer source'):
        runtime.auxiliary_helper_plan({**files,'environment/files/backend_server.py':b'changed'}, {'backend':record},'a'*64)


def test_selectable_grader_uses_clean_python_and_submitted_package(tmp_path,monkeypatch):
    import site,sys
    from data.calibforge.python_roles import adapt_selectable_grading
    candidate='/original/bin/python3'
    source='''import subprocess,sys

def check():
    first=subprocess.run(['/original/bin/python3','-c','import faiss'])
    second=subprocess.run(['/original/bin/python3','-c','import faiss; assert faiss.answer == 42'])
    third=subprocess.run(['/original/bin/python3','-c','import faiss; assert isinstance(faiss.answer,int)'])
    return first,second,third
'''
    tree=ast.parse(source);calls=[hashlib.sha256(ast.dump(n).encode()).hexdigest() for n in ast.walk(tree) if isinstance(n,ast.Call) and ast.unparse(n.func)=='subprocess.run']
    prefix=tmp_path/'submitted';root=__import__('pathlib').Path(site.getsitepackages([str(prefix)])[0]);root.mkdir(parents=True)
    (root/'faiss.py').write_text('answer = 42\n')
    (root/'json.py').write_text("raise RuntimeError('unrelated submitted module')")
    clean=tmp_path/'clean';(clean/'bin').mkdir(parents=True);child=clean/'aux/test/bin';child.mkdir(parents=True);(child/'python3').symlink_to(sys.executable)
    monkeypatch.setenv('OT_VERIFIER_BIN',str(clean/'bin'))
    record=dict(source_file='tests/test_outputs.py',source_sha256=hashlib.sha256(source.encode()).hexdigest(),selector='_find_python_with_faiss',main_executable=candidate,uses=calls,candidates=[candidate],observed={'prefix':str(prefix)})
    _,adapted=adapt_selectable_grading({'tests/test_outputs.py':source.encode()},{'test':record})
    scope={};exec(adapted,scope)
    assert [r.returncode for r in scope['check']()]==[0,0,0]
    (root/'faiss.py').write_text('answer = 999\n')
    assert scope['check']()[1].returncode!=0
    with pytest.raises(ValueError,match='source binding differs'):
        adapt_selectable_grading({'tests/test_outputs.py':(source+'# changed').encode()},{'test':record})
    with pytest.raises(ValueError,match='Unprepared original selector'):
        scope['_ot_selectable_run'](['/unknown/python','-c','import faiss'])
