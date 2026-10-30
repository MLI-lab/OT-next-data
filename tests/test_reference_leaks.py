import asyncio
from contextlib import nullcontext
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from validation.rules.dynamic import reference_leaks as files
from validation.stages import reference_leaks as leaks


def reference(path, content):
    path.write_bytes(content)
    return {'path': str(path), **files.fingerprint(str(path))}


def test_size_filter_exact_match_hardlinks_and_directory_loop(tmp_path, monkeypatch):
    ref = reference(tmp_path / 'reference', b'the fixed implementation')
    root = tmp_path / 'agent'
    root.mkdir()
    (root / 'copy').write_bytes(b'the fixed implementation')
    os.link(root / 'copy', root / 'alias')
    (root / 'wrong').write_bytes(b'x' * ref['size'])
    (root / 'large').write_bytes(b'x' * (ref['size'] + 100))
    (root / 'loop').symlink_to(root, target_is_directory=True)
    reads = []
    original = files.fingerprint
    def counted(path):
        reads.append(path)
        return original(path)
    monkeypatch.setattr(files, 'fingerprint', counted)
    result = files.scan(str(root), [ref])
    assert result['status'] == 'completed'
    assert set(result['files'][0]['matches']) == {str(root / 'copy'), str(root / 'alias')}
    assert len(reads) == 2
    assert str(root / 'large') not in reads


def test_capture_compares_contents_not_timestamps_and_excludes_git(tmp_path):
    reference(tmp_path / 'same', b'same')
    reference(tmp_path / 'changed', b'bug')
    reference(tmp_path / 'removed', b'delete')
    (tmp_path / '.git').mkdir()
    reference(tmp_path / '.git' / 'index', b'git data')
    before = files.snapshot(str(tmp_path))
    os.utime(tmp_path / 'same', (1, 1))
    (tmp_path / 'changed').write_bytes(b'fix')
    reference(tmp_path / 'created', b'new feature')
    (tmp_path / 'removed').unlink()
    (tmp_path / '.git' / 'index').write_bytes(b'new git metadata')
    evidence = files.changed_files(before, files.snapshot(str(tmp_path)))
    assert {Path(f['path']).name for f in evidence['files']} == {'changed', 'created'}
    assert evidence['deleted_files'] == [str(tmp_path / 'removed')]


@pytest.mark.parametrize('matched,total,status', [(0, 5, 'passed'), (1, 5, 'warning'),
                                                (1, 2, 'warning'), (2, 3, 'failed'),
                                                (4, 5, 'failed'), (5, 5, 'failed')])
def test_verdict_flags_every_match_and_rejects_strictly_above_half(matched, total, status):
    evidence = {'workspace': '/repo', 'deleted_files': []}
    obs = {'status': 'completed', 'files': [{'matches': ['/installed/x'] if i < matched else []}
                                           for i in range(total)]}
    result = leaks.verdict(evidence, obs)
    assert result['matched_file_fraction'] == matched / total
    assert result['matched_file_percent'] == pytest.approx(100 * matched / total)
    assert result['flagged'] == bool(matched)
    assert result['all_changed_files_found'] == (matched == total)
    assert result['above_rejection_threshold'] == (matched * 2 > total)
    assert result['status'] == status
    assert 'coverage' not in result and 'at_least_80_percent' not in result


@pytest.mark.parametrize('matched,status,decision', [(1, 'passed', 'passed'),
                                                    (2, 'passed', 'passed'),
                                                    (3, 'failed', 'archive')])
def test_publication_preserves_flags_and_rejects_only_above_half(matched, status, decision):
    from validation.publishing.publish import judge
    from validation.publishing.outcome_labels import labels
    result = leaks.verdict({'workspace': '/repo', 'deleted_files': []},
                          {'status': 'completed', 'files': [{'matches': ['/copy'] if i < matched else []}
                                                           for i in range(4)]})
    item = {'status': status, 'checks': [result]}
    assert judge(4, item)[0] == decision
    expected = 'static:reference-solution-leak' if matched > 2 else 'warning:reference-solution-leak'
    assert expected in labels(4, item)


