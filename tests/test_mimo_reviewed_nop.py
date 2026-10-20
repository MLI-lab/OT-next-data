import hashlib
import json

import pytest

from validation.publishing.combine import manually_reviewed_nop


@pytest.mark.parametrize('finding', [
    '000: verifier invocation did not finish',
    '000: verifier execution evidence is missing',
])
def test_review_requires_exact_report_and_zero_reward(monkeypatch, finding):
    item = {'task': '/tmp/format-code-task-000772', 'status': 'failed',
            'findings': [finding], 'rewards': [0.0],
            'not_robust': {'expected': 'result', 'observed': ['not_run'] * 4}}
    review = {'reviewed': '2026-10-18', 'output_line': '5 tests failed, 3 tests passed',
              'report_item_sha256': hashlib.sha256(json.dumps(
                  item, sort_keys=True, separators=(',', ':')).encode()).hexdigest()}
    from pathlib import Path
    original = Path.read_text
    monkeypatch.setattr(Path, 'read_text', lambda p, *a, **kw:
                        json.dumps({'format-code-task-000772': review})
                        if p.name == 'reviewed_mimo_nop_zeros.json' else original(p, *a, **kw))
    accepted = manually_reviewed_nop(item)
    assert accepted['status'] == 'passed'
    assert accepted['manual_review']['previous_findings'] == [finding]
    assert accepted['manual_review']['previous_not_robust'] == item['not_robust']
    from validation.publishing.publish import judge
    assert judge(3, accepted) == ('passed', '')
    for changed in (dict(item, rewards=[1.0]), dict(item, rewards=[]),
                    dict(item, findings=['000: verifier timeout']),
                    dict(item, task='/another-run/format-code-task-000772')):
        assert manually_reviewed_nop(changed) == changed
