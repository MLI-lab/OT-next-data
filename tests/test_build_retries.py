import asyncio
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

import pytest

from validation.stages import build_retries
from validation.publishing import publish


@pytest.mark.parametrize('retries, expected', [
    (['passed'] * 3, 'passed'),
    (['passed', 'error', 'passed'], 'failed'),
    (['error'] * 3, 'failed'),
])
def test_failed_cohort_gets_three_full_rounds(tmp_path, monkeypatch, retries, expected):
    calls = []
    sources = [tmp_path / 'good', tmp_path / 'bad']
    async def build(task, out, args):
        attempt = sum(name == task.name for name, _ in calls)
        calls.append((task.name, out))
        if task.name == 'good':
            return {'status': 'passed'}
        if attempt == 0:
            raise RuntimeError('initial startup failure')
        return {'status': retries[attempt - 1]}
    monkeypatch.setattr(build_retries.runtime, 'check_runtime_task', lambda *a: None)
    monkeypatch.setattr(build_retries.runtime, 'build_task', build)
    args = SimpleNamespace(concurrency=2, backend='apptainer')
    results = asyncio.run(build_retries.run_builds(sources, tmp_path / 'out', args, StringIO()))
    assert [name for name, _ in calls] == ['good', 'bad', 'bad', 'bad', 'bad']
    assert len({path for _, path in calls}) == 5
    assert results[0]['status'] == 'passed'
    assert results[1]['status'] == expected
    assert len(results[1]['build_attempts']) == 4
    assert publish.judge(3, results[1])[0] == ('passed' if expected == 'passed' else 'archive')


def test_pipeline_skips_failed_builds_in_both_trial_stages(tmp_path, monkeypatch):
    from validation import run
    from validation.stages.runner import parser
    args = parser().parse_args([str(tmp_path), '--out', str(tmp_path / 'out')])
    sources = [tmp_path / 'good', tmp_path / 'bad']
    seen = []
    def stage(number, args):
        if number == 3:
            items = [{'task': str(sources[0]), 'status': 'passed'},
                     {'task': str(sources[1]), 'status': 'failed', 'reason': 'unstable build'}]
        else:
            selected, skipped = build_retries.partition_builds(sources, args)
            seen.append((number, selected, skipped))
            items = skipped
        return tmp_path / f'{number}.json', {'items': items, 'has_findings': number == 3}
    monkeypatch.setattr(run, 'run_stage', stage)
    assert run.run_selected(args, [3, 4, 5]) == 1
    for number, selected, skipped in seen:
        assert selected == sources[:1]
        assert skipped[0]['blocked_by_stage'] == 3
        assert skipped[0]['task'] == str(sources[1])
    assert len(seen) == 2


def test_complete_publication_accepts_only_evidenced_build_skips(tmp_path, monkeypatch):
    frozen = {'sha256': 'hash'}
    monkeypatch.setattr(publish, 'read', lambda path: frozen)
    monkeypatch.setattr(publish, 'task_records', lambda _: [{'task_id': 'bad'}])
    skipped = {'task': 'bad', 'status': 'skipped', 'blocked_by_stage': 3}
    build = {'task': 'bad', 'status': 'failed', 'build_stability': 'unstable_build',
             'build_attempts': [{'status': 'error'}] * 4, 'reason': '0/3 retries passed'}
    reports = {n: {'complete': True, 'contract_sha256': 'hash', 'items': [item]}
               for n, item in [(1, {'task': 'bad', 'status': 'passed'}), (3, build), (4, skipped), (5, skipped)]}
    monkeypatch.setattr(publish, 'stage_reports', lambda _: reports)
    publish.require_complete(tmp_path, tmp_path / 'contract')
    assert publish.decide(reports, {'bad'})['bad']['archive'][0] == 3
    build.update(status='passed')
    with pytest.raises(ValueError, match='stage 4'):
        publish.require_complete(tmp_path, tmp_path / 'contract')
