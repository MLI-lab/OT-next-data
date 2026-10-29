"""One display outcome per attempt; preserve additional errors as evidence."""
LIMIT_OUTCOMES = {'task_timeout', 'turn_limit', 'context_limit', 'output_cap',
                  'task_memory_limit', 'repeated_output_truncation'}
ERROR_OUTCOMES = {'harness_error', 'model_server_error', 'verifier_error',
                  'environment_error', 'other_error'}
OUTCOME_LABELS = {
    'session_ended': 'tmux session closed',
    'environment_error': 'environment error',
    'verifier_error': 'verifier error',
    'model_server_error': 'model server error',
    'harness_error': 'harness error',
    'other_error': 'other error',
}


def classify_outcome(termination, errors):
    """Specific stops win; overlapping errors follow a fixed, documented order."""
    errors = errors or {}
    types = {name for names in errors.values() for name in names}
    if termination in LIMIT_OUTCOMES:
        return termination
    if termination == 'session_ended' or 'TmuxSessionEndedError' in types:
        return 'session_ended'
    if termination in ('context_recovery_failure', 'harness_error') or types & {
            'ContextBudgetExceededError', 'ContextManagementInfrastructureError'}:
        return 'harness_error'
    if termination in ERROR_OUTCOMES:
        return termination
    for group in ('model_server', 'verifier', 'environment', 'other'):
        if errors.get(group):
            return group + '_error'
    if termination == 'error':
        return 'other_error'
    return termination or 'not_recorded'
