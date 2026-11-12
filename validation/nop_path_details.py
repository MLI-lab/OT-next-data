"""Explain path-inspection failures caused by an accepted NOP memory limit."""

OOM_PATH_DETAIL = (
    'The NOP run hit the memory limit. This counts as a NOP pass because doing '
    'nothing earned no credit, but it prevented the final file checks.'
)


def explain_nop_path_oom(item):
    """Return a detail only when both accepted NOP OOM and missing observations are recorded."""
    paths = item.get('verifier_paths') or {}
    if paths.get('status') not in ('incomplete', 'execution_error'):
        return None
    if item.get('normal_baseline_status') != 'completed':
        return None
    accepted = item.get('expected_baseline_failures') or []
    if not any(x.get('reason') == 'verifier exceeded the task memory limit on the unchanged start state'
               for x in accepted):
        return None
    for trial in paths.get('trials', []):
        errors = trial.get('errors') or []
        if ('missing observations: after_verifier' in errors
                and any('after_verifier:' in e and 'Task memory limit exceeded' in e for e in errors)):
            return OOM_PATH_DETAIL
    return None
