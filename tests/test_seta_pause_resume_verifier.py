"""The SETA pause/resume verifier tolerates pre-suspension writes but rejects broken control."""
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from data.seta.patch import ORACLE_ZERO_REPAIRS, pack, patch_task, unpack

TASK = 'unix_linux_se__synth__502065'
OLD, NEW = ORACLE_ZERO_REPAIRS[TASK]['tests/test_outputs.py'][0]


def run_log_test(source, mode='correct'):
    class Worker:
        clock = 0.0
        size = 585
        state = 'S'
        stop_at = None
        resumed = False
        exited = False

        def poll(self):
            return 1 if self.exited else None

        def sleep(self, duration):
            self.clock += duration
            if self.state == 'S' and (not self.resumed or mode != 'no_progress'):
                self.size += 39
            if self.state == 'T' and mode == 'writes_while_stopped':
                self.size += 39

        def process_state(self, pid):
            if self.stop_at is not None and self.clock >= self.stop_at:
                self.state = 'T'
                self.stop_at = None
            return self.state

        def command(self, argv, **kwargs):
            action = argv[1]
            if mode == action + '_error':
                return SimpleNamespace(returncode=1, stderr='controller error')
            if action == 'pause':
                # Reproduce the real ambiguity: a legitimate write after the
                # original baseline measurement but before suspension.
                self.size += 39
                if mode == 'pause_kills':
                    self.exited, self.state = True, 'Z'
                elif mode != 'pause_noop':
                    self.stop_at = self.clock + 0.15
            else:
                self.resumed = True
                if mode == 'resume_kills':
                    self.exited, self.state = True, 'Z'
                elif mode != 'resume_noop':
                    self.state = 'S'
            return SimpleNamespace(returncode=0, stderr='')

    worker = Worker()
    namespace = {
        'running_worker': lambda: nullcontext(worker),
        'read_pid': lambda: 123,
        'get_process_state': worker.process_state,
        'CONTROLLER': '/controller', 'LOGFILE': '/progress.log',
        'subprocess': SimpleNamespace(run=worker.command),
        'time': SimpleNamespace(sleep=worker.sleep, monotonic=lambda: worker.clock),
        'os': SimpleNamespace(path=SimpleNamespace(getsize=lambda _: worker.size)),
    }
    exec(compile(source, 'migration-verifier.py', 'exec'), namespace)
    namespace['test_log_grows_after_pause_resume_cycle']()


def test_repair_tolerates_write_before_confirmed_suspension():
    with pytest.raises(AssertionError, match='Progress log grew during pause'):
        run_log_test(OLD)
    run_log_test(NEW)


@pytest.mark.parametrize('mode, message', [
    ('pause_error', 'pause failed'),
    ('resume_error', 'resume failed'),
    ('pause_noop', 'did not enter stopped state'),
    ('pause_kills', 'pause terminated'),
    ('writes_while_stopped', 'log grew while stopped'),
    ('resume_noop', 'did not resume'),
    ('resume_kills', 'resume terminated'),
    ('no_progress', 'did not resume and grow'),
])
def test_repair_still_rejects_broken_worker_control(mode, message):
    with pytest.raises(AssertionError, match=message):
        run_log_test(NEW, mode)


def test_patcher_changes_only_reviewed_log_test():
    original_test = '# Other tests remain unchanged.\n' + OLD + '\n'
    files = {
        'tests/test.sh': (b'#!/bin/bash\npython3 -m pytest /tests/test_outputs.py\n', 0o755),
        'tests/test_outputs.py': (original_test.encode(), 0o644),
        'solution/solve.sh': (b'original reference solution\n', 0o755),
        'instruction.md': (b'original instruction\n', 0o644),
        'task.toml': (b'original budgets\n', 0o644),
    }
    repaired = unpack(patch_task(pack(files), TASK))
    assert repaired['tests/test_outputs.py'] == (
        original_test.replace(OLD, NEW).encode(), 0o644)
    for name in files.keys() - {'tests/test_outputs.py'}:
        assert repaired[name] == files[name]
    # Never silently apply a reviewed repair to different input.
    files['tests/test_outputs.py'] = (original_test.replace('time.sleep(2)', 'time.sleep(9)').encode(), 0o644)
    with pytest.raises(ValueError, match='reviewed runtime repair anchor'):
        patch_task(pack(files), TASK)
