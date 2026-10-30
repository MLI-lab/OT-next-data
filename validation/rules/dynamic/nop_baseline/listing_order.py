"""Compare final rewards under shuffled Python directory listings.

No-op runs must score 0 and reference runs must score 1. Individual test results
are deliberately not compared: an unsolved task can legitimately pass some tests.
"""
import math


def compare(normal, shuffled, reward_key='reward', *, expected_reward=0, expected_count=None):
    def reward(result):
        return ((result.get('verifier_result') or {}).get('rewards') or {}).get(reward_key)
    def numeric(value):
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    scores = [reward(r) for _, r in shuffled]
    record = {'normal_trials': len(normal), 'shuffled_trials': len(shuffled),
              'shuffled_rewards': scores, 'expected_reward': expected_reward}
    errors = []
    if not shuffled or (expected_count is not None and len(shuffled) != expected_count):
        errors.append('missing shuffled trials')
    if any(r.get('exception_info') for _, r in shuffled):
        errors.append('shuffled trial execution error')
    if any(not numeric(score) for score in scores):
        errors.append('missing or invalid final reward')
    if expected_reward == 1 and (not normal or any(
            r.get('exception_info') or not numeric(reward(r)) or reward(r) != 1 for _, r in normal)):
        errors.append('passing normal reference baseline unavailable')
    if errors:
        return dict(record, status='incomplete', reason='; '.join(errors))
    wrong = [score for score in scores if score != expected_reward]
    label = 'reference solution' if expected_reward == 1 else 'no-op'
    return dict(record, status='flagged' if wrong else 'clean',
                reason=(f'shuffled {label} scored {wrong}; expected {expected_reward}' if wrong
                        else f'all shuffled {label} runs scored {expected_reward}'))