@pytest.mark.parametrize('matches,status', [([], 'passed'), (['/copy'], 'failed')])
def test_deletions_are_reported_but_do_not_dilute_content_matches(matches, status):
    evidence = {'workspace': '/repo', 'deleted_files': ['/repo/gone', '/repo/also-gone']}
    result = leaks.verdict(evidence, {'status': 'completed', 'files': [{'matches': matches}]})
    assert result['status'] == status
    assert result['changed_files'] == 1
    assert result['skipped_deleted_files'] == evidence['deleted_files']
    assert result['evaluation_status'] == 'completed'


def test_incomplete_scan_never_passes_even_with_deletions():
    evidence = {'workspace': '/repo', 'deleted_files': ['/repo/gone']}
    obs = {'status': 'incomplete', 'files': [{'matches': []}]}
    assert leaks.verdict(evidence, obs)['status'] == 'error'
    evidence['deleted_files'] = []
    obs['status'] = 'incomplete'
    assert leaks.verdict(evidence, obs)['status'] == 'error'
    obs['files'][0]['matches'] = ['/installed/fix']
    result = leaks.verdict(evidence, obs)
    assert result['status'] == 'error' and not result['all_changed_files_found']
    assert result['scan_status'] == 'incomplete'


def test_candidate_read_failure_is_incomplete(tmp_path, monkeypatch):
    ref = reference(tmp_path / 'candidate', b'fix')
    def failure(path):
        raise OSError('IO error')
    monkeypatch.setattr(files, 'fingerprint', failure)
    result = files.scan(str(tmp_path), [ref])
    assert result['status'] == 'incomplete'
    assert result['errors']


def test_stale_failed_or_conflicting_reference_evidence_rejected(tmp_path):
    task = tmp_path / 'task'
    task.mkdir()
    (task / 'instruction.md').write_text('old')
    (task / 'solve.sh').write_text('solution')
    out = tmp_path / 'out'
    report = out / 'stage_2_oracle_validation' / 'run' / 'summary.json'
    report.parent.mkdir(parents=True)
    evidence = {'version': files.VERSION, 'task_sha256': leaks.evidence_digest(task),
                'status': 'completed', 'workspace': '/repo', 'files': [], 'deleted_files': []}
    item = {'task': str(task), 'status': 'passed', 'reference_file_fingerprints': [evidence]}
    def save():
        report.write_text(json.dumps({'stage': 2, 'items': [item]}))
    save()
    assert leaks.collect(task, out) == evidence
    (task / 'instruction.md').write_text('normalized path')
    assert leaks.collect(task, out) == evidence
    (task / 'solve.sh').write_text('different solution')
    with pytest.raises(ValueError, match='No complete, matching'):
        leaks.collect(task, out)
    (task / 'solve.sh').write_text('solution')
    item['status'] = 'failed'
    save()
    with pytest.raises(ValueError, match='No complete, matching'):
        leaks.collect(task, out)
    item['status'] = 'passed'
    item['reference_file_fingerprints'].append({**evidence, 'deleted_files': ['/repo/deleted']})
    save()
    with pytest.raises(ValueError, match='different changed files'):
        leaks.collect(task, out)


def test_stage4_missing_evidence_is_incomplete_no_reference_not_applicable(tmp_path):
    with_ref, without_ref = tmp_path / 'ref', tmp_path / 'no-ref'
    (with_ref / 'solution').mkdir(parents=True)
    (with_ref / 'solution/solve.sh').write_text('true')
    without_ref.mkdir()
    items = [{'task': str(t), 'status': 'passed', 'checks': []} for t in (with_ref, without_ref)]
    asyncio.run(leaks.run_checks([with_ref, without_ref], items, tmp_path / 'scan',
                                SimpleNamespace(out=tmp_path / 'out', concurrency=2)))
    assert items[0]['status'] == 'error'
    assert items[0]['reference_leak_incomplete']
    assert items[1]['checks'][0]['status'] == 'not_applicable'


