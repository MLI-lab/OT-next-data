from copy import deepcopy
from validation.nop_path_details import explain_nop_path_oom, OOM_PATH_DETAIL


def evidence():
    return {'normal_baseline_status': 'completed',
            'expected_baseline_failures': [{'reason': 'verifier exceeded the task memory limit on the unchanged start state'}],
            'verifier_paths': {'status': 'incomplete', 'trials': [{'errors': [
                'after_verifier: RuntimeError: Task memory limit exceeded outside a recoverable agent terminal interaction',
                'missing observations: after_verifier']}]}}


def test_accepted_nop_oom_explains_missing_path_observations_without_mutation():
    item = evidence()
    original = deepcopy(item)
    assert explain_nop_path_oom(item) == OOM_PATH_DETAIL
    assert item == original


def test_unaccepted_oom_does_not_claim_nop_pass():
    item = evidence()
    item['expected_baseline_failures'] = []
    assert explain_nop_path_oom(item) is None


def test_parse_error_or_successful_path_check_is_not_relabelled():
    item = evidence()
    item['verifier_paths']['trials'][0]['errors'] = ['test.sh: SyntaxError: invalid syntax']
    assert explain_nop_path_oom(item) is None
    item = evidence()
    item['verifier_paths']['status'] = 'passed'
    assert explain_nop_path_oom(item) is None
