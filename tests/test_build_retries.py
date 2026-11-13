import asyncio
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

import pytest

from validation.stages import build_retries
from validation.publishing import publish


@pytest.mark.parametrize('retries, expected, stability, builds', [
    ([{'status': 'passed'}], 'passed', 'recovered', 2),
    ([{'status': 'error', 'reason': 'BridgeOutage'}, {'status': 'passed'}], 'passed', 'recovered', 3),
    # Infrastructure first and then the task's own failure: the result changed, not robust.
    ([{'status': 'failed', 'reason': 'image build failed: apt error'}] * 3, 'failed', 'unstable_build', 4),
    ([{'status': 'error', 'reason': 'BridgeOutage'}] * 3, 'error', 'infrastructure_failure', 4),
])
def test_only_infrastructure_failures_are_retried(tmp_path, monkeypatch, retries, expected, stability, builds):
    calls = []
    sources = [tmp_path / 'good', tmp_path / 'bad']
    async def build(task, out, args):
        attempt = sum(name == task.name for name, _ in calls)
        calls.append((task.name, out))
        if task.name == 'good':
            return {'status': 'passed'}
        if attempt == 0:
            return {'status': 'error', 'reason': 'BridgeOutage: worker restarted'}
        return dict(retries[attempt - 1])
    monkeypatch.setattr(build_retries.runtime, 'check_runtime_task', lambda *a: None)
    monkeypatch.setattr(build_retries.runtime, 'build_task', build)
    args = SimpleNamespace(concurrency=2, backend='apptainer', outcome_retries=3)
    results = asyncio.run(build_retries.run_builds(sources, tmp_path / 'out', args, StringIO()))
    assert [name for name, _ in calls] == ['good'] + ['bad'] * builds
    assert results[0]['status'] == 'passed' and 'build_attempts' not in results[0]
    assert (results[1]['status'], results[1]['build_stability']) == (expected, stability)
    assert results[1]['build_retry_summary']['builds'] == builds and results[1]['build_retry_summary']['nodes']
    assert publish.judge(1, results[1])[0] == {'passed': 'passed', 'failed': 'archive', 'error': 'not_run'}[expected]
    table = build_retries.summarize_retries(results)
    assert table['tasks_built_first_try'] == 1 and table['tasks_retried'] == 1


def test_network_mismatch_is_not_retried_and_does_not_block_later_stages(tmp_path, monkeypatch):
    calls = []
    async def build(task, out, args):
        calls.append(task.name)
        return {'status': 'passed', 'build_status': 'passed', 'environments': [{'environment': 'agent', 'status': 'passed', 'checks': [
            {'check': 'declared-internet-access', 'status': 'failed', 'expected_allow_internet': False}]}]}
    monkeypatch.setattr(build_retries.runtime, 'check_runtime_task', lambda *a: None)
    monkeypatch.setattr(build_retries.runtime, 'build_task', build)
    args = SimpleNamespace(concurrency=1, backend='apptainer', outcome_retries=3)
    item = asyncio.run(build_retries.run_builds([tmp_path / 'bad'], tmp_path / 'out', args, StringIO()))[0]
    assert calls == ['bad'] and item['build_status'] == 'passed' and 'build_attempts' not in item


@pytest.mark.parametrize('results, status, stability, outcome', [
    (['failed', 'failed', 'failed', 'failed'], 'failed', 'task_failure', 'archive'),
    (['failed', 'passed'], 'failed', 'unstable_build', 'archive'),
])
def test_own_build_failures_are_retried(tmp_path, monkeypatch, results, status, stability, outcome):
    calls = []
    async def build(task, out, args):
        result = results[len(calls)]
        calls.append(task.name)
        return {'status': result, **({'reason': 'image build failed'} if result == 'failed' else {})}
    monkeypatch.setattr(build_retries.runtime, 'check_runtime_task', lambda *a: None)
    monkeypatch.setattr(build_retries.runtime, 'build_task', build)
    args = SimpleNamespace(concurrency=1, backend='apptainer', outcome_retries=3)
    item = asyncio.run(build_retries.run_builds([tmp_path / 'bad'], tmp_path / 'out', args, StringIO()))[0]
    assert len(calls) == len(results) and (item['status'], item['build_stability']) == (status, stability)
    assert publish.judge(1, item)[0] == outcome


