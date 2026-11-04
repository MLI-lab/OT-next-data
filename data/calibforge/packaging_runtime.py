"""Reviewed package-installation tests using fresh private child environments.

The submitted project is built at its original test point. Only build inputs,
never the project or a trial's installed package, belong in the shared cache.
"""
import ast
import hashlib
import json
from pathlib import Path
from functools import lru_cache

from data.calibforge.python_roles import current_task


# This exact pristine image supplies the same managed 3.13.9 interpreter as
# the reviewed uvx family. Wheel preparation does not use a task installation.
BUILD_IMAGE = 'f9bd7475220d5526353ef265353308450a7bdd54589cbba6dea167167998caf5'


@lru_cache(maxsize=4)
def _records(path):
    return json.loads(path.read_text())


def role(files, source_file, image):
    records=_records(Path(__file__).with_name('task_packaging_roles.json'))
    matches=[record for record in records
             if record['source_file']==source_file
             and record['task']==current_task.get()
             and hashlib.sha256(files.get(source_file,b'')).hexdigest()==record['source_sha256']
             and all(path in files and hashlib.sha256(files[path]).hexdigest()==digest
                     for path,digest in record['evidence_sha256'].items())]
    if not matches:return None
    if (len(matches)!=1 or image.get('python')!=matches[0].get('python','3.13.9')
            or bool(image.get('original_uvx_path'))!=matches[0].get('original_uvx',True)):
        raise ValueError('CalibForge packaging requires its exact original Python selection')
    return matches[0]


