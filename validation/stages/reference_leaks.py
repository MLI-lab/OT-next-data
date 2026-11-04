"""Capture reference output in stage 2; scan the clean no-op container in stage 3."""
from contextvars import ContextVar
import json
import hashlib
from pathlib import Path
import shlex
import tempfile
from uuid import uuid4

from validation.rules.dynamic import reference_leaks as reference_files
from validation.stages.layout import REFERENCE

CHECK = 'reference-solution-leak'
ARTIFACT = 'reference-file-fingerprints.json'
enabled = ContextVar('capture_reference_files', default=False)
scan_requests = ContextVar('reference_leak_scan_requests', default=None)
scan_runtime = ContextVar('reference_leak_scan_runtime', default=None)
SCAN_ARTIFACT = 'reference-leak-scan.json'
# Shell words are fixed checker configuration, not task-controlled input. The
# /opt glob covers image-baked runtimes that are intentionally absent from PATH.
PROBE_PYTHONS = 'python3 python /usr/bin/python3 /usr/local/bin/python3 /opt/*/bin/python3 /opt/*/bin/python'
PROBE_TIMEOUT_SEC = 600


def evidence_digest(task):
    # The path repair edits only instruction.md. That does not change reference
    # output or the initial filesystem; other payloads do.
    from validation.contract import files_digest
    return files_digest((p.relative_to(task).as_posix(), p.read_bytes())
                        for p in task.rglob('*') if p.is_file() and p.relative_to(task).as_posix() != 'instruction.md')


async def probe(environment, request):
    source = Path(reference_files.__file__).read_text()
    remote = '/tmp/ot-reference-probe-' + uuid4().hex + '.json'
    sanity = 'import sys, hashlib, json; assert sys.version_info >= (3, 7); hashlib.sha256(b"probe")'
    command = (
        'umask 077; ot_probe_python=; for ot_candidate in ' + PROBE_PYTHONS + '; do '
        'if command -v "$ot_candidate" >/dev/null 2>&1 && '
        '"$ot_candidate" -I -c ' + shlex.quote(sanity) + ' >/dev/null 2>&1; then '
        'ot_probe_python=$ot_candidate; break; fi; done; '
        'if [ -z "$ot_probe_python" ]; then '
        'echo "reference probe needs an existing Python 3.7+ interpreter (PATH, /usr/bin, /usr/local/bin or /opt/*/bin)" >&2; '
        'exit 127; fi; '
        '"$ot_probe_python" -I -c ' + shlex.quote(source) + ' '
        + shlex.quote(json.dumps(request)) + ' > ' + shlex.quote(remote))
    try:
        result = await environment.exec(command, timeout_sec=PROBE_TIMEOUT_SEC)
        if result.return_code:
            raise RuntimeError('reference file probe failed: ' + (result.stderr or result.stdout or '')[-2000:])
        # Exec output is tail-limited by the worker. Snapshots can be much larger;
        # transfer the complete artifact instead of parsing truncated stdout.
        with tempfile.TemporaryDirectory(prefix='reference-probe-') as directory:
            local = Path(directory) / 'result.json'
            await environment.download_file(remote, local)
            return json.loads(local.read_text())
    finally:
        await environment.exec('rm -f -- ' + shlex.quote(remote), timeout_sec=30)


