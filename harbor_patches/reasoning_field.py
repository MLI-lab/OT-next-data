"""Preserve vLLM reasoning in Harbor trajectories.

Pinned Harbor reads reasoning_content, while vLLM returns reasoning. Map the
field when absent; do not overwrite existing content or resend it to the model.
"""


def with_reasoning_content(response):
    """The response with `reasoning` also available as `reasoning_content`."""
    if isinstance(response, dict):
        for choice in response.get('choices') or []:
            message = choice.get('message') if isinstance(choice, dict) else None
            if isinstance(message, dict) and message.get('reasoning_content') is None \
                    and isinstance(message.get('reasoning'), str):
                message['reasoning_content'] = message['reasoning']
    return response


def install():
    from harbor.llms.lite_llm import LiteLLM
    if getattr(LiteLLM, '_maps_reasoning_field', False):
        return
    original = LiteLLM._dispatch_chat

    async def dispatch(self, *args, **kwargs):
        return with_reasoning_content(await original(self, *args, **kwargs))

    LiteLLM._dispatch_chat = dispatch
    LiteLLM._maps_reasoning_field = True
