"""Source-bound roles for independently measured nested grading interpreters."""
import ast
import hashlib
import json
import re
from pathlib import Path, PurePosixPath
from functools import lru_cache
from contextvars import ContextVar

# The task being converted. Reviewed records name their task; the file hashes
# they also carry bind them to the exact files the decision is about.
current_task = ContextVar('calibforge_task', default=None)


def for_task(record):
    return record['task'] == current_task.get()


@lru_cache(maxsize=1)
def installation_roles():
    return json.loads(Path(__file__).with_name('task_installation_roles.json').read_text())


@lru_cache(maxsize=1)
def service_roles():
    return json.loads(Path(__file__).with_name('task_service_roles.json').read_text())


@lru_cache(maxsize=1)
def module_roles():
    return json.loads(Path(__file__).with_name('task_module_roles.json').read_text())


def task_module_roles(files):
    """Named agent modules reviewed against the instruction and supplied sources.

    The original grader hash is provenance; transformations of that grader do
    not change the instruction's ownership of these specific task modules.
    """
    return [record for record in module_roles() if for_task(record)
            and all(path in files and hashlib.sha256(files[path]).hexdigest()==digest
                    for path,digest in record['evidence_sha256'].items())]


def task_extension_loader(node, tree, files):
    """Recognize reviewed loaders that choose a submitted /app extension.

    Bind the entire loader function, not just its final call: the preceding
    directory search establishes that the selected library is task code.
    """
    parents={child:parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    function=parents.get(node)
    while function is not None and not isinstance(function,ast.FunctionDef):
        function=parents.get(function)
    if function is None:return False
    digest=hashlib.sha256(ast.dump(function,include_attributes=False).encode()).hexdigest()
    return any(digest in role.get('extension_loader_functions',[]) for role in task_module_roles(files))


@lru_cache(maxsize=1)
def initialization_roles():
    return json.loads(Path(__file__).with_name('task_initialization_roles.json').read_text())


def task_initialization_roles(files):
    script=hashlib.sha256(files.get('tests/test.sh',b'')).hexdigest()
    return [record for record in initialization_roles() if for_task(record)
            and record['test_script_sha256']==script]


def restored_shell_helpers(files):
    """Restore source-bound initialization scripts before running them."""
    assets,commands={},{}
    for role in task_initialization_roles(files):
        for line,name in role.get('tool_replacements',{}).items():
            path=role.get('tools',{}).get(name)
            if not path or line not in role['lines'] or not line.startswith(path+' '):
                raise ValueError('Initialization tool replacement differs')
            commands[line]='"${OT_VERIFIER_BIN:?prepared verifier runtime required}/'+name+'"'+line[len(path):]
        for item in role.get('restored_shell_helpers',[]):
            source,target,command=item['source'],item['target'],item['command']
            if (not re.fullmatch(r'tests/ot_[A-Za-z0-9_]+\.sh',target)
                    or source not in files
                    or hashlib.sha256(files[source]).hexdigest()!=item['sha256']
                    or command not in role['lines']
                    or not re.fullmatch(r'bash /[A-Za-z0-9_./-]+\.sh',command)):
                raise ValueError('CalibForge shell initialization source differs')
            if target in assets or target in files:
                raise ValueError('CalibForge shell initialization target collision')
            assets[target]=files[source]
            commands[command]='bash /'+target
    return assets,commands


def shell_package_installation(files, image_sha256):
    """Install the submitted project at its original grading step, privately."""
    assets,commands={},{}
    for role in task_module_roles(files):
        item=role.get('shell_package_installation')
        if not item:continue
        if (item['image_sha256']!=image_sha256
                or hashlib.sha256(files.get('tests/test.sh',b'')).hexdigest()!=item['test_script_sha256']
                or item['command'] not in files['tests/test.sh'].decode().splitlines()
                or item['argv'] not in (['install','--break-system-packages','-e','.'],['install','.'])):
            raise ValueError('CalibForge shell project installation differs')
        target='tests/ot_install_submitted_package.py'
        if target in assets or target in files:raise ValueError('Shell package helper collision')
        body='''import os,subprocess,sys
env={k:v for k,v in os.environ.items() if not k.startswith(('PYTHON','PIP_'))}
env['PIP_CONFIG_FILE']=os.devnull
offline=os.environ.get('OT_VERIFIER_OFFLINE')
options=['--no-index','--find-links',offline+'/wheels'] if offline else []
if offline:
    env.update(PIP_NO_INDEX='1',PIP_FIND_LINKS=offline+'/wheels')
raise SystemExit(subprocess.call([sys.executable,'-I','-m','pip','--isolated','install',*options,'--break-system-packages',*INSTALL_ARGS],env=env))
'''
        body=body.replace('INSTALL_ARGS',repr(['-e','.'] if '-e' in item['argv'] else ['.']))
        assets[target]=body.encode()
        command='"${OT_VERIFIER_PYTHON:?prepared verifier runtime required}" -I /'+target
        # Original pip3/pip fallback resolves to the same measured interpreter.
        if item['argv']==['install','.']:
            if item['command']!='    pip install . 2>/dev/null || true':
                raise ValueError('CalibForge project install fallback differs')
            commands[item['command'].strip()]=command+' 2>/dev/null || true'
        else:
            commands[item['command']]='cd /app && '+command+' 2>/dev/null || '+command+' 2>/dev/null || true'
    return assets,commands


def task_installation_role(call, paths, files):
    """Keep reviewed installation checks on the environment being evaluated.

    Bind the instruction, complete call AST and resolved executable. The call
    binding survives unrelated clean-child rewrites in the same source file.
    It never grants an exception to another call or an entire helper function.
    """
    if not files or not call.args or not isinstance(call.args[0], (ast.List, ast.Tuple)):
        return None
    argv = call.args[0]
    if len(argv.elts) < 3 or paths(argv.elts[1]) != {'-c'}:
        return None
    digest = hashlib.sha256(ast.dump(call, include_attributes=False).encode()).hexdigest()
    for record in installation_roles():
        if record.get('requires_source_binding') and not any(
                path.startswith('tests/') and path.endswith('.py')
                and hashlib.sha256(body).hexdigest() == record['source_sha256']
                for path, body in files.items()):
            continue
        executables = paths(argv.elts[0])
        if record['executables'] == ['sys.executable']:
            if (ast.unparse(argv.elts[0]) != 'sys.executable'
                    or not any(path.startswith('tests/') and path.endswith('.py')
                               and hashlib.sha256(body).hexdigest() == record['source_sha256']
                               for path, body in files.items())):
                continue
            executables = {'sys.executable'}
        if (for_task(record) and record['call_ast_sha256'] == digest
                and set(record['executables']) == executables):
            return record
    return None


def adapt_selector(files, record, name, measured):
    from data.calibforge.verifier_runtime import (static_paths, review_imports, preserve_cwd_task_imports,
        task_python_calls, grading_snippet_template, grading_helper_templates, DYNAMIC_SNIPPET_HELPER)
    source_file=record.get('source_file')
    if (not isinstance(source_file,str) or not source_file.startswith('tests/') or source_file not in files
            or record.get('source_sha256')!=hashlib.sha256(files[source_file]).hexdigest()):
        raise ValueError('CalibForge auxiliary Python source binding requires review')
    source=files[source_file].decode();tree=ast.parse(source)
    parents={child:node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    def owner(node):
        node=parents.get(node)
        while node is not None and not isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef)):
            node=parents.get(node)
        return node
    selector=record.get('selector')
    definitions=[node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name==selector]
    if len(definitions)!=1 or definitions[0].decorator_list or definitions[0].args.args:
        raise ValueError('CalibForge auxiliary Python selector definition requires review')
    definition=definitions[0]
    probed=[node for node in ast.parse(record.get('selector_code','')).body if isinstance(node,ast.FunctionDef) and node.name==selector]
    if len(probed)!=1 or ast.dump(probed[0])!=ast.dump(definition):
        raise ValueError('CalibForge auxiliary Python selector probe differs')
    aliases={};direct_calls=set()
    for node in ast.walk(tree):
        if isinstance(node,ast.Name) and node.id==selector and isinstance(node.ctx,ast.Load):
            call=parents[node];assignment=parents.get(call)
            if (isinstance(call,ast.Call) and call.func is node and not call.args and not call.keywords
                    and isinstance(assignment,(ast.List,ast.Tuple)) and assignment.elts[0] is call
                    and isinstance(parents.get(assignment),ast.Call)
                    and ast.unparse(parents[assignment].func) in ('subprocess.run','subprocess.call','subprocess.check_call','subprocess.check_output','subprocess.Popen')
                    and parents[assignment].args and parents[assignment].args[0] is assignment):
                direct_calls.add(call)
                continue
            if (not isinstance(call,ast.Call) or call.func is not node or call.args or call.keywords
                    or not isinstance(assignment,ast.Assign) or assignment.value is not call
                    or len(assignment.targets)!=1 or not isinstance(assignment.targets[0],ast.Name)):
                raise ValueError('CalibForge auxiliary Python selector use requires review')
            target=assignment.targets[0];scope=owner(target)
            stores=[n for n in ast.walk(scope or tree) if isinstance(n,ast.Name) and n.id==target.id
                    and isinstance(n.ctx,(ast.Store,ast.Del))]
            if scope is None and any(isinstance(n,ast.arg) and n.arg==target.id
                    or isinstance(n,ast.alias) and (n.asname or n.name.split('.')[0])==target.id
                    for n in ast.walk(tree)):
                raise ValueError('CalibForge global auxiliary Python alias binding requires review')
            if len(stores)!=1:
                raise ValueError('CalibForge auxiliary Python alias rebinding requires review')
            aliases[(scope,target.id)]=target
    if not aliases and not direct_calls:
        raise ValueError('CalibForge auxiliary Python selector has no reviewed callers')
    def alias_key(node):
        key=(owner(node),node.id)
        return key if key in aliases else (None,node.id) if (None,node.id) in aliases else None
    paths=static_paths(tree,'/'+source_file)
    # Preserve the named package under test in each original selector candidate
    # environment, without exposing those sites to unrelated grading imports.
    task_prefixes=[]
    for node in ast.walk(definition):
        if (isinstance(node,ast.Constant) and isinstance(node.value,str)
                and re.fullmatch(r'/[A-Za-z0-9_./-]+/bin/python3?(?:\.\d+)?',node.value)
                and '..' not in node.value.split('/')):
            prefix=str(PurePosixPath(node.value).parent.parent)
            if prefix not in task_prefixes:task_prefixes.append(prefix)
    for prefix in (measured['prefix'],str(PurePosixPath(record.get('main_executable',measured['executable'])).parent.parent)):
        if prefix not in task_prefixes:task_prefixes.append(prefix)
    executable="(__import__('os').environ['OT_VERIFIER_BIN'] + '/../aux/"+name+"/bin/python3')"
    replacements=[(definition,'def '+selector+'():\n    return '+executable)]
    calls={(node.lineno,node.col_offset):node for node in ast.walk(tree) if isinstance(node,ast.Call)}
    reviewed_names=set();uses=[];helpers={};dynamic=False
    package_helper=''
    package=record.get('package_installation')
    if package:
        package_edits,package_names,package_helper=selector_package_calls(
            tree,parents,aliases,alias_key,package,name)
        replacements.extend(package_edits);reviewed_names.update(package_names)
    for use in record['uses']:
        call=calls.get((use.get('line'),use.get('column')))
        if (call is None or ast.unparse(call.func) not in ('subprocess.run','subprocess.call','subprocess.check_call','subprocess.check_output','subprocess.Popen')
                or not call.args or not isinstance(call.args[0],(ast.List,ast.Tuple)) or len(call.args[0].elts)<2):
            raise ValueError('CalibForge auxiliary Python invocation binding requires review')
        argv=call.args[0];python=argv.elts[0]
        if ((python not in direct_calls and (not isinstance(python,ast.Name) or alias_key(python) not in aliases))
                or python in reviewed_names):
            raise ValueError('CalibForge auxiliary Python invocation alias requires review')
        reviewed_names.add(python);targets=paths(argv.elts[1]);role=use.get('role')
        if role=='task':
            if not targets or not all(path.startswith(('/app/','/workspace/')) and '..' not in path.split('/') for path in targets):
                raise ValueError('CalibForge auxiliary task-program target requires review')
            replacements.append((python,json.dumps(measured['executable'])))
        elif role=='task_package':
            from data.calibforge.verifier_runtime import task_package_command
            if not task_package_command(argv,paths,files,parents):
                raise ValueError('CalibForge auxiliary task package command requires review')
            replacements.append((python,json.dumps(measured['executable'])))
        elif role=='grading_script':
            if not targets or not all(target.startswith('/tests/') and target[1:] in files
                    and target.endswith('.py') and '..' not in target.split('/') for target in targets):
                raise ValueError('CalibForge auxiliary grading script requires fresh source')
            for target in targets:
                body=files[target[1:]].decode();context={**measured,'test_file':target}
                adapted,_=task_python_calls(body,measured['executable'],files=files,image=context)
                review_imports(ast.parse(adapted),files,context)
                if adapted!=body or preserve_cwd_task_imports(body,files,context)!=body:
                    raise ValueError('CalibForge auxiliary grading script adaptations require review')
                helpers[target[1:]]=hashlib.sha256(files[target[1:]]).hexdigest()
            replacements.append((python,executable+', "-E", "-s"'))
        elif role=='grading':
            snippets=paths(argv.elts[2]) if len(argv.elts)>2 and targets=={'-c'} else set()
            template=grading_snippet_template(argv.elts[2],tree) if len(argv.elts)>2 and targets=={'-c'} and not snippets else None
            choices=grading_helper_templates(argv.elts[2],tree) if len(argv.elts)>2 and targets=={'-c'} and not snippets and not template else None
            if template:snippets={template[0]}
            if choices:snippets={value[0] for value in choices}
            if not snippets:
                raise ValueError('CalibForge auxiliary grading snippet requires review')
            adapted={}
            for snippet in sorted(snippets):
                context={**measured,'test_file':'/'+source_file,'task_package_prefixes':task_prefixes}
                rewritten,_=task_python_calls(snippet,measured['executable'],files=files,image=context)
                review_imports(ast.parse(rewritten),files,context)
                adapted[snippet]=preserve_cwd_task_imports(rewritten,files,context)
            replacements.append((python,executable+', "-I"'))
            expression=ast.get_source_segment(source,argv.elts[2])
            if choices or template:
                if '_ot_checked_literal_snippet' in source:
                    raise ValueError('CalibForge auxiliary snippet helper collision')
                options=choices or [template]
                replacements.append((argv.elts[2],'_ot_checked_snippet_choices('+expression+', '+repr([(value,adapted[value],holes) for value,holes in options])+')'))
                dynamic=True
            elif any(before!=after for before,after in adapted.items()):
                expression=ast.get_source_segment(source,argv.elts[2])
                replacements.append((argv.elts[2],'('+repr(adapted)+')['+expression+']'))
        else:
            raise ValueError('CalibForge auxiliary Python role requires review')
        uses.append({**use,'targets':sorted(targets)})
    for node in ast.walk(tree):
        if (isinstance(node,ast.Name) and isinstance(node.ctx,ast.Load) and alias_key(node) in aliases
                and node not in reviewed_names):
            raise ValueError('CalibForge unreviewed auxiliary Python alias use')
    if not direct_calls <= reviewed_names:
        raise ValueError('CalibForge unreviewed direct auxiliary Python selector use')
    data=source.encode();offsets=[0]
    for line in data.splitlines(keepends=True):offsets.append(offsets[-1]+len(line))
    for node,value in sorted(replacements,key=lambda item:(item[0].lineno,item[0].col_offset),reverse=True):
        start=offsets[node.lineno-1]+node.col_offset;end=offsets[node.end_lineno-1]+node.end_col_offset
        data=data[:start]+value.encode()+data[end:]
    if dynamic:data+=DYNAMIC_SNIPPET_HELPER.encode()
    data+=package_helper.encode()
    ast.parse(data.decode())
    return source_file,data.decode(),dict(source_sha256=record['source_sha256'],selector=selector,uses=uses,
                                          python=measured['python'],executable=measured['executable'],task_package_prefixes=task_prefixes,helpers=helpers)


