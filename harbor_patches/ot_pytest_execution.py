"""Dependency-free pytest plugin recording outer verifier invocations only.

Loaded only for grading, not task setup or agent commands. Children inherit the
active invocation marker; nested pytest.main calls share it in-process. Neither
is a separate top-level verifier command.

Phases are taken from the hook that runs them, never from ``report.when``: a
verifier dependency can shadow instance attributes on every object (sure 1.2.3
installs an ``object.when`` property with a no-op setter, so pytest's own
``report.when`` is lost and its terminal reporter counts three passes per test).
"""
import json
import os
import uuid

import pytest


def _emit(config, event, data):
    # The token is read once at configure time: test suites may clear or
    # replace os.environ before the session ends (skew-92 did), and the END
    # record must still name the invocation it belongs to.
    token = config._ot_execution_token
    line = 'OT_VERIFIER_EXECUTION:' + token + ':' + event + ':' + json.dumps(data, separators=(',', ':'))
    # Use pytest's terminal writer while available; capture has stopped at
    # unconfigure, so the ordinary stream also reaches the verifier log.
    print('\n' + line, flush=True)


def pytest_configure(config):
    token = os.environ.get('OT_VERIFIER_EXECUTION_TOKEN')
    if not token or os.environ.get('OT_PYTEST_EXECUTION_ACTIVE') == token:
        return
    config._ot_execution_token = token
    config._ot_execution_previous = os.environ.get('OT_PYTEST_EXECUTION_ACTIVE')
    os.environ['OT_PYTEST_EXECUTION_ACTIVE'] = token
    state = {'version': 1, 'id': uuid.uuid4().hex, 'finished': False,
             'executed': 0, 'skipped': 0, 'setup_errors': 0,
             'collection_errors': 0, 'exitstatus': None}
    config._ot_execution_state = state
    config.pluginmanager.register(ExecutionRecorder(state), 'ot-outer-execution-recorder')
    _emit(config, 'BEGIN', {'version': 1, 'id': state['id']})


def _is_skip(excinfo):
    from _pytest.outcomes import Skipped
    return excinfo is not None and issubclass(excinfo[0], Skipped)


class ExecutionRecorder:
    def __init__(self, state):
        self.state = state

    def pytest_collectreport(self, report):
        if report.failed:
            self.state['collection_errors'] += 1

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_setup(self, item):
        outcome = yield
        excinfo = outcome.excinfo
        if excinfo is not None and not _is_skip(excinfo):
            self.state['setup_errors'] += 1

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_call(self, item):
        # The call phase ran for this item, whatever its outcome.
        self.state['executed'] += 1
        yield

    def pytest_runtest_logreport(self, report):
        if report.skipped:
            self.state['skipped'] += 1

    @pytest.hookimpl(hookwrapper=True, optionalhook=True)
    def pytest_report_from_serializable(self, config, data):
        # pytest-xdist: the controller runs no test itself, so the setup and
        # call hooks above never fire here. Every worker report reaches the
        # controller through this hook as a plain dict, whose 'when' and
        # 'outcome' keys cannot be shadowed by a dependency.
        yield
        if not isinstance(data, dict) or data.get('$report_type') != 'TestReport':
            return
        when, outcome = data.get('when'), data.get('outcome')
        if when == 'call':
            self.state['executed'] += 1
        elif when == 'setup' and outcome == 'failed':
            self.state['setup_errors'] += 1

    def pytest_sessionfinish(self, session, exitstatus):
        self.state['finished'] = True
        self.state['exitstatus'] = int(exitstatus)


def pytest_unconfigure(config):
    state = getattr(config, '_ot_execution_state', None)
    if state is None:
        return
    try:
        _emit(config, 'END', state)
    finally:
        previous = config._ot_execution_previous
        if previous is None:
            os.environ.pop('OT_PYTEST_EXECUTION_ACTIVE', None)
        else:
            os.environ['OT_PYTEST_EXECUTION_ACTIVE'] = previous
