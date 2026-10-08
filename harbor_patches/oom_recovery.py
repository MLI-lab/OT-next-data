"""Continue Terminus's current interaction after a bridge-confirmed task OOM."""
from contextvars import ContextVar
from functools import wraps
from uuid import uuid4

from harbor_patches.protected_step import AGENT_TAG, OOM_PREFIX, OOM_FAILED_PREFIX, FEEDBACK

TERMINAL = ContextVar('oom_recoverable_terminal_call', default=False)


class RecoveredTaskOOM(Exception):
    pass


class TaskMemoryLimitError(Exception):
    """Confirmed task OOM that cannot safely continue with the preserved state."""


def install():
    from harbor.environments.apptainer.apptainer import ApptainerEnvironment
    from harbor.agents.terminus_2.tmux_session import TmuxSession
    from harbor.agents.terminus_2.terminus_2 import Terminus2
    if getattr(ApptainerEnvironment, '_ot_oom_feedback', False):
        return
    execute = ApptainerEnvironment.exec

    @wraps(execute)
    async def exec_with_feedback(self, command, *args, **kwargs):
        if not TERMINAL.get():
            return await execute(self, command, *args, **kwargs)
        token = uuid4().hex
        # Keep the bounded-capture tag at the beginning for the worker patch.
        first, separator, rest = command.partition('\n')
        tagged = first + '\n' + AGENT_TAG + token + '\n' + rest if separator else AGENT_TAG + token + '\n' + command
        result = await execute(self, tagged, *args, **kwargs)
        if (result.stdout or '').startswith(OOM_FAILED_PREFIX + token + '\n'):
            raise TaskMemoryLimitError('Task OOM recovery failed: ' + result.stdout.split('\n', 1)[1])
        if (result.stdout or '').startswith(OOM_PREFIX + token + '\n'):
            raise RecoveredTaskOOM(FEEDBACK)
        return result

    ApptainerEnvironment.exec = exec_with_feedback
    ApptainerEnvironment._ot_oom_feedback = True

    async def restore_terminal(self):
        # The bridge restored the filesystem, not the shell. Do not reinstall
        # tmux or rerun setup. Cancellation still follows the original timeout.
        try:
            result = await self.environment.exec(self._tmux_start_session, user=self._user, timeout_sec=30)
        except Exception as exc:
            raise TaskMemoryLimitError(f'Task OOM recovery could not restart terminal: {exc}') from exc
        if result.return_code:
            raise TaskMemoryLimitError(f'Task OOM recovery could not restart terminal: {result.stderr}')
        self._previous_buffer = None
        self.environment._ot_oom_recoveries = getattr(self.environment, '_ot_oom_recoveries', 0) + 1
        self._ot_oom_feedback_pending = 'TASK_MEMORY_LIMIT: ' + FEEDBACK

    def wrap(name):
        original = getattr(TmuxSession, name)
        @wraps(original)
        async def call(self, *args, **kwargs):
            if not isinstance(self.environment, ApptainerEnvironment):
                return await original(self, *args, **kwargs)
            token = TERMINAL.set(True)
            try:
                result = await original(self, *args, **kwargs)
            except RecoveredTaskOOM:
                TERMINAL.reset(token)
                token = None
                await restore_terminal(self)
                result = ('', True) if name == 'send_keys_and_capture' else True if name == 'is_session_alive' else '' if name == 'capture_pane' else None
            finally:
                if token is not None:
                    TERMINAL.reset(token)
            feedback = getattr(self, '_ot_oom_feedback_pending', None)
            if feedback and name in ('send_keys_and_capture', 'capture_pane'):
                self._ot_oom_feedback_pending = None
                if name == 'send_keys_and_capture':
                    return feedback + '\n' + result[0], result[1]
                return feedback + '\n' + result
            return result
        setattr(TmuxSession, name, call)

    for name in ('send_keys_and_capture', 'capture_pane', 'send_keys', 'is_session_alive'):
        wrap(name)

    run = Terminus2.run
    @wraps(run)
    async def run_with_oom_metrics(self, instruction, environment, context):
        before = getattr(environment, '_ot_oom_recoveries', 0)
        try:
            return await run(self, instruction, environment, context)
        finally:
            context.metadata = dict(context.metadata or {},
                oom_recoveries=getattr(environment, '_ot_oom_recoveries', 0) - before)
    Terminus2.run = run_with_oom_metrics
