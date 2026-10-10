"""Execution evidence for Twisted ``trial`` verifiers, loaded through PYTHONPATH.

Python imports ``sitecustomize`` from sys.path at startup, so this module is
seen by every interpreter the verifier command starts. It does nothing unless
the process is the outer ``trial`` runner of the current verifier call: pytest
verifiers are recorded by ``ot_pytest_execution`` instead, and children inherit
the active marker. The record format is the one ``execution_evidence`` parses:
one BEGIN line when the runner starts and one END line with the reporter's
counts when ``trial`` has finished its only reporter.
"""
import json
import os
import sys
import uuid


def _is_outer_trial(argv, environ):
    token = environ.get('OT_VERIFIER_EXECUTION_TOKEN')
    if not token or environ.get('OT_PYTEST_EXECUTION_ACTIVE') == token:
        return False
    script = os.path.basename(argv[0]) if argv else ''
    if script == 'trial':
        return True
    # python -m twisted.trial [...] sets argv[0] to the package's __main__ path.
    return script == '__main__.py' and argv[0].replace(os.sep, '/').endswith('twisted/trial/__main__.py')


def trial_counts(result):
    """Translate one finished trial reporter into the pytest-shaped evidence counts."""
    try:
        from twisted.trial.runner import ErrorHolder
    except ImportError:  # pragma: no cover - counts still describe the run
        ErrorHolder = ()
    holders = sum(1 for test, _ in getattr(result, 'errors', []) if isinstance(test, ErrorHolder))
    tests_run = int(getattr(result, 'testsRun', 0))
    skipped = len(getattr(result, 'skips', []))
    return {'executed': max(tests_run - holders, 0), 'skipped': skipped,
            'setup_errors': 0, 'collection_errors': holders,
            'exitstatus': 0 if result.wasSuccessful() else 1}


def _emit(token, event, data):
    print('\nOT_VERIFIER_EXECUTION:' + token + ':' + event + ':'
          + json.dumps(data, separators=(',', ':')), flush=True)


def install(argv=None, environ=None):
    argv = sys.argv if argv is None else argv
    environ = os.environ if environ is None else environ
    if not _is_outer_trial(argv, environ):
        return None
    token = environ['OT_VERIFIER_EXECUTION_TOKEN']
    environ['OT_PYTEST_EXECUTION_ACTIVE'] = token
    state = {'version': 1, 'id': uuid.uuid4().hex, 'finished': False}
    _emit(token, 'BEGIN', {'version': 1, 'id': state['id']})
    from twisted.trial import reporter

    original_done = reporter.Reporter.done

    def done(self):
        try:
            return original_done(self)
        finally:
            if not state['finished'] and getattr(self, '_ot_outer_reporter', True):
                state['finished'] = True
                _emit(token, 'END', dict(state, **trial_counts(self)))

    reporter.Reporter.done = done
    return state


if __name__ != '__main__':
    try:
        install()
    except Exception:  # pragma: no cover - evidence must never break the verifier
        pass
