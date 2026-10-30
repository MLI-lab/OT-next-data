import json
from pathlib import Path
from types import SimpleNamespace

from validation.stages import static_gate


def test_preflight_blocks_failures_and_errors_but_not_warnings(tmp_path, monkeypatch):
    from validation import contract
    from validation.task_inputs import selection
    from validation.rules.static import terminal_bench
    from validation.stages import runner
    tasks = [tmp_path / name for name in ('good', 'warn', 'bad', 'error')]
    monkeypatch.setattr(contract, 'verify_materialized', lambda a: None)
    monkeypatch.setattr(selection, 'discover_tasks', lambda p: tasks)
    monkeypatch.setattr(selection, 'select_paths', lambda ts, a: ts)
    monkeypatch.setattr(runner, 'checkout', lambda name: tmp_path)
    monkeypatch.setattr(runner, 'static_concurrency', lambda a: 1)
    def checks(sources, out, **kwargs):
        out.mkdir(parents=True)
        (out / 'summary.json').write_text(json.dumps({'tasks': [
            {'task': str(t), 'status': status}
            for t, status in zip(tasks, ['passed', 'passed', 'failed', 'incomplete'])]}))
    monkeypatch.setattr(terminal_bench, 'run_checks', checks)
    args = SimpleNamespace(tasks=tmp_path, out=tmp_path / 'results', static_profile='training',
                           exclude=[], dry_run=False)
    static_gate.prepare(args, [1, 2, 3, 4])
    selected, skipped = static_gate.partition(tasks, args)
    assert [t.name for t in selected] == ['good', 'warn']
    assert {Path(i['task']).name for i in skipped} == {'bad', 'error'}
    assert all(i['blocked_by_stage'] == 4 for i in skipped)


def test_static_rejections_are_never_retried(tmp_path):
    from validation.stages.outcome_retries import retry_round
    args = SimpleNamespace(outcome_retries=3, dry_run=False)
    item = {'task': '/tasks/rejected', 'status': 'skipped', 'blocked_by_stage': 4}
    stages = {n: (tmp_path / f'{n}.json', {'items': [dict(item)]}) for n in (1, 2, 3)}
    def forbidden(number):
        raise AssertionError('Static rejection must not launch a container retry')
    retry_round(stages, args, None, forbidden, lambda *a: None)
    assert all(report['items'][0]['status'] == 'skipped' for _, report in stages.values())


def test_publication_accepts_skips_only_with_definitive_static_rejection():
    from validation.publishing.publish import build_blocked
    item = {'task': '/tasks/bad', 'status': 'skipped', 'blocked_by_stage': 4}
    check = {'check': 'check-agent-editable-test.py', 'status': 'failed'}
    report = {'complete': True, 'items': [{'task': '/tasks/bad', 'status': 'failed', 'checks': [check]}]}
    for stage in (1, 2, 3):
        assert build_blocked(stage, item, {4: report})
    check['status'] = 'error'
    assert not build_blocked(2, item, {4: report})
    assert not build_blocked(2, item, {})
