"""Supervise stopped controllers, repair their cause, and preserve recovery history.

Recovery agents handle local repairs; the separate supervisor model judges and
implements shared infrastructure fixes. Their structured
restart request is executed here, so only one controller manages a datasource.
Every stopped attempt keeps its original state, error, logs, and recovery decision.
"""

from contextlib import ExitStack, contextmanager
import base64
import fcntl
from datetime import datetime, timezone
import difflib
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import traceback
import uuid

from validation.patch_repair_loop import controller as core
from validation.patch_repair_loop.agents import parse_json_answer, wait_until
import time


def now():
    return datetime.now(timezone.utc).isoformat()


def read(path):
    return json.loads(path.read_text()) if path.exists() else {}


def record_failure(work, state, error, controller_log=None, exit_code=None):
    directory = Path(work) / 'occurred-failures' / f'failure-{uuid.uuid4().hex}'
    directory.mkdir(parents=True)
    core.save(directory / 'failure.json', {
        'at': now(), 'phase': state.get('phase'), 'generation': state.get('generation'),
        'error_type': type(error).__name__, 'reason': str(error),
        'blocker': state.get('blocker'), 'controller_log': controller_log,
        'exit_code': exit_code})
    core.save(directory / 'state-at-failure.json', state)
    (directory / 'traceback.txt').write_text(''.join(traceback.format_exception(error)))
    return directory


def infrastructure_directory():
    return core.ROOT / '.patch-repair-infra'


def infrastructure_revision():
    return read(infrastructure_directory() / 'revision.json').get('revision', 'original')


@contextmanager
def infrastructure_lock(*, exclusive=False):
    directory = infrastructure_directory()
    directory.mkdir(exist_ok=True)
    with (directory / 'lock').open('a+') as stream:
        fcntl.flock(stream, (fcntl.LOCK_EX | fcntl.LOCK_NB) if exclusive else fcntl.LOCK_SH)
        try:
            if not exclusive and (directory / 'pending.json').exists():
                raise RuntimeError('Interrupted shared infrastructure repair needs supervisor recovery')
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def shared_paths():
    return [str(core.ROOT / path) for path in (
        'hpc', 'harbor_patches', 'config/clusters.py', 'config/runtime.py',
        'validation/stages', 'tests')]


def restore_files(before, paths):
    after = file_snapshot(paths)
    for name in before.keys() | after.keys():
        if before.get(name) == after.get(name):
            continue
        path = Path(name)
        if name not in before:
            path.unlink()
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.is_symlink():
                path.unlink()
            if 'symlink' in before[name]:
                path.unlink(missing_ok=True)
                path.symlink_to(before[name]['symlink'])
            else:
                path.write_bytes(base64.b64decode(before[name]['content_base64']))
                path.chmod(before[name]['mode'])


def test_shared_fix(decision, directory):
    tests = decision.get('tests')
    if not isinstance(tests, list) or not tests or any(not isinstance(test, str) for test in tests):
        raise ValueError('Shared infrastructure changes require focused pytest test paths')
    for test in tests:
        path = Path(test.split('::', 1)[0])
        resolved = (core.ROOT / path).resolve()
        if not resolved.is_relative_to((core.ROOT / 'tests').resolve()) or not resolved.is_file():
            raise ValueError('Recovery test paths must name existing files under tests/')
    if not isinstance(decision.get('shared_fix_justification'), str) or not decision['shared_fix_justification'].strip():
        raise ValueError('Shared infrastructure changes require an explanation of their general applicability')
    command = [sys.executable, '-m', 'pytest', '-q', *tests]
    core.save(directory / 'test-command.json', command)
    with (directory / 'shared-fix-tests.log').open('w') as log:
        result = subprocess.run(command, cwd=core.ROOT, stdout=log, stderr=subprocess.STDOUT, timeout=600)
    core.save(directory / 'shared-fix-tests.json', {'exit_code': result.returncode, 'at': now()})
    if result.returncode:
        raise RuntimeError('Shared infrastructure regression tests failed; see shared-fix-tests.log')


def findings(config):
    return sorted(str(path) for path in (Path(config['work_root']) / 'infrastructure-findings').glob('*.json'))


def unreviewed_findings(config, state):
    """Findings the supervisor has not judged yet; reviewed ones do not recur forever."""
    reviewed = set(state.get('reviewed_findings', []))
    return [path for path in findings(config) if path not in reviewed]


