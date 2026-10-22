"""Give each repair controller a persistent, independently editable code copy.

Jobs already freeze code with their contracts. This isolates the longer-lived
controller and supervisor too; their infrastructure lock is checkout-local.
Source environments and content-addressed image/runtime bundles remain shared
read-only inputs. A supervisor never edits the installed Python environment.
"""
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import uuid

from validation.patch_repair_loop import controller as core


def default_root(work):
    identity = hashlib.sha256(str(Path(work).resolve()).encode()).hexdigest()[:16]
    return Path.home() / '.local/share/ot-next-data/repair-code' / identity


def copy_code(source, destination):
    def ignore(directory, names):
        excluded = set(shutil.ignore_patterns('__pycache__', '*.pyc', '.patch-repair*',
            'results', 'runs', 'pilot-results', '*.parquet', '*.sif', '*.tar', '*.tar.gz')(directory, names))
        excluded.update(name for name in names if (Path(directory) / name).is_symlink()
                        and not (Path(directory) / name).is_file())
        return excluded
    for name in ('validation', 'harbor_patches', 'hpc', 'config', 'data', 'tests', '.agents'):
        if (source / name).is_dir():
            shutil.copytree(source / name, destination / name, ignore=ignore)
    for name in ('requirements.txt', 'pyproject.toml', 'pytest.ini', 'env.sh', 'README.md'):
        if (source / name).is_file():
            shutil.copy2(source / name, destination / name)
    if (source / 'external').exists():
        (destination / 'external').symlink_to((source / 'external').resolve(), target_is_directory=True)
    revision = source / '.patch-repair-infra/revision.json'
    if revision.is_file():
        (destination / '.patch-repair-infra').mkdir()
        shutil.copy2(revision, destination / '.patch-repair-infra/revision.json')


def rebase(value, source, destination):
    if isinstance(value, list):
        return [rebase(item, source, destination) for item in value]
    if isinstance(value, str) and value.startswith(str(source) + '/'):
        return str(destination / Path(value).relative_to(source))
    return value


def prepare(config_path, destination=None):
    """Migrate an idle controller, retaining jobs, state and a before/after audit.

    Refuse a live controller/supervisor. Active frozen jobs can finish normally;
    no generation is reset and no submissions are made by this operation.
    """
    config_path = Path(config_path).resolve()
    original = json.loads(config_path.read_text())
    work = Path(original['work_root']).resolve()
    source = core.ROOT.resolve()
    destination = Path(destination or original.get('controller_code_root') or default_root(work)).resolve()
    marker = destination / '.repair-loop.json'
    if destination == source:
        if not marker.is_file() or json.loads(marker.read_text())['work_root'] != str(work):
            raise ValueError('Controller checkout is not owned by this loop')
        return destination
    with ExitStack() as stack:
        stack.enter_context(core.lock(work / '.supervisor.lock'))
        stack.enter_context(core.lock(work / '.controller.lock'))
        stack.enter_context(core.lock(Path(original['patcher']).parent / '.patch-repair.lock'))
        from validation.patch_repair_loop.recovery import infrastructure_lock
        stack.enter_context(infrastructure_lock())
        state_path = work / 'loop-state.json'
        state = json.loads(state_path.read_text()) if state_path.exists() else None
        if state and state['config_sha256'] != core.config_digest(original):
            raise ValueError('Configuration changed before isolation; reconcile it first')
        if destination.exists():
            if not marker.is_file() or json.loads(marker.read_text())['work_root'] != str(work):
                raise ValueError('Refusing to overwrite an unrelated controller checkout')
            source = Path(json.loads(marker.read_text())['source_root'])
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix='.isolate-', dir=destination.parent) as temporary:
                staged = Path(temporary) / 'code'
                staged.mkdir()
                copy_code(source, staged)
                core.save(staged / '.repair-loop.json', {'work_root': str(work), 'source_root': str(source)})
                staged.rename(destination)
        updated = dict(original, controller_code_root=str(destination),
                       dataset_identity_root=original.get('dataset_identity_root',
                           str(Path(original['patcher']).parent)))
        for key in ('patcher', 'patch_command'):
            updated[key] = rebase(updated[key], source, destination)
        if not Path(updated['patcher']).is_relative_to(destination) or not Path(updated['patcher']).is_file():
            raise ValueError('Patcher must belong to the isolated controller checkout')
        audit = work / 'operator-decisions' / ('code-isolation-' + uuid.uuid4().hex)
        core.save(audit / 'config-before.json', original)
        core.save(audit / 'config-after.json', updated)
        if state:
            core.save(audit / 'state-before.json', state)
            state['config_sha256'] = core.config_digest(updated)
            state['controller_code_root'] = str(destination)
            core.save(state_path, state)
        core.save(config_path, updated)
        core.save(work / 'config.json', updated)
        core.save(destination / 'loop-config.json', updated)
        core.save(audit / 'result.json', {'status': 'isolated', 'code_root': str(destination),
                  'existing_jobs_unchanged': True})
    return destination


def enter(config_path, command):
    """CLI entry: re-exec from the owned copy before importing recovery agents."""
    config_path = Path(config_path).resolve()
    code = prepare(config_path)
    if code == core.ROOT.resolve():
        return
    environment = {**os.environ, 'PYTHONPATH': str(code)}
    os.chdir(code)
    os.execve(sys.executable, [sys.executable, '-m', 'validation.patch_repair_loop', command,
               '--config', str(Path(config_path).resolve())], environment)
