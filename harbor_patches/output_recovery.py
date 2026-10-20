"""Bound Terminus output recovery and check context before its recursive retries."""
from functools import wraps
from dataclasses import replace

from harbor.llms.base import ContextLengthExceededError


class RepeatedOutputTruncationError(ContextLengthExceededError):
    """A scored agent stop; reuse Harbor's non-retryable, verifier-safe base."""


def install():
    from harbor.agents.terminus_2.terminus_2 import Terminus2

    if getattr(Terminus2, '_ot_output_recovery', False):
        return
    query = Terminus2._query_llm
    recover = Terminus2._recover_output_overflow
    reset = Terminus2._reset_per_run_state

    @wraps(reset)
    def reset_state(self, *args, **kwargs):
        result = reset(self, *args, **kwargs)
        self._ot_output_truncations = 0
        return result

    @wraps(query)
    async def query_with_context(self, chat, prompt, request_context):
        if getattr(self, '_ot_output_truncations', 0):
            summary = await self._check_proactive_summarization(
                chat, request_context.original_instruction, request_context.session,
                pending_prompt=prompt,
            )
            if summary:
                # Resume only from the handoff. The retry count belongs to the
                # agent run and survives replacement of the conversation.
                prompt, refs = summary
                self._pending_subagent_refs = refs
                self._pending_handoff_prompt = prompt
        response = await query(self, chat, prompt, request_context)
        if not self._parser.parse_response(response.content).error:
            self._ot_output_truncations = 0
        return response

    @wraps(recover)
    async def recover_bounded(self, *, chat, prompt, error, request_context):
        salvaged = self._parser.salvage_truncated_response(
            error.truncated_response or '[TRUNCATED RESPONSE NOT AVAILABLE]')
        if salvaged and not self._parser.parse_response(salvaged).error:
            from harbor.agents.terminus_2.terminus_2 import _enqueue_episode_text
            if request_context.logging_paths.response is not None:
                _enqueue_episode_text(request_context.logging_paths.response, salvaged)
            self._ot_output_truncations = 0
            return replace(error.partial_response, content=salvaged)
        if not salvaged:
            self._ot_output_truncations = getattr(self, '_ot_output_truncations', 0) + 1
            if self._ot_output_truncations >= 3:
                chat.append_response(prompt, error.partial_response)
                chat.reset_provider_response_chain()
                self._stop_reason = 'repeated_output_truncation'
                raise RepeatedOutputTruncationError(
                    'Three consecutive output-truncated responses could not be recovered '
                    'into usable responses. No actions from these responses were executed.')
        response = await recover(self, chat=chat, prompt=prompt, error=error,
                                 request_context=request_context)
        if not self._parser.parse_response(response.content).error:
            self._ot_output_truncations = 0
        return response

    Terminus2._reset_per_run_state = reset_state
    Terminus2._query_llm = query_with_context
    Terminus2._recover_output_overflow = recover_bounded
    Terminus2._ot_output_recovery = True