def recover(config, state_path, failure):
    state = read(state_path)
    needs_supervisor = (state.get('supervisor_pending') or unreviewed_findings(config, state) or
                        (infrastructure_directory() / 'pending.json').exists())
    if not needs_supervisor:
        with infrastructure_lock():
            action = recover_locked(config, state_path, failure)
        if action != 'escalate':
            return action
    # There is deliberately no fallback to the datasource/recovery model.
    if not (config.get('supervisor_model') or config.get('agent_models', {}).get('supervisor')):
        raise ValueError('Set supervisor_model to review shared infrastructure findings; recovery cannot make those edits')
    acquired = False
    try:
        with infrastructure_lock(exclusive=True):
            acquired = True
            pending_path = infrastructure_directory() / 'pending.json'
            pending = read(pending_path)
            if pending:
                restore_files(read(Path(pending['snapshot'])), pending['paths'])
                core.save(Path(pending['snapshot']).parent / 'shared-fix-rollback.json', {
                    'at': now(), 'reason': 'Interrupted supervisor repair; restored original shared files'})
                pending_path.unlink()
            shared = shared_paths() if config.get('supervisor_shared_infra', True) else []
            return recover_locked(config, state_path, failure, shared=shared, role='supervisor')
    except BlockingIOError:
        if acquired:
            raise
        core.save(failure / 'shared-infrastructure-wait.json', {
            'at': now(), 'reason': 'Other controllers are using shared code; waiting for the supervisor'})
        return 'wait'


def controller_locks(config):
    stack = ExitStack()
    try:
        stack.enter_context(core.lock(Path(config['patcher']).parent / '.patch-repair.lock'))
        stack.enter_context(core.lock(Path(config['work_root']) / '.controller.lock'))
    except BaseException:
        stack.close()
        raise
    return stack


def jobs(work):
    """Unknown or active Slurm jobs prevent starting a fresh validation generation."""
    identifiers = set()
    for root in [*Path(work).glob('generation-*'), Path(work) / 'publication']:
        for name in ('job.json', 'submission.json'):
            for path in root.rglob(name):
                value = read(path)
                if value.get('job_id'):
                    identifiers.add(str(value['job_id']))
    if not identifiers:
        return []
    terminal = {'COMPLETED', 'FAILED', 'CANCELLED', 'TIMEOUT', 'OUT_OF_MEMORY',
                'NODE_FAIL', 'PREEMPTED', 'BOOT_FAIL', 'DEADLINE', 'REVOKED'}
    states = {}
    try:
        result = subprocess.run(['sacct', '-X', '-n', '-P', '-j', ','.join(sorted(identifiers)),
                                 '--format', 'JobIDRaw,State'], capture_output=True,
                                text=True, check=True, timeout=30)
        for line in result.stdout.splitlines():
            fields = line.split('|')
            if len(fields) >= 2 and fields[0] in identifiers and fields[1].strip():
                states[fields[0]] = fields[1].split()[0].rstrip('+')
    except (OSError, subprocess.SubprocessError):
        pass
    return [{'job_id': job, 'state': states.get(job, 'UNKNOWN'),
             'finished': states.get(job) in terminal} for job in sorted(identifiers)]


def file_snapshot(paths):
    result = {}
    for root in map(Path, paths):
        files = root.rglob('*') if root.is_dir() and not root.is_symlink() else [root]
        for path in files:
            relative = path.relative_to(root) if path != root else Path(path.name)
            if any(part.startswith('.') or part in ('__pycache__', 'pilot-results')
                   for part in relative.parts):
                continue
            if path.is_symlink():
                result[str(path)] = {'symlink': os.readlink(path)}
                continue
            if not path.is_file():
                continue
            data = path.read_bytes()
            result[str(path)] = {'sha256': hashlib.sha256(data).hexdigest(),
                                 'text': data.decode('utf-8', errors='replace') if len(data) <= 1_000_000 else None,
                                 'content_base64': base64.b64encode(data).decode(),
                                 'mode': path.stat().st_mode & 0o777}
    return result


def save_changes(directory, before, after):
    changed = [path for path in sorted(before.keys() | after.keys()) if before.get(path) != after.get(path)]
    core.save(directory / 'changed-files.json', changed)
    diff = []
    for path in changed:
        old = before.get(path, {}).get('text')
        new = after.get(path, {}).get('text')
        if old is not None or new is not None:
            diff.extend(difflib.unified_diff((old or '').splitlines(True), (new or '').splitlines(True),
                                           fromfile=path + ' (before)', tofile=path + ' (after)'))
    (directory / 'changes.diff').write_text(''.join(diff))
    return changed