def test_deletion_only_reference_is_not_applicable_without_starting_scan(tmp_path, monkeypatch):
    task = tmp_path / 'task'
    (task / 'solution').mkdir(parents=True)
    (task / 'solution/solve.sh').write_text('rm obsolete')
    monkeypatch.setattr(leaks, 'collect', lambda *args: {
        'workspace': '/repo', 'files': [], 'deleted_files': ['/repo/obsolete']})
    async def unexpected(*args):
        pytest.fail('deletion-only reference must not start a content scan')
    monkeypatch.setattr(leaks, 'scan_task', unexpected)
    items = [{'task': str(task), 'status': 'passed', 'checks': []}]
    asyncio.run(leaks.run_checks([task], items, tmp_path / 'scan',
                                SimpleNamespace(out=tmp_path / 'out', concurrency=1)))
    check = items[0]['checks'][0]
    assert check['status'] == check['evaluation_status'] == 'not_applicable'
    assert check['skipped_deleted_files'] == ['/repo/obsolete']
    assert not check['all_changed_files_found']
    assert not items[0].get('reference_leak_incomplete')


def test_empty_reference_without_deletions_is_still_incomplete():
    assert leaks.verdict({'workspace': '/repo', 'deleted_files': []},
                         {'status': 'completed', 'files': []})['status'] == 'error'


def test_oracle_hook_captures_before_upload_and_before_verifier(tmp_path, monkeypatch):
    from harbor.trial.trial import Trial
    events = []
    baseline = {'root': '/repo', 'status': 'completed', 'files': {}, 'errors': []}
    after = {**baseline, 'files': {'/repo/fixed': {'size': 3, 'sha256': 'hash'}}}
    async def probe(env, request):
        events.append(request)
        return baseline if len(events) == 1 else after
    async def agent(self):
        events.append('reference')
    monkeypatch.setattr(leaks, 'probe', probe)
    monkeypatch.setattr(Trial, '_reference_files_installed', False, raising=False)
    monkeypatch.setattr(Trial, '_run_agent_phase', agent)
    leaks.install()
    fake = SimpleNamespace(agent_environment=SimpleNamespace(with_default_user=lambda user: nullcontext()),
                           task=SimpleNamespace(paths=SimpleNamespace(task_dir=tmp_path)),
                           paths=SimpleNamespace(trial_dir=tmp_path))
    async def run():
        token = leaks.enabled.set(True)
        try:
            await Trial._run_agent_phase(fake)
        finally:
            leaks.enabled.reset(token)
    asyncio.run(run())
    assert events == [{'mode': 'snapshot'}, 'reference', {'mode': 'snapshot', 'root': '/repo'}]
    evidence = json.loads((tmp_path / leaks.ARTIFACT).read_text())
    assert evidence['status'] == 'completed'
    assert evidence['files'] == [{'path': '/repo/fixed', 'size': 3, 'sha256': 'hash'}]


def test_scan_refuses_unprepared_backend_before_start(tmp_path, monkeypatch):
    monkeypatch.delenv('OT_IMAGES_PREPARED', raising=False)
    with pytest.raises(ValueError, match='prepare cached images'):
        asyncio.run(leaks.scan_task(tmp_path, tmp_path, SimpleNamespace(backend='apptainer'), {}))


