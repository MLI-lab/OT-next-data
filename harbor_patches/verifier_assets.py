"""Keep per-trial reference files private on the controller until grading.

Some tasks generate reference programs during initialization. These bytes must
not enter a shared runtime cache. Capture happens strictly before the agent and
restoration strictly after it; a missing capture never reads the modified task.
"""
import ast
import hashlib
import json
from pathlib import Path, PurePosixPath
import shlex
import tarfile
import tempfile
from uuid import uuid4

REFERENCE = 'ot-verifier-assets.json'
FORMAT = 'ot-verifier-assets-v1'
TARGET = '/tests/.ot-verifier-assets'


CHILD_ENVIRONMENT = r'''
import os as _ot_os
import subprocess as _ot_subprocess
import shlex as _ot_shlex
import re as _ot_re
_ot_reference_paths = frozenset(__OT_PATHS__)
_ot_task_python = __OT_TASK_PYTHON__
_ot_original_popen = _ot_subprocess.Popen
class _OtReferencePopen(_ot_original_popen):
    def __init__(self, args, *positional, **kwargs):
        try:
            values = args if isinstance(args, (list, tuple)) else _ot_shlex.split(args) if isinstance(args, str) else []
        except ValueError:
            values = []
        executable = _ot_os.fsdecode(values[0]) if values and isinstance(values[0], (str, bytes, _ot_os.PathLike)) else ''
        interpreter = _ot_os.path.basename(executable)
        python_command = bool(_ot_re.fullmatch(r'python(?:3(?:\.\d+)?)?', interpreter))
        script = next((v for v in values[1:] if isinstance(v, (str, bytes, _ot_os.PathLike))
                       and not _ot_os.fsdecode(v).startswith('-')), None)
        reference = executable in _ot_reference_paths or (
            (python_command or interpreter in ('bash', 'sh')) and script is not None
            and _ot_os.fsdecode(script) in _ot_reference_paths)
        if (not reference and _ot_task_python and executable == _ot_os.environ.get('OT_VERIFIER_PYTHON')
                and isinstance(args, (list, tuple)) and script is not None):
            args = [_ot_task_python, *args[1:]]
        if reference:
            env = dict(_ot_os.environ if kwargs.get('env') is None else kwargs['env'])
            python = _ot_os.environ['OT_VERIFIER_PYTHON']
            env.pop('PYTHONPATH', None)
            env.pop('PYTHONHOME', None)
            kwargs['env'] = env
            if isinstance(args, (list, tuple)) and len(args) > 1 and python_command:
                args = [python, '-I', *args[1:]]
            elif isinstance(args, str) and kwargs.get('shell'):
                args = _ot_re.sub(r'^(\s*)(?:/usr/(?:local/)?bin/|/bin/)?python(?:3(?:\.\d+)?)?(?=\s)',
                                 lambda m: m[1] + _ot_shlex.quote(python) + ' -I', args, count=1)
        super().__init__(args, *positional, **kwargs)
_ot_subprocess.Popen = _OtReferencePopen
'''


def child_environment(paths, task_python=None):
    return CHILD_ENVIRONMENT.replace('__OT_PATHS__', repr(sorted(paths))).replace('__OT_TASK_PYTHON__', repr(task_python))


def definition(task):
    path = Path(task) / 'tests' / REFERENCE
    if not path.is_file():
        return None
    value = json.loads(path.read_text())
    if not isinstance(value, dict) or value.get('format') != FORMAT or not isinstance(value.get('paths'), list):
        raise ValueError('Invalid verifier asset declaration')
    if set(value) - {'format', 'paths', 'task_paths', 'task_python', 'task_launchers'}:
        raise ValueError('Unknown verifier asset declaration fields')
    paths = value['paths']
    if not all(isinstance(name, str) for name in paths):
        raise ValueError('Invalid verifier asset path')
    if len(paths) != len(set(paths)):
        raise ValueError('Duplicate verifier asset path')
    launchers = value.get('task_launchers', [])
    if not isinstance(launchers, list) or not all(isinstance(p, str) and p in paths for p in launchers):
        raise ValueError('Invalid task launcher declaration')
    for name in paths:
        p = PurePosixPath(name)
        if (not name.startswith(('/app/', '/opt/', '/home/')) or '..' in p.parts
                or str(p) != name or any(ord(c) < 32 for c in name)):
            raise ValueError('Invalid verifier asset path')
    task_paths = value.get('task_paths', [])
    task_python = value.get('task_python')
    if task_python is not None and (not isinstance(task_python, str) or not task_python.startswith('/')
                                   or any(ord(c) < 32 for c in task_python)):
        raise ValueError('Invalid task interpreter')
    if not isinstance(task_paths, list) or not all(
            isinstance(name, str) and name.startswith('/') and str(PurePosixPath(name)) == name
            and '..' not in PurePosixPath(name).parts and not any(ord(c) < 32 for c in name)
            for name in task_paths):
        raise ValueError('Invalid verifier task import path')
    return value


