import copy
import hashlib
import json

import pytest

from validation.publishing import combine
from validation.publishing.publish import judge
from validation.publishing.outcome_labels import labels
from validation.stages.layout import REFERENCE


@pytest.fixture
def reviewed(monkeypatch):
    item = {'task': '/tmp/example', 'status': 'failed', 'rewards': [0.0],
            'findings': ['000: expected reward 1, got 0.0']}
    entry = {'report_item_sha256': hashlib.sha256(json.dumps(
        item, sort_keys=True, separators=(',', ':')).encode()).hexdigest(),
        'observed_rewards': [0.0], 'replayed_rewards': [1.0],
        'acceptance_class': 'verified-reference-parser-false-positive'}
    monkeypatch.setattr(combine, 'reviewed_multiswe_references', lambda: {'example': entry})
    return item, entry


def test_replayed_reference_pass_preserves_observed_score(reviewed):
    item, _ = reviewed
    before = copy.deepcopy(item)
    result = combine.manually_reviewed_reference(item)
    assert item == before
    assert result['rewards'] == [1.0]
    assert result['observed_rewards'] == [0.0]
    assert result['manual_review']['previous_rewards'] == [0.0]
    assert result['manual_review']['previous_findings'] == before['findings']
    assert judge(REFERENCE, result) == ('passed', '')
    assert labels(REFERENCE, result) == []


@pytest.mark.parametrize('change', [
    {'job_dir': '/tmp/new-run'}, {'findings': ['exception: memory limit exceeded']},
    {'rewards': []}, {'rewards': [1.0]}, {'task': '/tmp/other'}, {'status': 'error'},
])
def test_reference_review_does_not_transfer(reviewed, change):
    item, _ = reviewed
    changed = dict(item, **change)
    assert combine.manually_reviewed_reference(changed) == changed


@pytest.mark.parametrize('change', [
    {'replayed_rewards': [0.0]}, {'observed_rewards': []},
    {'acceptance_class': 'unverified'},
])
def test_reference_review_requires_successful_parser_replay(reviewed, change):
    item, entry = reviewed
    entry.update(change)
    assert combine.manually_reviewed_reference(item) == item
