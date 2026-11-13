import io
import json
import tarfile

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from validation.publishing import filter_tasks as ft
from validation.publishing.readme import render_compact, complete_batch_distribution
from validation.run import uncell


def row(name='task', failures=()):
    result = {'task_id': name, 'dataset': 'CalibForge', 'content_sha256': 'placeholder',
              **{k: {'status': 'pass'} for k in ft.REQUIRED_CHECKS}}
    for k in ft.JUDGE_CRITERIA:
        result[k] = {'verdicts': {ft.JUDGES['qwen']: ['fail' if k in failures else 'pass'],
                                 ft.JUDGES['glm']: ['pass']}, 'details': {}}
    return result


def test_only_additions_do_not_include_both_or_other_failures():
    inc, short = 'VERIFIER-ACCEPTS-INCOMPLETE', 'VERIFIER-ACCEPTS-SHORTCUTS'
    rows = [row('clean'), row('inc', [inc]), row('short', [short]),
            row('both', [inc, short]), row('inc-and-leak', [inc, 'ANSWER-LEAK'])]
    assert set(ft.select(rows, 'qwen', [inc])[0]) == {'clean', 'inc'}
    assert set(ft.select(rows, 'qwen', [short])[0]) == {'clean', 'short'}
    assert set(ft.select(rows, 'glm')[0]) == {r['task_id'] for r in rows}


def test_warnings_stay_but_missing_checks_and_reviews_do_not():
    warning, incomplete, missing, unknown = (row(x) for x in ['warning', 'incomplete', 'missing', 'unknown'])
    warning['repository_history']['status'] = 'warning'
    incomplete['build']['status'] = 'execution_error'
    missing['ANSWER-LEAK']['verdicts'] = {}
    unknown['ANSWER-LEAK']['verdicts'][ft.JUDGES['qwen']] = ['pass', 'no_verdict']
    assert set(ft.select([warning, incomplete, missing, unknown], 'qwen')[0]) == {'warning'}


def test_repeated_review_fail_is_not_majority_voted_away():
    r = row()
    r['ANSWER-LEAK']['verdicts'][ft.JUDGES['qwen']] = ['pass', 'pass', 'fail']
    assert not ft.select([r], 'qwen')[0]


def test_duplicate_results_rejected_even_when_one_fails():
    bad = row()
    bad['nop']['status'] = 'fail'
    with pytest.raises(ValueError, match='Duplicate'):
        ft.select([bad, row()])


def inputs(tmp_path, mismatch=False):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w:gz') as archive:
        data = b'Write a program.'
        member = tarfile.TarInfo('instruction.md')
        member.size = len(data)
        archive.addfile(member, io.BytesIO(data))
    binary = stream.getvalue()
    digest = ft.payload_hash(binary)
    r = row()
    r['content_sha256'] = 'other-version' if mismatch else digest
    manifest = {'tasks': {'task': {'content_sha256': digest, 'environments': [{'bundle': 'used'}]},
                          'excluded': {'content_sha256': 'x', 'environments': [{'bundle': 'unused'}]}},
                'bundles': {'used': {}, 'unused': {}}, 'missing': []}
    table = pa.Table.from_pylist([{'path': 'task', 'task_binary': binary, 'content_sha256': digest}])
    table = table.replace_schema_metadata({b'ot.images.v1': json.dumps(manifest).encode()})
    pq.write_table(table, tmp_path / 'tasks.parquet')
    # Empty structs cannot be written by Parquet; omit unused explanation maps.
    for c in ft.JUDGE_CRITERIA:
        del r[c]['details']
    pq.write_table(pa.Table.from_pylist([r]), tmp_path / 'results.parquet')
    return tmp_path / 'tasks.parquet', tmp_path / 'results.parquet'


