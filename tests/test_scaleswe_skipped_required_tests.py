import json

import pytest

from data.scaleswe.patch import SKIPPED_REQUIRED_TEST_REASON, repair_skipped_required_tests

NOCPP = 'tests/test_fixedvariablecomposite.py::TestRoofDualityComposite::test_nocpp_error'


def test_removes_only_the_inapplicable_required_test():
    contents = {'tests/test_ids.json': json.dumps([NOCPP, 'tests/test_fixedvariablecomposite.py::TestRoofDualityComposite::test_sample']).encode()}
    edits = repair_skipped_required_tests(contents, 'dwavesystems_dimod_pr432')
    assert json.loads(contents['tests/test_ids.json']) == ['tests/test_fixedvariablecomposite.py::TestRoofDualityComposite::test_sample']
    assert edits == [{'file': 'tests/test_ids.json', 'phase': 'verifier-required-test-skipped', 'removed': [NOCPP], 'reason': SKIPPED_REQUIRED_TEST_REASON}]


def test_other_tasks_and_changed_lists_are_left_alone():
    contents = {'tests/test_ids.json': b'["tests/test_x.py::test_a"]'}
    assert repair_skipped_required_tests(contents, 'some_other_task_pr1') == []
    with pytest.raises(ValueError, match='changed since review'):
        repair_skipped_required_tests(contents, 'dwavesystems_dimod_pr432')
