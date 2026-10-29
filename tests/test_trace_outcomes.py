from validation.metrics.trace_outcomes import classify_outcome


def test_terminal_closure_has_one_label_regardless_of_reporting_path():
    assert classify_outcome('session_ended', {}) == 'session_ended'
    assert classify_outcome('error', {'environment': ['TmuxSessionEndedError'],
                                     'verifier': ['VerifierTimeoutError']}) == 'session_ended'
    assert classify_outcome('error', {'environment': ['TmuxBatchProtocolError']}) == 'environment_error'


def test_context_bug_is_harness_error_not_model_server_failure():
    assert classify_outcome('context_recovery_failure', {}) == 'harness_error'
    assert classify_outcome('error', {'model_server': ['ContextManagementInfrastructureError']}) == 'harness_error'


def test_multiple_errors_have_deterministic_single_category():
    assert classify_outcome('task_timeout', {'verifier': ['VerifierTimeoutError']}) == 'task_timeout'
    assert classify_outcome('error', {'verifier': ['VerifierTimeoutError'],
                                     'environment': ['TmuxCommandError']}) == 'verifier_error'
    assert classify_outcome('task_complete', {}) == 'task_complete'
    assert classify_outcome(None, {}) == 'not_recorded'


def test_outcome_is_idempotent():
    for category in ['harness_error', 'model_server_error', 'verifier_error',
                     'environment_error', 'other_error', 'session_ended']:
        assert classify_outcome(category, {}) == category