def test_export_checks_hashes_and_preserves_selected_cache_metadata(tmp_path):
    tasks, results = inputs(tmp_path)
    summary = ft.export(tasks, results, tmp_path / 'out', 'qwen', experiment2=True)
    assert [v['tasks'] for v in summary['groups'].values()] == [1, 1, 1]
    out = pq.read_table(tmp_path / 'out/clean/tasks.parquet')
    manifest = json.loads(out.schema.metadata[b'ot.images.v1'])
    assert set(manifest['tasks']) == {'task'}
    assert set(manifest['bundles']) == {'used'}
    assert out.to_pylist() == pq.read_table(tasks).to_pylist()
    with pytest.raises(FileExistsError):
        ft.export(tasks, results, tmp_path / 'out', 'qwen', experiment2=True)


def test_stale_results_never_become_a_finished_export(tmp_path):
    tasks, results = inputs(tmp_path, mismatch=True)
    with pytest.raises(ValueError, match='another task version'):
        ft.export(tasks, results, tmp_path / 'out', 'qwen')
    assert not (tmp_path / 'out/tasks.parquet').exists()


def test_compact_card_counts_are_computed_and_hypothesis_is_not_a_result():
    r = row()
    text, _ = render_compact([r], {'payload_tasks': 1, 'experiment2': True}, 'CalibForge')
    assert '# Qwen: clean 1, clean + incomplete-only 1, clean + shortcuts-only 1' in text
    assert '**Hypothesis:**' in text
    assert 'Previous release' not in text
    assert 'Current coverage' not in text
    assert 'AI detection' not in text


def test_attempt_distribution_uses_selected_batch_not_earlier_successes():
    def attempt(state, wins, attempts):
        return {'passk_target': {'status': state, 'details': json.dumps({
            'k': 4, 'selected_batch': {'successes': wins, 'valid_attempts': attempts},
            'original_run': {'successes': 4, 'valid_attempts': 4}})}}
    counts = complete_batch_distribution([
        attempt('pass', 0, 4), attempt('pass', 2, 4), attempt('pass', 1, 3),
        {'passk_target': {'status': 'not_requested', 'details': 'Changed task needs rerun'}}
    ], 'passk_target')
    assert counts == {0: 1, 2: 1, 'incomplete': 1, 'not_run': 1, 'excluded': 1, 'success_elsewhere': 1}


def test_not_run_distribution_separates_excluded_tasks_from_eligible_retries():
    eligible, excluded = row('eligible'), row('excluded')
    excluded['build']['status'] = 'fail'
    for task in (eligible, excluded):
        task['passk_target'] = {'status': 'not_run'}
    counts = complete_batch_distribution([eligible, excluded], 'passk_target')
    assert counts == {'not_run': 2, 'excluded': 1, 'awaiting': 1}


def test_rule_summary_separates_warnings_and_judge_only_groups():
    clean, warning, fail, incomplete = [row(n) for n in ['clean', 'warning', 'fail', 'incomplete']]
    warning['repository_history']['status'] = 'warning'
    fail['nop']['status'] = 'fail'
    incomplete['build']['status'] = 'execution_error'
    warning['ANSWER-LEAK']['verdicts'][ft.JUDGES['qwen']] = ['fail']
    text, kept = render_compact([uncell(r) for r in [clean, warning, fail, incomplete]], {}, 'CalibForge')
    assert len(kept) == 2
    assert '| All applicable rules pass, no warnings | 1 |' in text
    assert '| Passes with warnings, no failures or incomplete checks | 1 |' in text
    assert '| Qwen3.8-27B | 1 | 1 | 0 |' in text
    assert text.index('## Rule-based checks') < text.index('## LLM-as-a-judge results')


def test_status_maps_keep_legacy_selection_semantics():
    import copy
    legacy = [row('clean'), row('incomplete', ['VERIFIER-ACCEPTS-INCOMPLETE'])]
    legacy[0]['ANSWER-LEAK']['verdicts'][ft.JUDGES['qwen']] = ['pass', 'no_verdict']
    current = copy.deepcopy(legacy)
    for r in current:
        for criterion in ft.JUDGE_CRITERIA:
            cell = r[criterion]
            cell['status'] = list(cell.pop('verdicts').items())
    for judge in ('qwen', 'glm'):
        for allowed in ((), ('VERIFIER-ACCEPTS-INCOMPLETE',)):
            selected, reasons = ft.select(current, judge, allowed)
            previous, previous_reasons = ft.select(legacy, judge, allowed)
            assert set(selected) == set(previous)
            assert reasons == previous_reasons