def shell_task_launcher(line, files):
    """Move a literal submitted-program launch into a fresh, non-grading helper."""
    import shlex
    instruction=files.get('instruction.md',b'').decode()
    service_role = next((record for record in service_roles()
                         if record['line']==line and for_task(record)
                         and record['test_script_sha256']==hashlib.sha256(files.get('tests/test.sh',b'')).hexdigest()),None)
    background=line.endswith(' &')
    original_command=line[:-2] if background else line
    command=original_command.removesuffix(' || true')
    optional=command!=original_command
    cwd=None
    match=re.match(r'^cd (/(?:app|workspace)(?:/[A-Za-z0-9_.-]+)*) && ',command)
    if match:
        cwd=match[1]
        if '..' in cwd.split('/'):return None
        command=command[match.end():]
    virtualenv=service_role.get('virtualenv') if service_role else None
    if virtualenv:
        if (not re.fullmatch(r'/(?:app|workspace)(?:/[A-Za-z0-9_.-]+)+',virtualenv)
                or '..' in virtualenv.split('/') or not command.startswith('source '+virtualenv+'/bin/activate && ')):
            return None
        command=command.removeprefix('source '+virtualenv+'/bin/activate && ')
    if any(char in command for char in ('$', '`', ';', '|', '&', '<', '>')):
        return None
    try:
        argv=shlex.split(command)
    except ValueError:
        return None
    module=(argv[2] if len(argv)==3 and argv[1]=='-m' and service_role
            and re.fullmatch(r'[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*',argv[2]) else None)
    target=argv[1] if len(argv)>1 else ''
    if cwd and not target.startswith('/'):
        target=cwd+'/'+target
    if (len(argv)<2 or not re.fullmatch(r'(?:/[A-Za-z0-9_./-]+/)?python3?(?:\.\d+)?',argv[0])
            or (not module and (not target.startswith(('/app/','/workspace/')) or not target.endswith('.py')))
            or '..' in argv[1].split('/') or '..' in argv[0].split('/')):
        return None
    if module:target='module:'+module
    if target not in instruction and service_role is None:
        return None
    key=hashlib.sha256(line.encode()).hexdigest()[:20]
    path='tests/ot_task_program_'+key+'.py'
    if path in files:
        raise ValueError('CalibForge task-program helper filename collision requires review')
    environment=('os.environ["VIRTUAL_ENV"]='+repr(virtualenv)+'\n'
                 'os.environ["VIRTUAL_ENV_PROMPT"]='+repr(virtualenv.rsplit('/',1)[-1])+'\n'
                 'os.environ["PATH"]='+repr(virtualenv+'/bin:')+'+os.environ.get("PATH", "")\n'
                 'os.environ.pop("PYTHONHOME",None)\n') if virtualenv else ''
    source=('"""Execute the submitted task program; grading remains in the clean parent."""\n'
            'import os\nargv='+repr(argv)+'\n'+('os.chdir('+repr(cwd)+')\n' if cwd else '')+environment+'os.execvp(argv[0],argv)\n')
    replacement='"${OT_VERIFIER_PYTHON:?prepared verifier runtime required}" -I /'+path
    if optional:replacement+=' || true'
    if background:replacement+=' &'
    elif cwd:replacement='cd '+shlex.quote(cwd)+' && '+replacement
    return path,source,replacement,dict(original_command=line,task_program=target,task_interpreter=argv[0],
                                        task_cwd=cwd,background=background,task_virtualenv=virtualenv,
                                        service_role=service_role,
                                        helper=path,helper_sha256=hashlib.sha256(source.encode()).hexdigest())


