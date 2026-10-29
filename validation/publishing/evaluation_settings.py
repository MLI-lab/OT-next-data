"""Describe historical evaluation budgets from frozen evidence, never live defaults."""
from copy import deepcopy
import json


def from_contract(contract):
    args = contract.get('arguments', {})
    profile = contract.get('execution_profile', {})
    assets = profile.get('local_model_assets') or {}
    spec = assets.get('spec') or {}
    overrides = args.get('agent_kwargs') or {}
    calls = overrides.get('llm_call_kwargs') or {}
    output = calls.get('max_tokens', calls.get('max_completion_tokens',
                     overrides.get('max_tokens', spec.get('max_output_tokens'))))
    sampling = deepcopy(spec.get('sampling') or {})
    if 'temperature' in overrides:
        sampling['temperature'] = overrides['temperature']
    extra = {**(overrides.get('extra_body') or {}), **(calls.get('extra_body') or {})}
    sampling.update({k: v for k, v in extra.items() if k != 'chat_template_kwargs'})
    thinking = (extra.get('chat_template_kwargs') or {}).get('enable_thinking', spec.get('thinking'))
    return {
        'model': args.get('serve_model') or args.get('model'),
        'hf_repo': spec.get('hf_repo'), 'weights_revision': assets.get('revision'),
        'agent': args.get('agent'), 'attempts': args.get('attempts'),
        'context_tokens': args.get('serve_context') if args.get('serve_model') else None,
        'intended_output_tokens_per_call': output,
        'output_limit_status': 'declared; request forwarding not verified',
        'request_output_tokens': None,
        'thinking': thinking, 'sampling': sampling or None,
        'agent_timeout': 'task-specific; see saved task.toml and resolved trial timeouts',
        'contract_sha256': contract.get('sha256'),
    }


def with_trial(settings, trial):
    """Attach saved runtime configuration without claiming HTTP-level verification."""
    result = deepcopy(settings)
    agent = trial.get('agent') or {}
    kwargs = agent.get('kwargs') or {}
    calls = kwargs.get('llm_call_kwargs') or {}
    limit = calls.get('max_tokens', calls.get('max_completion_tokens'))
    result['request_output_tokens'] = limit
    if limit is not None:
        result['output_limit_status'] = 'present in saved request kwargs; HTTP payload not audited'
    elif agent.get('name') == 'terminus-2' and kwargs.get('max_tokens') is not None:
        result['output_limit_status'] = 'top-level only; explicit request cap not forwarded by Terminus'
    else:
        result['output_limit_status'] = 'unknown; no explicit cap in saved request kwargs'
    result['resolved_timeouts'] = trial.get('resolved_timeouts')
    return result


def model_id(settings):
    """The Hugging Face model ID, so cards never show a serving alias like openai/coder-30b."""
    if settings.get('hf_repo'):
        return settings['hf_repo']
    name = settings.get('model')
    if not name:
        return None
    from config.models import MODELS
    key = name.removeprefix('openai/').removeprefix('hosted_vllm/')
    spec = MODELS.get(key)
    if not spec:
        # Trace Parquets store spellings such as qwen3.8-27b or the lower-cased card name.
        spec = next((m for m in MODELS.values() if m.hf_repo and key.lower() in
                     (m.name.lower(), m.hf_repo.lower(), m.hf_repo.rsplit('/', 1)[-1].lower())), None)
    return spec.hf_repo if spec and spec.hf_repo else name


def markdown(settings):
    """One row per evidence group; keep different runs of the same model separate."""
    def cell(value):
        if value is None:
            return 'not recorded'
        if isinstance(value, bool):
            return 'on' if value else 'off'
        if isinstance(value, (dict, list)):
            value = json.dumps(value, sort_keys=True)
        return str(value).replace('|', '\\|').replace('\n', ' ')
    lines = ['## Evaluation settings', '',
             'Context is the total prompt plus response window for one model call. '
             'The response budget includes reasoning tokens and is not a whole-attempt token budget.', '',
             '| Model | Context tokens (configured) | Intended response tokens per call | Output-limit evidence | Thinking | Sampling |',
             '| --- | ---: | ---: | --- | --- | --- |']
    for s in settings:
        lines.append('| ' + ' | '.join([cell(model_id(s))] + [cell(s.get(k)) for k in (
            'context_tokens', 'intended_output_tokens_per_call', 'output_limit_status', 'thinking', 'sampling')]) + ' |')
    lines += ['', 'Configured limits are not measurements of token usage. A missing explicit response cap '
              'does not mean unlimited generation: the serving context, backend defaults, stopping conditions '
              'and task timeouts still apply. Unknown historical settings are not filled from current model defaults.', '']
    return '\n'.join(lines)
