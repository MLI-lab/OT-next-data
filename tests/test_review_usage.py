import io
import json
from pathlib import Path
import tarfile

import pytest

from validation.publishing import review_usage as usage
from validation.publishing.report import write_report


def result(cost=None, inp=1000, cache=300, out=200):
    return {'agent_result': {'n_input_tokens': inp, 'n_cache_tokens': cache,
                             'n_output_tokens': out, 'cost_usd': cost}}


def test_cache_pricing_preserves_reported_cost_and_reprices_offline(tmp_path):
    (tmp_path / 'agent').mkdir()
    (tmp_path / 'agent/trajectory.json').write_text(json.dumps({
        'final_metrics': {'extra': {'total_cache_creation_input_tokens': 100}}}))
    rates = {'input': 2, 'output': 8, 'cache_read': 0.2, 'cache_write': 2.5}
    collected = usage.collect([(tmp_path, result(0.04))], 'claude-code', 'test/model', rates)
    row = collected['trials'][0]
    assert row['estimated_cost_usd'] == pytest.approx((600*2 + 300*0.2 + 100*2.5 + 200*8)/1e6)
    assert row['cost_usd'] == row['reported_cost_usd'] == 0.04
    report = {'stage_layout': 'environment-first-v2', 'stage': 5, 'items': [{'task': 't', 'usage': collected}]}
    updated = usage.reprice(json.loads(json.dumps(report)), {'input': 1, 'output': 1})
    assert updated['items'][0]['usage']['trials'][0]['estimated_cost_usd'] == 0.0012
    assert updated['usage']['metrics']['cost_usd']['total'] == 0.04


def test_missing_cost_is_not_zero_and_failed_attempts_are_counted(tmp_path):
    failed = result(0.02)
    failed['exception_info'] = {'exception_type': 'AgentTimeoutError'}
    collected = usage.collect([(tmp_path, failed), (tmp_path, result())], 'terminus-2', 'model', expected_trials=2)
    metrics = collected['summary']['metrics']
    assert metrics['input_tokens']['total'] == 2000
    assert metrics['cost_usd']['total'] is None
    assert metrics['cost_usd']['known_subtotal'] == 0.02
    assert metrics['cost_usd']['trials_with_data'] == 1
    assert metrics['cost_usd']['average_per_task'] is None
    # Supplied fallback pricing completes cost coverage without dropping failures.
    report = usage.reprice({'stage_layout': 'environment-first-v2', 'stage': 5, 'items': [{'task': 't', 'usage': collected}]}, {'input': 1, 'output': 4})
    assert report['usage']['metrics']['cost_usd']['total'] == pytest.approx(0.0218)


def test_missing_trials_and_unstarted_tasks_make_totals_incomplete(tmp_path):
    collected = usage.collect([(tmp_path, result(0.02))], 'a', 'm', expected_trials=2)
    assert collected['summary']['metrics']['input_tokens']['total'] is None
    summary = usage.summarize_items([{'task': 'a', 'usage': collected}, {'task': 'b', 'status': 'error'}], 2)
    assert summary['expected_trials'] == 4 and summary['task_count'] == 2
    assert summary['metrics']['cost_usd']['total'] is None


def test_average_uses_unique_tasks_and_all_attempts(tmp_path):
    collected = usage.collect([(tmp_path, result(0.03)), (tmp_path, result(0.01))], 'a', 'm', expected_trials=2)
    items = [{'task': 'same-task', 'usage': collected}, {'task': 'same-task', 'usage': collected}]
    summary = usage.summarize_items(items)
    assert summary['task_count'] == 1 and summary['trial_count'] == 4
    assert summary['metrics']['cost_usd']['average_per_task'] == 0.08


def test_elapsed_time_average_is_reported_for_complete_review_timings():
    rows = [{'elapsed_seconds': 30.0}, {'elapsed_seconds': 90.0}]
    summary = usage.summarize_rows(rows, expected=2, tasks=2)
    metric = summary['metrics']['elapsed_seconds']
    assert metric['total'] == 120.0
    assert metric['average_per_task'] == 60.0