def validate_decision(value, role='recovery'):
    actions = ('wait', 'restart', 'stop', 'escalate') if role == 'recovery' else ('wait', 'restart', 'stop')
    if value.get('action') not in actions:
        raise ValueError(f'{role} action must be one of {actions}')
    if value.get('kind') not in ('infrastructure', 'agent', 'controller', 'publication'):
        raise ValueError('Recovery requires a failure kind')
    if not isinstance(value.get('cause'), str) or not value['cause'].strip():
        raise ValueError('Recovery requires a cause')
    if not isinstance(value.get('evidence'), list) or not value['evidence'] or any(
            not isinstance(path, str) or not path.strip() for path in value['evidence']):
        raise ValueError('Recovery requires evidence paths')
    if value['action'] == 'restart' and (value.get('restart_from') not in ('checkpoint', 'stage3') or
            not isinstance(value.get('restart_prompt'), str) or not value['restart_prompt'].strip()):
        raise ValueError('Restart requires checkpoint or stage3 and a restart prompt')
    if value['action'] == 'wait' and not value.get('wait_reason'):
        raise ValueError('Wait requires the condition that must recover')
    return value


def recover_locked(config, state_path, failure, shared=(), role='recovery'):
    """Run one recovery while both datasource and controller locks are held."""
    if shared and role != 'supervisor':
        raise ValueError('Only the supervisor role may edit shared infrastructure')
    state = read(state_path)
    for phase, setting, counter in [('fixer', 'max_repairs', 'repair_count'),
                                     ('final_retry', 'max_infra_retries', 'infra_retry_count')]:
        if state.get('phase') == phase:
            core.check_limit(config, state, setting, counter)
    setting, counter = (('max_supervisor_reviews', 'supervisor_count') if role == 'supervisor'
                        else ('max_recoveries', 'recovery_count'))
    core.check_limit(config, state, setting, counter)
    state['status'] = 'blocked'
    state[counter] = state.get(counter, 0) + 1
    core.save(state_path, state)
    directory = failure / f'{role}-{state[counter]:04d}'
    directory.mkdir(parents=True)
    editable = (list(shared) if role == 'supervisor' else
                [str(Path(config['patcher']).parent), *config.get('recovery_edit_paths', [])])
    before = file_snapshot(editable)
    core.save(directory / 'files-before.json', before)
    shared_before = file_snapshot(shared)
    pending_path = infrastructure_directory() / 'pending.json'
    if shared:
        core.save(directory / 'shared-files-before.json', shared_before)
        core.save(pending_path, {'snapshot': str(directory / 'shared-files-before.json'), 'paths': list(shared)})
    accepted_shared = False
    try:
        answer = core.agent_call(config, role, {
            'failure_dir': str(failure), 'state': state,
            'recovery_escalation': state.get('supervisor_pending'),
            'work_root': config['work_root'], 'editable_paths': editable,
            'shared_infrastructure_paths': list(shared),
            'infrastructure_findings': str(Path(config['work_root']) / 'infrastructure-findings'),
            'jobs': jobs(config['work_root']), 'source': config['source'],
            'recovery_history': str(Path(config['work_root']) / 'occurred-failures'),
            'publication_status': read(Path(config['work_root']) / 'publication/publish-status.json'),
        }, directory, role, watched_paths=editable)
        decision = validate_decision(parse_json_answer(answer), role)
        core.save(directory / 'decision.json', decision)
        if shared and file_snapshot(shared) != shared_before:
            test_shared_fix(decision, directory)
            core.save(infrastructure_directory() / 'revision.json', {
                'revision': uuid.uuid4().hex, 'at': now(), 'evidence': str(directory)})
            accepted_shared = True
    finally:
        after = file_snapshot(editable)
        core.save(directory / 'files-after.json', after)
        save_changes(directory, before, after)
        if shared and not accepted_shared:
            shared_changed = file_snapshot(shared) != shared_before
            restore_files(shared_before, shared)
            if shared_changed:
                core.save(directory / 'shared-fix-rollback.json', {
                    'at': now(), 'reason': 'Shared changes were not validated; restored original files'})
        if shared:
            pending_path.unlink(missing_ok=True)
        changed = before != file_snapshot(editable)
        if changed:
            state['recovery_requires_stage3'] = True
            state['review_setup'] = True
            core.save(state_path, state)
    if decision['action'] == 'escalate':
        state['supervisor_pending'] = str(directory / 'decision.json')
        core.save(Path(config['work_root']) / 'infrastructure-findings' / f'recovery-{state[counter]:04d}.json', {
            'source': 'recovery', 'decision': str(directory / 'decision.json'),
            'cause': decision['cause'], 'evidence': decision['evidence']})
    elif role == 'supervisor':
        state.pop('supervisor_pending', None)
        state['reviewed_findings'] = sorted(set(state.get('reviewed_findings', [])) | set(findings(config)))
    if decision['action'] == 'restart':
        fresh = decision['restart_from'] == 'stage3' or state.get('recovery_requires_stage3', False)
        # An interrupted mutating agent must finish its reporting contract first.
        # An idle fixer holds no partial edit; its stale evidence is replaced by stage 3.
        if state.get('phase') in ('proposer', 'implementer') or (
                state.get('phase') == 'fixer' and not core.fixer_idle(config, state)):
            fresh = False
        if fresh:
            active = [job for job in jobs(config['work_root']) if not job['finished']]
            if active:
                core.save(directory / 'restart.json', {'status': 'waiting', 'jobs': active})
                return 'wait'
            if (Path(config['work_root']) / 'publication/job.json').exists():
                raise RuntimeError('Publication was submitted; reconcile it before replacing validated results')
            core.restart_stage3(state)
        state.update(status='running', agent_attempt=state.get('agent_attempt', 0) + 1,
                     recovery_handoff={'failure_dir': str(failure), 'decision': str(directory / 'decision.json'),
                                       'restart_prompt': decision['restart_prompt']})
        state.pop('blocker', None)
        core.save(state_path, state)
    if decision['action'] != 'restart':
        state['blocker'] = {'kind': decision['kind'], 'reason': decision['cause'],
                            'evidence': str(directory / 'decision.json')}
        core.save(state_path, state)
    core.save(directory / 'restart.json', {'status': decision['action'], 'at': now()})
    return decision['action']


