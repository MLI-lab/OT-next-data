import json
from validation.patch_repair_loop.reports import summarize


def test_static_warnings_are_diagnostics_not_missing_results(tmp_path):
    checks = [{'check': 'check-task-absolute-path.sh', 'status': 'warning', 'optional': False},
              {'check': 'check_ai_detection.py', 'status': 'skipped', 'optional': True}]
    items = [{'task': 'warn', 'status': 'passed', 'checks': checks},
             {'task': 'skip', 'status': 'passed', 'checks': [{'check': 'required', 'status': 'skipped'}]},
             {'task': 'missing', 'status': 'passed', 'checks': []}]
    (tmp_path / 'stage-1-static.json').write_text(json.dumps({'complete': True, 'contract_sha256': 'frozen', 'items': items}))
    result = summarize(tmp_path, ['warn', 'skip', 'missing'], [1])
    assert result['passed_task_ids'] == ['warn']
    assert result['stage_counts']['1'] == {'passed': 1, 'missing_or_skipped_check': 2}
    assert result['check_counts']['1:check-task-absolute-path.sh:warning'] == 1
    assert not any(f['task_id'] == 'warn' for f in result['failures'])