def adapt_literal(files, record, name, measured):
    """Adapt independently measured literal -c graders, keeping task imports."""
    from data.calibforge.verifier_runtime import (static_paths, review_imports, preserve_cwd_task_imports,
        task_python_calls, grading_snippet_template, grading_helper_templates, DYNAMIC_SNIPPET_HELPER)
    path=record.get('source_file')
    if (not isinstance(path,str) or not path.startswith('tests/') or path not in files
            or record.get('source_sha256')!=hashlib.sha256(files[path]).hexdigest()):
        raise ValueError('CalibForge literal Python source binding requires review')
    source=files[path].decode();tree=ast.parse(source);paths=static_paths(tree,'/'+path)
    calls={(node.lineno,node.col_offset):node for node in ast.walk(tree) if isinstance(node,ast.Call)}
    executable="__import__('sys').executable" if name is None else "(__import__('os').environ['OT_VERIFIER_BIN'] + '/../aux/"+name+"/bin/python3')"
    edits=[];proof=[];required_modules={};dynamic=False
    for use in record['uses']:
        call=calls.get((use.get('line'),use.get('column')))
        if (call is None or ast.unparse(call.func) not in ('subprocess.run','subprocess.call','subprocess.check_call','subprocess.check_output','subprocess.Popen')
                or not call.args or not isinstance(call.args[0],(ast.List,ast.Tuple)) or len(call.args[0].elts)<3
                or use.get('role')!='grading'):
            raise ValueError('CalibForge literal Python invocation role requires review')
        environment = next((keyword.value for keyword in call.keywords if keyword.arg=='env'), None)
        pythonpath = None
        environment_role = next((role['literal_environment'] for role in task_module_roles(files)
            if role.get('test_source_sha256') == hashlib.sha256(source.encode()).hexdigest()
            and role.get('literal_environment',{}).get('image_sha256') == record.get('sha256')
            and role['literal_environment'].get('executable') == measured.get('executable')
            and role['literal_environment'].get('python') == measured.get('python')), None)
        if environment is not None and environment_role:
            if ast.unparse(environment) != environment_role['variable']:
                raise ValueError('Literal Python environment source differs')
            pythonpath = environment_role['task_root']
        elif environment is not None:
            # -I ignores PYTHONPATH. Restore only proven task modules through
            # the selective finder, never the complete task directory.
            if (not isinstance(environment, ast.Dict) or len(environment.keys)!=2
                    or environment.keys[0] is not None or ast.unparse(environment.values[0])!='os.environ'
                    or not isinstance(environment.keys[1],ast.Constant) or environment.keys[1].value!='PYTHONPATH'
                    or not isinstance(environment.values[1],ast.Constant)
                    or not isinstance(environment.values[1].value,str)
                    or not re.fullmatch(r'/(?:app|workspace)(?:/[A-Za-z0-9_.-]+)*',environment.values[1].value)
                    or '..' in environment.values[1].value.split('/')):
                raise ValueError('CalibForge literal Python invocation environment requires review')
            pythonpath = environment.values[1].value
        argv=call.args[0]
        if (paths(argv.elts[0])!={record.get('probe_invocation')} or paths(argv.elts[1])!={'-c'}
                or use.get('call_sha256')!=hashlib.sha256(ast.get_source_segment(source,call).encode()).hexdigest()):
            raise ValueError('CalibForge literal Python invocation binding differs')
        context={**measured,'test_file':'/'+path}
        if pythonpath:
            context['implicit_task_root']=pythonpath
        cwd_argument=next((kw.value for kw in call.keywords if kw.arg=='cwd'),None)
        if cwd_argument is not None:
            cwd=paths(cwd_argument)
        else:
            workdirs=re.findall(r'^WORKDIR\s+(\S+)\s*$',files.get('environment/Dockerfile',b'').decode(),re.M)
            cwd=set(workdirs) if len(workdirs)==1 and not re.search(r'\b(?:cd|pushd|popd)\b',files['tests/test.sh'].decode()) else set()
        if not pythonpath and len(cwd)==1 and all(p in ('/app','/workspace') or p.startswith(('/app/','/workspace/')) for p in cwd):
            context['implicit_task_root']=next(iter(cwd))
        snippets=paths(argv.elts[2]);template=grading_snippet_template(argv.elts[2],tree) if not snippets else None
        choices=grading_helper_templates(argv.elts[2],tree) if not snippets and not template else None
        if template:snippets={template[0]}
        if choices:snippets={value[0] for value in choices}
        if not snippets:raise ValueError('CalibForge literal Python grading snippet requires review')
        adapted={}
        for snippet in sorted(snippets):
            inner=ast.parse(snippet)
            # A bare import probe may be checking an installation the agent was
            # asked to change. Do not classify that environment check as grading.
            def installation_probe(node):
                if isinstance(node,(ast.Import,ast.ImportFrom)):return True
                if not isinstance(node,ast.Expr) or not isinstance(node.value,ast.Call) or ast.unparse(node.value.func)!='print':return False
                return all(isinstance(value,ast.Constant) or isinstance(value,ast.Attribute) and value.attr=='__version__' for value in node.value.args)
            imported = {alias.name.split('.')[0] for node in inner.body if isinstance(node,ast.Import) for alias in node.names}
            imported.update(node.module.split('.')[0] for node in inner.body if isinstance(node,ast.ImportFrom) and node.module)
            instruction = files.get('instruction.md',b'').decode()
            explicit_source_probe = bool(choices and imported) and all(
                any(root+'/'+module+'.py' in instruction for root in ('/app','/workspace')) for module in imported)
            from data.calibforge.verifier_runtime import task_distributions
            from packaging.utils import canonicalize_name
            installed_modules = {module for role in task_module_roles(files) for module in role.get('installed_modules', [])}
            submitted_packages = {canonicalize_name(module) for module in imported - installed_modules} <= task_distributions(files)
            if imported and all(installation_probe(node) for node in inner.body) and not explicit_source_probe and not submitted_packages:
                raise ValueError('CalibForge literal Python installation check requires task role review')
            from data.calibforge.source_inputs import adapt_snippet
            prepared=preserve_task_metadata(adapt_snippet(snippet,files),files,context)
            rewritten,_=task_python_calls(prepared,measured['executable'],files=files,image=context)
            imports,task_imports=review_imports(ast.parse(rewritten),files,context)
            for module in set(imports)-set(task_imports):
                origin=measured.get('unversioned_module_origins',{}).get(module)
                if origin:required_modules[module]=origin
            adapted[snippet]=preserve_cwd_task_imports(rewritten,files,context)
            if environment_role:
                from data.calibforge.verifier_runtime import installed_task_import_adapter
                finder=installed_task_import_adapter(environment_role['modules'], measured['prefix'], roots=environment_role['roots'])
                adapted[snippet]=finder+adapted[snippet]
        edits.append((argv.elts[0],executable+', "-I"'))
        expression=ast.get_source_segment(source,argv.elts[2])
        if choices:
            if '_ot_checked_literal_snippet' in source:raise ValueError('CalibForge literal snippet helper collision')
            options=[(value,adapted[value],holes) for value,holes in choices]
            edits.append((argv.elts[2], '_ot_checked_snippet_choices('+expression+', '+repr(options)+')'))
            dynamic=True
        elif template:
            if '_ot_checked_literal_snippet' in source:raise ValueError('CalibForge literal snippet helper collision')
            edits.append((argv.elts[2], '_ot_checked_literal_snippet('+expression+', '+repr(template[0])+', '+repr(adapted[template[0]])+', '+repr(template[1])+')'))
            dynamic=True
        elif any(before!=after for before,after in adapted.items()):
            edits.append((argv.elts[2],'('+repr(adapted)+')['+expression+']'))
        proof.append(use)
    # Return original-source offsets so several distinct child runtimes can
    # safely adapt one uploaded test module without shifting each other's uses.
    offsets=[0]
    for line in source.encode().splitlines(keepends=True):offsets.append(offsets[-1]+len(line))
    encoded=[(offsets[node.lineno-1]+node.col_offset,offsets[node.end_lineno-1]+node.end_col_offset,value) for node,value in edits]
    return path,encoded,(DYNAMIC_SNIPPET_HELPER if dynamic else ''),required_modules,dict(
        source_sha256=record['source_sha256'],uses=proof,python=measured['python'],executable=measured['executable'])