def test_stage4_fresh_cached_container_healthcheck_user_and_cleanup(tmp_path, monkeypatch):
    from contextlib import contextmanager
    from harbor.environments.factory import EnvironmentFactory
    from harbor.models.task import task as task_module
    from validation.stages import harbor as runtime
    monkeypatch.setenv('OT_IMAGES_PREPARED', '1')
    monkeypatch.setattr(runtime, 'install_runtime_patches', lambda: None)
    monkeypatch.setattr(runtime, 'check_runtime_task', lambda *args: None)
    monkeypatch.setattr(runtime, 'environment_config', lambda args: {'kwargs': {}})
    spec = SimpleNamespace(build_timeout_sec=10)
    spec.model_copy = lambda **kwargs: spec
    task = SimpleNamespace(short_name='task', paths=SimpleNamespace(environment_dir=tmp_path),
                           config=SimpleNamespace(environment=spec, agent=SimpleNamespace(user='solver')))
    monkeypatch.setattr(task_module, 'Task', lambda path: task)
    events = []
    class Environment:
        async def start(self, force_build):
            events.append(('start', force_build))
        async def run_healthcheck(self):
            events.append('healthcheck')
        @contextmanager
        def with_default_user(self, user):
            assert user == 'solver'
            events.append(('user', user))
            yield
        async def stop(self, delete):
            events.append(('stop', delete))
    monkeypatch.setattr(EnvironmentFactory, 'create_environment', lambda **kwargs: Environment())
    async def probe(env, request):
        assert request == {'mode': 'scan', 'root': '/', 'files': [{'path': '/repo/fix', 'size': 3, 'sha256': 'hash'}]}
        events.append('scan')
        return {'status': 'completed', 'files': [{'matches': ['/installed/fix']}]}
    monkeypatch.setattr(leaks, 'probe', probe)
    args = SimpleNamespace(backend='apptainer', force_build=False, trial_cpus=None, trial_memory_mb=None)
    evidence = {'files': [{'path': '/repo/fix', 'size': 3, 'sha256': 'hash'}],
                'workspace': '/repo', 'deleted_files': []}
    result = asyncio.run(leaks.scan_task(tmp_path, tmp_path / 'trial', args, evidence))
    assert events == [('start', False), 'healthcheck', ('user', 'solver'), 'scan', ('stop', True)]
    assert result['all_changed_files_found'] and result['status'] == 'failed'


def test_probe_executes_standalone_scanner_without_uploads(tmp_path):
    import subprocess
    import shutil
    reference(tmp_path / 'file with spaces', b'fixed')
    for index in range(4000):
        reference(tmp_path / f'fixture-{index:04d}', b'fixture')
    class Environment:
        async def exec(self, command, timeout_sec):
            result = subprocess.run(command, shell=True, cwd=tmp_path, text=True, capture_output=True,
                                    timeout=timeout_sec)
            return SimpleNamespace(return_code=result.returncode, stdout=result.stdout[-500_000:], stderr=result.stderr)
        async def download_file(self, source_path, target_path):
            assert Path(source_path).stat().st_size > 500_000
            shutil.copyfile(source_path, target_path)
    result = asyncio.run(leaks.probe(Environment(), {'mode': 'snapshot'}))
    assert result['status'] == 'completed'
    assert len(result['files']) == 4001
    assert str(tmp_path / 'file with spaces') in result['files']


@pytest.mark.parametrize('baked_python', [False, True])
def test_probe_finds_image_python_outside_path_or_reports_missing(tmp_path, monkeypatch, baked_python):
    import shutil
    import shlex
    import subprocess
    import sys
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    # Isolated mode must not import modules from task-controlled files.
    (workspace / 'json.py').write_text('raise RuntimeError("task module must not be imported")')
    (workspace / 'fixed').write_text('fixed content')
    bin_dir = tmp_path / 'path'
    bin_dir.mkdir()
    (bin_dir / 'rm').symlink_to(shutil.which('rm'))
    opt = tmp_path / 'image opt'
    if baked_python:
        python = opt / 'grader-python' / 'bin' / 'python3'
        python.parent.mkdir(parents=True)
        python.symlink_to(sys.executable)
    monkeypatch.setattr(leaks, 'PROBE_PYTHONS', 'python3 python ' + shlex.quote(str(opt)) + '/*/bin/python3')
    class Environment:
        async def exec(self, command, timeout_sec):
            result = subprocess.run(command, shell=True, cwd=workspace,
                                    env={**os.environ, 'PATH': str(bin_dir)},
                                    text=True, capture_output=True, timeout=timeout_sec)
            return SimpleNamespace(return_code=result.returncode, stdout=result.stdout, stderr=result.stderr)
        async def download_file(self, source_path, target_path):
            shutil.copyfile(source_path, target_path)
    if baked_python:
        result = asyncio.run(leaks.probe(Environment(), {'mode': 'snapshot'}))
        assert result['status'] == 'completed'
        assert result['files'][str(workspace / 'fixed')]['sha256'] == files.fingerprint(workspace / 'fixed')['sha256']
    else:
        with pytest.raises(RuntimeError, match='needs an existing Python 3.8'):
            asyncio.run(leaks.probe(Environment(), {'mode': 'snapshot'}))