def test_pipeline_skips_failed_builds_in_both_trial_stages(tmp_path, monkeypatch):
    from validation import run
    from validation.stages.runner import parser
    args = parser().parse_args([str(tmp_path), '--out', str(tmp_path / 'out')])
    sources = [tmp_path / 'good', tmp_path / 'bad']
    seen = []
    def stage(number, args):
        if number == 1:
            items = [{'task': str(sources[0]), 'status': 'passed'},
                     {'task': str(sources[1]), 'status': 'failed', 'reason': 'unstable build'}]
        else:
            selected, skipped = build_retries.partition_builds(sources, args)
            seen.append((number, selected, skipped))
            items = skipped
        return tmp_path / f'{number}.json', {'items': items, 'has_findings': number == 1}
    monkeypatch.setattr(run, 'run_stage', stage)
    args.fix_pip_pins = False
    assert run.run_selected(args, [1, 2, 3]) == 1
    for number, selected, skipped in seen:
        assert selected == sources[:1]
        assert skipped[0]['blocked_by_stage'] == 1
        assert skipped[0]['task'] == str(sources[1])
    assert len(seen) == 2


def test_complete_publication_accepts_only_evidenced_build_skips(tmp_path, monkeypatch):
    frozen = {'sha256': 'hash'}
    monkeypatch.setattr(publish, 'read', lambda path: frozen)
    monkeypatch.setattr(publish, 'task_records', lambda _: [{'task_id': 'bad'}])
    skipped = {'task': 'bad', 'status': 'skipped', 'blocked_by_stage': 1}
    build = {'task': 'bad', 'status': 'failed', 'build_stability': 'unstable_build',
             'build_attempts': [{'status': 'error'}] * 4, 'reason': '0/3 retries passed'}
    reports = {n: {'stage_layout': 'environment-first-v3', 'complete': True, 'contract_sha256': 'hash', 'items': [item]}
               for n, item in [(0, {'task': 'bad', 'status': 'passed'}), (1, build), (2, skipped), (3, skipped)]}
    monkeypatch.setattr(publish, 'stage_reports', lambda _: reports)
    publish.require_complete(tmp_path, tmp_path / 'contract')
    assert publish.decide(reports, {'bad'})['bad']['archive'][0] == 1
    build.update(status='passed')
    with pytest.raises(ValueError, match='stage 2'):
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
                'timings_seconds': {'preparation': 61}}]}
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
    assert json.loads((review/record['evidence']).read_text())['setup_speed']['status'] == 'failed'
    # A slow setup does not fail the build or block later stages.
    assert json.loads((review/record['evidence']).read_text())['status'] == 'passed'


@pytest.mark.parametrize('values', [dict(review_setup=0), dict(review_setup=-1),
    dict(review_setup=5, force_build=True), dict(preparation_max_seconds=float('nan'))])
def test_setup_review_rejects_invalid_policy(values):
    with pytest.raises(ValueError):
        build_retries.configure_preparation_review(SimpleNamespace(**values))


