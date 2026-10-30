"""Machine-readable validation findings, independent of the stage where observed."""
from validation.stages.layout import NOP, REFERENCE
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
    if re.search(r'verifier (?:execution (?:evidence|context|counts)|invocation)', text):
        return 'verifier-execution-error'
    if (item.get('status') in ('error', 'skipped', 'previewed') or
            re.search(r'exception|missing numeric reward|trials, found', text, re.I)):
        return 'validation-not-run'
    return None


def labels(stage, item, not_required=()):
    if item.get('infrastructure_error'):
        return ['infrastructure-error']
    static = item.get('environment_static') if stage == 1 else None
    if static:
        found = _labels(4, static, not_required)
        if static.get('status') != 'passed':
            return found
        return sorted(set(found + _labels(stage, item, not_required)))
    return _labels(stage, item, not_required)


def _labels(stage, item, not_required=()):
    if stage == 4:
        warning_labels = {'check-task-absolute-path.sh': 'warning:relative-instruction-path',
                          'reference-solution-leak': 'warning:reference-solution-leak',
                          'check-pip-pinning.sh': 'warning:unpinned-dependency',
                          'check-artifact-efficiency.py': 'warning:large-artifact'}
        warnings = {warning_labels[c['check']] for c in item.get('checks', [])
                    if c['status'] == 'warning' and c['check'] in warning_labels}
        return sorted(warnings | {'static:' + c['check'].removeprefix('check-').removesuffix('.sh')
                       for c in item.get('checks', [])
                       if c['status'] == 'failed' and c['check'] not in not_required})
    if stage == 1:
        if item.get('environment_leak') == 'failed':
            return sorted({'environment-leak:' + ('after-repair:' if ((e.get('leaks') or {}).get('repair') or {}).get('version') else '')
                           + c['check'].removeprefix('repository-')
                           for e in item.get('environments', []) if e.get('environment') == 'agent'
                           for c in (e.get('leaks') or {}).get('checks', []) if c['status'] == 'failed'})
        if item.get('environment_leak') in ('error', 'not_run'):
            return ['validation-not-run']
        if item.get('build_stability') == 'unstable_build':
            return ['build-not-robust']
        if item.get('build_stability') == 'infrastructure_failure':
            return ['infrastructure-error']
        if item.get('build_stability') == 'recovered':
            # Recovery alone does not establish that infrastructure caused the failure.
            attempts = item.get('build_attempts', [])
            cause = execution_label(attempts[0]) if attempts else None
            return ['infra-recovered' if cause == 'infrastructure-error' else 'build-recovered']
        if item.get('status') == 'passed':
            return sorted({'warning:' + c['check']
                           for e in item.get('environments', []) if e.get('environment') == 'agent'
                           for c in (e.get('leaks') or {}).get('checks', []) if c['status'] == 'warning'})
        return ['build-execution-error']
    if stage not in (REFERENCE, NOP):
        return []
    if item.get('blocked_by_stage') == 1:
        return ['blocked-by-build']
    problem = execution_label(item)
    if problem:
        return [problem]
    rewards = item.get('rewards') or []
    if stage == REFERENCE and any(r != 1 for r in rewards):
        return ['reference-solution-reward-zero' if all(r == 0 for r in rewards)
                else 'reference-solution-unexpected-reward']
    if (item.get('do_not_modify') or {}).get('status') == 'fail':
        return ['dynamic:do-not-modify']
    if stage == NOP and any(r != 0 for r in rewards):
        return ['no-op-reward-one' if any(r == 1 for r in rewards) else 'no-op-unexpected-reward']
    return []
