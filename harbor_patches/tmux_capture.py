"""Bound Apptainer terminal content without truncating Harbor's batch framing.

Marin Harbor #136 tails the entire exec response; #137 puts status markers at
its beginning. Bound each capture before those markers are added instead.
The lifecycle worker supplies the budget from its actual per-stream limit.
"""
from functools import wraps

BATCH_TAG = '# ot-next-data: bounded tmux captures\n'
FRAME_RESERVE = 1024
DEFAULT_CAPTURE_BYTES = 200_000
BUDGET_VAR = '_OT_TMUX_CAPTURE_BYTES'


def bounded_capture(command):
    # The sentinel preserves small captures. Dropping the first retained line
    # after byte limiting avoids starting inside a UTF-8 character. Subshells
    # and pipefail preserve capture failures, including the upstream pipeline.
    return ("(set -o pipefail; { printf '\\n'; (" + command + "); } | "
            f'tail -c "${{{BUDGET_VAR}:-{DEFAULT_CAPTURE_BYTES}}}" | sed \'1d\')')


def install():
    from harbor.agents.terminus_2.tmux_session import TmuxSession
    from harbor.environments.apptainer.apptainer import ApptainerEnvironment
    if getattr(TmuxSession, '_ot_bounded_batch_captures', False):
        return
    original = TmuxSession._build_batch_script

    @wraps(original)
    def build(self, batches, markers):
        script = original(self, batches, markers)
        if not isinstance(self.environment, ApptainerEnvironment):
            return script
        assert len((markers.visible + '\n' + markers.full + '\n').encode()) < FRAME_RESERVE
        for entire in (False, True):
            capture = self._tmux_capture_pane(capture_entire=entire)
            line = capture + ' || exit $?'
            if script.count(line) != 1:
                raise RuntimeError('Harbor tmux batch layout changed; review bounded capture patch')
            script = script.replace(line, bounded_capture(capture) + ' || exit $?')
        return BATCH_TAG + script

    TmuxSession._build_batch_script = build
    TmuxSession._ot_bounded_batch_captures = True


def install_worker(worker):
    instance = worker.ApptainerInstance
    if getattr(instance, '_ot_bounded_batch_captures', False):
        return
    original = instance.exec

    @wraps(original)
    def execute(self, payload):
        command = payload.get('command', '')
        if command.startswith(BATCH_TAG):
            budget = (self.max_exec_output_chars - FRAME_RESERVE) // 2
            if budget < 1:
                raise ValueError('Apptainer exec output limit is too small for tmux batch framing')
            # Bytes bound Python character counts conservatively. Keep room for
            # both captures and markers; ordinary exec truncation is unchanged.
            payload = dict(payload, command=f'{BUDGET_VAR}={budget}\n' + command)
        return original(self, payload)

    instance.exec = execute
    instance._ot_bounded_batch_captures = True