DYNAMIC_TASK_IMPORT_HELPER = '''
def _ot_import_submitted_module(name):
    import importlib.machinery as machinery
    import importlib.util as util
    import pathlib
    import sys
    if not isinstance(name, str) or not name.isidentifier() or name in sys.stdlib_module_names or name in ('pytest', 'pluggy', 'pip'):
        raise ImportError('Invalid submitted module name')
    spec = machinery.PathFinder.find_spec(name, ['/app'])
    if spec is None or spec.loader is None or spec.origin is None:
        raise ImportError('Submitted module not found: ' + name)
    origin = pathlib.Path(spec.origin).resolve()
    if not origin.is_relative_to('/app') or not origin.is_file():
        raise ImportError('Submitted module escaped /app')
    existing = sys.modules.get(name)
    if existing is not None and getattr(existing, '__file__', None) == spec.origin:
        return existing
    module = util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module
'''


def remove_unused_task_paths(text, files):
    """Remove reviewed path insertions that import no task module from that root."""
    original = hashlib.sha256(text.encode()).hexdigest()
    for role in task_module_roles(files):
        if role.get('test_source_sha256') == original:
            for line in role.get('unused_import_path_lines', []):
                if text.count(line) != 1:
                    raise ValueError('Unused import path source differs')
                text = text.replace(line, '', 1)
    return text


def adapt_dynamic_task_imports(text, files):
    """Preserve source-bound submitted module discovery without exposing /app globally."""
    records = [role for role in task_module_roles(files)
               if role.get('test_source_sha256') == hashlib.sha256(text.encode()).hexdigest()
               and role.get('dynamic_import_calls')]
    if not records:
        return text, ''
    if '_ot_import_submitted_module' in text:
        raise ValueError('Submitted module helper collision')
    expected = {digest for role in records for digest in role['dynamic_import_calls']}
    edits = []
    found = set()
    for node in ast.walk(ast.parse(text)):
        if not isinstance(node, ast.Call):
            continue
        digest = hashlib.sha256(ast.dump(node, include_attributes=False).encode()).hexdigest()
        if digest in expected:
            if ast.unparse(node.func) not in ('__import__', 'importlib.import_module') or len(node.args) != 1 or node.keywords:
                raise ValueError('Submitted module import call differs')
            edits.append(node.func)
            found.add(digest)
    if found != expected:
        raise ValueError('Submitted module import source differs')
    data = text.encode()
    offsets = [0]
    for line in data.splitlines(keepends=True):
        offsets.append(offsets[-1] + len(line))
    for node in sorted(edits, key=lambda n: (n.lineno, n.col_offset), reverse=True):
        start, end = offsets[node.lineno-1]+node.col_offset, offsets[node.end_lineno-1]+node.end_col_offset
        data = data[:start] + b'_ot_import_submitted_module' + data[end:]
    return data.decode(), DYNAMIC_TASK_IMPORT_HELPER


