import copy
import hashlib
import json

import pytest

from validation.publishing import combine


def fingerprint(item):
    return hashlib.sha256(json.dumps(item, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


@pytest.fixture
def reviewed(monkeypatch):
    item = {'task': '/tmp/multiswe/example', 'status': 'failed', 'rewards': [0.0],
            'findings': ['verifier invocation failed during collection or execution'],
            'not_robust': {'reason': 'collection error'}, 'job_dir': '/tmp/original-run'}
    entry = {'report_item_sha256': fingerprint(item), 'oracle_status': 'passed',
             'oracle_rewards': [1.0], 'reason': 'Reference adds the missing API.'}
    monkeypatch.setattr(combine, 'reviewed_multiswe_zeros', lambda: {'example': entry})
    return item, entry


def test_review_preserves_original_and_removes_live_archive_flag(reviewed):
    item, _ = reviewed
    original = copy.deepcopy(item)
    result = combine.manually_reviewed_nop(item)
    assert item == original
    assert result['status'] == 'passed' and result['findings'] == []
    assert result['rewards'] == [0.0]
    assert 'not_robust' not in result
    assert result['manual_review']['previous_not_robust'] == original['not_robust']
    assert result['manual_review']['previous_findings'] == original['findings']


@pytest.mark.parametrize('change', [
    {'rewards': []}, {'rewards': [1.0]}, {'rewards': [0.0, 1.0]},
    {'findings': ['verifier timeout']}, {'job_dir': '/tmp/new-run'},
    {'task': '/tmp/multiswe/other'}, {'status': 'pending'},
])
def test_review_never_transfers_to_changed_result(reviewed, change):
    item, _ = reviewed
    changed = dict(item, **change)
    assert combine.manually_reviewed_nop(changed) == changed


@pytest.mark.parametrize('rewards', [[], [1.0], [0.0, 1.0]])
def test_even_matching_hash_cannot_override_wrong_or_missing_reward(reviewed, rewards):
    item, entry = reviewed
    item['rewards'] = rewards
    entry['report_item_sha256'] = fingerprint(item)
    assert combine.manually_reviewed_nop(item) == item


def test_reference_must_pass(reviewed):
    item, entry = reviewed
    entry['oracle_status'] = 'failed'
    assert combine.manually_reviewed_nop(item) == item


@pytest.fixture
def negative_review(monkeypatch):
    item = {'task': '/tmp/multiswe/reviewed-negative', 'status': 'failed', 'rewards': [],
            'findings': ['exception: no parsed test results'],
            'not_robust': {'reason': 'missing reward'}, 'job_dir': '/tmp/original-run'}
    entry = {'report_item_sha256': fingerprint(item), 'oracle_status': 'passed',
             'oracle_rewards': [1.0], 'observed_rewards': [],
             'acceptance_class': 'expected-baseline-build-or-import-failure',
             'reason': 'Tests require an API supplied by the reference patch.'}
    monkeypatch.setattr(combine, 'reviewed_multiswe_negatives', lambda: {'reviewed-negative': entry})
    return item, entry


@pytest.mark.parametrize('acceptance_class', ['expected-baseline-build-or-import-failure', 'expected-baseline-oom'])
def test_explicit_negative_review_preserves_missing_reward(negative_review, acceptance_class):
    from validation.publishing.publish import judge
    from validation.stages.layout import NOP
    item, entry = negative_review
    entry['acceptance_class'] = acceptance_class
    original = copy.deepcopy(item)
    result = combine.manually_reviewed_nop(item)
    assert item == original
    assert result['status'] == 'passed'
    assert result['rewards'] == []
    assert result['manual_review']['previous_rewards'] == []
    assert result['manual_review']['previous_findings'] == original['findings']
    assert 'not_robust' not in result
    assert judge(NOP, result)[0] == 'passed'


@pytest.mark.parametrize('change', [
    {'rewards': [1.0]}, {'rewards': [0.0]}, {'job_dir': '/tmp/new-run'},
    {'findings': ['exception: unrelated OOM']}, {'task': '/tmp/multiswe/other-negative'},
])
def test_negative_review_does_not_accept_different_evidence(negative_review, change):
    item, _ = negative_review
    changed = dict(item, **change)
    assert combine.manually_reviewed_nop(changed) == changed


@pytest.mark.parametrize('change', [
    {'oracle_status': 'failed'}, {'oracle_rewards': [0.0]},
    {'acceptance_class': 'generic-crash'}, {'observed_rewards': [1.0]},
])
def test_negative_review_requires_eligible_class_and_passing_reference(negative_review, change):
    item, entry = negative_review
    entry.update(change)
    assert combine.manually_reviewed_nop(item) == item
