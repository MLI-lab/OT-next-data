import asyncio
from types import SimpleNamespace

from validation.stages import path_diagnostics as diagnostics


def test_scan_does_not_repeat_task_healthcheck(tmp_path, monkeypatch):
    from data.utils import resolve_absolute_paths
    (tmp_path / 'instruction.md').write_text('file.py')
    monkeypatch.setattr(resolve_absolute_paths, 'flagged_paths', lambda task: (['file.py'], {}))
    class Environment:
        async def run_healthcheck(self):
            raise AssertionError('must not repeat setup')
        async def exec(self, command, **kwargs):
            return SimpleNamespace(return_code=0, stdout='/\n' if command == 'pwd -P' else '/repo/file.py\0', stderr='')
    report = asyncio.run(diagnostics.scan(Environment(), tmp_path, ['/repo'], 'after-verifier'))
    assert report['status'] == 'completed'
    assert report['observation_phase'] == 'after-verifier'
    assert report['healthcheck']['status'] == 'not-rerun-lifecycle-owned'


def test_reference_created_is_distinct_from_verifier_only():
    baseline = {'status': 'completed', 'findings': [
        {'relative_path': 'halfnormal.py', 'matches': []}]}
    observed = {'findings': [
        {'relative_path': 'halfnormal.py', 'status': 'unique', 'matches': ['/repo/halfnormal.py']}]}
    finding = diagnostics.compare(baseline, observed)['findings'][0]
    assert finding['classification'] == 'reference-created'
    assert finding['new_matches'] == ['/repo/halfnormal.py']
    assert not finding['automatic_replacement_allowed']


def test_oracle_hook_records_two_phases_and_preserves_failed_baseline(tmp_path, monkeypatch):
    import json
    import pytest
    from harbor.trial.trial import Trial
    events = []
    async def original(self, **kwargs):
        events.append('reference')
        if kwargs.get('fail'):
            raise RuntimeError('reference failed')
    async def scan(environment, task, roots, phase):
        events.append(phase)
        return {'status': 'completed', 'findings': [{'relative_path': 'new.py',
            'matches': ['/repo/new.py'] if 'after-reference' in phase else []}]}
    monkeypatch.setattr(diagnostics, 'scan', scan)
    monkeypatch.setattr(Trial, '_path_diagnostics_installed', False, raising=False)
    monkeypatch.setattr(Trial, '_run_agent_phase', original)
    diagnostics.install()
    fake = SimpleNamespace(agent_environment=object(), task=SimpleNamespace(paths=SimpleNamespace(task_dir=tmp_path)),
                           paths=SimpleNamespace(trial_dir=tmp_path))
    async def run():
        token = diagnostics.observations.set({'roots': ['/repo']})
        try:
            await Trial._run_agent_phase(fake)
            report = json.loads((tmp_path / 'instruction-path-diagnostics.json').read_text())
            assert report['observations'][0]['findings'][0]['classification'] == 'reference-created'
            assert events == ['after-task-setup-before-reference-solution', 'reference',
                              'after-reference-solution-before-verifier']
            with pytest.raises(RuntimeError, match='reference failed'):
                await Trial._run_agent_phase(fake, fail=True)
            report = json.loads((tmp_path / 'instruction-path-diagnostics.json').read_text())
            assert report['observations'] == []
        finally:
            diagnostics.observations.reset(token)
    asyncio.run(run())