def test_per_judge_json_preserves_all_reviews():
    from validation.judge_cells import decode
    legacy = [row('clean'), row('failure', ['VERIFIER-ACCEPTS-INCOMPLETE'])]
    legacy[0]['ANSWER-LEAK']['verdicts'][ft.JUDGES['qwen']] = ['fail', 'no_verdict']
    current = [{k: json.dumps(decode(v)) if k in ft.JUDGE_CRITERIA else v
                for k, v in r.items()} for r in legacy]
    for judge in ('qwen', 'glm'):
        for allowed in ((), ('VERIFIER-ACCEPTS-INCOMPLETE',)):
            selected, reasons = ft.select(current, judge, allowed)
            previous, previous_reasons = ft.select(legacy, judge, allowed)
            assert set(selected) == set(previous)
            assert reasons == previous_reasons
    assert json.loads(current[0]['ANSWER-LEAK'])[ft.JUDGES['qwen']]['status'] == 'fail'


def passk_cell(wins, state=None, attempts=4):
    return {'status': state or ('pass' if wins else 'fail'), 'details': json.dumps({
        'k': 4, 'selected_batch': {'successes': wins, 'valid_attempts': attempts},
        'original_run': {'successes': 4, 'valid_attempts': 4}})}


def test_passk_filter_composes_with_rules_and_independent_judge():
    rows = [row(str(n)) for n in range(5)]
    for n, r in enumerate(rows):
        r['passk_target'] = passk_cell(n)
        r['passk_teacher'] = passk_cell(4 - n)
    assert set(ft.select(rows, passk_model='qwen', successes=[1, 2])[0]) == {'1', '2'}
    rows[0]['nop']['status'] = 'warning'
    assert set(ft.select(rows, 'qwen', passk_model='glm', successes=[4])[0]) == {'0'}
    rows[1]['nop']['status'] = 'fail'
    rows[2]['ANSWER-LEAK']['verdicts'][ft.JUDGES['qwen']] = ['fail']
    assert not ft.select(rows, 'qwen', passk_model='qwen', successes=[1, 2])[0]
    assert set(ft.select(rows, 'qwen', ['ANSWER-LEAK'], passk_model='qwen', successes=[1, 2])[0]) == {'2'}


def test_passk_zero_keeps_resource_failures_but_not_unfinished_or_previous_successes():
    rows = [row(n) for n in ('zero', 'unfinished', 'missing', 'short', 'malformed')]
    rows[0]['passk_target'] = passk_cell(0)
    rows[1]['passk_target'] = passk_cell(0, 'inconclusive')
    rows[3]['passk_target'] = passk_cell(0, attempts=3)
    rows[4]['passk_target'] = {'status': 'fail', 'details': 'No usable result'}
    selected, reasons = ft.select(rows, passk_model='qwen', successes=[0])
    assert set(selected) == {'zero'}
    assert reasons == {'passk_incomplete': 4}
    assert len(ft.select(rows)[0]) == 5


@pytest.mark.parametrize('model,successes', [(None, [0]), ('qwen', None), ('qwen', []), ('other', [1]), ('glm', [5])])
def test_passk_requires_model_and_valid_counts(model, successes):
    with pytest.raises(ValueError):
        ft.select([], passk_model=model, successes=successes)


def test_export_records_passk_filter_and_preserves_results(tmp_path):
    tasks, results = inputs(tmp_path)
    rows = pq.read_table(results).to_pylist()
    rows[0]['passk_target'] = passk_cell(2)
    pq.write_table(pa.Table.from_pylist(rows), results)
    summary = ft.export(tasks, results, tmp_path / 'out', 'qwen', experiment2=True,
                        passk_model='qwen', successes=[2, 1, 2])
    assert summary['passk_filter'] == {'model': 'qwen', 'column': 'passk_target', 'k': 4, 'successes': [1, 2]}
    assert all(g['tasks'] == 1 for g in summary['groups'].values())
    assert pq.read_table(tmp_path / 'out/clean/results.parquet').to_pylist() == rows