def install():
    from harbor.trial.trial import Trial
    if getattr(Trial, '_reference_files_installed', False):
        return
    original = Trial._run_agent_phase

    async def agent(self, *args, **kwargs):
        requests = scan_requests.get()
        task = self.task.paths.task_dir
        evidence = requests.pop(task.name, None) if requests is not None else None
        if evidence is not None:
            record = {'evidence_sha256': evidence_signature(evidence), 'runtime_sha256': scan_runtime.get()}
            try:
                if evidence_digest(task) != evidence['task_sha256']:
                    raise ValueError('Task changed since reference fingerprint capture')
                if getattr(self.task.config, 'steps', None):
                    raise ValueError('Multi-step leak scan is not supported')
                with self.agent_environment.with_default_user(kwargs.get('user')):
                    observation = await probe(self.agent_environment,
                                              {'mode': 'scan', 'root': '/', 'files': evidence['files']})
                record['result'] = verdict(evidence, observation)
            except Exception as exc:
                record['result'] = {'check': CHECK, 'status': 'error',
                                    'scan_status': 'incomplete', 'reason': str(exc)}
            (self.paths.trial_dir / SCAN_ARTIFACT).write_text(json.dumps(record, indent=2) + '\n')
        if not enabled.get():
            return await original(self, *args, **kwargs)
        task = self.task.paths.task_dir
        path = self.paths.trial_dir / ARTIFACT
        report = {'version': reference_files.VERSION, 'task_sha256': evidence_digest(task),
                  'status': 'incomplete', 'phase': 'after-setup-before-verifier',
                  'scope': 'Regular-file contents under the initial agent working directory; .git excluded.',
                  'files': [], 'deleted_files': []}
        before = None
        try:
            if getattr(getattr(self.task, 'config', None), 'steps', None):
                raise ValueError('Multi-step reference capture is not yet supported')
            with self.agent_environment.with_default_user(kwargs.get('user')):
                before = await probe(self.agent_environment, {'mode': 'snapshot'})
        except Exception as exc:
            report['error'] = str(exc)
        path.write_text(json.dumps(report, indent=2) + '\n')
        # Do not prevent the reference evaluation when fingerprinting is unavailable.
        # The stage-3 scan then reports the incomplete evidence.
        result = await original(self, *args, **kwargs)
        if before is not None:
            try:
                with self.agent_environment.with_default_user(kwargs.get('user')):
                    after = await probe(self.agent_environment, {'mode': 'snapshot', 'root': before['root']})
                report.update(reference_files.changed_files(before, after))
            except Exception as exc:
                report.update(status='incomplete', error=str(exc))
        path.write_text(json.dumps(report, indent=2) + '\n')
        return result

    Trial._run_agent_phase = agent
    Trial._reference_files_installed = True


def read_evidence(output):
    records = {}
    for path in sorted(Path(output).glob(f'stage_{REFERENCE}_*/*/summary.json')):
        report = json.loads(path.read_text())
        if report.get('stage') != REFERENCE:
            continue
        for item in report.get('items', []):
            if item.get('status') not in ('passed', 'completed'):
                continue
            records.setdefault(Path(item.get('task', '')).name, []).extend(item.get('reference_file_fingerprints', []))
    return records


def collect(task, output, records=None):
    """Reuse only successful, content-matching reference-stage evidence in this run."""
    digest = evidence_digest(task)
    records = read_evidence(output) if records is None else records
    candidates = [e for e in records.get(task.name, [])
                  if e.get('version') == reference_files.VERSION and e.get('task_sha256') == digest]
    valid = [e for e in candidates if e.get('status') == 'completed']
    if not valid:
        raise ValueError(f'No complete, matching reference fingerprints from a successful stage {REFERENCE} in this output directory; rerun stage {REFERENCE}')
    signatures = {json.dumps({'files': sorted(e['files'], key=lambda f: f['path']),
                              'deleted_files': e['deleted_files'], 'workspace': e['workspace']}, sort_keys=True)
                  for e in valid}
    if len(signatures) != 1:
        raise ValueError('Reference attempts produced different changed files; exact-match coverage is ambiguous')
    return valid[-1]


def matched_text(files, limit=5):
    """Which solution files were already readable, and where: e.g. /app/fix.py (at /tmp/fix.py)."""
    def where(match):
        return match if isinstance(match, str) else match.get('path') or match.get('location') or str(match)
    parts = [f"{f.get('path')} (at {', '.join(where(m) for m in f['matches'][:2])})"
             for f in files if f.get('matches')]
    return ', '.join(parts[:limit]) + (f' and {len(parts) - limit} more' if len(parts) > limit else '')


