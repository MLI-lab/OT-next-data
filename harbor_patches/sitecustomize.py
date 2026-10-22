"""Execution evidence for ``trial`` and ``unittest`` verifiers.

The verifier setup copies this module into every interpreter's site-packages as
``ot_execution_hook.py`` with a ``.pth`` import line, so every interpreter the
verifier command starts imports it, whatever the task does with PYTHONPATH
(only when that copy fails is it loaded as ``sitecustomize`` through PYTHONPATH,
the earlier way). It does nothing unless
the process is the outer ``trial`` or ``unittest`` runner (``python -m unittest``,
Django's ``manage.py test``) of the current verifier call: pytest
verifiers are recorded by ``ot_pytest_execution`` instead, and children inherit
the active marker. The record format is the one ``execution_evidence`` parses:
one BEGIN line when the runner starts and one END line with the reporter's
counts when ``trial`` has finished its only reporter. Keep it importable by
Python 2 and 3.5 (no f-strings, no print keyword arguments).
"""
import json
import os
import sys
import uuid
import atexit


def _is_outer_trial(argv, environ):
    token = environ.get('OT_VERIFIER_EXECUTION_TOKEN')
    if not token or environ.get('OT_PYTEST_EXECUTION_ACTIVE') == token:
        return False
    script = os.path.basename(argv[0]) if argv else ''
    if script == 'trial':
        return True
    # python -m twisted.trial [...] sets argv[0] to the package's __main__ path.
    return script == '__main__.py' and argv[0].replace(os.sep, '/').endswith('twisted/trial/__main__.py')


def _is_direct_verifier(argv, environ):
    token = environ.get('OT_VERIFIER_EXECUTION_TOKEN')
    if not token or environ.get('OT_PYTEST_EXECUTION_ACTIVE') == token:
        return False
    # nl2bash's shell verifier invokes /tests/verifier.py directly. Record
    # that actual verifier process instead of requiring it to use pytest.
    if not argv:
        return False
    script = argv[0].replace(os.sep, '/')
    if script.endswith('/tests/verifier.py'):
        return True
    # FineEnvs invokes its deterministic answer grader directly, not through
    # a test runner. Only the reviewed grader bytes qualify, including the
    # version that renames correctness to reward. Do not record arbitrary
    # scripts merely because their filename is grader.py.
    if script.endswith('/tests/grader.py'):
        import hashlib
        try:
            with open(argv[0], 'rb') as source:
                digest = hashlib.sha256(source.read()).hexdigest()
            return digest in (
                '16f906e9248fa8b7e1141939e425e88e59d52d65e98e360bbacef3720a43629f',
                '6eef67d9219af848bec7293c384505290069f4f0aeca2fa8c608f0733689bdfb')
        except (IOError, OSError):
            return False
    return False


def _command_line():
    """The interpreter's own arguments: while site runs, ``sys.argv[0]`` of
    ``python -m unittest`` is still just ``-m``."""
    original = getattr(sys, 'orig_argv', None)
    if original:
        return list(original[1:])
    try:
        with open('/proc/self/cmdline', 'rb') as stream:
            return [a.decode('utf-8', 'replace') for a in stream.read().split(b'\0')[1:] if a]
    except OSError:
        return []


PYTEST_SCRIPTS = ('pytest', 'py.test', 'pytest-3', 'py.test-3')


def _is_pytest_process(command):
    """python -m pytest / py.test, or a pytest console script, from the interpreter's own
    command line (sys.argv does not exist yet while site runs)."""
    rest = list(command)
    while rest and rest[0].startswith('-') and rest[0] != '-m':
        rest = rest[1:]
    if rest[:1] == ['-m']:
        return rest[1:2] in (['pytest'], ['py.test'])
    return bool(rest) and os.path.basename(rest[0]) in PYTEST_SCRIPTS


