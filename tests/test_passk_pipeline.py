"""Stages 6/7 in the pipeline: results.parquet columns, teacher routing, task selection, model resolution."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from validation import run as results
from validation.stages import judge_selection, passk_stage
from validation.stages import runner
from validation.stages.runner import select_passk_sources


def item(task, outcome, successes, allocation, requirements):
    attempt = {'trial': f'/j/{task}__0', 'reward': 1 if successes else 0, 'resources': {'agent': {'memory_peak_mb': 900}},
               'allocation': allocation, 'calibration': {'kind': 'valid'}}
    passk = {'model': 'openai/qwen35-35b-a3b', 'k': 4, 'outcome': outcome}
    if outcome != 'inconclusive':
        passk.update(valid_attempts=4, successes=successes, solved=successes > 0, complete=True)
    return {'task': f'/tasks/{task}', 'status': 'inconclusive' if outcome == 'inconclusive' else 'completed',
            'passk': passk, 'calibration': {
                'outcome': outcome, 'reason': 'resource problem at the maximum allocation (memory_mb)' if outcome == 'inconclusive' else None,
                'final_allocation': allocation, 'upstream': {'cpus': 1, 'memory_mb': 2048, 'storage_mb': 10240},
                'requirements': requirements, 'rounds': [{'allocation': allocation, 'decision': 'final', 'attempts': 4}],
                'final_attempts': [attempt] * 4, 'earlier_attempts': 0}}


BASE = {'cpus': 2, 'memory_mb': 8192, 'storage_mb': 20480}


def test_target_and_teacher_columns_and_merged_resources(tmp_path):
    path = tmp_path / 'results.parquet'
    results.update(path, [{'dataset': 'd', 'task_id': t, 'content_sha256': 'validated', 'nop': 'pass'} for t in 'abc'])
    six = {'stage': 6, 'items': [item('a', 'solved', 2, BASE, {'cpus': 2, 'memory_mb': 3072, 'storage_mb': 10240}),
                                 item('b', 'unsolved', 0, BASE, {'cpus': 2, 'memory_mb': 2048, 'storage_mb': 10240}),
                                 item('c', 'inconclusive', 0, {**BASE, 'memory_mb': 16384}, {'cpus': 2, 'memory_mb': 17408, 'storage_mb': 10240}),
                                 {'task': '/tasks/skipped', 'status': 'skipped', 'reason': 'not evaluated: failed nop'}]}
    results.export_report(path, six, {'a': 'h6', 'b': 'h6', 'c': 'h6', 'skipped': 'h6'}, dataset='d')
    rows = {r['task_id']: r for r in results.read(path)}
    assert [rows[t]['passk_target_outcome'] for t in 'abc'] == ['solved', 'unsolved', 'inconclusive']
    assert (rows['a']['passk_target_successes'], rows['a']['passk_target_valid_attempts']) == (2, 4)
    assert not any(k in rows['a'] for k in ('passk_target', 'passk_teacher'))  # no pass/fail status cell
    assert rows['a']['content_sha256'] == 'validated' and rows['a']['nop'] == 'pass'  # attached, not replaced
    assert rows['skipped'].get('passk_target_outcome') is None  # skipped tasks get no pass@k values
    attempts = json.loads(rows['a']['passk_target_attempt_results'])
    assert attempts['k'] == 4 and len(attempts['final_attempts']) == 4
    # The teacher's calibration supersedes the target model's requirement; both histories stay.
    teacher_alloc = {**BASE, 'cpus': 4}
    seven = {'stage': 7, 'items': [item('b', 'solved', 1, teacher_alloc, {'cpus': 4, 'memory_mb': 4096, 'storage_mb': 10240})]}
    results.export_report(path, seven, {'b': 'h7'}, dataset='d')
    b = {r['task_id']: r for r in results.read(path)}['b']
    resources = json.loads(b['resources'])
    assert b['passk_teacher_outcome'] == 'solved' and b['passk_target_outcome'] == 'unsolved'
    assert resources['requirements'] == {'cpus': 4, 'memory_mb': 4096, 'storage_mb': 10240} and resources['source'] == 'teacher successful traces'
    assert resources['target_allocation'] == BASE and resources['teacher_allocation'] == teacher_alloc
    # A later target-model export does not take the requirement back from the teacher.
    results.export_report(path, {'stage': 6, 'items': [item('b', 'unsolved', 0, BASE, {'cpus': 2, 'memory_mb': 2048, 'storage_mb': 10240})]},
                          {'b': 'h6'}, dataset='d')
    assert json.loads({r['task_id']: r for r in results.read(path)}['b']['resources'])['source'] == 'teacher successful traces'
    # Both models solved a: use the teacher recommendation, not a cross-model minimum.
    results.export_report(path, {'stage': 7, 'items': [item('a', 'solved', 3, teacher_alloc, {'cpus': 4, 'memory_mb': 2048, 'storage_mb': 10240})]},
                          {'a': 'h7'}, dataset='d')
    a = json.loads({r['task_id']: r for r in results.read(path)}['a']['resources'])
    assert a['requirements'] == {'cpus': 4, 'memory_mb': 2048, 'storage_mb': 10240} and a['source'] == 'teacher successful traces'


def test_teacher_routing_from_this_run_and_from_results(tmp_path):
    runs = {6: (None, {'stage': 6, 'items': [item('a', 'solved', 1, BASE, {}), item('b', 'unsolved', 0, {**BASE, 'cpus': 4}, {}),
                                 item('c', 'inconclusive', 0, BASE, {})]})}
    args = SimpleNamespace(out=tmp_path, judge_results=[])
    judge_selection.teacher_routing(args, runs)
    assert args._teacher_routing['routing'] == {'b': {**BASE, 'cpus': 4}}
    assert args._teacher_routing['outcomes'] == {'a': 'solved', 'b': 'unsolved', 'c': 'inconclusive'}
    # Every task with a valid target batch keeps its calibrated allocation for --teacher-tasks all.
    assert args._teacher_routing['allocations'] == {'a': BASE, 'b': {**BASE, 'cpus': 4}}
    # Separate submission: read stage 6 from results.parquet.
    results.export_report(tmp_path / 'results.parquet', runs[6][1], {'a': 'h', 'b': 'h', 'c': 'h'}, dataset='d')
    later = SimpleNamespace(out=tmp_path, judge_results=[])
    judge_selection.teacher_routing(later, {})
    assert later._teacher_routing['routing'] == {'b': {**BASE, 'cpus': 4}}


def test_stage_selection_skips_failed_checks_and_non_zero_tasks(tmp_path):
    sources = [Path(f'/tasks/{n}') for n in ('fails-nop', 'solved', 'unsolved', 'unknown')]
    args = SimpleNamespace(_judge_failed={'results': [], 'stages_this_run': [],
                                          'failed': {'fails-nop': [('h', ['nop'])]}},
                           _teacher_routing={'routing': {'unsolved': BASE}, 'outcomes': {'solved': 'solved'}},
                           teacher_tasks='unsolved')
    report = {'items': []}
    assert [s.name for s in select_passk_sources(6, sources, {}, report, args)] == ['solved', 'unsolved', 'unknown']
    report = {'items': []}
    assert [s.name for s in select_passk_sources(7, sources, {}, report, args)] == ['unsolved']
    reasons = {Path(i['task']).name: i['reason'] for i in report['items']}
    assert reasons['fails-nop'].startswith('not evaluated: failed nop')
    assert reasons['solved'] == 'not evaluated by the teacher: target model solved'
    assert reasons['unknown'] == 'not evaluated by the teacher: target model no target-model result'


def test_teacher_runs_every_task_by_default(tmp_path):
    sources = [Path(f'/tasks/{n}') for n in ('fails-nop', 'solved', 'unsolved', 'unknown')]
    args = SimpleNamespace(_judge_failed={'results': [], 'stages_this_run': [],
                                          'failed': {'fails-nop': [('h', ['nop'])]}},
                           _teacher_routing={'routing': {'unsolved': BASE}, 'outcomes': {'solved': 'solved'},
                                             'allocations': {'solved': BASE, 'unsolved': BASE}})
    report = {'items': []}
    assert [s.name for s in select_passk_sources(7, sources, {}, report, args)] == ['solved', 'unsolved', 'unknown']
    assert report['teacher_routing']['rule'].startswith('all tasks')
    assert runner.parser().parse_args([]).teacher_tasks == 'all'


def test_each_stage_serves_its_own_model():
    def args(**kw):
        values = dict(model=None, api_base=None, serve_model=None, contract=None,
                      target_model='Qwen/Qwen3.5-35B-A3B', teacher_model='zai-org/GLM-5.3-Flash')
        return SimpleNamespace(**(values | kw))
    six, seven = args(), args()
    passk_stage.resolve_models(six, [0, 1, 2, 3, 5, 6])
    passk_stage.resolve_models(seven, [7])
    assert (six.serve_model, seven.serve_model) == ('qwen35-35b-a3b', 'glm-5.3-flash')
    with pytest.raises(ValueError, match='separate submissions'):
        passk_stage.resolve_models(args(), [6, 7])
    explicit = args(model='openai/custom')
    passk_stage.resolve_models(explicit, [6, 7])  # an explicit endpoint serves both
    assert explicit.serve_model is None
