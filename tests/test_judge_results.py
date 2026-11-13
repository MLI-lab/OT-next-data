"""results.parquet format 5: one column per judge rubric criterion, every review per judge."""
import json
import pyarrow.parquet as pq

from validation import run as results


def review(trial, **checks):
    return {'trial': trial, 'checks': {name: {'outcome': outcome, 'explanation': f'{name} {outcome} ({trial})'}
                                       for name, outcome in checks.items()}}


def stage5(model, items, attempts=1):
    return {'stage': 5, 'review_model': model, 'review_attempts': attempts, 'items': items}


def cells(path, task='t'):
    row = next(r for r in results.read(path) if r['task_id'] == task)
    return row, {k: v for k, v in row.items() if results.is_judge_cell(v)}


def test_each_criterion_lists_every_review_and_missing_verdicts(tmp_path):
    path = tmp_path / 'results.parquet'
    results.export_report(path, stage5('openai/qwen38-27b', [
        {'task': '/x/t', 'status': 'findings', 'verdicts': [review('a/001', A='pass', B='fail'), review('a/000', A='fail', B='pass')]},
        {'task': '/x/u', 'status': 'failed', 'verdicts': [], 'findings': ['000: no valid verdict after one retry (OOM)']},
        {'task': '/x/s', 'status': 'skipped', 'reason': 'not judged: failed nop'},
    ], attempts=2), {'t': 'j1', 'u': 'j2', 's': 'j3'})
    row, judged = cells(path)
    assert set(judged) == {'A', 'B'} and 'llm_review' not in row
    assert [r['status'] for r in results.judge_reviews(judged['A']['Qwen/Qwen3.8-27B'])] == ['fail', 'pass']  # review 1 = attempt 000
    assert [r['details'] for r in results.judge_reviews(judged['A']['Qwen/Qwen3.8-27B'])] == ['A fail (a/000)', 'A pass (a/001)']
    _, missing = cells(path, 'u')
    assert [r['status'] for r in results.judge_reviews(missing['B']['Qwen/Qwen3.8-27B'])] == ['no_verdict', 'no_verdict']
    assert 'OOM' in missing['B']['Qwen/Qwen3.8-27B']['details']
    _, skipped = cells(path, 's')
    assert skipped['A']['Qwen/Qwen3.8-27B']['status'] == 'not_run'
    assert pq.read_schema(path).metadata[b'ot.results.version'] == b'5'


def test_judges_merge_by_task_id_and_keep_validation_results(tmp_path):
    path = tmp_path / 'results.parquet'
    results.update(path, [{'dataset': 'd', 'task_id': 't', 'content_sha256': 'validated', 'nop': 'pass', 'verifier_paths': 'fail'}])
    results.export_report(path, stage5('openai/qwen38-27b', [{'task': '/j/t', 'status': 'findings', 'verdicts': [review('0', A='fail')]}]),
                          {'t': 'judge-copy-1'}, dataset='d')
    results.export_report(path, stage5('zai-org/GLM-5.3-Flash', [{'task': '/j/t', 'status': 'passed', 'verdicts': [review('0', A='pass')]}]),
                          {'t': 'judge-copy-2'}, dataset='d')
    row, judged = cells(path)
    assert row['content_sha256'] == 'validated' and row['nop'] == 'pass' and row['verifier_paths'] == 'fail'
    assert {m: v['status'] for m, v in judged['A'].items()} == {'Qwen/Qwen3.8-27B': 'fail', 'zai-org/GLM-5.3-Flash': 'pass'}
    # New task files replace the row, judge reviews included: they were of the old files.
    results.update(path, [{'dataset': 'd', 'task_id': 't', 'content_sha256': 'repaired', 'nop': 'pass'}])
    row, judged = cells(path)
    assert row['content_sha256'] == 'repaired' and row['verifier_paths'] == 'not_run'
    assert all(v['status'] == 'not_run' for v in judged['A'].values())


def test_same_judge_rerun_replaces_reviews_but_missing_verdicts_never_overwrite(tmp_path):
    path = tmp_path / 'results.parquet'
    qwen = 'openai/qwen38-27b'
    results.export_report(path, stage5(qwen, [{'task': 't', 'status': 'failed', 'verdicts': [review('0', A='fail')], 'findings': ['x']}], 2),
                          {'t': 'h'})
    assert [r['status'] for r in results.judge_reviews(cells(path)[1]['A']['Qwen/Qwen3.8-27B'])] == ['fail', 'no_verdict']
    # A retry with one review: a missing verdict does not replace review 1 ...
    results.export_report(path, stage5(qwen, [{'task': 't', 'status': 'failed', 'verdicts': [], 'findings': ['oom']}]), {'t': 'h'})
    assert [r['status'] for r in results.judge_reviews(cells(path)[1]['A']['Qwen/Qwen3.8-27B'])] == ['fail', 'no_verdict']
    # ... and a full rerun replaces both reviews.
    results.export_report(path, stage5(qwen, [{'task': 't', 'status': 'passed', 'verdicts': [review('0', A='pass'), review('1', A='pass')]}], 2),
                          {'t': 'h'})
    assert [r['status'] for r in results.judge_reviews(cells(path)[1]['A']['Qwen/Qwen3.8-27B'])] == ['pass', 'pass']