@pytest.mark.parametrize('task_seconds, verifier_seconds, verifier_status, buckets', [
    ([10]*5, [2]*5, 'passed', []),
    ([20, 20, 20, 50, 50], [2]*5, 'passed', ['task-setup-needs-review']),
    ([10]*5, [2, 2, 2, 8, 8], 'passed', []),
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
            'environments': [{'environment': 'agent', 'status': 'passed', 'timings_seconds': {'start': task_seconds[i] + 120, 'container_start': 120, 'preparation': task_seconds[i]}}],
            'verifier_preparation': [{'environment': 'verifier', 'status': verifier_status,
                'timings_seconds': {'preparation': verifier_seconds[i]}}]}
    monkeypatch.setattr(build_retries.runtime, 'check_runtime_task', lambda *a: None)
    monkeypatch.setattr(build_retries.runtime, 'build_task', build)
    args = parser().parse_args([str(source), '--review-setup'])
    result = asyncio.run(build_retries.run_builds([source], tmp_path/'out', args, StringIO()))[0]
    assert result['review_buckets'] == buckets
    # Setup speed is its own check; the build fails only if no preparation succeeded.
    assert result['setup_speed']['status'] == ('failed' if buckets else 'passed')
    assert result['status'] == ('failed' if verifier_status == 'error' else 'passed')
    assert len(calls) == 5
    for bucket in ('task-setup-needs-review', 'verifier-setup-needs-review'):
        index = json.loads((tmp_path/'out/review'/bucket/'index.json').read_text())
        assert bool(index['flagged']) == (bucket in buckets)


def test_review_help_states_preparation_limits():
    from validation.stages.runner import parser
    assert 'each task or verifier preparation <=60s' in ' '.join(parser().format_help().split())


def test_missing_reference_is_not_applicable_even_after_build_failure(tmp_path):
    from validation.stages.runner import parser, run_trial_batch
    from validation.stages.layout import REFERENCE
    source = tmp_path / 'missing-reference'
    source.mkdir()
    args = parser().parse_args([])
    args._build_failures = {str(source): {'report': 'build.json', 'reason': 'image failed'}}
    selected, skipped = build_retries.partition_builds([source], args, reference=True)
    assert selected == [source] and not skipped
    items = run_trial_batch(REFERENCE, selected, tmp_path, args, tmp_path)
    assert items[0]['status'] == items[0]['listing_order']['status'] == 'not_applicable'
    selected, skipped = build_retries.partition_builds([source], args)
    assert not selected and skipped[0]['blocked_by_stage'] == 1


@pytest.mark.parametrize('error', [
    'Bridge job j-1 failed: Task disk limit exceeded: the task wrote 1088 MiB, its task.toml storage is 1024 MiB',
    'Task setup exited 153: line 5: 285 File size limit exceeded dd if=/dev/zero of=/var/tmp/x',
    'Bridge job j-2 failed: Task memory limit exceeded during environment startup'])
def test_exceeding_declared_limits_is_a_failed_build_with_a_verdict(tmp_path, monkeypatch, error):
    from validation import run
    async def build(task, out, args):
        return {'status': 'error', 'environments': [{'environment': 'agent', 'status': 'error', 'error': error}]}
    monkeypatch.setattr(build_retries.runtime, 'check_runtime_task', lambda *a: None)
    monkeypatch.setattr(build_retries.runtime, 'build_task', build)
    args = SimpleNamespace(concurrency=1, backend='apptainer', outcome_retries=3)
    item = asyncio.run(build_retries.run_builds([tmp_path / 'heavy'], tmp_path / 'out', args, StringIO()))[0]
    assert (item['status'], item['build_status'], item['build_stability']) == ('failed', 'failed', 'task_failure')
    assert error.split(': ', 1)[-1][:20] in item['reason'] or 'limit exceeded' in item['reason']
    row, = run.observations({1: {'items': [dict(item, task=str(tmp_path / 'heavy'))]}}, {'heavy': 'h'})
    assert row['build'] == 'fail'


@pytest.mark.parametrize('preparation_runs', [1, 2])
@pytest.mark.parametrize('malformed', [True, False])
def test_dockerfile_verifier_is_setup_failure(tmp_path, monkeypatch, preparation_runs, malformed):
    from validation.outcomes import normalize
    source = tmp_path / 'task'
    (source / 'tests').mkdir(parents=True)
    (source / 'environment').mkdir()
    dockerfile = 'FROM ubuntu:24.04\nRUN apt-get update\n'
    (source / 'environment/Dockerfile').write_text(dockerfile)
    (source / 'tests/test.sh').write_text(dockerfile.rstrip() if malformed else
                                         '#!/bin/sh\npython3 -m pytest /tests/test_outputs.py\n')
    calls = []
    async def build(task, out, args):
        calls.append(task)
        return {'status': 'passed', 'environments': [
            {'environment': 'agent', 'status': 'passed', 'timings_seconds': {'start': 1}}]}
    monkeypatch.setattr(build_retries.runtime, 'check_runtime_task', lambda *a: None)
    monkeypatch.setattr(build_retries.runtime, 'build_task', build)
    args = SimpleNamespace(concurrency=1, backend='apptainer', preparation_runs=preparation_runs)
    journal = StringIO()
    item = asyncio.run(build_retries.run_builds([source], tmp_path / 'out', args, journal))[0]
    assert len(calls) == preparation_runs
    assert item['build_status'] == ('failed' if malformed else 'passed')
    if malformed:
        assert item['environment_setup_status'] == 'passed'
        assert normalize('build', 'fail', item)[0] == 'fail'
        assert 'Malformed verifier entrypoint' in journal.getvalue()
