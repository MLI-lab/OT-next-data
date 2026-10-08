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


@pytest.mark.parametrize('seconds, statuses, expected', [
    ([20, 25, 30, 40, 60], ['passed'] * 5, True),
    ([20, 25, 30, 40, 61], ['passed'] * 5, False),
    ([31] * 5, ['passed'] * 5, False),
    ([10] * 5, ['passed', 'error', 'passed', 'passed', 'passed'], False),
])
def test_preparation_rule_measures_every_fresh_run(tmp_path, monkeypatch, seconds, statuses, expected):
    calls = []
    async def build(task, out, args):
        i = len(calls)
        calls.append(out)
        return {'status': statuses[i], 'environments': [{'environment': 'agent',
                'timings_seconds': {'start': seconds[i], 'inspect': 100, 'stop': 50}}]}
    monkeypatch.setattr(build_retries.runtime, 'check_runtime_task', lambda *a: None)
    monkeypatch.setattr(build_retries.runtime, 'build_task', build)
    args = SimpleNamespace(concurrency=1, backend='apptainer', preparation_runs=5,
        preparation_median_target_seconds=30, preparation_max_seconds=60)
    result = asyncio.run(build_retries.run_builds([tmp_path/'task'], tmp_path/'out', args, StringIO()))[0]
    assert len(calls) == len(set(calls)) == 5
    assert result['preparation_timing']['seconds'] == seconds
    assert result['preparation_timing']['accepted'] is expected
    assert result['status'] == ('passed' if expected else 'failed')


def test_preparation_missing_measurement_fails_closed(tmp_path, monkeypatch):
    async def build(*args):
        return {'status': 'passed', 'environments': []}
    monkeypatch.setattr(build_retries.runtime, 'check_runtime_task', lambda *a: None)
    monkeypatch.setattr(build_retries.runtime, 'build_task', build)
    args = SimpleNamespace(concurrency=1, backend='apptainer', preparation_runs=5)
    result = asyncio.run(build_retries.run_builds([tmp_path/'task'], tmp_path/'out', args, StringIO()))[0]
    assert result['status'] == 'failed'
    assert not result['preparation_timing']['all_runs_successful']


@pytest.mark.parametrize('option, count', [([], 5), (['3'], 3)])
def test_setup_review_defaults_and_portable_evidence(tmp_path, monkeypatch, option, count):
    import json
    import tarfile
    from validation.stages.runner import parser
    args = parser().parse_args([str(tmp_path), '--review-setup', *option])
    source = tmp_path / 'slow'
    source.mkdir()
    (source / 'instruction.md').write_text('task payload')
    async def build(*args):
        return {'status': 'passed', 'environments': [{'environment': 'agent',
                'timings_seconds': {'start': 61}}]}
    monkeypatch.setattr(build_retries.runtime, 'check_runtime_task', lambda *a: None)
    monkeypatch.setattr(build_retries.runtime, 'build_task', build)
    result = asyncio.run(build_retries.run_builds([source], tmp_path/'out', args, StringIO()))[0]
    assert len(result['preparation_runs']) == count
    assert result['preparation_timing']['mean_target_seconds'] == 30
    assert result['preparation_timing']['max_limit_seconds'] == 60
    review = tmp_path/'out/review/setup-speed'
    index = json.loads((review/'index.json').read_text())
    assert index['passed'] == 0
    record = index['flagged'][0]
    assert 'exceeds 60s' in record['reason']
    with tarfile.open(review/record['task_archive']) as archive:
        assert archive.extractfile('slow/instruction.md').read() == b'task payload'
    assert json.loads((review/record['evidence']).read_text())['status'] == 'failed'


@pytest.mark.parametrize('values', [dict(review_setup=0), dict(review_setup=-1),
    dict(review_setup=5, force_build=True), dict(preparation_max_seconds=float('nan'))])
def test_setup_review_rejects_invalid_policy(values):
    with pytest.raises(ValueError):
        build_retries.configure_preparation_review(SimpleNamespace(**values))


@pytest.mark.parametrize('task_seconds, verifier_seconds, verifier_status, buckets', [
    ([10]*5, [2]*5, 'passed', []),
    ([20, 20, 20, 50, 50], [2]*5, 'passed', ['task-setup-needs-review']),
    ([10]*5, [2, 2, 2, 8, 8], 'passed', ['verifier-setup-needs-review']),
    ([10]*5, [0, 0, 0, 0, 61], 'passed', ['verifier-setup-needs-review']),
    ([40]*5, [2]*5, 'error', ['task-setup-needs-review', 'verifier-setup-needs-review']),
])
def test_review_has_independent_mean_budgets_and_buckets(tmp_path, monkeypatch, task_seconds,
                                                        verifier_seconds, verifier_status, buckets):
    import json
    from validation.stages.runner import parser
    source = tmp_path/'task'
    source.mkdir()
    calls = []
    async def build(*a):
        i = len(calls); calls.append(i)
        return {'status': 'error' if verifier_status == 'error' else 'passed',
            'environments': [{'environment': 'agent', 'status': 'passed', 'timings_seconds': {'start': task_seconds[i]}}],
            'verifier_preparation': [{'environment': 'verifier', 'status': verifier_status,
                'mean_target_seconds': 3, 'timings_seconds': {'preparation': verifier_seconds[i]}}]}
    monkeypatch.setattr(build_retries.runtime, 'check_runtime_task', lambda *a: None)
    monkeypatch.setattr(build_retries.runtime, 'build_task', build)
    args = parser().parse_args([str(source), '--review-setup'])
    result = asyncio.run(build_retries.run_builds([source], tmp_path/'out', args, StringIO()))[0]
    assert result['review_buckets'] == buckets
    assert len(calls) == 5
    for bucket in ('task-setup-needs-review', 'verifier-setup-needs-review'):
        index = json.loads((tmp_path/'out/review'/bucket/'index.json').read_text())
        assert bool(index['flagged']) == (bucket in buckets)


def test_review_help_renders_percent_budget():
    from validation.stages.runner import parser
    assert '5% of timeout' in ' '.join(parser().format_help().split())
