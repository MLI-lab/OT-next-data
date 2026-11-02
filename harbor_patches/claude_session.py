"""Claude Code judge sessions: the same time limit and verdict check as Terminus-2 judges.

Active only inside ``terminus_session.active(policy)`` (validation/stages/env_review.py):

- Review time limit: a PostToolUse hook adds terminus_session.TIME_LIMIT to the
  first tool result after ``review_sec``. Harbor's hard agent limit (task.toml)
  is review + grace + slack, as for Terminus-2.
- Completion: when Claude Code exits, the verdict file is read from the
  container and validated. An invalid file resumes the same session
  (``--continue``) with terminus_session.INVALID_VERDICT, ``max_rejections`` times.

Claude Code has no persistent terminal, so the Terminus-2 terminal recovery does
not apply. Events are recorded in the trial's agent metadata under ``review_session``.
"""
from __future__ import annotations

import json
import shlex
import time
from functools import wraps

from harbor_patches.terminus_session import INVALID_VERDICT, POLICY, TIME_LIMIT, _minutes

AGENT_DIR = '/logs/agent'
HOOK = f'{AGENT_DIR}/ot-time-limit.sh'
STARTED = f'{AGENT_DIR}/ot-review-started'
SENT = f'{AGENT_DIR}/ot-time-limit-sent'


def hook_script(review_sec, message):
    """POSIX sh: emit the time-limit context once, after review_sec since STARTED."""
    payload = json.dumps({'hookSpecificOutput': {'hookEventName': 'PostToolUse', 'additionalContext': message}})
    return (f'#!/bin/sh\ncat >/dev/null\n'
            f'[ -e {SENT} ] && exit 0\n'
            f'started=$(cat {STARTED} 2>/dev/null) || exit 0\n'
            f'[ $(( $(date +%s) - started )) -ge {int(review_sec)} ] || exit 0\n'
            f': > {SENT}\n'
            f'printf %s {shlex.quote(payload)}\n')


def settings(config_dir):
    return {'hooks': {'PostToolUse': [{'matcher': '*', 'hooks': [{'type': 'command', 'command': f'sh {HOOK}'}]}]}}


def install():
    from harbor.agents.installed.claude_code import ClaudeCode
    if getattr(ClaudeCode, '_ot_claude_session', False):
        return
    run = ClaudeCode.run

    @wraps(run)
    async def run_review(self, instruction, environment, context):
        policy = POLICY.get()
        if policy is None:
            return await run(self, instruction, environment, context)
        config_dir = f'{AGENT_DIR}/sessions'
        message = TIME_LIMIT.format(artifact=policy.artifact, minutes=_minutes(policy.grace_sec))
        setup = (f'mkdir -p {config_dir} && printf %s {shlex.quote(hook_script(policy.review_sec, message))} > {HOOK} '
                 f'&& printf %s {shlex.quote(json.dumps(settings(config_dir)))} > {config_dir}/settings.json '
                 f'&& rm -f {SENT} && date +%s > {STARTED}')
        await self.exec_as_agent(environment, command=setup)
        state = {'rejections': 0, 'events': []}
        started = time.monotonic()
        try:
            await run(self, instruction, environment, context)
            while policy.validate and state['rejections'] < policy.max_rejections:
                error = None
                try:
                    read = await environment.exec(f'cat {shlex.quote(policy.artifact)}', timeout_sec=60)
                    if read.return_code:
                        error = f'the file could not be read ({(read.stderr or "").strip()[:300] or "missing"})'
                    else:
                        policy.validate(read.stdout or '')
                except ValueError as exc:
                    error = str(exc)
                except Exception as exc:  # environment trouble is not the model's error
                    state['events'].append({'kind': 'verdict_check_failed', 'error': str(exc)[:500]})
                if not error:
                    break
                state['rejections'] += 1
                state['events'].append({'kind': 'completion_rejected', 'error': error[:1000],
                                        'elapsed_sec': round(time.monotonic() - started, 1)})
                # Keep the first round's stream log; the session itself continues in the same files.
                await environment.exec(f'cp {AGENT_DIR}/claude-code.txt '
                                       f'{AGENT_DIR}/claude-code.round{state["rejections"] - 1}.txt', timeout_sec=60)
                flags = self.build_cli_flags
                self.build_cli_flags = lambda: (flags() + ' --continue').strip()
                try:
                    await run(self, INVALID_VERDICT.format(artifact=policy.artifact, error=error), environment, context)
                finally:
                    self.build_cli_flags = flags
        finally:
            sent = False
            try:
                sent = (await environment.exec(f'test -e {SENT}', timeout_sec=30)).return_code == 0
            except Exception:
                pass
            context.metadata = dict(context.metadata or {}, review_session={
                'agent': 'claude-code', 'review_sec': policy.review_sec, 'grace_sec': policy.grace_sec,
                'time_limit_message': sent, 'completion_rejections': state['rejections'], 'events': state['events']})

    ClaudeCode.run = run_review
    ClaudeCode._ot_claude_session = True
