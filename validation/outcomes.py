"""Shared, evidence-based categories for checks without a usable verdict.

Categories describe observations, not whether the task or run configuration caused
them. Do not inspect old attempts or unrelated diagnostic subchecks for causes.
"""
import json
import re

CATEGORIES = {'skipped', 'infrastructure_error', 'timeout', 'oom', 'execution_error', 'unknown'}
INFRA = re.compile(r'No space left on device|Disk quota exceeded|BridgeOutage|bridge outage|'
                   r'node failure|NODE_FAIL|workers? (?:are )?dead|no LD_PRELOAD in fakeroot|'
                   r'Container environment damaged', re.I)
TIMEOUT = re.compile(r'\b\w*TimeoutError\b|timed out|deadline exceeded|time limit (?:exceeded|reached)|'
                     r'waited \d+ s for a worker or Slurm step', re.I)
OOM = re.compile(r'\b\w*OutOfMemoryError\b|\bMemoryError\b|out.of.memory|\boom[_ -]?kill(?:ed)?\b|'
                 r'\bOOM\b|cannot allocate memory|task_memory_limit', re.I)
CRASH = re.compile(r'\bexception\b|\b\w+Error\b|Traceback|segmentation fault|verifier.*crash', re.I)
MISSING = re.compile(r'missing (?:numeric )?reward|no (?:valid )?reward|wrote no reward|'
                     r'no (?:valid )?result|did not produce a result|trials, found', re.I)


def message(record):
    return '\n'.join(json.dumps(record[key], default=str) for key in
                     ('reason', 'error', 'findings', 'exception_info', 'exception_type', 'stderr')
                     if record.get(key))


def category(record, verdict='not_run', phase='unknown'):
    """Return standardized fields, or None for a completed check verdict."""
    if record.get('classification_version') == 1 and record.get('category') in CATEGORIES:
        return {key: record[key] for key in ('classification_version', 'category', 'reason_code', 'phase')}
    status = record.get('status')
    phase = record.get('phase') or phase
    text = message(record)
    def found(kind, reason):
        return {'classification_version': 1, 'category': kind, 'reason_code': reason, 'phase': phase}
    if status in ('not_applicable', 'not-applicable'):
        return found('skipped', 'not_applicable')
    if status in ('skipped', 'previewed') or record.get('blocked_by_stage') is not None:
        return found('skipped', 'blocked_by_previous_check' if record.get('blocked_by_stage') is not None else 'not_requested')
    # An explicitly successful check can contain diagnostic logs mentioning errors.
    if verdict in ('pass', 'warning'):
        return None
    if phase == 'checker' and verdict == 'fail' and status in ('failed', 'fail', 'flagged', 'findings'):
        return None  # A checker finding may describe an error in the task's code.
    if status in ('oom', 'out_of_memory') or record.get('oom_killed') is True or OOM.search(text):
        return found('oom', 'memory_exhausted')
    if status in ('timeout', 'timed_out') or TIMEOUT.search(text):
        return found('timeout', 'time_limit_reached')
    if INFRA.search(text):
        return found('infrastructure_error', 'infrastructure_failure')
    # The old retry controller used this marker for *any* exhausted retry chain.
    # That is missing evidence, not proof of infrastructure failure.
    legacy_retry = record.get('infrastructure_error')
    legacy_retry = isinstance(legacy_retry, dict) and set(legacy_retry) == {'observed'}
    if (record.get('infrastructure_error') and not legacy_retry or
            record.get('failure_kind') == 'infrastructure' or
            status in ('infrastructure_error', 'infra_error')):
        return found('infrastructure_error', 'infrastructure_failure')
    if record.get('exception_info') or CRASH.search(text):
        if phase == 'unknown':
            phase = 'verifier' if 'verifier' in text.lower() else 'unknown'
        return found('execution_error', 'process_error')
    if re.search(r'verifier (?:invocation|did not run|could not collect|executed no tests|execution (?:evidence|context|counts))', text, re.I):
        return found('execution_error', 'verifier_execution_error')
    if record.get('rewards') == [] or MISSING.search(text):
        return found('execution_error', 'missing_result')
    # Inspect execution errors in the current environment, not its check findings.
    for env in record.get('environments', []):
        if env.get('status') not in ('passed', 'completed'):
            issue = category({k: env[k] for k in ('status', 'error', 'reason', 'exception_info') if k in env},
                             'not_run' if env.get('status') == 'error' else verdict, 'setup')
            if issue:
                return issue
    if verdict == 'not_run':
        for child in record.get('checks', []) + record.get('trials', []):
            if child.get('status') not in ('passed', 'completed', 'clean', 'warning'):
                issue = category(child, verdict, phase)
                if issue:
                    return issue
    if isinstance(record.get('result'), dict) and verdict == 'not_run':
        return category(record['result'], verdict, phase)
    if status in ('error', 'incomplete', 'failed', 'not_checked') and verdict == 'not_run':
        return found('execution_error' if text or status in ('error', 'incomplete') else 'unknown',
                     'execution_incomplete' if text or status in ('error', 'incomplete') else 'missing_evidence')
    if verdict == 'not_run' or legacy_retry or record.get('unresolved_after_retries'):
        return found('unknown', 'missing_evidence')
    return None


def check_phase(name):
    if name == 'build':
        return 'setup'
    if name == 'nop':
        return 'verifier'
    if name.startswith('passk_'):
        return 'agent'
    if name in ('reference', 'reference_determinism', 'nop_determinism'):
        return 'unknown'  # Could be the solution or the verifier; do not guess.
    return 'checker'


def normalize(name, verdict, record):
    record = dict(record)
    issue = category(record, verdict, check_phase(name))
    if issue:
        record.update(issue)
        return 'not_run', record
    return verdict, record