def test_nop_stage_is_not_reference_evidence(tmp_path):
    report = tmp_path / 'stage_3_nop_validation' / 'run' / 'summary.json'
    report.parent.mkdir(parents=True)
    report.write_text(json.dumps({'stage': 3, 'items': [
        {'task': '/tasks/example', 'status': 'passed',
         'reference_file_fingerprints': [{'status': 'completed'}]}]}))
    assert leaks.read_evidence(tmp_path) == {}


@pytest.mark.parametrize('layout', ['normal', 'bare', 'worktree', 'alternate'])
def test_scan_finds_packed_and_unreachable_git_blobs(tmp_path, layout):
    import subprocess
    repo = tmp_path / 'unusual-location' / 'project'
    repo.mkdir(parents=True)
    def git(*args, cwd=repo, data=None):
        return subprocess.run(['git', *args], cwd=cwd, input=data, capture_output=True,
                              check=True).stdout.decode().strip()
    git('init', '--bare' if layout == 'bare' else '--quiet')
    gitdir = repo if layout == 'bare' else repo / '.git'
    content = b'the exact reference solution\n'
    oid = git('hash-object', '-w', '--stdin', data=content)
    # Put the solution into a pack without a branch or working-tree copy.
    git('pack-objects', str(gitdir / 'objects/pack/pack'), data=(oid + '\n').encode())
    (gitdir / 'objects' / oid[:2] / oid[2:]).unlink()
    loose = git('hash-object', '-w', '--stdin', data=b'another hidden solution')
    scan_root = repo.parent
    if layout == 'worktree':
        linked = tmp_path / 'linked'
        linked.mkdir()
        (linked / '.git').write_text('gitdir: ' + str(gitdir) + '\n')
        scan_root = linked
    elif layout == 'alternate':
        other = tmp_path / 'alternate'
        other.mkdir()
        git('init', '--quiet', cwd=other)
        (other / '.git/objects/info/alternates').write_text(str(gitdir / 'objects') + '\n')
        scan_root = other
    refs = [reference(tmp_path / 'reference', content),
            reference(tmp_path / 'reference2', b'another hidden solution')]
    result = files.scan(str(scan_root), refs)
    assert result['status'] == 'completed', result['errors']
    for entry, object_id in zip(result['files'], [oid, loose]):
        assert any(m.startswith('git-blob:') and m.endswith(':' + object_id) for m in entry['matches'])
    assert leaks.verdict({'workspace': '/repo'}, result)['status'] == 'failed'


def test_git_scan_missing_git_or_failed_command_is_incomplete(tmp_path, monkeypatch):
    import subprocess
    subprocess.run(['git', 'init', '--quiet', str(tmp_path)], check=True)
    ref = reference(tmp_path / 'reference', b'solution')
    monkeypatch.setattr(files.shutil, 'which', lambda name: None)
    result = files.scan(str(tmp_path), [ref])
    assert result['status'] == 'incomplete'
    assert 'git is unavailable' in result['errors'][0]
    monkeypatch.setattr(files.shutil, 'which', lambda name: '/bin/false')
    result = files.scan(str(tmp_path), [ref])
    assert result['status'] == 'incomplete'
    assert 'cannot enumerate' in result['errors'][0]


def test_git_blob_scan_timeout_is_incomplete(tmp_path, monkeypatch):
    import subprocess
    subprocess.run(['git', 'init', '--quiet', str(tmp_path)], check=True)
    ref = reference(tmp_path / 'reference', b'solution')
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], 120)
    monkeypatch.setattr(files.subprocess, 'run', timeout)
    result = files.scan(str(tmp_path), [ref])
    assert result['status'] == 'incomplete'
    assert 'timed out' in result['errors'][0]


