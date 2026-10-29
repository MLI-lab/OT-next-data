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


def _reviewed_script(path, digests):
    """True only for these exact reviewed script bytes, never for a matching filename alone."""
    import hashlib
    try:
        with open(path, 'rb') as source:
            return hashlib.sha256(source.read()).hexdigest() in digests
    except (IOError, OSError):
        return False


# FineEnvs answer grader, including the version that renames correctness to reward.
FINEENVS_GRADERS = ('16f906e9248fa8b7e1141939e425e88e59d52d65e98e360bbacef3720a43629f',
                    '6eef67d9219af848bec7293c384505290069f4f0aeca2fa8c608f0733689bdfb')
# CrossCodeEval metric scorer: one file across all 7573 v1/v2 tasks (checked 2026-10-29).
CCEVAL_VERIFIERS = ('d61b975f41ac37eda4404dd388718981cfbfdf6bcd1eddf8d2f61d7b88b4b3d8',)
# BugsInPy tornado reporter: unittest.main(testRunner=RecordingRunner), a TextTestRunner
# subclass that does not override run(); one file across the v11 candidates (checked 2026-10-29).
BUGSINPY_UNITTEST_REPORTERS = ('bfcb3b76d5ddb81f587706cf53094ef4de32051240e2dc4f3103b3c3a2c34bf7',)


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
        return _reviewed_script(argv[0], FINEENVS_GRADERS)
    # CrossCodeEval runs its deterministic metric scorer directly; reviewed bytes only.
    if script.endswith('/tests/cceval_verifier.py'):
        return _reviewed_script(argv[0], CCEVAL_VERIFIERS)
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


# Reviewed verifier scripts that run one in-process pytest.main(): they receive the
# pytest plugin like a pytest process. Exact bytes only. ScaleSWE tests/score.py
# (all v1/v2 variants, checked 2026-10-29; each calls pytest.main once).
REVIEWED_PYTEST_DRIVERS = {
    '/tests/score.py': (
    '2c557ae855ef79acd81ff0d137215cdc4602eb42b94b2abc3d730e61fd59b305',
    '5d3bd58dc1da577b8a626fe634ae23934172955d601640bb032b6822a0aae09a',
    '6706d8b59dfda015587993a972bc43c4b95c34b659d5a1a0b9cad2b90bf6519e',
    '7de17a9ba612b569894ff0c95b5331de8c0011cc7e5f1d7705bfad47f9076819',
    '7e1488f1ac1d300b0aec5d883b5887fb4e54aa1d2651ad028b6a404d8bf7a739',
    '932923a2e6bc580b0df36ed5a06020f3ba4d4bd494b013893b641f763c9a57aa',
    '997a51b135c75782f8876579b6858ab6d441a3469588c4b4a06903515aca3348',
    '9a2a84dab82a43acd3c8fe61c6a227a9261304c2c3f7da698f9608869d9c3935',
    'b02fb6ffce8b5a698b0b598520b659608c44ddfef5dc1cfd11cca3deaf2cf65e',
    'b62d31433014374405c2016f9b48c87e5613bd6fc74dd6c142e5130e31588a8c',
    'beaaac9f14b30d1e1d2031ef3416dc365f9de2fa2b5eb0c1b1e5a419ce96014c',
    'cb46a2995a97248a2d19bf307dcc8d900188b33b5638e527ca7d0e482ede5e42',
    'd56765cc56aa420a9d6cedb5b94f2154d6d417bfff580a3f60111d2cfbdcd800',
    'fc6c4a24d17c3ab788db5ee74b697203b2c3e42d70c12d08da3a8cd7b8e09cf5',
    ),
}


def _is_reviewed_pytest_driver(command):
    rest = list(command)
    while rest and rest[0].startswith('-') and rest[0] not in ('-m', '-c'):
        rest = rest[1:]
    if not rest or rest[0].startswith('-'):
        return False
    script = rest[0].replace(os.sep, '/')
    return any(script.endswith(suffix) and _reviewed_script(rest[0], digests)
               for suffix, digests in REVIEWED_PYTEST_DRIVERS.items())


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
    command = _command_line() if command is None else command
    if not (_is_pytest_process(command) or _is_reviewed_pytest_driver(command)):
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
    if script.endswith('/tests/run_unittest.py'):
        return _reviewed_script(argv[0], BUGSINPY_UNITTEST_REPORTERS)
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