def test_zero_local_api_estimate_and_unknown_cache(tmp_path):
    collected = usage.collect([(tmp_path, result(cache=None))], 'a', 'local', {'input': 0, 'output': 0})
    assert collected['summary']['metrics']['cost_usd']['total'] == 0
    row = collected['trials'][0]
    usage.price(row, {'input': 1, 'output': 4, 'cache_read': 0.1})
    assert row['estimated_cost_usd'] is None
    assert 'missing' in row['estimation_note']
    row['cache_read_input_tokens'] = 2000
    usage.price(row, {'input': 1, 'output': 4})
    assert row['estimated_cost_usd'] is None
    assert 'exceed' in row['estimation_note']


@pytest.mark.parametrize('rates', [{}, [], {'input': 1}, {'input': -1, 'output': 0},
    {'input': True, 'output': 0}, {'input': float('nan'), 'output': 0}, {'input': 0, 'output': 1, 'typo': 2}])
def test_invalid_pricing_is_rejected(rates):
    with pytest.raises(ValueError, match='pricing'):
        usage.validate_pricing(rates)


def test_archive_exports_usage_and_repriceable_stage_report(tmp_path):
    collected = usage.collect([(tmp_path, result(0.03))], 'a', 'm')
    stage = {'stage_layout': 'environment-first-v2', 'stage': 5, 'name': 'llm_rubric_review', 'complete': True,
             'items': [{'task': '/tasks/a', 'status': 'findings', 'usage': collected}]}
    archive = tmp_path / 'evidence.tar.gz'
    payload = json.dumps(stage).encode()
    with tarfile.open(archive, 'w:gz') as tar:
        entry = tarfile.TarInfo('results/stage_5_llm_rubric_review/abc/summary.json')
        entry.size = len(payload)
        tar.addfile(entry, io.BytesIO(payload))
    out = tmp_path / 'report'
    summary = write_report(archive, out)
    assert summary['review_usage']['metrics']['cost_usd']['total'] == 0.03
    assert summary['stages'][0]['usage']['metrics']['cost_usd']['average_per_task'] == 0.03
    assert json.loads((out / 'reviews.json').read_text())['tasks'][0]['usage'] == collected
    assert json.loads((out / 'stage-5-abc.json').read_text()) == stage


def test_shard_merge_recomputes_usage_from_all_tasks(tmp_path, monkeypatch):
    from tests.test_shards import fake_contract, report_for, write_shards
    from validation import shards
    fake_contract(monkeypatch, stages=(5,))
    def make(index):
        report = report_for(5, index, 2)
        for item in report['items']:
            item['usage'] = usage.collect([(tmp_path, result(0.01))], 'a', 'm')
        report['usage'] = usage.summarize_items(report['items'])
        return report
    write_shards(tmp_path, 2, {5: make})
    summary = shards.merge('contract.json', tmp_path, tmp_path / 'merged/report')
    merged = json.loads(Path(summary['stages'][5]).read_text())
    assert merged['usage']['task_count'] == 7
    assert merged['usage']['metrics']['cost_usd']['total'] == pytest.approx(0.07)
    assert merged['usage']['metrics']['cost_usd']['average_per_task'] == pytest.approx(0.01)


def test_stage_review_keeps_spend_when_verdict_is_missing(tmp_path, monkeypatch):
    from validation.stages import runner
    options = runner.parser().parse_args(['--review-evidence', 'files', '--review-agent', 'terminus-2', '--review-model', 'test/judge',
                                          '--review-pricing', '{"input": 1, "output": 4}'])
    monkeypatch.setattr(runner, 'resolve_review_defaults', lambda *args: None)
    staged = tmp_path / 'staged'
    staged.mkdir()
    options.out = tmp_path / 'out'
    (staged / 'instruction.md').write_text('Review /app/rubric.toml')
    (staged / 'environment').mkdir()
    (staged / 'setup_files').mkdir()
    (staged / 'environment/Dockerfile').write_text('FROM ubuntu:24.04\n')
    (staged / 'rubric-skips.json').write_text('{}')
    (staged / 'rubric.toml').write_text('[[criteria]]\nname="verifiable"\n')
    monkeypatch.setattr(runner.prepare_review_tasks, 'stage_review', lambda *args: staged)
    monkeypatch.setattr(runner.runtime, 'check_runtime_task', lambda *args: None)
    monkeypatch.setattr(runner.runtime, 'job_config', lambda *args: {})
    async def execute(config):
        job = tmp_path / 'job'
        trial = job / 'trial'
        trial.mkdir(parents=True, exist_ok=True)
        (trial / 'result.json').write_text(json.dumps({**result(), 'verifier_result': {'rewards': {'reward': 0}}}))
        return job
    monkeypatch.setattr(runner.runtime, 'execute_job', execute)
    reviewed = runner.run_review(5, tmp_path / 'task', tmp_path / 'review', options, tmp_path)
    assert reviewed['status'] == 'error'
    assert 'Missing or invalid criteria' in reviewed['findings'][0]
    assert len(reviewed['usage']['trials']) == 3
    assert reviewed['usage']['summary']['metrics']['cost_usd']['total'] == pytest.approx(3 * 0.0018)


