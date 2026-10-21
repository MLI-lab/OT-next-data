"""Capture reference output in stage 2; scan a clean agent environment in stage 4."""
import asyncio
from contextvars import ContextVar
import json
import os
from pathlib import Path
import shlex
import tempfile
from uuid import uuid4

from validation.checks import reference_files
from validation.stages.layout import REFERENCE

CHECK = 'reference-solution-leak'
ARTIFACT = 'reference-file-fingerprints.json'
enabled = ContextVar('capture_reference_files', default=False)


def evidence_digest(task):
    # Stage 4 can normalize instruction paths after the reference stage. That does not
    # change reference output or the initial filesystem; other payloads do.
    from validation.contract import files_digest
    return files_digest((p.relative_to(task).as_posix(), p.read_bytes())
                        for p in task.rglob('*') if p.is_file() and p.relative_to(task).as_posix() != 'instruction.md')


async def probe(environment, request):
    source = Path(reference_files.__file__).read_text()
    remote = '/tmp/ot-reference-probe-' + uuid4().hex + '.json'
    command = ('umask 077; python3 -c ' + shlex.quote(source) + ' '
               + shlex.quote(json.dumps(request)) + ' > ' + shlex.quote(remote))
    try:
        result = await environment.exec(command, timeout_sec=300)
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
            from validation.stages.container_reuse import _scope
            if _scope.get() is not None:
                raise ValueError('reference fingerprint capture requires a fresh trial, not reused verifier state')
            if getattr(getattr(self.task, 'config', None), 'steps', None):
                raise ValueError('Multi-step reference capture is not yet supported')
            with self.agent_environment.with_default_user(kwargs.get('user')):
                before = await probe(self.agent_environment, {'mode': 'snapshot'})
        except Exception as exc:
            report['error'] = str(exc)
        path.write_text(json.dumps(report, indent=2) + '\n')
        # Do not prevent the reference evaluation when fingerprinting is unavailable.
        # Stage 4 will explicitly reject the incomplete evidence.
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


def verdict(evidence, observation):
    files = observation['files']
    count = sum(bool(f['matches']) for f in files)
    total = len(files)
    fraction = count / total if total else 0
    complete = bool(total and observation['status'] == 'completed' and not evidence.get('deleted_files'))
    above_threshold = count * 2 > total
    status = 'error' if not complete else 'failed' if above_threshold else 'warning' if count else 'passed'
    return {'check': CHECK, 'status': status,
            'scan_status': observation['status'], 'flagged': bool(count),
            'evaluation_status': 'completed' if complete else 'incomplete',
            'reference_task_sha256': evidence.get('task_sha256'),
            'fingerprint_version': evidence.get('version'),
            'matched_files': count, 'changed_files': total, 'matched_file_fraction': fraction,
            'matched_file_percent': 100 * fraction,
            'rejection_threshold_percent': 50, 'above_rejection_threshold': above_threshold,
            'all_changed_files_found': bool(total and count == total and complete),
            'deleted_files': evidence.get('deleted_files', []), 'files': files,
            'stats': observation.get('stats', {}), 'errors': observation.get('errors', []),
            'scope': evidence.get('scope'), 'workspace': evidence['workspace'],
            'reason': ('Incomplete scan or deleted reference files cannot be checked by exact file matching' if not complete else
                       f'{count}/{total} changed files ({100 * fraction:.2f}%) already readable before solving; '
                       + ('above the 50% rejection threshold' if above_threshold else 'flagged without rejection') if count else
                       'No exact copies of the captured reference changes found')}


async def scan_task(task_path, out, args, evidence):
    # The cluster worker refuses cache misses when OT_IMAGES_PREPARED is set.
    # Other backends do not yet expose a verified cache-only startup contract.
    if args.backend != 'apptainer' or os.environ.get('OT_IMAGES_PREPARED') != '1':
        raise ValueError('Reference leak scan requires the Apptainer worker with OT_IMAGES_PREPARED=1; prepare cached images first')
    from validation.stages import harbor as runtime
    from validation.stages.task_setup import detect, prepare, upload_setup
    from harbor_patches.start_wait import run_with_budget, status_reader
    runtime.install_runtime_patches()
    from harbor.environments.factory import EnvironmentFactory
    from harbor.models.environment_type import EnvironmentType
    from harbor.models.task.task import Task
    from harbor.models.trial.paths import TrialPaths

    if args.force_build:
        raise ValueError('Reference leak scanning requires cached images, not --force-build')
    task = Task(task_path)
    runtime.check_runtime_task(task_path, args.backend)
    spec = task.config.environment
    overrides = {}
    if args.trial_cpus:
        overrides['cpus'] = args.trial_cpus
    if args.trial_memory_mb:
        overrides['memory_mb'] = args.trial_memory_mb
    spec = spec.model_copy(update=overrides)
    paths = TrialPaths(out)
    paths.mkdir()
    env = EnvironmentFactory.create_environment(
        type=EnvironmentType(args.backend), environment_dir=task.paths.environment_dir,
        environment_name=f'{task.short_name}-reference-leak', session_id=f'leak-{uuid4().hex[:12]}',
        trial_paths=paths, task_env_config=spec, **runtime.environment_config(args)['kwargs'])
    try:
        waited = status_reader(env)
        await run_with_budget(
            prepare(env, command=detect(task_path), timeout_sec=spec.build_timeout_sec, force_build=False,
                    upload=lambda: upload_setup(env, task_path), waited=waited), spec.build_timeout_sec, waited)
        await env.run_healthcheck()
        # No oracle or verifier payload is uploaded to this environment.
        with env.with_default_user(task.config.agent.user):
            observation = await probe(env, {'mode': 'scan', 'root': '/', 'files': evidence['files']})
        return verdict(evidence, observation)
    finally:
        await env.stop(delete=True)


async def run_checks(sources, items, out, args):
    by_name = {Path(i['task']).name: i for i in items}
    records = read_evidence(args.out)
    semaphore = asyncio.Semaphore(args.concurrency)
    async def one(task):
        async with semaphore:
            if not (task / 'solution/solve.sh').is_file():
                result = {'check': CHECK, 'status': 'not_applicable', 'reason': 'No reference solution supplied'}
            else:
                try:
                    evidence = collect(task, args.out, records)
                    if not evidence['files']:
                        raise ValueError('Reference produced no changed regular files in the captured workspace')
                    result = await scan_task(task, out / task.name, args, evidence)
                except Exception as exc:
                    result = {'check': CHECK, 'status': 'error', 'scan_status': 'incomplete', 'reason': str(exc)}
            item = by_name[task.name]
            item.setdefault('checks', []).append(result)
            if result.get('scan_status') == 'incomplete' or result.get('evaluation_status') == 'incomplete':
                item['reference_leak_incomplete'] = True
            if result['status'] in ('failed', 'error'):
                item['status'] = 'failed' if result['status'] == 'failed' and item['status'] != 'error' else 'error'
    await asyncio.gather(*(one(task) for task in sources))
