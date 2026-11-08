"""Stage 5 skips tasks with a failed stage 0-3 check; warnings and missing results are judged."""
from pathlib import Path
from types import SimpleNamespace

from validation.run import update
from validation.stages.judge_selection import exclusions, failed_by_task, prepare


def row(task, digest, **cells):
    return {'dataset': 'demo', 'task_id': task, 'content_sha256': digest, **cells}


def results(tmp_path):
    path = tmp_path / 'results.parquet'
    update(path, [
        row('failed', 'a', verifier_paths='fail', nop='pass'),
        row('warned', 'b', **{'pip-pinning': 'warning', 'task-absolute-path': 'warning'}),
        row('missing', 'c', verifier_paths='execution_error', nop='oom', build='timeout', reference='infrastructure_error'),
        row('blocked', 'd', build='pass', nop='blocked'),
        row('clean', 'e', nop='pass'),
        row('reviewed', 'f', nop='pass', llm_review='fail'),  # a judge finding is not a stage 0-3 failure
    ])
    return path


def test_only_failed_checks_exclude(tmp_path):
    found = failed_by_task([results(tmp_path)])
    sources = [Path(n) for n in ('failed', 'warned', 'missing', 'blocked', 'clean', 'reviewed', 'unknown')]
    hashes = dict(zip(['failed', 'warned', 'missing', 'blocked', 'clean', 'reviewed'], 'abcdef'))
    assert exclusions(sources, hashes, found) == {'failed': ['verifier_paths']}


def test_same_file_row_wins_and_other_hashes_still_count(tmp_path):
    first, second = tmp_path / 'one.parquet', tmp_path / 'two.parquet'
    update(first, [row('task', 'old', agent_editable_test='fail')])
    update(second, [row('task', 'new', agent_editable_test='pass', nop='pass')])
    found = failed_by_task([first, second])
    # The judged file matches the repaired row: the old failure no longer applies.
    assert exclusions([Path('task')], {'task': 'new'}, found) == {}
    # A judge contract normalizes the task again (different hash): the recorded failure still counts.
    assert exclusions([Path('task')], {'task': 'judge-copy'}, found) == {'task': ['agent_editable_test']}


def test_prepare_reads_own_results_extra_files_and_this_runs_stages(tmp_path):
    other = tmp_path / 'elsewhere'
    other.mkdir()
    update(other / 'results.parquet', [row('dnm', 'x', do_not_modify='fail')])
    out = tmp_path / 'run'
    out.mkdir()
    update(out / 'results.parquet', [row('static', 'y', agent_editable_test='fail')])
    report = {'stage': 3, 'task_content_sha256': {'nop-task': 'z'},
              'items': [{'task': '/tasks/nop-task', 'status': 'failed', 'normal_baseline_status': 'failed', 'rewards': [1]}]}
    args = SimpleNamespace(out=out, judge_results=[other / 'results.parquet'], judge_failed_tasks='skip')
    prepare(args, {3: (None, report)})
    found = args._judge_failed['failed']
    excluded = exclusions([Path(n) for n in ('dnm', 'static', 'nop-task')], {'dnm': 'x', 'static': 'y', 'nop-task': 'z'}, found)
    assert excluded == {'dnm': ['do_not_modify'], 'static': ['agent_editable_test'], 'nop-task': ['nop']}
    assert [r['path'] for r in args._judge_failed['results']] == [str(out / 'results.parquet'), str(other / 'results.parquet')]
    assert all(len(r['sha256']) == 64 for r in args._judge_failed['results'])


def test_include_judges_everything(tmp_path):
    args = SimpleNamespace(out=tmp_path, judge_results=[results(tmp_path)], judge_failed_tasks='include')
    prepare(args, {})
    assert args._judge_failed is None
