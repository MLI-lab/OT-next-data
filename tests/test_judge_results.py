"""results.parquet format 4: one column per judge rubric criterion, every review per judge."""
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
    assert judged['A']['verdicts'] == {'Qwen/Qwen3.8-27B': ['fail', 'pass']}  # review 1 = attempt 000
    assert judged['A']['details']['Qwen/Qwen3.8-27B'] == ['A fail (a/000)', 'A pass (a/001)']
    assert judged['A']['task_sha256'] == {'Qwen/Qwen3.8-27B': 'j1'}
    _, missing = cells(path, 'u')
    assert missing['B']['verdicts'] == {'Qwen/Qwen3.8-27B': ['no_verdict', 'no_verdict']}
    assert 'OOM' in missing['B']['details']['Qwen/Qwen3.8-27B'][0]
    _, skipped = cells(path, 's')
    assert skipped == {}
    assert pq.read_schema(path).metadata[b'ot.results.version'] == b'4'


def test_judges_merge_by_task_id_and_keep_validation_results(tmp_path):
    path = tmp_path / 'results.parquet'
    results.update(path, [{'dataset': 'd', 'task_id': 't', 'content_sha256': 'validated', 'nop': 'pass', 'verifier_paths': 'fail'}])
    results.export_report(path, stage5('openai/qwen38-27b', [{'task': '/j/t', 'status': 'findings', 'verdicts': [review('0', A='fail')]}]),
                          {'t': 'judge-copy-1'}, dataset='d')
    results.export_report(path, stage5('zai-org/GLM-5.3-Flash', [{'task': '/j/t', 'status': 'passed', 'verdicts': [review('0', A='pass')]}]),
                          {'t': 'judge-copy-2'}, dataset='d')
    row, judged = cells(path)
    assert row['content_sha256'] == 'validated' and row['nop'] == 'pass' and row['verifier_paths'] == 'fail'
    assert judged['A']['verdicts'] == {'Qwen/Qwen3.8-27B': ['fail'], 'zai-org/GLM-5.3-Flash': ['pass']}
    assert judged['A']['task_sha256'] == {'Qwen/Qwen3.8-27B': 'judge-copy-1', 'zai-org/GLM-5.3-Flash': 'judge-copy-2'}
    # A new task file replaces the validation row but keeps the judge cells.
    results.update(path, [{'dataset': 'd', 'task_id': 't', 'content_sha256': 'repaired', 'nop': 'pass'}])
    row, judged = cells(path)
    assert row['content_sha256'] == 'repaired' and row['verifier_paths'] == 'not_requested'
    assert set(judged['A']['verdicts']) == {'Qwen/Qwen3.8-27B', 'zai-org/GLM-5.3-Flash'}


def test_same_judge_rerun_replaces_reviews_but_missing_verdicts_never_overwrite(tmp_path):
    path = tmp_path / 'results.parquet'
    qwen = 'openai/qwen38-27b'
    results.export_report(path, stage5(qwen, [{'task': 't', 'status': 'failed', 'verdicts': [review('0', A='fail')], 'findings': ['x']}], 2),
                          {'t': 'h'})
    assert cells(path)[1]['A']['verdicts']['Qwen/Qwen3.8-27B'] == ['fail', 'no_verdict']
    # A retry with one review: a missing verdict does not replace review 1 ...
    results.export_report(path, stage5(qwen, [{'task': 't', 'status': 'failed', 'verdicts': [], 'findings': ['oom']}]), {'t': 'h'})
    assert cells(path)[1]['A']['verdicts']['Qwen/Qwen3.8-27B'] == ['fail', 'no_verdict']
    # ... and a full rerun replaces both reviews.
    results.export_report(path, stage5(qwen, [{'task': 't', 'status': 'passed', 'verdicts': [review('0', A='pass'), review('1', A='pass')]}], 2),
                          {'t': 'h'})
    assert cells(path)[1]['A']['verdicts']['Qwen/Qwen3.8-27B'] == ['pass', 'pass']


def test_format_3_files_are_still_read(tmp_path):
    import pyarrow as pa
    path = tmp_path / 'old.parquet'
    schema = pa.schema([('dataset', pa.string()), ('task_id', pa.string()), ('content_sha256', pa.string()),
                        ('nop', pa.struct([('status', pa.string()), ('details', pa.string())]))], metadata={b'ot.results.version': b'3'})
    pq.write_table(pa.Table.from_pylist([{'dataset': 'd', 'task_id': 't', 'content_sha256': 'h', 'nop': {'status': 'pass'}}], schema=schema), path)
    assert results.read(path)[0]['nop'] == 'pass'