def declared(task):
    return (Path(task) / 'tests' / REFERENCE).is_file()


def register(environment, task, evidence_dir=None):
    config = definition(task)
    if config is None:
        return
    if getattr(environment, '_ot_verifier_asset_snapshot', None) is not None:
        raise RuntimeError('Previous verifier asset snapshot was not discarded')
    environment._ot_verifier_asset_definition = config
    environment._ot_verifier_asset_evidence = evidence_dir
    if not getattr(environment, '_ot_verifier_asset_cleanup', False):
        original = environment.stop
        async def stop(*args, **kwargs):
            try:
                return await original(*args, **kwargs)
            finally:
                snapshot = getattr(environment, '_ot_verifier_asset_snapshot', None)
                if snapshot is not None:
                    snapshot['private'].cleanup()
                environment._ot_verifier_asset_snapshot = None
                environment._ot_verifier_asset_definition = None
        environment.stop = stop
        environment._ot_verifier_asset_cleanup = True


async def capture(environment):
    config = getattr(environment, '_ot_verifier_asset_definition', None)
    if config is None:
        return None
    if getattr(environment, '_ot_verifier_asset_snapshot', None) is not None:
        raise RuntimeError('Refusing to recapture verifier assets after initialization')
    private = tempfile.TemporaryDirectory(prefix='ot-verifier-assets-')
    root = Path(private.name)
    staging = '/tmp/ot-verifier-assets-' + uuid4().hex
    quoted = shlex.quote(staging)
    # The pinned bridge treats a zero-byte download as missing. Keep the
    # missing-file list nonempty even when every reference was captured.
    command = f'set -e; mkdir -m 700 {quoted}; : > {quoted}/paths; printf "\\n" > {quoted}/missing; '
    for path in config['paths']:
        q = shlex.quote(path)
        relative = shlex.quote(path.lstrip('/'))
        command += (f'if [ -f {q} ]; then printf "%s\\0" {relative} >> {quoted}/paths; '
                    f'else printf "%s\\n" {q} >> {quoted}/missing; fi; ')
    command += f'tar --dereference --hard-dereference -cf {quoted}/files.tar -C / --null -T {quoted}/paths'
    try:
        result = await environment.exec(command, user='root', timeout_sec=120)
        if result.return_code:
            raise RuntimeError('Could not capture initialized verifier assets: ' + (result.stderr or '')[-1500:])
        await environment.download_file(source_path=staging + '/files.tar', target_path=root / 'files.tar')
        await environment.download_file(source_path=staging + '/missing', target_path=root / 'missing')
        wanted = {p.lstrip('/') for p in config['paths']}
        captured = []
        with tarfile.open(root / 'files.tar') as archive:
            for member in archive.getmembers():
                if member.name not in wanted or not member.isfile():
                    raise ValueError('Unexpected member in verifier asset capture')
                wanted.remove(member.name)
                data = archive.extractfile(member).read()
                destination = root / 'original' / member.name
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(data)
                captured.append(dict(path='/' + member.name, sha256=hashlib.sha256(data).hexdigest(),
                                     mode=member.mode & 0o777, bytes=len(data)))
        missing = [p for p in (root / 'missing').read_text().splitlines() if p]
        if wanted != {p.lstrip('/') for p in missing}:
            raise ValueError('Incomplete verifier asset capture')
        record = dict(phase='after-task-setup-before-agent', files=captured, missing=missing,
                      shared_cache=False, cache_writeback=False)
        environment._ot_verifier_asset_snapshot = dict(private=private, config=config, record=record)
        evidence = getattr(environment, '_ot_verifier_asset_evidence', None)
        if evidence is not None:
            Path(evidence).mkdir(parents=True, exist_ok=True)
            (Path(evidence) / 'verifier-assets-captured.json').write_text(json.dumps(record, indent=2) + '\n')
        return record
    except BaseException:
        private.cleanup()
        raise
    finally:
        await environment.exec('rm -rf -- ' + quoted, user='root', timeout_sec=30)