def test_format_3_files_are_still_read(tmp_path):
    import pyarrow as pa
    path = tmp_path / 'old.parquet'
    schema = pa.schema([('dataset', pa.string()), ('task_id', pa.string()), ('content_sha256', pa.string()),
                        ('nop', pa.struct([('status', pa.string()), ('details', pa.string())]))], metadata={b'ot.results.version': b'3'})
    pq.write_table(pa.Table.from_pylist([{'dataset': 'd', 'task_id': 't', 'content_sha256': 'h', 'nop': {'status': 'pass'}}], schema=schema), path)
    assert results.read(path)[0]['nop'] == 'pass'


def test_evaluation_reports_with_their_own_repair_records_do_not_replace_validation_rows(tmp_path):
    path = tmp_path / 'results.parquet'
    results.update(path, [{'dataset': 'd', 'task_id': 't', 'content_sha256': 'validated', 'nop': 'pass', 'build': 'pass'}])
    report = stage5('openai/qwen38-27b', [{'task': '/j/t', 'status': 'findings', 'verdicts': [review('0', A='fail')]}])
    report['automatic_repairs'] = {'instruction-suffix': {'t': {'applied': False}}}
    results.export_report(path, report, {'t': 'judge-copy'}, dataset='d')
    row, judged = cells(path)
    assert (row['content_sha256'], row['nop'], row['build']) == ('validated', 'pass', 'pass')
    assert judged['A']['Qwen/Qwen3.8-27B']['status'] == 'fail'


def test_legacy_judge_maps_read_and_rewrite_as_status(tmp_path):
    import pyarrow as pa
    path = tmp_path / 'legacy.parquet'
    reviews = pa.map_(pa.string(), pa.list_(pa.string()))
    schema = pa.schema([('task_id', pa.string()), ('dataset', pa.string()), ('content_sha256', pa.string()),
                        ('A', pa.struct([('verdicts', reviews), ('details', reviews)]))],
                       metadata={b'ot.results.version': b'4'})
    old = [{'task_id': 't', 'A': {'verdicts': [('model', ['fail', 'pass'])],
                                'details': [('model', ['first', 'second'])]}},
           {'task_id': 'u', 'A': None}]
    pq.write_table(pa.Table.from_pylist(old, schema=schema), path)
    rows = results.read(path)
    results.write(path, rows)
    assert results.read(path)[0] == rows[0]
    assert results.read(path)[1]['A']['model']['status'] == 'not_run'
    raw = json.loads(pq.read_table(path).to_pylist()[0]['A'])
    assert raw['model']['status'] == 'fail'
    assert raw['model']['reviews'] == [{'status': 'fail', 'details': 'first'},
                                       {'status': 'pass', 'details': 'second'}]


def test_skipped_judge_keeps_completed_reviews_and_records_other_models(tmp_path):
    path = tmp_path / 'results.parquet'
    results.export_report(path, stage5('openai/qwen38-27b', [
        {'task': 't', 'status': 'passed', 'verdicts': [review('0', A='pass')]}]), {'t': 'h'})
    skipped = {'task': 't', 'status': 'skipped', 'reason': 'Earlier check failed'}
    results.export_report(path, stage5('openai/qwen38-27b', [skipped,
        {'task': 'u', 'status': 'passed', 'verdicts': [review('0', A='pass')]}]), {'t': 'h', 'u': 'u'})
    assert cells(path)[1]['A']['Qwen/Qwen3.8-27B']['status'] == 'pass'
    results.export_report(path, stage5('zai-org/GLM-5.3-Flash', [skipped,
        {'task': 'u', 'status': 'passed', 'verdicts': [review('0', A='pass')]}]), {'t': 'h', 'u': 'u'})
    assert cells(path)[1]['A']['zai-org/GLM-5.3-Flash'] == {
        'status': 'not_run', 'details': 'Earlier check failed'}


def test_entire_judge_batch_skipped_has_explicit_cells(tmp_path):
    path = tmp_path / 'results.parquet'
    report = stage5('openai/qwen38-27b', [{'task': 't', 'status': 'skipped', 'reason': 'Failed nop'}])
    report['review_criteria'] = ['A', 'B']
    results.export_report(path, report, {'t': 'h'})
    assert cells(path)[1]['A']['Qwen/Qwen3.8-27B'] == {'status': 'not_run', 'details': 'Failed nop'}
