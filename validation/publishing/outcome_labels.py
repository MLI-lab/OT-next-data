"""Machine-readable validation findings, independent of the stage where observed."""
import re


def execution_label(item):
    """Only classify explicit execution evidence; reward mismatches are not evidence."""
    text = '\n'.join([str(item.get('reason') or ''), *map(str, item.get('findings', []))])
    if re.search(r'build(?:ing)?[^\n]*(?:fail|timed out|timeout)|(?:Build|ImageBuild)\w*(?:Error|Timeout)', text, re.I):
        return 'build-execution-error'
    if re.search(r'BridgeOutage|No space left on device|workers? (?:are )?dead', text, re.I):
        return 'infrastructure-error'
    if re.search(r'verifier (?:did not run|could not collect|executed no tests)', text):
        return 'verifier-execution-error'
    if (item.get('status') in ('error', 'skipped', 'previewed') or
            re.search(r'exception|missing numeric reward|trials, found', text, re.I)):
        return 'validation-not-run'
    return None


def labels(stage, item, not_required=()):
    if stage == 1:
        warnings = {'warning:relative-instruction-path' for c in item.get('checks', []) if c['status'] == 'warning' and c['check'] == 'check-task-absolute-path.sh'}
        return sorted(warnings | {'static:' + c['check'].removeprefix('check-').removesuffix('.sh')
                       for c in item.get('checks', [])
                       if c['status'] == 'failed' and c['check'] not in not_required})
    if stage == 3:
        if item.get('build_stability') == 'unstable_build':
            return ['unstable-build-under-our-infra']
        if item.get('build_stability') == 'recovered':
            # Recovery alone does not establish that infrastructure caused the failure.
            attempts = item.get('build_attempts', [])
            cause = execution_label(attempts[0]) if attempts else None
            return ['infra-recovered' if cause == 'infrastructure-error' else 'build-recovered']
        return [] if item.get('status') == 'passed' else ['build-execution-error']
    if stage not in (4, 5):
        return []
    if item.get('blocked_by_stage') == 3:
        return ['blocked-by-build']
    problem = execution_label(item)
    if problem:
        return [problem]
    rewards = item.get('rewards') or []
    if stage == 4 and any(r != 1 for r in rewards):
        return ['reference-solution-reward-zero' if all(r == 0 for r in rewards)
                else 'reference-solution-unexpected-reward']
    if stage == 5 and any(r != 0 for r in rewards):
        return ['no-op-reward-one' if any(r == 1 for r in rewards) else 'no-op-unexpected-reward']
    return []
