import asyncio
from contextlib import nullcontext
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from validation.checks import reference_files as files
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


def test_incomplete_scan_and_deletions_never_pass():
    evidence = {'workspace': '/repo', 'deleted_files': ['/repo/gone']}
    obs = {'status': 'completed', 'files': [{'matches': []}]}
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


def test_nop_stage_is_not_reference_evidence(tmp_path):
    report = tmp_path / 'stage_3_nop_validation' / 'run' / 'summary.json'
    report.parent.mkdir(parents=True)
    report.write_text(json.dumps({'stage': 3, 'items': [
        {'task': '/tasks/example', 'status': 'passed',
         'reference_file_fingerprints': [{'status': 'completed'}]}]}))
    assert leaks.read_evidence(tmp_path) == {}
