"""Terminus-2 sessions: terminal recovery for every run; time limit and verdict checks for judges.

Terminal recovery applies to every Terminus-2 trial (solver, pass@k, judge):

- Lost terminal (``exit``, ``set -e`` then a failing command, ...): a new tmux
  session is started in the same container and the model is told what was lost.
  After ``MAX_CONSECUTIVE`` recoveries without a successful command in between,
  the session ends as before.
- Harness tmux call timeout (the batch exec did not return within the command's
  wait plus Harbor's margin): poll until the container responds and its screen
  can be read, or Harbor's hard agent limit cancels the wait. Return the screen
  as a normal observation. Only a confirmed missing terminal is restarted.

Judge reviews started inside ``active(policy)`` (validation/stages/env_review.py)
also get:

- Review time limit: after ``review_sec`` the next model turn gets the final-answer
  message. Grace starts when that message is sent. Waiting to send it is
  bounded by one grace period; Harbor's outer limit is review + 2*grace + slack.
- Completion: when the model confirms completion, the verdict file is read from
  the container and validated. An invalid file refuses completion
  ``max_rejections`` times with the exact error, in the same session.

Events are recorded in the trial's agent metadata under ``terminal_session``,
judge settings and events under ``review_session``.
"""
from __future__ import annotations

import asyncio

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from functools import wraps
import shlex
import time
from typing import Callable

MAX_CONSECUTIVE = 3
REVIEW_EVENTS = {'time_limit_message', 'completion_rejected', 'verdict_check_failed',
                 'finish_instruction_timeout', 'grace_timeout'}

TERMINAL_RESTARTED = (
    'NOTE: The shell exited during your last command (for example after `exit`, or `set -e` '
    'followed by a failing command), which closed the terminal. A new terminal has been started '
    'in the same container. Files written so far are kept. The working directory, environment '
    'variables, shell options and any processes started from the old shell are gone. Commands '
    'that did not run before the shell closed were not repeated.')
TERMINAL_LOST_UNRESPONSIVE = (
    'NOTE: The terminal stopped responding for more than {seconds} s while your last command was '
    'running and has been closed. A new terminal has been started in the same container. Files '
    'written so far are kept. The working directory, environment variables, shell options and any '
    'processes started from the old shell are gone.')
TIME_LIMIT = ('The review time limit has been reached. Do not investigate further. Write {artifact} '
              'now from the evidence you have. You have {minutes} minutes.')
INVALID_VERDICT = ('{artifact} is not valid, so the review cannot be accepted yet: {error}. '
                   'Correct the file, then mark the task complete again.')


@dataclass(frozen=True)
class Policy:
    review_sec: float
    grace_sec: float
    artifact: str = '/app/verdicts.json'
    validate: Callable[[str], None] | None = None  # raises ValueError with a model-facing message
    max_rejections: int = 1


POLICY = ContextVar('ot_terminus_review_policy', default=None)
STATE = ContextVar('ot_terminus_session_state', default=None)


@contextmanager
def active(policy):
    token = POLICY.set(policy)
    try:
        yield
    finally:
        POLICY.reset(token)


def new_state(policy):
    return {'policy': policy, 'started': time.monotonic(), 'consecutive': 0, 'terminal_restarts': 0,
            'rejections': 0, 'grace_sent': False, 'events': []}


def _minutes(seconds):
    value = seconds / 60
    return f'{value:g}'


def _event(state, kind, **details):
    state['events'].append({'kind': kind, 'elapsed_sec': round(time.monotonic() - state['started'], 1), **details})


POLL_SEC = 30


async def _answered(session, command):
    """The container's reply, or None when it did not answer in time."""
    try:
        result = await session.environment.exec(command, user=session._user, timeout_sec=60)
    except Exception:
        return None
    if _exec_timed_out(result.return_code, result.stderr or ''):
        return None
    return result