def test_together_defaults_match_exact_models_and_freeze_rates():
    from validation.publishing import review_pricing
    from validation.stages.runner import parser
    options = parser().parse_args(['--review-local', '--serve-model', 'glm-5.3'])
    first = review_pricing.for_stage(options, 5)
    assert first['rates'] == {'input': 1.4, 'output': 4.4, 'cache_read': 0.26}
    assert first['source']['model'] == 'zai-org/GLM-5.3'
    # Frozen args survive serialization; the run does not need the live catalog.
    from types import SimpleNamespace
    restored = SimpleNamespace(resolved_review_pricing=json.loads(json.dumps(options.resolved_review_pricing)))
    assert review_pricing.for_stage(restored, 5) == first
    first['rates']['input'] = 999
    assert review_pricing.together_reference('zai-org/GLM-5.3')['rates']['input'] == 1.4
    assert review_pricing.together_reference('together_ai/zai-org/GLM-5.3')['rates']['input'] == 1.4
    assert review_pricing.together_reference('zai-org/GLM-5.3-Flash')['rates'] is None


def test_unknown_local_model_is_unpriced_and_manual_zero_wins():
    from validation.publishing.review_pricing import for_stage
    from validation.stages.runner import parser
    options = parser().parse_args(['--review-local', '--serve-model', 'coder-30b'])
    assert for_stage(options, 5)['rates'] is None
    assert for_stage(options, 5)['source']['status'] == 'no_verified_match'
    options = parser().parse_args(['--review-local', '--serve-model', 'glm-5.3',
                                  '--review-pricing', '{"input": 0, "output": 0}'])
    assert for_stage(options, 5) == {'rates': {'input': 0, 'output': 0}, 'source': {'basis': 'user_supplied'}}


def test_analysis_gets_its_own_price_and_repricing_updates_provenance(tmp_path):
    from validation.publishing.review_pricing import for_stage
    from validation.stages.runner import parser
    options = parser().parse_args(['--review-model', 'together_ai/zai-org/GLM-5.3',
                                  '--analysis-model', 'together_ai/openai/gpt-oss-120b'])
    assert for_stage(options, 5)['rates']['input'] == 1.4
    resolved = for_stage(options, 8)
    assert resolved['rates']['input'] == 0.15
    collected = usage.collect([(tmp_path, result())], 'terminus-2', options.analysis_model,
                              resolved['rates'], pricing_source=resolved['source'])
    assert collected['trials'][0]['pricing_source']['basis'] == 'reference_api_equivalent'
    report = usage.reprice({'stage_layout': 'environment-first-v2', 'stage': 8, 'items': [{'task': 't', 'usage': collected}]}, {'input': 1, 'output': 1})
    assert report['items'][0]['usage']['trials'][0]['pricing_source'] == {'basis': 'user_supplied'}


def test_gpu_hours_are_reserved_stage_time_without_dollar_conversion(monkeypatch):
    from datetime import datetime, timezone
    from validation.stages import runner
    monkeypatch.setattr(runner.time, 'monotonic', lambda: 3700)
    timing = runner.stage_timing((datetime.now(timezone.utc), 100), 4, serving_gpu_count=8)
    assert timing['serving_gpu_hours'] == 8
    assert timing['serving_gpu_count'] == 8
    assert not any('cost' in key for key in timing)
    assert 'serving_gpu_hours' not in runner.stage_timing((datetime.now(timezone.utc), 100), 4)