def verdict(evidence, observation):
    files = observation['files']
    count = sum(bool(f['matches']) for f in files)
    total = len(files)
    fraction = count / total if total else 0
    deleted = evidence.get('deleted_files', [])
    deletion_only = bool(not total and deleted and observation['status'] == 'not_applicable')
    complete = bool(total and observation['status'] == 'completed')
    above_threshold = count * 2 > total
    status = ('not_applicable' if deletion_only else 'error' if not complete else
              'failed' if above_threshold else 'warning' if count else 'passed')
    return {'check': CHECK, 'status': status,
            'scan_status': observation['status'], 'flagged': bool(count),
            'evaluation_status': 'not_applicable' if deletion_only else 'completed' if complete else 'incomplete',
            'reference_task_sha256': evidence.get('task_sha256'),
            'fingerprint_version': evidence.get('version'),
            'matched_files': count, 'changed_files': total, 'matched_file_fraction': fraction,
            'matched_file_percent': 100 * fraction,
            'rejection_threshold_percent': 50, 'above_rejection_threshold': above_threshold,
            'all_changed_files_found': bool(total and count == total and complete),
            'deleted_files': deleted, 'skipped_deleted_files': deleted,
            'deletion_policy': 'Deleted files have no post-solution contents to hash; excluded from exact-content matching and its denominator.',
            'files': files,
            'stats': observation.get('stats', {}), 'errors': observation.get('errors', []),
            'scope': evidence.get('scope'), 'workspace': evidence['workspace'],
            'scan_scope': 'Agent-readable regular files and local Git blobs across the filesystem, excluding /proc, /sys and /dev.',
            'reason': ('Reference only deletes files; no added or modified file contents to check' if deletion_only else
                       'Incomplete scan or no captured added or modified file contents' if not complete else
                       f'{count}/{total} changed files ({100 * fraction:.2f}%) already readable before solving: '
                       + matched_text(files) + '; '
                       + ('above the 50% rejection threshold' if above_threshold else 'flagged without rejection') if count else
                       'No exact copies of the captured reference changes found')}


def evidence_signature(evidence):
    return hashlib.sha256(json.dumps(evidence, sort_keys=True).encode()).hexdigest()


def runtime_signature(args):
    settings = {key: getattr(args, key, None) for key in
                ('backend', 'environment_kwargs', 'trial_cpus', 'trial_memory_mb', 'network_mode')}
    return hashlib.sha256(json.dumps(settings, sort_keys=True, default=str).encode()).hexdigest()


def prepare_nop_scans(tasks, output):
    return plan_nop_scans(tasks, output)[0]


def plan_nop_scans(tasks, output):
    """Scan requests for the stage-3 no-op containers, and the final result of
    every task that needs no scan: no reference solution, a reference that only
    deletes files, or missing/incomplete reference evidence."""
    records = read_evidence(output)
    requests, results = {}, {}
    for task in tasks:
        if not (task / 'solution/solve.sh').is_file():
            results[task.name] = {'check': CHECK, 'status': 'not_applicable', 'reason': 'No reference solution supplied'}
            continue
        try:
            evidence = collect(task, output, records)
        except (ValueError, OSError) as exc:
            # No usable stage-2 evidence: the check depends on the reference run.
            results[task.name] = {'check': CHECK, 'status': 'skipped', 'blocked_by_stage': REFERENCE,
                                  'reason': str(exc)}
            continue
        if evidence['files']:
            requests[task.name] = evidence
        elif evidence.get('deleted_files'):
            results[task.name] = verdict(evidence, {'status': 'not_applicable', 'files': []})
        else:
            results[task.name] = {'check': CHECK, 'status': 'error', 'scan_status': 'incomplete',
                                  'reason': 'Reference produced no changed regular files in the captured workspace'}
    return requests, results
