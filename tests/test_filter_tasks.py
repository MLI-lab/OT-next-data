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
    assert '| Qwen | 1 | 1 | 1 |' in text
    assert 'not a measured result' in text
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
    assert counts == {0: 1, 2: 1, 'incomplete': 1, 'not_run': 1, 'success_elsewhere': 1}


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