def adapt(files, source_file, image):
    """Return source edits and a helper only for an entirely reviewed test file."""
    record=role(files,source_file,image)
    if record is None:return files[source_file].decode(),'',None
    source=files[source_file].decode();tree=ast.parse(source)
    if any(isinstance(n,ast.Name) and n.id.startswith('_ot_packaging_') for n in ast.walk(tree)):
        raise ValueError('CalibForge packaging helper name collision')
    parents={child:node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    edits=[];calls=[]
    for node in ast.walk(tree):
        if (isinstance(node,ast.Call) and ast.unparse(node.func)=='os.path.exists'
                and len(node.args)==1 and isinstance(node.args[0],ast.Constant)
                and node.args[0].value in record.get('fresh_venv_guards', [])):
            edits.append((node.func,'_ot_packaging_has_venv'))
        if not isinstance(node,ast.Call) or ast.unparse(node.func)!='subprocess.run':continue
        digest=hashlib.sha256(ast.dump(node).encode()).hexdigest()
        if digest not in record['calls']:
            raise ValueError('CalibForge packaging invocation requires review')
        owner=parents.get(node)
        while owner is not None and not isinstance(owner,ast.FunctionDef):owner=parents.get(owner)
        if owner is None:raise ValueError('CalibForge packaging cannot run before helper initialization')
        calls.append(digest)
        if digest in record.get('primary_python_calls',[]):continue
        if digest==record.get('shell_call'):
            edits.append((node.func,'_ot_packaging_shell_run'));continue
        if digest==record.get('temporary_script_call'):
            edits.append((node.func,'_ot_packaging_script_run'));continue
        edits.append((node.func,'_ot_packaging_run'))
        argv=node.args[0] if node.args else None
        if (isinstance(argv,ast.List) and len(argv.elts)==4
                and isinstance(argv.elts[0],ast.Constant) and argv.elts[0].value in ('python','python3')
                and all(isinstance(value,ast.Constant) for value in argv.elts[1:3])
                and [value.value for value in argv.elts[1:3]]==['-m','venv']):
            # The measured uvx family prepends its own bin directory to PATH.
            # This literal child therefore used the same parent Python.
            edits.append((argv.elts[0],'_ot_packaging_primary_python'))
    # A reviewed file may pass creation commands through a local run helper.
    for child in ast.walk(tree):
        if isinstance(child,ast.Attribute) and ast.unparse(child)=='sys.executable':
            edits.append((child,'_ot_packaging_primary_python'))
    if sorted(calls)!=sorted(record['calls']):
        raise ValueError('CalibForge packaging call coverage differs')
    data=source.encode();offsets=[0]
    for line in data.splitlines(keepends=True):offsets.append(offsets[-1]+len(line))
    for node,text in sorted(edits,key=lambda item:(item[0].lineno,item[0].col_offset),reverse=True):
        start=offsets[node.lineno-1]+node.col_offset;end=offsets[node.end_lineno-1]+node.end_col_offset
        data=data[:start]+text.encode()+data[end:]
    fixture = record.get('local_http_fixture')
    if fixture is not None and fixture != {'command':'datafetch','url':'https://httpbin.org/get'}:
        raise ValueError('CalibForge packaging HTTP fixture requires review')
    primary = record.get('primary_commands', [])
    if primary and primary not in (['python','pip','filestats','fs-analyze'],
            ['python','pip','datapipeline','datapipeline-process','datapipeline-validate','datapipeline-transform'],
            ['pip','taskflow','tf'], ['pip']):
        raise ValueError('CalibForge primary packaging commands require review')
    suffix=''
    auxiliary=record.get('auxiliary_installer')
    if auxiliary:
        if auxiliary!='package_installer' or primary!=['pip','taskflow','tf']:
            raise ValueError('Auxiliary packaging installer requires review')
        suffix="\n_ot_packaging_auxiliary = True\n_ot_packaging_primary_python = _ot_packaging_os.environ['OT_VERIFIER_BIN']+'/../aux/package_installer/bin/python3'\n"
    if record.get('shell_commands'):
        if primary!=['pip'] or not record.get('grading_scripts'):
            raise ValueError('Reviewed shell packaging requires literal grading sources')
        suffix+='\n_ot_packaging_shell_commands='+repr(record['shell_commands'])+'\n_ot_packaging_grading_scripts='+repr(record['grading_scripts'])+'\n'+SHELL_HELPER
    return data.decode(),HELPER+'\n_ot_packaging_http_fixture='+repr(fixture)+'\n_ot_packaging_primary_commands='+repr(primary)+'\n'+suffix,record


def offline_spec(record):
    builder=record.get('offline_builder',dict(image_sha256=BUILD_IMAGE,python='/opt/verifier/bin/python'))
    return dict(apt=[],uv=True,python=[dict(tool='uv',python=builder['python'],
                requirements=record['build_requirements'])],image_sha256=builder['image_sha256'])


HELPER = '''
# Clean packaging children: no installed submitted project enters a shared cache.
import os as _ot_packaging_os
import sys as _ot_packaging_sys
import sysconfig as _ot_packaging_sysconfig
import tempfile as _ot_packaging_tempfile
import subprocess as _ot_packaging_subprocess
from pathlib import Path as _ot_packaging_Path
_ot_packaging_primary_python = _ot_packaging_sys.executable
_ot_packaging_roots = {}
_ot_packaging_http_fixture = None
_ot_packaging_primary_commands = []
_ot_packaging_auxiliary = False

def _ot_packaging_scripts():
    if not _ot_packaging_auxiliary:
        return _ot_packaging_sysconfig.get_path('scripts')
    result=_ot_packaging_subprocess.run([_ot_packaging_primary_python,'-I','-c',"import sysconfig;print(sysconfig.get_path('scripts'))"],capture_output=True,text=True,check=True)
    return result.stdout.strip()

def _ot_packaging_has_venv(path):
    return path in _ot_packaging_roots

def _ot_packaging_fetch_fixture(argv, kwargs):
    import http.server, json, threading
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body=json.dumps({'url':'http://127.0.0.1:'+str(self.server.server_port)+self.path,
                             'args':{},'headers':dict(self.headers),'origin':self.client_address[0]}).encode()
            self.send_response(200)
            self.send_header('Content-Type','application/json')
            self.send_header('Content-Length',str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *args):
            pass
    server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    env=dict(_ot_packaging_os.environ if kwargs.get('env') is None else kwargs['env'])
    env['NO_PROXY']=env['no_proxy']='127.0.0.1,localhost'
    try:
        return _ot_packaging_run([argv[0],'http://127.0.0.1:'+str(server.server_port)+'/get'],**{**kwargs,'env':env})
    finally:
        server.shutdown()
        server.server_close()
        thread.join()

def _ot_packaging_run(argv, **kwargs):
    argv = [str(value) for value in argv]
    if kwargs.get('shell') or 'executable' in kwargs:
        raise ValueError('Unreviewed packaging shell invocation')
    if (_ot_packaging_http_fixture and len(argv)==2
            and _ot_packaging_Path(argv[0]).name==_ot_packaging_http_fixture['command']
            and argv[1]==_ot_packaging_http_fixture['url']):
        return _ot_packaging_fetch_fixture(argv, kwargs)
    environment = kwargs.get('env')
    env = dict(_ot_packaging_os.environ if environment is None else environment)
    for key in list(env):
        if key.startswith(('PYTHON', 'PIP_', 'UV_')):
            del env[key]
    env['PIP_CONFIG_FILE'] = _ot_packaging_os.devnull
    offline = _ot_packaging_os.environ.get('OT_VERIFIER_OFFLINE')
    if offline:
        # python -m build creates another private build environment; its pip
        # child must use the same prepared inputs as explicit pip invocations.
        env['PIP_NO_INDEX'] = '1'
        env['PIP_FIND_LINKS'] = offline + '/wheels'
    kwargs['env'] = env
    if _ot_packaging_primary_commands:
        primary = str(_ot_packaging_Path(_ot_packaging_primary_python).parent.parent)
        _ot_packaging_roots[primary] = primary
    if (len(argv)==2 and argv[0]=='which' and argv[1] in _ot_packaging_primary_commands):
        if kwargs!={'capture_output':True,'text':True,'env':env}:
            raise ValueError('Unreviewed packaging executable lookup')
        scripts = _ot_packaging_Path(_ot_packaging_scripts())
        scripts.resolve().relative_to(_ot_packaging_Path(primary).resolve())
        candidate=scripts/argv[1]
        found=candidate.is_file() and _ot_packaging_os.access(candidate,_ot_packaging_os.X_OK)
        return _ot_packaging_subprocess.CompletedProcess(argv,0 if found else 1,str(candidate)+'\\n' if found else '', '')
    if argv[0] in _ot_packaging_primary_commands:
        argv[0] = primary + '/bin/' + argv[0]
    if len(argv) == 3 and argv[:2] == ['unzip', '-l']:
        argv[0] = _ot_packaging_os.environ['OT_VERIFIER_BIN'] + '/unzip'
        return _ot_packaging_subprocess.run(argv, **kwargs)
    if argv[:3] == [_ot_packaging_primary_python, '-m', 'venv']:
        if len(argv) != 4 or not _ot_packaging_Path(argv[3]).is_absolute() or argv[3] == '/' or '..' in _ot_packaging_Path(argv[3]).parts:
            raise ValueError('Unreviewed packaging venv target')
        private = _ot_packaging_tempfile.mkdtemp(prefix='.ot-calibforge-packaging-', dir='/tests')
        result = _ot_packaging_subprocess.run([_ot_packaging_primary_python, '-I', '-m', 'venv', private], **kwargs)
        if result.returncode == 0:
            _ot_packaging_roots[argv[3]] = private
        return result
    if '/' not in argv[0] and env.get('VIRTUAL_ENV') in _ot_packaging_roots:
        original = env['VIRTUAL_ENV']
        private = _ot_packaging_roots[original]
        paths = env.get('PATH', '').split(':')
        if not paths or paths[0] != original + '/bin':
            raise ValueError('Packaging CLI PATH does not select its fresh child')
        # Preserve the original test's venv selection for bare submitted CLIs.
        # Their shebangs and dependencies now belong to the private child.
        argv[0] = original + '/bin/' + argv[0]
        env['PATH'] = ':'.join(private + '/bin' if path == original + '/bin' else path for path in paths)
        env['VIRTUAL_ENV'] = private
    matches = [(original, private) for original, private in _ot_packaging_roots.items()
               if argv[0].startswith(original + '/bin/')]
    if len(matches) != 1:
        raise ValueError('Packaging command is not in a fresh verifier-created environment')
    original, private = matches[0]
    relative = argv[0][len(original):]
    if '..' in _ot_packaging_Path(relative).parts:
        raise ValueError('Packaging executable escapes its private environment')
    argv[0] = private + relative
    tool = _ot_packaging_Path(argv[0]).name
    pip_args = argv[1:] if tool == 'pip' else argv[3:] if tool in ('python','python3') and argv[1:3] == ['-m', 'pip'] else None
    if pip_args is not None:
        actions=('install','uninstall','show') if _ot_packaging_primary_commands else ('install',)
        if not pip_args or pip_args[0] not in actions:
            raise ValueError('Unreviewed packaging pip action')
        # Image-native Python can retain Debian's EXTERNALLY-MANAGED marker.
        # This prefix is the verifier's private writable runtime, never /usr.
        options = ['--break-system-packages'] if _ot_packaging_primary_commands and pip_args[0] in ('install','uninstall') else []
        offline = _ot_packaging_os.environ.get('OT_VERIFIER_OFFLINE')
        if offline and pip_args[0]=='install':
            options += ['--no-index', '--find-links', offline + '/wheels']
        argv = [private + '/bin/python', '-I', '-m', 'pip', '--isolated', pip_args[0], *options, *pip_args[1:]]
    elif tool in ('python','python3'):
        if _ot_packaging_primary_commands and argv[1:3]==['-m','build']:
            if not offline:
                raise ValueError('Primary build requires trusted prepared installer inputs')
            # Build's isolated backend still runs at the original test point.
            # Its installer is the prepared uv, avoiding distro ensurepip tools
            # outside the clean private Python runtime.
            env['PATH'] = offline+'/bin:'+env.get('PATH','')
            env['UV_NO_INDEX'] = env['UV_OFFLINE'] = '1'
            env['UV_FIND_LINKS'] = offline+'/wheels'
            env['UV_PYTHON_DOWNLOADS'] = 'never'
            env['UV_NO_CONFIG'] = '1'
            env['UV_CACHE_DIR'] = _ot_packaging_tempfile.mkdtemp(prefix='.ot-build-cache-',dir='/tests')
            argv += ['--installer','uv']
        argv.insert(1, '-I')
    elif _ot_packaging_primary_commands and tool in _ot_packaging_primary_commands:
        # Debian Python installs new console scripts in local/bin. Resolve
        # that scheme from this clean Python, while keeping it in our prefix.
        scripts = _ot_packaging_Path(_ot_packaging_scripts())
        scripts.resolve().relative_to(_ot_packaging_Path(private).resolve())
        argv[0] = str(scripts / tool)
    return _ot_packaging_subprocess.run(argv, **kwargs)
'''


SHELL_HELPER = r'''
def _ot_packaging_shell_run(command, **kwargs):
    if command not in _ot_packaging_shell_commands or kwargs.pop('shell',None) is not True:
        raise ValueError('Unreviewed package shell command')
    entry=_ot_packaging_shell_commands[command]
    result=_ot_packaging_run(entry['argv'],**kwargs)
    if entry.get('ignore_failure'):
        result=_ot_packaging_subprocess.CompletedProcess(result.args,0,result.stdout,'')
    return result

def _ot_packaging_script_run(argv, **kwargs):
    if len(argv)!=2 or argv[0]!='python3' or kwargs.get('shell'):
        raise ValueError('Unreviewed temporary package grader')
    path=_ot_packaging_Path(argv[1])
    if not path.is_absolute():raise ValueError('Temporary package grader path is not absolute')
    source=path.read_text()
    if source not in _ot_packaging_grading_scripts:
        raise ValueError('Temporary package grader source differs')
    return _ot_packaging_subprocess.run([_ot_packaging_sys.executable,'-I','-c',source],**kwargs)
'''