def adapt_generated_scripts(text, files, image):
    """Review literal temporary graders before writing them; run with clean Python."""
    from data.calibforge.verifier_runtime import grading_helper_templates, preserve_cwd_task_imports
    records = [role for role in task_module_roles(files)
               if role.get('test_source_sha256') == hashlib.sha256(text.encode()).hexdigest()
               and role.get('generated_script_write_calls')]
    if not records:
        return text, ''
    if len(records) != 1 or '_ot_generated_' in text:
        raise ValueError('Generated grading script roles overlap')
    record = records[0]
    invocation = record.get('generated_script_invocation')
    if invocation and image.get('original_native_invocation') != invocation:
        raise ValueError('Generated grader requires its measured original interpreter')
    tree = ast.parse(text)
    writes, runs = set(record['generated_script_write_calls']), set(record['generated_script_run_calls'])
    found_writes, found_runs, edits, sources = set(), set(), [], {}
    for call in ast.walk(tree):
        if not isinstance(call, ast.Call):
            continue
        digest = hashlib.sha256(ast.dump(call, include_attributes=False).encode()).hexdigest()
        if digest in writes:
            if len(call.args) != 1 or call.keywords:
                raise ValueError('Generated script writer differs')
            choices = grading_helper_templates(call.args[0], tree)
            if not choices or any(holes for _, holes in choices):
                raise ValueError('Generated script content is not fully literal')
            for source, _ in choices:
                if any(isinstance(n, ast.Attribute) and ast.unparse(n) == 'sys.executable'
                       for n in ast.walk(ast.parse(source))):
                    raise ValueError('Generated script nested interpreter requires review')
                sources[source] = preserve_cwd_task_imports(source, files, {**image, 'implicit_task_root': record['generated_script_cwd']})
            expression = ast.get_source_segment(text, call.args[0])
            edits.append((call.args[0], '_ot_generated_source(' + expression + ')'))
            found_writes.add(digest)
        if digest in runs:
            if (ast.unparse(call.func) != 'subprocess.run' or len(call.args) != 1
                    or not isinstance(call.args[0], ast.List) or len(call.args[0].elts) != 2):
                raise ValueError('Generated script invocation differs')
            edits.extend([(call.func, '_ot_generated_run'), (call.args[0].elts[0], repr('ot-clean-python'))])
            found_runs.add(digest)
    if found_writes != writes or found_runs != runs:
        raise ValueError('Generated script source binding differs')
    data = text.encode(); offsets = [0]
    for line in data.splitlines(keepends=True):
        offsets.append(offsets[-1] + len(line))
    for node, replacement in sorted(edits, key=lambda item:(item[0].lineno,item[0].col_offset), reverse=True):
        start, end = offsets[node.lineno-1]+node.col_offset, offsets[node.end_lineno-1]+node.end_col_offset
        data = data[:start] + replacement.encode() + data[end:]
    helper = '\n_ot_generated_sources = ' + repr(sources) + '''
def _ot_generated_source(content):
    return _ot_generated_sources[content]

def _ot_generated_run(argv, **kwargs):
    import pathlib
    import subprocess
    import sys
    if len(argv) != 2 or argv[0] != 'ot-clean-python' or kwargs.get('shell'):
        raise ValueError('Unexpected generated grader invocation')
    script = pathlib.Path(argv[1])
    if not script.is_absolute() or script.read_text() not in _ot_generated_sources.values():
        raise ValueError('Generated grader content changed')
    return subprocess.run([sys.executable, '-I', str(script)], **kwargs)
'''
    return data.decode(), helper


def restore_verifier_helpers(files):
    """Restore explicitly reviewed Docker COPY helpers from immutable task bytes."""
    restored = dict(files)
    for role in task_module_roles(files):
        for target, source in role.get('verifier_helper_sources', {}).items():
            if (not re.fullmatch(r'tests/[A-Za-z0-9_]+\.py', target)
                    or not source.startswith('environment/files/')
                    or source not in role['evidence_sha256']):
                raise ValueError('Verifier helper source binding requires review')
            if target in restored and restored[target] != files[source]:
                raise ValueError('Verifier helper conflicts with supplied tests')
            restored[target] = files[source]
    return restored


def adapt_project_tests(text, files, image):
    """Run reviewed pristine regression suites from freshly uploaded /tests code."""
    from data.calibforge.verifier_runtime import preserve_cwd_task_imports
    records=[r for r in task_module_roles(files) if r.get('test_source_sha256')==hashlib.sha256(text.encode()).hexdigest()
             and r.get('project_test_calls')]
    if not records:return text,''
    if len(records)!=1 or '_ot_project_pytest' in text:raise ValueError('Project test role collision')
    record=records[0];sources={}
    for target,entry in record['project_test_sources'].items():
        if 'text' in entry:
            body=entry['text']
            if (hashlib.sha256(body.encode()).hexdigest()!=entry['sha256']
                    or record.get('project_image_sha256')!=image.get('source_image_sha256')):
                raise ValueError('Pristine project test image or source differs')
        else:
            body=files[entry['source_file']].decode()
        tree=ast.parse(body)
        # These source-bound suites use __file__ only to construct this import
        # path. Resolve it against its original location before relocation.
        edits=[]
        for call in ast.walk(tree):
            if isinstance(call,ast.Call) and ast.unparse(call.func)=='sys.path.insert':
                edits.append((call.args[1],repr(entry['import_root'])))
        data=body.encode();offsets=[0]
        for line in data.splitlines(keepends=True):offsets.append(offsets[-1]+len(line))
        for node,value in sorted(edits,key=lambda x:(x[0].lineno,x[0].col_offset),reverse=True):
            a,b=offsets[node.lineno-1]+node.col_offset,offsets[node.end_lineno-1]+node.end_col_offset
            data=data[:a]+value.encode()+data[b:]
        body=data.decode()
        rewritten_tree=ast.parse(body)
        parents={child:parent for parent in ast.walk(rewritten_tree) for child in ast.iter_child_nodes(parent)}
        for node in ast.walk(rewritten_tree):
            if not isinstance(node,ast.Name) or node.id!='__file__':continue
            child=node;ancestor=parents.get(node)
            while ancestor is not None:
                if (isinstance(ancestor,ast.If) and ast.unparse(ancestor.test)=="__name__ == '__main__'"
                        and child in ancestor.body):break
                child=ancestor;ancestor=parents.get(ancestor)
            if ancestor is None:
                raise ValueError('Relocated project test file semantics require review')
        if any(isinstance(n,ast.Attribute) and ast.unparse(n)=='sys.executable' for n in ast.walk(ast.parse(body))):
            raise ValueError('Project test nested interpreter requires review')
        sources[target]=preserve_cwd_task_imports(body,files,{**image,'implicit_task_root':entry['import_root'],'test_file':target})
    expected=set(record['project_test_calls']);found=set();edits=[]
    for call in ast.walk(ast.parse(text)):
        if not isinstance(call,ast.Call):continue
        digest=hashlib.sha256(ast.dump(call,include_attributes=False).encode()).hexdigest()
        if digest not in expected:continue
        if (ast.unparse(call.func)!='subprocess.run' or len(call.args)!=1
                or not isinstance(call.args[0],ast.List) or len(call.args[0].elts)<4):
            raise ValueError('Project test invocation differs')
        edits.extend([(call.func,'_ot_project_pytest'),(call.args[0].elts[0],repr('ot-clean-python'))]);found.add(digest)
    if found!=expected:raise ValueError('Project test source differs')
    data=text.encode();offsets=[0]
    for line in data.splitlines(keepends=True):offsets.append(offsets[-1]+len(line))
    for node,value in sorted(edits,key=lambda x:(x[0].lineno,x[0].col_offset),reverse=True):
        a,b=offsets[node.lineno-1]+node.col_offset,offsets[node.end_lineno-1]+node.end_col_offset
        data=data[:a]+value.encode()+data[b:]
    groups=record.get('project_test_groups',{})
    if any(not group or not set(group)<=sources.keys() for group in groups.values()):
        raise ValueError('Project test group includes unreviewed sources')
    helper='\n_ot_project_test_sources = '+repr(sources)+'\n_ot_project_test_groups = '+repr(groups)+'''
def _ot_project_pytest(argv, **kwargs):
    import os
    import pathlib
    import subprocess
    import sys
    import tempfile
    if (argv[:3] != ['ot-clean-python','-m','pytest']
            or argv[3] not in {*_ot_project_test_sources,*_ot_project_test_groups} or kwargs.get('shell')):
        raise ValueError('Unexpected project regression invocation')
    with tempfile.TemporaryDirectory(prefix='.ot-project-tests-',dir='/tests') as directory:
        members=_ot_project_test_groups.get(argv[3],[argv[3]])
        for member in members:
            target=pathlib.Path(directory)/pathlib.Path(member).name
            target.write_text(_ot_project_test_sources[member])
        if argv[3] in _ot_project_test_groups:
            target=pathlib.Path(directory)
        env=dict(kwargs.pop('env',os.environ))
        env['PYTEST_DISABLE_PLUGIN_AUTOLOAD']='1'
        env.pop('PYTEST_ADDOPTS',None)
        env.pop('PYTEST_PLUGINS',None)
        return subprocess.run([sys.executable,'-I','-m','pytest','--import-mode=importlib','--noconftest',str(target),*argv[4:]],env=env,**kwargs)
'''
    return data.decode(),helper