def test_real_linked_worktree_reads_common_object_store(tmp_path):
    import subprocess
    repo, linked = tmp_path / 'repo', tmp_path / 'linked'
    repo.mkdir()
    def git(*args):
        return subprocess.run(['git', '-C', str(repo), *args], check=True,
                              capture_output=True).stdout.decode().strip()
    git('init', '--quiet')
    (repo / 'file').write_bytes(b'solved content')
    git('add', 'file')
    git('-c', 'user.name=Test', '-c', 'user.email=test@example.org', 'commit', '-qm', 'solution')
    oid = git('rev-parse', 'HEAD:file')
    git('worktree', 'add', '--detach', str(linked))
    (linked / 'file').unlink()
    ref = reference(tmp_path / 'reference', b'solved content')
    result = files.scan(str(linked), [ref])
    assert result['status'] == 'completed', result['errors']
    assert result['files'][0]['matches'] == [
        'git-blob:' + git('rev-parse', '--absolute-git-dir') + '/worktrees/linked:' + oid]


def test_nop_scans_once_before_agent_and_stage4_reuses_it(tmp_path, monkeypatch):
    from harbor.trial.trial import Trial
    events = []
    task = tmp_path / 'task'
    (task / 'solution').mkdir(parents=True)
    (task / 'solution/solve.sh').write_text('true')
    evidence = {'version': files.VERSION, 'status': 'completed',
                'task_sha256': leaks.evidence_digest(task), 'workspace': '/app',
                'files': [{'path': '/app/fix', 'size': 3, 'sha256': 'hash'}], 'deleted_files': []}
    async def original(self, *args, **kwargs):
        events.append('agent')
    async def probe(env, request):
        events.append('scan')
        return {'status': 'completed', 'files': [{'matches': []}]}
    monkeypatch.setattr(Trial, '_reference_files_installed', False, raising=False)
    monkeypatch.setattr(Trial, '_run_agent_phase', original)
    monkeypatch.setattr(leaks, 'probe', probe)
    leaks.install()
    trial = tmp_path / 'trial'
    trial.mkdir()
    fake = SimpleNamespace(task=SimpleNamespace(paths=SimpleNamespace(task_dir=task),
                                               config=SimpleNamespace(steps=None)),
                           paths=SimpleNamespace(trial_dir=trial),
                           agent_environment=SimpleNamespace(with_default_user=lambda user: nullcontext()))
    args = SimpleNamespace(out=tmp_path, concurrency=1)
    runtime_token = leaks.scan_runtime.set(leaks.runtime_signature(args))
    token = leaks.scan_requests.set({task.name: evidence})
    try:
        asyncio.run(Trial._run_agent_phase(fake))
        asyncio.run(Trial._run_agent_phase(fake))
    finally:
        leaks.scan_requests.reset(token)
        leaks.scan_runtime.reset(runtime_token)
    assert events == ['scan', 'agent', 'agent']
    scan = json.loads((trial / leaks.SCAN_ARTIFACT).read_text())
    summary = tmp_path / 'stage_3_nop_validation/run/summary.json'
    summary.parent.mkdir(parents=True)
    summary.write_text(json.dumps({'items': [{'reference_leak_scans': [scan]}]}))
    monkeypatch.setattr(leaks, 'read_evidence', lambda out: {task.name: [evidence]})
    async def forbidden(*args):
        raise AssertionError('Matching NOP scan should avoid another container')
    monkeypatch.setattr(leaks, 'scan_task', forbidden)
    items = [{'task': str(task), 'status': 'passed'}]
    asyncio.run(leaks.run_checks([task], items, tmp_path / 'scan', args))
    assert items[0]['checks'][0]['status'] == 'passed'
    assert leaks.read_scans(tmp_path, 'different-runtime') == {}
    evidence['files'][0]['sha256'] = 'different'
    assert leaks.evidence_signature(evidence) not in leaks.read_scans(tmp_path)
