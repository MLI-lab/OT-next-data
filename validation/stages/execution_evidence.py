"""Read invocation-scoped execution evidence without interpreting captured child output."""
import json
import re


MANIFEST = 'execution-context.json'


def scope_output(output, token):
    """Parse records from outer runner invocations; ignore their console prose.

    A context token belongs to this verifier call, not to a task or datasource.
    Missing records are rejected by the caller, without a text-log fallback.
    """
    pattern = re.compile(r'^OT_VERIFIER_EXECUTION:' + re.escape(token) + r':(BEGIN|END):([^\n]*)$', re.M)
    parts, states = [], []
    cursor, active = 0, None
    for match in pattern.finditer(output):
        try:
            state = json.loads(match[2])
            if state['version'] != 1 or not isinstance(state['id'], str):
                raise ValueError('invalid record')
        except (ValueError, KeyError, TypeError):
            return output, [], 'invalid verifier execution evidence'
        if match[1] == 'BEGIN':
            if active is not None:
                return output, [], 'overlapping verifier execution evidence'
            parts.append(output[cursor:match.start()])
            active = state['id']
        else:
            if active != state['id']:
                return output, [], 'unmatched verifier execution evidence'
            counts = ('executed', 'skipped', 'setup_errors', 'collection_errors')
            if any(type(state.get(k)) is not int or state[k] < 0 for k in counts):
                return output, [], 'invalid verifier execution counts'
            if state.get('finished') is not True or type(state.get('exitstatus')) is not int:
                return output, [], 'verifier invocation did not finish'
            if state['exitstatus'] not in (0, 1, 2, 3, 4, 5):
                return output, [], 'invalid verifier exit status'
            states.append(state)
            cursor, active = match.end(), None
    if active is not None:
        return output, states, 'verifier invocation did not finish'
    parts.append(output[cursor:])
    return ''.join(parts), states, None