def _exec_timed_out(return_code, text):
    """The bridge's own exec limit (-1) or Apptainer's ("FATAL: context deadline exceeded", 255)."""
    return ((return_code == -1 and 'timed out' in text)
            or (return_code == 255 and 'context deadline exceeded' in text))


def _session_missing(result):
    text = (result.stderr or '') + (result.stdout or '')
    return result.return_code == 1 or any(part in text for part in
                                          ("can't find session", 'no server running', 'no sessions'))


async def _restart(session, state):
    """Start a new tmux session in the same container; False when the cap or the restart fails."""
    if state['consecutive'] >= MAX_CONSECUTIVE:
        _event(state, 'terminal_recovery_exhausted')
        return False
    try:
        result = await session.environment.exec(session._tmux_start_session, user=session._user, timeout_sec=60)
    except Exception as exc:
        _event(state, 'terminal_restart_failed', error=str(exc)[:500])
        return False
    if result.return_code:
        _event(state, 'terminal_restart_failed', error=(result.stderr or '')[:500])
        return False
    session._previous_buffer = None
    state['consecutive'] += 1
    state['terminal_restarts'] += 1
    _event(state, 'terminal_restarted')
    return True


def install():
    from harbor.agents.terminus_2.tmux_session import (TmuxSession, TmuxSessionEndedError,
                                                       TmuxBatchProtocolError)
    from harbor.agents.terminus_2.terminus_2 import Terminus2
    if getattr(Terminus2, '_ot_terminus_session', False):
        return

    capture = TmuxSession.send_keys_and_capture

    @wraps(capture)
    async def send_keys_and_capture(self, keystroke_batches):
        state = STATE.get()
        if state is None:
            return await capture(self, keystroke_batches)
        try:
            output, alive = await capture(self, keystroke_batches)
        except TmuxSessionEndedError:
            if not await _restart(self, state):
                raise
            return TERMINAL_RESTARTED + '\n', True
        except TmuxBatchProtocolError as exc:
            # Only a batch exec that hit its own time limit; other protocol errors stay fatal.
            message = str(exc)
            if not (('return_code=-1' in message and 'timed out' in message)
                    or ('return_code=255' in message and 'context deadline exceeded' in message)):
                raise
            seconds = round(sum(max(0.0, b.duration_sec) for b in keystroke_batches)
                            + self._batch_exec_timeout_margin_sec)
            _event(state, 'terminal_unresponsive', seconds=seconds)
            started = time.monotonic()
            while True:
                probe = await _answered(self, f'tmux has-session -t {shlex.quote(self._session_name)}')
                if probe is not None:
                    if _session_missing(probe):
                        waited = time.monotonic() - started
                        if not await _restart(self, state):
                            raise exc
                        return TERMINAL_LOST_UNRESPONSIVE.format(seconds=round(seconds + waited)) + '\n', True
                    if probe.return_code:
                        raise exc
                    screen = await _answered(self, self._tmux_capture_pane(capture_entire=False))
                    if screen is not None:
                        if screen.return_code:
                            raise exc
                        _event(state, 'screen_read_again', waited_sec=round(time.monotonic() - started))
                        return screen.stdout or '', True
                # A missing reply is not evidence of a lost session. Cancellation from
                # Harbor's hard agent limit propagates through both exec and sleep.
                await asyncio.sleep(POLL_SEC)
        if not alive:
            if await _restart(self, state):
                return (output.rstrip('\n') + '\n\n' + TERMINAL_RESTARTED + '\n'), True
            return output, alive
        if any(b.keystrokes for b in keystroke_batches):
            state['consecutive'] = 0
        return output, alive

    TmuxSession.send_keys_and_capture = send_keys_and_capture

    alive_check = TmuxSession.is_session_alive

    @wraps(alive_check)
    async def is_session_alive(self):
        alive = await alive_check(self)
        state = STATE.get()
        if alive or state is None:
            return alive
        if await _restart(self, state):
            state['pending_note'] = TERMINAL_RESTARTED
            return True
        return False

    TmuxSession.is_session_alive = is_session_alive

    interact = Terminus2._handle_llm_interaction

    @wraps(interact)
    async def handle_llm_interaction(self, chat, prompt, *args, **kwargs):
        state = STATE.get()
        if state is None:
            return await interact(self, chat, prompt, *args, **kwargs)
        if state.pop('pending_note', None):
            prompt = TERMINAL_RESTARTED + '\n\n' + prompt
        policy = state['policy']
        if policy is None:
            return await interact(self, chat, prompt, *args, **kwargs)
        if not state['grace_sent'] and time.monotonic() - state['started'] >= policy.review_sec:
            state['grace_sent'] = True
            deadline = state.get('deadline')
            if deadline is not None:
                deadline.reschedule(asyncio.get_running_loop().time() + policy.grace_sec)
            _event(state, 'time_limit_message')
            prompt = prompt.rstrip('\n') + '\n\n' + TIME_LIMIT.format(
                artifact=policy.artifact, minutes=_minutes(policy.grace_sec))
        result = await interact(self, chat, prompt, *args, **kwargs)
        # Validate at the confirming completion, after earlier turns have written the file.
        if (result.is_task_complete and self._pending_completion and policy.validate
                and state['rejections'] < policy.max_rejections):
            error = None
            try:
                read = await self._session.environment.exec(f'cat {shlex.quote(policy.artifact)}',
                                                            user=self._session._user, timeout_sec=60)
                if read.return_code:
                    error = f'the file could not be read ({(read.stderr or "").strip()[:300] or "missing"})'
                else:
                    policy.validate(read.stdout or '')
            except ValueError as exc:
                error = str(exc)
            except Exception as exc:  # environment trouble is not the model's error
                _event(state, 'verdict_check_failed', error=str(exc)[:500])
            if error:
                state['rejections'] += 1
                _event(state, 'completion_rejected', error=error[:1000])
                message = INVALID_VERDICT.format(artifact=policy.artifact, error=error)
                feedback = (result.feedback + '\n' if result.feedback else '') + 'WARNINGS: ' + message
                return replace(result, is_task_complete=False, feedback=feedback)
        return result

    Terminus2._handle_llm_interaction = handle_llm_interaction

    run = Terminus2.run

    @wraps(run)
    async def run_review(self, instruction, environment, context):
        policy = POLICY.get()
        state = new_state(policy)
        token = STATE.set(state)
        try:
            if policy is None:
                return await run(self, instruction, environment, context)
            # Bound the wait for an in-flight call/command to yield so we can send
            # the finish instruction. Once sent, give it a fresh full grace period.
            async with asyncio.timeout(policy.review_sec + policy.grace_sec) as deadline:
                state['deadline'] = deadline
                try:
                    return await run(self, instruction, environment, context)
                finally:
                    if deadline.expired():
                        _event(state, 'grace_timeout' if state['grace_sent'] else 'finish_instruction_timeout')
        except TimeoutError as exc:
            if state.get('deadline') is None or not state['deadline'].expired():
                raise
            from harbor.trial.errors import AgentTimeoutError
            phase = 'final-answer grace period' if state['grace_sent'] else 'wait to send finish instruction'
            raise AgentTimeoutError(f'Judge exceeded {phase}') from exc
        finally:
            STATE.reset(token)
            metadata = dict(context.metadata or {}, terminal_session={
                'terminal_restarts': state['terminal_restarts'],
                'events': [e for e in state['events'] if e['kind'] not in REVIEW_EVENTS]})
            if policy is not None:
                metadata['review_session'] = {
                    'review_sec': policy.review_sec, 'grace_sec': policy.grace_sec,
                    'finish_wait_sec': policy.grace_sec, 'grace_starts': 'finish_instruction_sent',
                    'time_limit_message': state['grace_sent'], 'completion_rejections': state['rejections'],
                    'events': [e for e in state['events'] if e['kind'] in REVIEW_EVENTS]}
            context.metadata = metadata

    Terminus2.run = run_review
    Terminus2._ot_terminus_session = True
