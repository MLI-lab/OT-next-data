"""The run-level retry rule: not-run tasks without expectations, mismatches with them."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from validation.publishing.publish import judge, reproducibility_text
from validation.stages.outcome_retries import FORMAT, load_expectations, retry_round

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
    return {'task': f'/tasks/{task}', 'status': 'skipped', 'blocked_by_stage': 3, 'reason': 'build failed'}


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
        result[number] = (path, {'stage': number, 'items': items, 'has_findings': False})
    return result


def saved(path, report):
    path.write_text(json.dumps(report))


def contract_for(*tasks):
    return {'tasks': [{'task_id': t, 'sha256': SHA} for t in tasks]}


def args_for(expected=None, retries=3):
    return SimpleNamespace(outcome_retries=retries, expected_outcomes=expected, dry_run=False)


def expectations(tmp_path, **tasks):
    path = tmp_path / 'expected.json'
    path.write_text(json.dumps({'format': FORMAT, 'note': 'audit note', 'tasks': {
        name: {'sha256': sha, 'stages_passed': stages} for name, (sha, stages) in tasks.items()}}))
    return path


def test_only_not_run_tasks_are_rerun_and_wrong_rewards_stay_results(tmp_path):
    stage = runs(tmp_path, s4=[trial('ok'), trial('crash', 'failed', [], CRASH), trial('wrong', 'failed', [0.0], WRONG)])
    batches = Scripted({4: {'crash': [trial('crash', 'failed', [], CRASH), trial('crash')]}})
    summary = retry_round(stage, args_for(), contract_for('ok', 'crash', 'wrong'), batches, saved)
    assert batches.calls == [(4, ['crash'], 'retry-1'), (4, ['crash'], 'retry-2')]
    items = {Path(i['task']).name: i for i in stage[4][1]['items']}
    assert items['crash']['status'] == 'passed' and len(items['crash']['outcome_attempts']) == 2
    assert items['crash']['outcome_attempts'][0]['findings'] == CRASH
    assert 'outcome_attempts' not in items['wrong'] and judge(4, items['wrong'])[0] == 'archive'
    assert summary[4]['retried_not_run'] == 1 and summary[4]['recovered_not_run'] == 1
    assert summary[4]['tasks_with_expectation'] == 0 and stage[4][1]['outcome_retries'] == summary[4]
    assert json.loads(stage[4][0].read_text())['outcome_retries']['history']['crash']['outcomes'] == ['not_run', 'not_run', 'passed']


def test_task_without_a_result_after_the_last_attempt_is_archived_as_not_robust(tmp_path):
    stage = runs(tmp_path, s5=[trial('crash', 'failed', [], CRASH)])
    batches = Scripted({5: {'crash': [trial('crash', 'failed', [], CRASH)] * 3}})
    summary = retry_round(stage, args_for(), contract_for('crash'), batches, saved)
    item = stage[5][1]['items'][0]
    assert len(batches.calls) == 3 and len(item['outcome_attempts']) == 3
    assert item['not_robust'] == {'expected': 'result', 'observed': ['not_run'] * 4}
    outcome, reason = judge(5, item)
    assert outcome == 'archive' and reason == ('not robust: no result in 4 attempts under this infrastructure; '
                                               'observed not_run, not_run, not_run, not_run')
    assert summary[5]['unresolved_not_run'] == ['crash'] and stage[5][1]['has_findings'] is True


def test_recovered_build_lets_blocked_trials_run_in_the_same_round(tmp_path):
    stage = runs(tmp_path, s3=[build('img', 'error', 'container start timed out after 600 s'), build('fine')],
                 s4=[blocked('img'), trial('fine')], s5=[blocked('img'), trial('fine', rewards=[0.0])])
    batches = Scripted({3: {'img': [build('img')]}, 4: {'img': [trial('img')]}, 5: {'img': [trial('img', rewards=[0.0])]}})
    summary = retry_round(stage, args_for(), contract_for('img', 'fine'), batches, saved)
    assert batches.calls == [(3, ['img'], 'retry-1'), (4, ['img'], 'retry-1'), (5, ['img'], 'retry-1')]
    assert all(judge(n, stage[n][1]['items'][0])[0] == 'passed' for n in (3, 4, 5))
    assert [summary[n]['recovered_not_run'] for n in (3, 4, 5)] == [1, 1, 1]


def test_trials_wait_while_the_build_keeps_failing(tmp_path):
    stage = runs(tmp_path, s3=[build('img', 'error', 'container start timed out after 600 s')], s4=[blocked('img')])
    batches = Scripted({3: {'img': [build('img', 'error', 'container start timed out after 600 s')] * 3}})
    retry_round(stage, args_for(), contract_for('img'), batches, saved)
    assert [c[0] for c in batches.calls] == [3, 3, 3]
    assert judge(3, stage[3][1]['items'][0])[0] == 'archive' and judge(4, stage[4][1]['items'][0])[0] == 'archive'
    assert 'no result in 4 attempts' in stage[3][1]['items'][0]['findings'][-1]
    assert 'no result in 1 attempts' in stage[4][1]['items'][0]['findings'][-1]


def test_expectation_mismatch_is_rerun_whatever_the_finding_text(tmp_path):
    expected = expectations(tmp_path, flaky=(SHA, [1, 3, 4, 5]), steady=(SHA, [1, 3, 4, 5]))
    stage = runs(tmp_path, s4=[trial('steady'), trial('flaky', 'failed', [0.0], WRONG)])
    batches = Scripted({4: {'flaky': [trial('flaky', 'failed', [0.0], WRONG), trial('flaky')]}})
    summary = retry_round(stage, args_for(expected), contract_for('steady', 'flaky'), batches, saved)
    assert [c[1] for c in batches.calls] == [['flaky'], ['flaky']]
    flaky = stage[4][1]['items'][1]
    assert flaky['status'] == 'passed' and len(flaky['outcome_attempts']) == 2
    assert summary[4]['mode'] == 'expected' and summary[4]['tasks_with_expectation'] == 2
    assert summary[4]['matched_first_attempt'] == 1 and summary[4]['matched_after_retries'] == {'2': 1}
    assert summary[4]['not_robust'] == [] and summary[4]['expected_outcomes_note'] == 'audit note'


def test_never_matching_task_is_archived_as_not_robust(tmp_path):
    expected = expectations(tmp_path, broken=(SHA, [1, 3, 4, 5]))
    stage = runs(tmp_path, s3=[build('broken', 'error', 'ImageBuildError: index unreachable')])
    assert judge(3, stage[3][1]['items'][0])[0] == 'archive'     # a result without an expectation
    batches = Scripted({3: {'broken': [build('broken', 'error', 'ImageBuildError: again')] * 3}})
    summary = retry_round(stage, args_for(expected), contract_for('broken'), batches, saved)
    item = stage[3][1]['items'][0]
    assert len(batches.calls) == 3
    assert item['not_robust'] == {'expected': 'passed', 'observed': ['archive'] * 4}
    outcome, reason = judge(3, item)
    assert outcome == 'archive' and reason == 'not robust: the earlier audit passed this stage; observed archive, archive, archive, archive over 4 attempts'
    assert summary[3]['not_robust'] == ['broken'] and summary[3]['matched_first_attempt'] == 0


def test_changed_or_unknown_content_falls_back_to_the_classifier(tmp_path):
    expected = expectations(tmp_path, changed=('b' * 64, [1, 3, 4, 5]))
    stage = runs(tmp_path, s4=[trial('changed', 'failed', [0.0], WRONG), trial('unknown', 'failed', [0.0], WRONG)])
    batches = Scripted({})
    summary = retry_round(stage, args_for(expected), contract_for('changed', 'unknown'), batches, saved)
    assert batches.calls == [] and summary[4]['tasks_with_expectation'] == 0
    assert all(h['mode'] == 'classifier' for h in summary[4]['history'].values())


def test_expected_not_passed_is_a_match_when_the_stage_fails_again(tmp_path):
    expected = expectations(tmp_path, dropped=(SHA, [1, 3]))
    stage = runs(tmp_path, s4=[trial('dropped', 'failed', [0.0], WRONG)])
    summary = retry_round(stage, args_for(expected), contract_for('dropped'), Scripted({}), saved)
    assert summary[4]['matched_first_attempt'] == 1 and 'not_robust' not in stage[4][1]['items'][0]


def test_retries_are_skipped_for_dry_runs_zero_and_ungated_stages(tmp_path):
    stage = runs(tmp_path, s4=[trial('crash', 'failed', [], CRASH)])
    assert retry_round(stage, args_for(retries=0), contract_for('crash'), Scripted({}), saved) is None
    preview = args_for()
    preview.dry_run = True
    assert retry_round(stage, preview, contract_for('crash'), Scripted({}), saved) is None
    assert retry_round(runs(tmp_path / 'other', s1=[{'task': 'x', 'status': 'error'}]), args_for(),
                       contract_for('x'), Scripted({}), saved) is None
    assert stage[4][1]['items'][0]['findings'] == CRASH


def test_expected_outcomes_file_is_validated():
    with pytest.raises(ValueError):
        load_expectations(Path(__file__))


def test_description_reports_reproducibility():
    retries = {'4': {
        'mode': 'expected', 'tasks_with_expectation': 10, 'matched_first_attempt': 7,
        'matched_after_retries': {'1': 1, '3': 1}, 'not_robust': ['t-9'],
        'retried_not_run': 0, 'recovered_not_run': 0, 'unresolved_not_run': []}}
    text = '\n'.join(reproducibility_text(retries))
    assert '### Reproducibility and retries' in text
    assert '| 4 | expected | 10 | 7 | 1 / 0 / 1 | 1 | 0 | 0 | 0 |' in text
    assert 'Archived as not robust: stage 4: t-9' in text
    retries['4']['expected_outcomes_note'] = 'manual audit 2026-10-10'
    assert 'Expected outcomes: manual audit 2026-10-10' in '\n'.join(reproducibility_text(retries))
    assert reproducibility_text(None) == [] and reproducibility_text({}) == []


def test_builder_writes_the_format_from_a_source(tmp_path, monkeypatch):
    from validation import expected_outcomes as builder
    monkeypatch.setattr(builder, 'inventory', lambda source: ([{'task_id': 'a', 'sha256': SHA}], []))
    out = tmp_path / 'expected.json'
    builder.main(['audited', str(tmp_path), '--stages', '1,3,4,5', '--note', 'test', '--out', str(out)])
    assert load_expectations(out) == {'a': {'sha256': SHA, 'stages_passed': [1, 3, 4, 5]}}


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
        return {n: {'stage': n, 'not_robust': [], 'history': {'x': {}}} for n in stage_runs}
    monkeypatch.setattr(run, 'run_stage', fake_stage)
    monkeypatch.setattr('validation.stages.outcome_retries.retry_round', fake_round)
    monkeypatch.setattr('validation.stages.runner.checkout', lambda name: tmp_path)
    options = SimpleNamespace(out=tmp_path, dry_run=False, trials=None, contract=None, outcome_retries=3,
                              expected_outcomes=None, reuse_validation_containers=False, prebuild_only=False,
                              fix_instruction_paths=False, fix_pip_pins=False, tasks=tmp_path)
    monkeypatch.setattr('validation.stages.normalize_paths.prepare', lambda a, n: None)
    monkeypatch.setattr('data.utils.resolve_pip_pins.prepare', lambda a, n: None)
    assert run.run_selected(options, [3, 4, 5]) == 1
    assert order == [('stage', 3), ('stage', 4), ('stage', 5), ('retry', [3, 4, 5])]
    pipeline = json.loads(next(tmp_path.glob('pipeline-*.json')).read_text())
    assert set(pipeline['outcome_retries']) == {'3', '4', '5'}
    assert 'history' not in pipeline['outcome_retries']['3']
    assert all(entry['status'] == 'findings' for entry in pipeline['stages'])
