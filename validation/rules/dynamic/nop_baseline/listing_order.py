"""Stage 3: tests that fail on the unsolved task only when directory listing order varies.

Filesystems list directory entries in different orders. A task whose bug depends on
that order (e.g. results that change with file processing order) can look already
solved on a node where the order happens to be stable: its tests pass on the
unchanged code. The shuffled no-op runs the verifier with Python directory listings
in random order (harbor_patches/sitecustomize.shuffle_listing). A test that passes in
every normal no-op run but fails in a shuffled one depends on listing order.
Per-test outcomes come from the pytest recorder; other runners are not assessed.
"""
import json


def test_outcomes(path, result):
    """{test id: outcome} of one trial's verifier call, or None without per-test records."""
    from validation.stages.execution_evidence import MANIFEST, scope_output
    from validation.stages.harbor import verifier_output
    context = path / 'verifier' / MANIFEST
    if not context.is_file():
        return None
    try:
        token = json.loads(context.read_text())['token']
    except (ValueError, KeyError, TypeError):
        return None
    _, invocations, problem = scope_output(verifier_output(path, result.get('verifier_result') or {}), token)
    recorded = [i['tests'] for i in invocations if isinstance(i.get('tests'), dict)]
    if problem or not recorded:
        return None
    outcomes = {}
    for tests in recorded:
        for test, outcome in tests.items():
            if outcomes.get(test) not in ('failed', 'error'):
                outcomes[test] = outcome
    return outcomes


def compare(normal, shuffled, reward_key='reward'):
    """Compare per-test outcomes of the normal and shuffled no-op trials of one task."""
    normal_outcomes = [o for o in (test_outcomes(p, r) for p, r in normal) if o is not None]
    shuffled_outcomes = [o for o in (test_outcomes(p, r) for p, r in shuffled) if o is not None]
    rewards = [((r.get('verifier_result') or {}).get('rewards') or {}).get(reward_key) for _, r in shuffled]
    record = {'normal_trials': len(normal), 'shuffled_trials': len(shuffled), 'shuffled_rewards': rewards}
    if not normal_outcomes or not shuffled_outcomes:
        record.update(status='not_assessed', reason='no per-test outcomes recorded (only pytest records them)')
        return record
    stable = set(normal_outcomes[0])
    for outcomes in normal_outcomes:
        stable &= {test for test, outcome in outcomes.items() if outcome == 'passed'}
    flagged = sorted(test for test in stable
                     if any(outcomes.get(test) in ('failed', 'error') for outcomes in shuffled_outcomes))
    record.update(status='flagged' if flagged else 'clean', tests=flagged,
                  assessed_normal_trials=len(normal_outcomes), assessed_shuffled_trials=len(shuffled_outcomes))
    if flagged:
        record['reason'] = (f'{len(flagged)} test(s) pass on the unsolved task in every normal no-op run '
                            'but fail with shuffled directory listing order')
    return record