def launch(config_path, work):
    directory = Path(work) / 'occurred-failures/controller-runs'
    directory.mkdir(parents=True, exist_ok=True)
    log = directory / f'{uuid.uuid4().hex}.log'
    with log.open('w') as stream:
        result = subprocess.run([sys.executable, '-m', 'validation.patch_repair_loop', 'run',
                                 '--config', str(Path(config_path).resolve())], cwd=core.ROOT,
                                stdout=stream, stderr=subprocess.STDOUT)
    return result.returncode, str(log)


def supervise(config_path):
    config = core.load_config(config_path)
    work = Path(config['work_root'])
    state_path = work / 'loop-state.json'
    with core.lock(work / '.supervisor.lock'):
        initial = not state_path.exists()
        while True:
            with controller_locks(config):
                state = read(state_path)
                publication = read(work / 'publication/publish-status.json')
                if publication.get('status') == 'completed' and publication.get('pull_request'):
                    state.update(status='complete', pull_request=publication['pull_request'])
                    core.save(state_path, state)
                if state.get('pull_request') or (state.get('status') == 'complete' and not config.get('publish', True)):
                    return state
                if not initial:
                    failure = Path(state['last_failure']) if state.get('last_failure') else record_failure(
                        work, state, RuntimeError('Controller is no longer running without a confirmed PR'))
                    state['last_failure'] = str(failure)
                    core.save(state_path, state)
                    try:
                        action = recover(config, state_path, failure)
                    except Exception as exc:
                        state = read(state_path)
                        state.update(status='blocked', last_failure=str(record_failure(work, state, exc)))
                        core.save(state_path, state)
                        raise
                    if action == 'stop':
                        return read(state_path)
                else:
                    action = 'restart'
            if action == 'wait':
                wait_until(time.time() + config['recovery_wait_seconds'])
                continue
            previous_failure = read(state_path).get('last_failure')
            code, log = launch(config_path, work)
            initial = False
            with controller_locks(config):
                state = read(state_path)
                # Preserve the outcome of each actual relaunch, including a crash or missing state.
                if previous_failure:
                    core.save(Path(previous_failure) / f'controller-outcome-{uuid.uuid4().hex}.json', {
                        'at': now(), 'exit_code': code, 'controller_log': log,
                        'status': state.get('status'), 'phase': state.get('phase'),
                        'pull_request': state.get('pull_request')})
                if not state.get('pull_request') and not (
                        state.get('status') == 'complete' and not config.get('publish', True)):
                    if not state.get('last_failure') or state.get('last_failure') == previous_failure:
                        failure = record_failure(work, state, RuntimeError(
                            f'Controller exited with code {code} without a confirmed PR'), log, code)
                        # A missing checkpoint cannot be reconstructed by guessing.
                        if not state.get('phase'):
                            raise RuntimeError(f'Controller did not save a checkpoint; inspect {failure}')
                        state['last_failure'] = str(failure)
                        core.save(state_path, state)
                    else:
                        core.save(Path(state['last_failure']) / 'controller-exit.json', {
                            'at': now(), 'exit_code': code, 'controller_log': log})
