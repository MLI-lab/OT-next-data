"""The run-level retry rule: retry missing results, never wrong rewards."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from validation.publishing.publish import decide, judge, reproducibility_text
from validation.stages.outcome_retries import retry_round

SHA = 'a' * 64
CRASH = ['trial-1: exception: {"exception_type": "EnvironmentStartTimeoutError"}']
WRONG = ['trial-1: expected reward 1, got 0.0']


def trial(task, status='passed', rewards=(1.0,), findings=()):
    return {'task': f'/tasks/{task}', 'status': status, 'rewards': list(rewards), 'findings': list(findings)}


def build(task, status='passed', reason=None):
    result = {'task': f'/tasks/{task}', 'status': status, 'environments': [{'status': status}]}
    if reason:
        result['reason'] = reason
    return result


def blocked(task):
    return {'task': f'/tasks/{task}', 'status': 'skipped', 'blocked_by_stage': 1, 'reason': 'build failed'}


class Scripted:
    """Per stage, scripted fresh items for successive rerun rounds; records what was rerun."""
    def __init__(self, script):
        self.script, self.calls, self.rounds = script, [], {}

    def __call__(self, number):
        def batch(sources, out):
            self.calls.append((number, [s.name for s in sources], out.name))
            round_ = self.rounds.get(number, 0)
            self.rounds[number] = round_ + 1
            return [self.script[number][s.name][round_] for s in sources]
        return batch


def runs(tmp_path, **stage_items):
    result = {}
    for key, items in stage_items.items():
        number = int(key[1:])
        path = tmp_path / f'stage_{number}' / 'summary.json'
        path.parent.mkdir(parents=True)
        result[number] = (path, {'stage_layout': 'environment-first-v2', 'stage': number, 'items': items, 'has_findings': False})
    return result


def saved(path, report):
    path.write_text(json.dumps(report))


def contract_for(*tasks):
    return {'tasks': [{'task_id': t, 'sha256': SHA} for t in tasks]}


def args_for(retries=3):
    return SimpleNamespace(outcome_retries=retries, dry_run=False)


def test_only_not_run_tasks_are_rerun_and_wrong_rewards_stay_results(tmp_path):
    stage = runs(tmp_path, s2=[trial('ok'), trial('crash', 'failed', [], CRASH), trial('wrong', 'failed', [0.0], WRONG)])
    batches = Scripted({2: {'crash': [trial('crash', 'failed', [], CRASH), trial('crash')]}})
    summary = retry_round(stage, args_for(), contract_for('ok', 'crash', 'wrong'), batches, saved)
    assert batches.calls == [(2, ['crash'], 'retry-1'), (2, ['crash'], 'retry-2')]
    items = {Path(i['task']).name: i for i in stage[2][1]['items']}
    assert items['crash']['status'] == 'passed' and len(items['crash']['outcome_attempts']) == 2
    assert items['crash']['outcome_attempts'][0]['findings'] == CRASH
    assert 'outcome_attempts' not in items['wrong'] and judge(3, items['wrong'])[0] == 'archive'
    assert summary[2]['retried_not_run'] == 1 and summary[2]['recovered_not_run'] == 1
    assert stage[2][1]['outcome_retries'] == summary[2]
    assert json.loads(stage[2][0].read_text())['outcome_retries']['history']['crash']['outcomes'] == ['not_run', 'not_run', 'passed']


def test_task_without_a_result_after_the_last_attempt_is_an_infrastructure_error(tmp_path):
    stage = runs(tmp_path, s3=[trial('crash', 'failed', [], CRASH)])
    batches = Scripted({3: {'crash': [trial('crash', 'failed', [], CRASH)] * 3}})
    summary = retry_round(stage, args_for(), contract_for('crash'), batches, saved)
    item = stage[3][1]['items'][0]
    assert len(batches.calls) == 3 and len(item['outcome_attempts']) == 3
    assert 'not_robust' not in item and item['infrastructure_error'] == {'observed': ['not_run'] * 4}
    outcome, reason = judge(2, item)
    assert outcome == 'not_run' and reason.startswith('infrastructure error: no result in 4 attempts; no verdict on the task')
    assert reason.endswith('EnvironmentStartTimeoutError"}')      # the observed cause follows
    assert summary[3]['unresolved_not_run'] == ['crash'] and stage[3][1]['has_findings'] is True


def test_task_without_a_reference_solution_is_not_retried_or_held_against_it():
    item = {'task': '/tasks/open', 'status': 'not_applicable', 'reason': 'no solution/solve.sh for oracle validation'}
    assert judge(2, item) == ('not_applicable', item['reason'])
    build = {'stage': 1, 'complete': True, 'items': [{'task': '/tasks/open', 'status': 'passed'}]}
    reference = {'stage': 2, 'complete': True, 'items': [item]}
    decision = decide({1: build, 2: reference}, {'open'})['open']
    assert decision['passed'] == [1] and decision['archive'] is None and 2 not in decision['not_run']


def test_recovered_build_lets_blocked_trials_run_in_the_same_round(tmp_path):
    stage = runs(tmp_path, s1=[build('img', 'error', 'container start timed out after 600 s'), build('fine')],
                 s2=[blocked('img'), trial('fine')], s3=[blocked('img'), trial('fine', rewards=[0.0])])
    batches = Scripted({1: {'img': [build('img')]}, 2: {'img': [trial('img')]}, 3: {'img': [trial('img', rewards=[0.0])]}})
    summary = retry_round(stage, args_for(), contract_for('img', 'fine'), batches, saved)
    assert batches.calls == [(1, ['img'], 'retry-1'), (2, ['img'], 'retry-1'), (3, ['img'], 'retry-1')]
    assert all(judge(n, stage[n][1]['items'][0])[0] == 'passed' for n in (1, 2, 3))
    assert [summary[n]['recovered_not_run'] for n in (1, 2, 3)] == [1, 1, 1]


def test_trials_wait_while_the_build_keeps_failing(tmp_path):
    stage = runs(tmp_path, s1=[build('img', 'error', 'container start timed out after 600 s')], s2=[blocked('img')])
    batches = Scripted({1: {'img': [build('img', 'error', 'container start timed out after 600 s')] * 3}})
    retry_round(stage, args_for(), contract_for('img'), batches, saved)
    assert [c[0] for c in batches.calls] == [1, 1, 1]
    assert judge(1, stage[1][1]['items'][0])[0] == 'not_run' and judge(3, stage[2][1]['items'][0])[0] == 'not_run'
    assert 'no result in 4 attempts' in stage[1][1]['items'][0]['findings'][-1]
    assert 'no result in 1 attempts' in stage[2][1]['items'][0]['findings'][-1]


def test_retries_are_skipped_for_dry_runs_zero_and_ungated_stages(tmp_path):
    stage = runs(tmp_path, s2=[trial('crash', 'failed', [], CRASH)])
    assert retry_round(stage, args_for(retries=0), contract_for('crash'), Scripted({}), saved) is None
    preview = args_for()
    preview.dry_run = True
    assert retry_round(stage, preview, contract_for('crash'), Scripted({}), saved) is None
    assert retry_round(runs(tmp_path / 'other', s4=[{'task': 'x', 'status': 'error'}]), args_for(),
                       contract_for('x'), Scripted({}), saved) is None
    assert stage[2][1]['items'][0]['findings'] == CRASH


def test_pipeline_runs_the_round_once_after_all_stages(tmp_path, monkeypatch):
    from validation import run
    order = []

    def fake_stage(number, args):
        order.append(('stage', number))
        path = tmp_path / f'stage-{number}.json'
        path.write_text('{}')
        return path, {'has_findings': False, 'items': []}

    def fake_round(stage_runs, args, contract, batch_for, save):
        order.append(('retry', sorted(stage_runs)))
        for n in stage_runs:
            stage_runs[n][1]['has_findings'] = True
        return {n: {'stage_layout': 'environment-first-v2', 'stage': n, 'not_robust': [], 'history': {'x': {}}} for n in stage_runs}
    monkeypatch.setattr(run, 'run_stage', fake_stage)
    monkeypatch.setattr('validation.stages.outcome_retries.retry_round', fake_round)
    monkeypatch.setattr('validation.stages.runner.checkout', lambda name: tmp_path)
    options = SimpleNamespace(out=tmp_path, dry_run=False, trials=None, contract=None, outcome_retries=3,
                              prebuild_only=False,
                              fix_instruction_paths=False, fix_pip_pins=False, tasks=tmp_path)
    monkeypatch.setattr('validation.stages.normalize_paths.prepare', lambda a, n: None)
    monkeypatch.setattr('validation.rules.static.pip_pinning.repair.prepare', lambda a, n: None)
    assert run.run_selected(options, [1, 2, 3]) == 1
    assert order == [('stage', 1), ('stage', 2), ('stage', 3), ('retry', [1, 2, 3])]
    pipeline = json.loads(next(tmp_path.glob('pipeline-*.json')).read_text())
    assert set(pipeline['outcome_retries']) == {'1', '2', '3'}
    assert 'history' not in pipeline['outcome_retries']['3']
    assert all(entry['status'] == 'findings' for entry in pipeline['stages'])


def test_reviewed_cause_replaces_the_generic_not_robust_text():
    item = {'task': '/tasks/gone', 'status': 'failed', 'findings': ['not robust: ...'],
            'not_robust': {'expected': 'passed', 'observed': ['archive'] * 4},
            'archive_cause': 'the source repository is no longer available'}
    assert judge(1, item) == ('archive', 'the source repository is no longer available (same result in all 4 attempts)')


def test_description_reports_infrastructure_retries():
    text = '\n'.join(reproducibility_text({'2': {
        'retried_not_run': 3, 'recovered_not_run': 2, 'unresolved_not_run': ['a']}}))
    assert '| 2 | 3 | 2 | 1 |' in text
    assert 'are not archived' in text
    assert reproducibility_text(None) == []


def test_retired_option_is_not_accepted():
    from validation.stages.runner import parser
    with pytest.raises(SystemExit):
        parser().parse_args(['tasks', '--expected-outcomes', 'audit.json'])


def test_old_audit_contract_is_not_silently_reinterpreted():
    from validation.contract import verify_source
    for contract in ({'expected_outcomes_sha256': 'old-audit-hash'},
                     {'arguments': {'expected_outcomes': '/old/audit.json'}}):
        with pytest.raises(ValueError, match='retired expected-outcomes'):
            verify_source(SimpleNamespace(), contract)