def clean_script(data, path, python, task_paths, reference_paths=(), task_python=None):
    """Keep original paths and behavior; choose clean Python for supplied scripts."""
    if data.startswith(b'\x7fELF') or b'\0' in data[:128]:
        return data
    try:
        text = data.decode()
    except UnicodeDecodeError:
        return data
    first = text.splitlines()[0] if text.splitlines() else ''
    is_python = path.endswith('.py') or (first.startswith('#!') and 'python' in first)
    if is_python:
        lines = text.splitlines(keepends=True)
        if lines and lines[0].startswith('#!'):
            lines.pop(0)
        text = ''.join(lines)
        tree = ast.parse(text)
        after = 0
        for node in tree.body:
            if (isinstance(node, ast.ImportFrom) and node.module == '__future__') or (
                    after == 0 and isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
                    and isinstance(node.value.value, str)):
                after = node.end_lineno
            else:
                break
        lines = text.splitlines(keepends=True)
        bootstrap = 'import sys as _ot_sys, os as _ot_os\n_ot_sys.path.append(_ot_os.path.dirname(__file__))\n'
        bootstrap += ''.join(f'_ot_sys.path.append({p!r})\n' for p in task_paths)
        bootstrap += child_environment(reference_paths, task_python)
        return ('#!' + python + ' -I\n' + ''.join(lines[:after]) + bootstrap + ''.join(lines[after:])).encode()
    if first.startswith('#!') and ('/sh' in first or 'bash' in first):
        import re
        text = re.sub(r'(?m)^(\s*(?:exec\s+)?)(?:/usr/(?:local/)?bin/)?python(?:3(?:\.\d+)?)?(?=\s)',
                      lambda m: m[1] + shlex.quote(python) + ' -I' if any(
                          re.match(r'\s+' + re.escape(p) + r'(?:\s|$)', text[m.end():])
                          for p in reference_paths) else m[0], text)
        return text.encode()
    return data


async def stage(environment, context, python, evidence_dir):
    path = Path(context) / REFERENCE
    if not path.is_file():
        return None
    config = json.loads(path.read_text())
    snapshot = getattr(environment, '_ot_verifier_asset_snapshot', None)
    if snapshot is None or snapshot['config'] != config:
        raise RuntimeError('No matching pre-agent verifier asset capture; refusing to read agent-modified files')
    if not python:
        raise RuntimeError('Verifier assets require the clean verifier interpreter')
    source = Path(snapshot['private'].name) / 'original'
    with tempfile.TemporaryDirectory(prefix='ot-verifier-assets-upload-') as temporary:
        target = Path(temporary)
        files = []
        for item in snapshot['record']['files']:
            data = (source / item['path'].lstrip('/')).read_bytes()
            if hashlib.sha256(data).hexdigest() != item['sha256']:
                raise ValueError('Private verifier asset snapshot changed')
            launchers = config.get('task_launchers', [])
            if item['path'] not in launchers:
                data = clean_script(data, item['path'], python, config.get('task_paths', []),
                                    set(config['paths']) - set(launchers), config.get('task_python'))
            output = target / item['path'].lstrip('/')
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(data)
            output.chmod(item['mode'])
            files.append(dict(item, restored_sha256=hashlib.sha256(data).hexdigest()))
        record = dict(phase='after-agent-before-verifier', files=files,
                      missing=snapshot['record']['missing'], shared_cache=False, cache_writeback=False)
        (target / 'manifest.json').write_text(json.dumps(record, indent=2) + '\n')
        result = await environment.exec('rm -rf -- ' + TARGET + ' && mkdir -m 755 ' + TARGET, user='root')
        if result.return_code:
            raise RuntimeError('Could not clear previous private verifier asset staging')
        await environment.upload_dir(source_dir=target, target_dir=TARGET)
    evidence = Path(evidence_dir)
    evidence.mkdir(parents=True, exist_ok=True)
    (evidence / 'verifier-assets-restored.json').write_text(json.dumps(record, indent=2) + '\n')
    return record