def adapt_wrapped_grading(text, files):
    """Expose an exact reviewed Valgrind child for ordinary clean-Python review."""
    records=[role for role in task_module_roles(files)
             if role.get('test_source_sha256')==hashlib.sha256(text.encode()).hexdigest()
             and role.get('valgrind_call_ast_sha256')]
    if not records:return text,''
    if len(records)!=1 or '_ot_valgrind_' in text:
        raise ValueError('Valgrind grading wrapper collision')
    edits=[];record=records[0]
    for node in ast.walk(ast.parse(text)):
        if not isinstance(node,ast.Call) or hashlib.sha256(ast.dump(node).encode()).hexdigest()!=record['valgrind_call_ast_sha256']:
            continue
        if ast.unparse(node.func)!='subprocess.run' or len(node.args)!=1 or not isinstance(node.args[0],ast.List):
            raise ValueError('Valgrind grading invocation differs')
        argv=node.args[0]
        if (len(argv.elts)!=6 or [ast.literal_eval(n) for n in argv.elts[:3]]!=['valgrind','--leak-check=full','--error-exitcode=99']
                or ast.unparse(argv.elts[3])!='sys.executable' or ast.literal_eval(argv.elts[4])!='-c'
                or any(k.arg not in ('cwd','capture_output','text','timeout') for k in node.keywords)):
            raise ValueError('Valgrind grading arguments differ')
        edits.extend([(node.func,'_ot_valgrind_run'),(argv,ast.unparse(ast.List(elts=argv.elts[3:],ctx=ast.Load())))])
    if len(edits)!=2:raise ValueError('Valgrind grading call binding differs')
    data=text.encode();offsets=[0]
    for line in data.splitlines(keepends=True):offsets.append(offsets[-1]+len(line))
    for node,value in sorted(edits,key=lambda item:(item[0].lineno,item[0].col_offset),reverse=True):
        start=offsets[node.lineno-1]+node.col_offset;end=offsets[node.end_lineno-1]+node.end_col_offset
        data=data[:start]+value.encode()+data[end:]
    helper='''
def _ot_valgrind_run(argv, **kwargs):
    import os, subprocess, sys
    if argv[:3] != [sys.executable, '-I', '-c'] or len(argv) != 4:
        raise ValueError('Valgrind requires the isolated clean grading interpreter')
    env = dict(os.environ)
    env['VALGRIND_LIB'] = env['OT_VERIFIER_BIN'] + '/../native-resources/valgrind'
    env['LD_LIBRARY_PATH'] = (env['LD_LIBRARY_PATH'] + ':' if env.get('LD_LIBRARY_PATH') else '') + '/usr/lib/debug'
    env['GLIBCPP_FORCE_NEW'] = env['GLIBCXX_FORCE_NEW'] = '1'
    return subprocess.run([env['OT_VERIFIER_BIN']+'/valgrind', '--leak-check=full', '--error-exitcode=99', *argv], env=env, **kwargs)
'''
    return data.decode(),helper