def announce_pytest_plugin(environ=None, command=None):
    """Load ot_pytest_execution only in pytest processes of an interpreter that has it next
    to this hook. A global PYTEST_PLUGINS made every other pytest (an interpreter the
    install did not find, a venv created by tox) abort on the missing plugin; such a pytest
    now runs its tests unrecorded. The plugin restores PYTEST_PLUGINS at configure time, so
    processes the tests start do not inherit it."""
    environ = os.environ if environ is None else environ
    token = environ.get('OT_VERIFIER_EXECUTION_TOKEN')
    if not token or environ.get('OT_PYTEST_EXECUTION_ACTIVE') == token:
        return False
    if not os.path.isfile(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ot_pytest_execution.py')):
        return False
    if not _is_pytest_process(_command_line() if command is None else command):
        return False
    previous = environ.get('PYTEST_PLUGINS')
    if previous and 'ot_pytest_execution' in previous.split(','):
        return False
    environ['OT_PYTEST_PLUGINS_RESTORE'] = json.dumps(previous)
    environ['PYTEST_PLUGINS'] = 'ot_pytest_execution' + (',' + previous if previous else '')
    return True


def _is_outer_unittest(argv, environ, command=None):
    token = environ.get('OT_VERIFIER_EXECUTION_TOKEN')
    if not token or environ.get('OT_PYTEST_EXECUTION_ACTIVE') == token or not argv:
        return False
    if argv[0] == '-m':
        command = _command_line() if command is None else command
        # python [-X ...] -m unittest [...]: the module follows its -m.
        return any(a == '-m' and command[i + 1:i + 2] == ['unittest'] for i, a in enumerate(command))
    script = argv[0].replace(os.sep, '/')
    # Django's manage.py test ends in TextTestRunner as well.
    return (script.endswith('/unittest/__main__.py')
            or (os.path.basename(script) == 'manage.py' and len(argv) > 1 and argv[1] == 'test'))


def unittest_counts(result):
    """Translate one finished unittest result into the pytest-shaped evidence counts."""
    def kind(test):
        return type(test).__name__
    errors = [test for test, _ in getattr(result, 'errors', [])]
    # The loader's _FailedTest stands for a test module that could not be imported;
    # _ErrorHolder for a failing setUpClass/setUpModule.
    collection = sum(1 for t in errors if kind(t) == '_FailedTest')
    setup = sum(1 for t in errors if kind(t) == '_ErrorHolder')
    skipped = len(getattr(result, 'skipped', []))
    tests_run = int(getattr(result, 'testsRun', 0))
    return {'executed': max(tests_run - collection - skipped, 0), 'skipped': skipped,
            'setup_errors': setup, 'collection_errors': collection,
            'exitstatus': 0 if result.wasSuccessful() else 1}


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


# Read at import: test suites may clear os.environ before the END record.
_EXECUTION_LOG = os.environ.get('OT_VERIFIER_EXECUTION_LOG')


def _emit(token, event, data):
    line = '\nOT_VERIFIER_EXECUTION:' + token + ':' + event + ':' + json.dumps(data, separators=(',', ':')) + '\n'
    # The call's own log keeps records out of the output the task's grader parses.
    if _EXECUTION_LOG:
        try:
            with open(_EXECUTION_LOG, 'a') as log:
                log.write(line)
            return
        except (IOError, OSError):
            pass
    # Not print(..., flush=True): Python 2 interpreters import this module too.
    sys.stdout.write(line)
    sys.stdout.flush()


def install(argv=None, environ=None):
    if argv is None:
        # Before Python 3.8 (and in 2.7) site runs before sys.argv exists (MiMo 000543,
        # python3.5 -m unittest); "-m" defers to the interpreter's own command line.
        argv = getattr(sys, 'argv', None)
        if argv is None:
            rest = _command_line()
            while rest and rest[0].startswith('-') and rest[0] != '-m':
                rest = rest[1:]
            argv = ['-m'] if rest[:1] == ['-m'] else rest
    environ = os.environ if environ is None else environ
    if _is_direct_verifier(argv, environ):
        token = environ['OT_VERIFIER_EXECUTION_TOKEN']
        environ['OT_PYTEST_EXECUTION_ACTIVE'] = token
        state = {'version': 1, 'id': uuid.uuid4().hex, 'finished': False,
                 'executed': 1, 'skipped': 0, 'setup_errors': 0,
                 'collection_errors': 0, 'exitstatus': 0}
        _emit(token, 'BEGIN', {'version': 1, 'id': state['id']})
        original_excepthook = sys.excepthook

        def excepthook(exc_type, exc_value, traceback):
            state['exitstatus'] = 1
            return original_excepthook(exc_type, exc_value, traceback)

        sys.excepthook = excepthook
        original_exit = sys.exit

        def exit_with_status(code=0):
            state['exitstatus'] = int(code or 0) if isinstance(code, (int, type(None))) else 1
            return original_exit(code)

        sys.exit = exit_with_status

        def finish():
            if not state['finished']:
                state['finished'] = True
                _emit(token, 'END', dict(state))

        atexit.register(finish)
        return state
    if _is_outer_unittest(argv, environ):
        token = environ['OT_VERIFIER_EXECUTION_TOKEN']
        environ['OT_PYTEST_EXECUTION_ACTIVE'] = token
        state = {'version': 1, 'id': uuid.uuid4().hex, 'finished': False, 'runner': 'unittest',
                 'executed': 0, 'skipped': 0, 'setup_errors': 0, 'collection_errors': 0, 'exitstatus': None}
        _emit(token, 'BEGIN', {'version': 1, 'id': state['id']})
        from unittest import runner

        original_run = runner.TextTestRunner.run

        def run(self, test):
            result = original_run(self, test)
            if not state['finished']:
                state['finished'] = True
                state.update(unittest_counts(result))
            return result

        runner.TextTestRunner.run = run

        def finish():
            # Not finished: the process exited before a test run completed.
            _emit(token, 'END', dict(state))

        atexit.register(finish)
        return state
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
        announce_pytest_plugin()
    except Exception:  # pragma: no cover - evidence must never break the verifier
        pass
    try:
        install()
    except Exception:  # pragma: no cover - evidence must never break the verifier
        pass
