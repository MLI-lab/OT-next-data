"""Log commands and container state when Harbor loses a tmux session.

Pinned Harbor raises before recording the failed step; retain diagnostics
without replacing the original error or changing agent behavior.
"""
import json

PROBE = ('echo "--- tmux sessions"; tmux ls 2>&1; echo "--- tmux sockets"; ls -la /tmp/tmux-* 2>&1; '
         'echo "--- processes"; ps -eo pid,ppid,user,etime,stat,args 2>&1 | head -60; '
         'echo "--- memory events"; cat /sys/fs/cgroup/memory.events 2>&1; '
         # --fakeroot gives each session a helper (faked) reached through a message queue; a
         # shell whose queue is gone dies at its next file operation.
         'echo "--- fakeroot key of this command: $FAKEROOTKEY"; '
         'for p in $(pgrep -x tmux 2>/dev/null; pgrep -f "tmux: server" 2>/dev/null); do '
         'echo "tmux $p: $(tr "\\0" "\\n" < /proc/$p/environ 2>/dev/null | grep -E "FAKEROOTKEY|LD_PRELOAD" | tr "\\n" " ")"; done; '
         'echo "--- message queues"; ipcs -q -p 2>&1 | head -5; ipcs -q 2>&1 | wc -l')


def install():
    from harbor.agents.terminus_2 import tmux_session as module
    session = module.TmuxSession
    if getattr(session, '_reports_session_end', False):
        return
    original = session.send_keys_and_capture

    async def send_keys_and_capture(self, keystroke_batches):
        try:
            return await original(self, keystroke_batches)
        except module.TmuxSessionEndedError:
            report = {'session': self._session_name,
                      'keystrokes': [{'keys': b.keystrokes, 'duration_sec': b.duration_sec} for b in keystroke_batches]}
            try:
                probe = await self.environment.exec(command=PROBE, timeout_sec=30, user=self._user)
                report['container'] = (probe.stdout or '') + (probe.stderr or '')
            except Exception as exc:  # the diagnosis must never replace the original error
                report['container'] = f'probe failed: {exc!r}'
            self._logger.error('tmux session ended; diagnostics: %s', json.dumps(report, default=str))
            raise

    session.send_keys_and_capture = send_keys_and_capture
    session._reports_session_end = True