def adapt_stdin_grading(text, files, image):
    """Separate literal stdin graders from a reviewed supplied regression script."""
    from data.calibforge.verifier_runtime import static_paths, preserve_cwd_task_imports, task_python_calls
    records=[role for role in task_module_roles(files)
             if role.get('test_source_sha256')==hashlib.sha256(text.encode()).hexdigest()
             and role.get('stdin_grading_helper')]
    if not records:return text,''
    if len(records)!=1 or '_ot_stdin_' in text or not image.get('original_uvx_path'):
        raise ValueError('Stdin grading helper requires its original primary Python')
    record=records[0]['stdin_grading_helper'];tree=ast.parse(text);paths=static_paths(tree)
    if any(not re.fullmatch(r'/tests/[A-Za-z0-9_]+\.py',target) or target[1:] not in files
           for target in record['scripts'].values()):
        raise ValueError('Stdin grader requires fresh supplied regression scripts')
    definitions=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name==record['function']]
    if len(definitions)!=1 or hashlib.sha256(ast.dump(definitions[0]).encode()).hexdigest()!=record['function_ast_sha256']:
        raise ValueError('Stdin grading helper source differs')
    parent={child:node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    sources={};seen=set()
    for node in ast.walk(tree):
        if not isinstance(node,ast.Name) or node.id!=record['function'] or not isinstance(node.ctx,ast.Load):continue
        call=parent.get(node)
        if not isinstance(call,ast.Call) or call.func is not node or len(call.args)!=1:
            raise ValueError('Stdin grading helper has an unreviewed caller')
        target=paths(call.args[0])
        if target=={'/dev/stdin'}:
            value=next((k.value for k in call.keywords if k.arg=='input_data'),None)
            snippets=paths(value) if value is not None else set()
            if len(snippets)!=1:raise ValueError('Stdin grading caller is not literal')
            source=next(iter(snippets));context={**image,'implicit_task_root':'/app'}
            reviewed,_=task_python_calls(source,None,files=files,image=context)
            sources[source]=preserve_cwd_task_imports(reviewed,files,context)
        elif len(target)==1 and next(iter(target)) in record['scripts']:
            if any(k.arg=='input_data' for k in call.keywords):raise ValueError('File grader has unexpected stdin')
            seen.update(target)
        else:raise ValueError('Stdin grading caller target differs')
    if seen!=set(record['scripts']) or not sources:raise ValueError('Stdin grading caller coverage differs')
    calls=[n for n in ast.walk(definitions[0]) if isinstance(n,ast.Call) and ast.unparse(n.func)=='subprocess.run']
    if len(calls)!=1:raise ValueError('Stdin grading invocation differs')
    call=calls[0];argv=call.args[0]
    if not isinstance(argv,ast.List) or len(argv.elts)!=2 or ast.unparse(argv.elts[0])!='sys.executable':
        raise ValueError('Stdin grading interpreter differs')
    edits=[(call.func,'_ot_stdin_run'),(argv.elts[0],repr('ot-clean-python'))]
    data=text.encode();offsets=[0]
    for line in data.splitlines(keepends=True):offsets.append(offsets[-1]+len(line))
    for node,value in sorted(edits,key=lambda item:(item[0].lineno,item[0].col_offset),reverse=True):
        start=offsets[node.lineno-1]+node.col_offset;end=offsets[node.end_lineno-1]+node.end_col_offset
        data=data[:start]+value.encode()+data[end:]
    helper='\n_ot_stdin_sources='+repr(sources)+'\n_ot_stdin_scripts='+repr(record['scripts'])+'''
def _ot_stdin_run(argv, **kwargs):
    import sys, subprocess
    if len(argv)!=2 or argv[0]!='ot-clean-python' or kwargs.get('shell'):
        raise ValueError('Unexpected stdin grading invocation')
    if argv[1]=='/dev/stdin':
        kwargs['input']=_ot_stdin_sources[kwargs['input']]
        target='/dev/stdin'
    else:
        target=_ot_stdin_scripts[argv[1]]
    return subprocess.run([sys.executable,'-I',target],**kwargs)
'''
    return data.decode(),helper


def pristine_project_suite(files, image_sha256, image):
    """Restore a source-bound upstream suite, retaining its submitted package imports."""
    from data.calibforge.verifier_runtime import installed_task_import_adapter
    records=[role for role in task_module_roles(files) if role.get('pristine_suite')
             and hashlib.sha256(files.get('tests/test_outputs.py',b'')).hexdigest()==role.get('test_source_sha256')]
    if not records:return {},None,''
    if len(records)!=1:raise ValueError('Pristine suite roles overlap')
    record=records[0]['pristine_suite'];package=record['package']
    if image_sha256!=record['image_sha256'] or not re.fullmatch(r'[A-Za-z_]\w*',package):
        raise ValueError('Pristine suite image binding differs')
    root='/tests/ot_pristine_'+package
    assets={'tests/ot_pristine_'+package+'/'+package+'/__init__.py':b''}
    for path,entry in record['files'].items():
        is_test = bool(re.fullmatch(re.escape(package)+r'/tests/[A-Za-z0-9_]+\.py',path))
        is_resource = bool(re.fullmatch(re.escape(package)+r'/test_images/[A-Za-z0-9_.-]+\.fits\.fz',path))
        if not (is_test or is_resource):
            raise ValueError('Pristine suite member path requires review')
        import base64
        body=entry['text'].encode() if 'text' in entry else base64.b64decode(entry['base64'],validate=True)
        if hashlib.sha256(body).hexdigest()!=entry['sha256'] or len(body)!=entry['bytes']:
            raise ValueError('Pristine suite member digest differs')
        if is_resource:
            assets[root[1:]+'/'+path]=body
            continue
        tree=ast.parse(body)
        for node in ast.walk(tree):
            if isinstance(node,ast.Import):imports={alias.name.split('.')[0] for alias in node.names}
            elif isinstance(node,ast.ImportFrom) and not node.level:imports={node.module.split('.')[0]}
            else:imports=set()
            if imports-set(image['stdlib'])-set(image.get('import_names',[]))-{'pytest'}:
                raise ValueError('Pristine suite dependency missing from clean runtime')
            if (isinstance(node,ast.Attribute) and ast.unparse(node)=='sys.executable'
                    or isinstance(node,ast.Call) and ast.unparse(node.func).startswith(('subprocess.','os.exec'))):
                raise ValueError('Pristine suite child execution requires review')
            if isinstance(node,ast.ImportFrom) and node.level>2:
                raise ValueError('Pristine suite relative import escapes its package')
        text=body.decode()
        for node in ast.walk(tree):
            if isinstance(node,ast.Call) and ast.unparse(node.func)=='importlib.resources.files':
                if (len(node.args)!=1 or not isinstance(node.args[0],ast.Constant) or node.args[0].value!=package
                        or not any(p.startswith(package+'/test_images/') for p in record['files'])):
                    raise ValueError('Pristine suite resource source requires review')
                original=ast.get_source_segment(body.decode(),node)
                text=text.replace(original,"__import__('pathlib').Path("+repr(root+'/'+package)+")")
        body=text.encode()
        assets[root[1:]+'/'+path]=body
    source=files['tests/test_outputs.py'].decode();tree=ast.parse(source);edits=[]
    for node in ast.walk(tree):
        if not isinstance(node,ast.Call) or hashlib.sha256(ast.dump(node).encode()).hexdigest()!=record['call_ast_sha256']:continue
        if (ast.unparse(node.func)!='subprocess.run' or not node.args or not isinstance(node.args[0],ast.List)
                or len(node.args[0].elts)!=5 or ast.unparse(node.args[0].elts[0])!='sys.executable'):
            raise ValueError('Pristine suite invocation differs')
        edits.extend([(node.func,'_ot_pristine_suite_run'),(node.args[0].elts[0],repr('ot-clean-python'))])
    if len(edits)!=2:raise ValueError('Pristine suite call binding differs')
    data=source.encode();offsets=[0]
    for line in data.splitlines(keepends=True):offsets.append(offsets[-1]+len(line))
    for node,value in sorted(edits,key=lambda item:(item[0].lineno,item[0].col_offset),reverse=True):
        start=offsets[node.lineno-1]+node.col_offset;end=offsets[node.end_lineno-1]+node.end_col_offset
        data=data[:start]+value.encode()+data[end:]
    suite=root+'/'+package+'/tests'
    bootstrap=installed_task_import_adapter({package},image['prefix'])+'\n'+'''import importlib, importlib.util, sys
importlib.import_module(PACKAGE)
class SuiteFinder:
    @staticmethod
    def find_spec(fullname,path=None,target=None):
        if fullname==PACKAGE+'.tests':
            return importlib.util.spec_from_file_location(fullname,SUITE+'/__init__.py',submodule_search_locations=[SUITE])
sys.meta_path.insert(0,SuiteFinder)
importlib.import_module(PACKAGE+'.tests')
import pytest
raise SystemExit(pytest.main(['-c','/dev/null','--rootdir',SUITE,'--confcutdir',SUITE,'--import-mode=importlib',SUITE,*sys.argv[1:]]))
'''.replace('PACKAGE',repr(package)).replace('SUITE',repr(suite))
    helper='\n_ot_pristine_suite_bootstrap='+repr(bootstrap)+'\n_ot_pristine_suite_targets='+repr(record['targets'])+'''
def _ot_pristine_suite_run(argv, **kwargs):
    import sys,subprocess
    if (len(argv)!=5 or argv[:3]!=['ot-clean-python','-m','pytest']
            or argv[3] not in _ot_pristine_suite_targets or argv[4]!='-v' or kwargs.get('shell')):
        raise ValueError('Unexpected pristine suite invocation')
    return subprocess.run([sys.executable,'-I','-c',_ot_pristine_suite_bootstrap,argv[4]],**kwargs)
'''
    return assets,data.decode(),helper


def preserve_task_metadata(text, files, image):
    """Read only the named submitted distribution's version, without importing it."""
    from data.calibforge.verifier_runtime import task_distributions
    names=set(task_distributions(files))|{name for role in task_module_roles(files) for name in role.get('installed_modules',[])}
    calls=[node for node in ast.walk(ast.parse(text)) if isinstance(node,ast.Call)
           and ast.unparse(node.func)=='importlib.metadata.version' and len(node.args)==1
           and isinstance(node.args[0],ast.Constant) and node.args[0].value in names and not node.keywords]
    if not calls:return text
    prefix=image.get('task_package_prefixes',image.get('prefix'))
    prefixes=prefix if isinstance(prefix,list) else [prefix]
    if not prefixes or any(not isinstance(path,str) or not path.startswith('/') or '..' in path.split('/') for path in prefixes):
        raise ValueError('Submitted distribution metadata prefix requires review')
    data=text.encode();offsets=[0]
    for line in data.splitlines(keepends=True):offsets.append(offsets[-1]+len(line))
    for call in sorted(calls,key=lambda n:(n.lineno,n.col_offset),reverse=True):
        node=call.func;a=offsets[node.lineno-1]+node.col_offset;b=offsets[node.end_lineno-1]+node.end_col_offset
        data=data[:a]+b'_ot_task_distribution_version'+data[b:]
    helper='\n_ot_task_metadata_names='+repr(sorted({call.args[0].value for call in calls}))+'\n_ot_task_metadata_prefixes='+repr(prefixes)+'''
def _ot_task_distribution_version(name):
    from importlib import metadata as _ot_metadata
    import pathlib,site,re
    if name not in _ot_task_metadata_names:raise ValueError('Unreviewed task distribution')
    roots=site.getsitepackages(_ot_task_metadata_prefixes)
    for root in list(roots):
        for pth in pathlib.Path(root).glob('*.pth'):
            for line in pth.read_text(errors='replace').splitlines():
                if line.startswith('/') and pathlib.Path(line).is_dir():roots.append(line)
    normalize=lambda value:re.sub('[-_.]+','-',value).lower()
    for distribution in _ot_metadata.distributions(path=roots):
        if normalize(distribution.metadata.get('Name',''))==normalize(name):return distribution.version
    raise _ot_metadata.PackageNotFoundError(name)
'''
    # The helper must be defined before an immediate -c metadata lookup.
    return helper+'\n'+data.decode()


def selector_package_calls(tree, parents, aliases, alias_key, record, name):
    """Review composite pip argv uses, preserving their original install points."""
    alias=record['alias']
    assignments=[n for n in ast.walk(tree) if isinstance(n,ast.Assign)
                 and len(n.targets)==1 and isinstance(n.targets[0],ast.Name) and n.targets[0].id==alias]
    stores=[n for n in ast.walk(tree) if isinstance(n,ast.Name) and n.id==alias and isinstance(n.ctx,(ast.Store,ast.Del))]
    if len(assignments)!=1 or len(stores)!=1:
        raise ValueError('Composite package alias binding differs')
    value=assignments[0].value
    if (not isinstance(value,ast.List) or len(value.elts)!=3
            or not isinstance(value.elts[0],ast.Name) or alias_key(value.elts[0]) not in aliases
            or [ast.literal_eval(n) for n in value.elts[1:]]!=['-m','pip']):
        raise ValueError('Composite package argv differs')
    expected=set(record['calls']);seen=set();edits=[]
    for node in ast.walk(tree):
        if not isinstance(node,ast.Name) or node.id!=alias or not isinstance(node.ctx,ast.Load):continue
        binary=parents[node];call=parents.get(binary)
        if (not isinstance(binary,ast.BinOp) or not isinstance(binary.op,ast.Add) or binary.left is not node
                or not isinstance(binary.right,ast.List) or not binary.right.elts
                or not isinstance(binary.right.elts[0],ast.Constant) or binary.right.elts[0].value not in ('install','show')
                or not isinstance(call,ast.Call) or ast.unparse(call.func)!='subprocess.run'
                or not call.args or call.args[0] is not binary):
            raise ValueError('Composite package alias use requires review')
        digest=hashlib.sha256(ast.dump(call).encode()).hexdigest()
        if digest not in expected:raise ValueError('Composite package call differs')
        seen.add(digest);edits.append((call.func,'_ot_selector_package_run'))
    if seen!=expected:raise ValueError('Composite package call coverage differs')
    helper='\n_ot_selector_package_name='+repr(name)+'''
def _ot_selector_package_run(argv, **kwargs):
    import os as _ot_package_os
    import subprocess as _ot_package_subprocess
    clean=_ot_package_os.environ['OT_VERIFIER_BIN']+'/../aux/'+_ot_selector_package_name+'/bin/python3'
    if argv[:3]!=[clean,'-m','pip'] or argv[3] not in ('install','show') or kwargs.get('shell'):
        raise ValueError('Unreviewed composite package command')
    env=dict(kwargs.pop('env',_ot_package_os.environ))
    for key in list(env):
        if key.startswith(('PYTHON','PIP_','UV_')):env.pop(key)
    env['PIP_CONFIG_FILE']='/dev/null'
    args=argv[3:]
    offline=_ot_package_os.environ.get('OT_VERIFIER_OFFLINE')
    if args[0]=='install' and offline:
        args=[args[0],'--no-index','--find-links',offline+'/wheels',*args[1:]]
        env.update(PIP_NO_INDEX='1',PIP_FIND_LINKS=offline+'/wheels')
    return _ot_package_subprocess.run([clean,'-I','-m','pip','--isolated',*args],env=env,**kwargs)
'''
    return edits,{value.elts[0]},helper


def shell_dependency_installation(files, image_sha256):
    """Retain source-bound system dependency installs at their original point."""
    assets,commands={},{}
    for role in task_module_roles(files):
        item=role.get('shell_dependency_installation')
        if not item:continue
        script=files.get('tests/test.sh',b'').decode()
        if (hashlib.sha256(script.encode()).hexdigest()!=item['test_script_sha256']
                or image_sha256!=item['spec']['image_sha256']
                or script.count(item['block'])!=1
                or item['spec'].get('apt')!=['python3-numpy']
                or item['block']!='apt-get update -qq 2>/dev/null || true\napt-get install -y -qq python3-numpy 2>/dev/null || true'):
            raise ValueError('System dependency installation binding differs')
        target='tests/ot_system_numpy.sh'
        if target in files:raise ValueError('System dependency helper collision')
        assets[target]=('#!/bin/bash\n'
            'if [ -n "${OT_VERIFIER_OFFLINE:-}" ]; then\n'
            '  export APT_CONFIG="$OT_VERIFIER_OFFLINE/apt.conf"\n'
            '  # A single mapped root UID cannot switch to the _apt user.\n'
            '  if { read -r _ot_uid_inside _ot_uid_outside _ot_uid_count && ! read -r _ot_uid_extra; } </proc/self/uid_map &&\n'
            '     [ "$_ot_uid_inside" = 0 ] && [ "$_ot_uid_count" = 1 ]; then\n'
            '    printf \'APT::Sandbox::User "root";\\n\' >"$OT_VERIFIER_OFFLINE/apt/conf.d/zzzzz-ot-calibforge-rootmap" || exit $?\n'
            '  fi\nfi\n'+item['block']+'\n').encode()
        commands[item['block']]='bash /'+target
    return assets,commands


def adapt_selectable_grading(files, records):
    """Keep a submitted package selector while separating its grading children."""
    from data.calibforge.verifier_runtime import installed_task_import_adapter, static_paths
    first=next(iter(records.values()));path=first['source_file'];source=files[path].decode()
    digest=hashlib.sha256(source.encode()).hexdigest()
    if (any(r['source_file']!=path or r['source_sha256']!=digest or r['selector']!='_find_python_with_faiss'
            or r['main_executable']!=first['main_executable'] for r in records.values())
            or '_ot_selectable_' in source):
        raise ValueError('Selectable grading source binding differs')
    tree=ast.parse(source);paths=static_paths(tree,'/'+path);calls=[];snippets={'import faiss'};edits=[]
    for node in ast.walk(tree):
        if isinstance(node,ast.Attribute) and ast.unparse(node)=='sys.executable':
            edits.append((node,repr(first['main_executable'])))
        if not isinstance(node,ast.Call) or ast.unparse(node.func)!='subprocess.run':continue
        calls.append(hashlib.sha256(ast.dump(node).encode()).hexdigest())
        edits.append((node.func,'_ot_selectable_run'))
        argv=node.args[0]
        if not isinstance(argv,ast.List):raise ValueError('Selectable grading argv differs')
        if len(argv.elts)==3 and paths(argv.elts[1])=={'-c'}:
            values=paths(argv.elts[2])
            if not values:raise ValueError('Selectable grader must have literal source')
            snippets.update(values)
    if any(sorted(r['uses'])!=sorted(calls) for r in records.values()):
        raise ValueError('Selectable grading call coverage differs')
    if len(snippets)!=3:raise ValueError('Selectable grading literal coverage differs')
    routes={}
    for name,record in records.items():
        prefix=record['observed']['prefix']
        if not prefix.startswith('/') or '..' in prefix.split('/'):
            raise ValueError('Selectable task prefix differs')
        adapter=installed_task_import_adapter({'faiss'},prefix)
        metadata='''from importlib import metadata
import site,re,sys
name=sys.argv[1]
assert name in ('faiss-cpu','faiss-gpu')
normalize=lambda value:re.sub('[-_.]+','-',value).lower()
names={normalize(d.metadata.get('Name','')) for d in metadata.distributions(path=site.getsitepackages(PREFIXES))}
# The original command also failed when pip was absent in this candidate.
raise SystemExit(0 if name in names and 'pip' in names else 1)
'''.replace('PREFIXES',repr([prefix]))
        for candidate in record['candidates']:
            if candidate in routes or not candidate.startswith('/') or '..' in candidate.split('/'):
                raise ValueError('Selectable Python candidate binding differs')
            routes[candidate]=dict(auxiliary=name,snippets={code:adapter+'\n'+code for code in sorted(snippets)},metadata=metadata)
    data=source.encode();offsets=[0]
    for line in data.splitlines(keepends=True):offsets.append(offsets[-1]+len(line))
    for node,value in sorted(edits,key=lambda item:(item[0].lineno,item[0].col_offset),reverse=True):
        a=offsets[node.lineno-1]+node.col_offset;b=offsets[node.end_lineno-1]+node.end_col_offset
        data=data[:a]+value.encode()+data[b:]
    helper='\n_ot_selectable_routes='+repr(routes)+'''
def _ot_selectable_run(argv, **kwargs):
    import os as _ot_selectable_os
    import shutil as _ot_selectable_shutil
    import subprocess as _ot_selectable_subprocess
    if kwargs.get('shell'):raise ValueError('Unreviewed selectable shell command')
    if len(argv)==2 and argv[0]=='which' and argv[1] in ('python3','python'):
        path=_ot_selectable_shutil.which(argv[1])
        return _ot_selectable_subprocess.CompletedProcess(argv,0 if path else 1,(path+'\\n') if path else '','')
    if len(argv)==2 and argv[1]=='/app/benchmark.py':
        # This is the submitted program, not a grading child.
        return _ot_selectable_subprocess.run(argv,**kwargs)
    route=_ot_selectable_routes.get(argv[0])
    if route is None:raise ValueError('Unprepared original selector Python: '+str(argv[0]))
    clean=_ot_selectable_os.environ['OT_VERIFIER_BIN']+'/../aux/'+route['auxiliary']+'/bin/python3'
    if len(argv)==3 and argv[1]=='-c' and argv[2] in route['snippets']:
        return _ot_selectable_subprocess.run([clean,'-I','-c',route['snippets'][argv[2]]],**kwargs)
    if len(argv)==5 and argv[1:4]==['-m','pip','show'] and argv[4] in ('faiss-cpu','faiss-gpu'):
        return _ot_selectable_subprocess.run([clean,'-I','-c',route['metadata'],argv[4]],**kwargs)
    raise ValueError('Unreviewed selectable grading invocation')
'''
    return path,data.decode()+helper
