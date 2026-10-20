"""Audited Together reference prices, frozen with each review contract.

No network calls during validation. Add only verified exact model matches;
unknown models remain unpriced and can be repriced from saved token counts.
"""
from copy import deepcopy

CHECKED_ON = '2026-10-11'
# USD per million tokens, standard serverless inference (not batch).
TOGETHER = {
    'zai-org/GLM-5.3': {
        'rates': {'input': 1.40, 'output': 4.40, 'cache_read': 0.26},
        'url': 'https://www.together.ai/models/glm-5-3'},
    'openai/gpt-oss-120b': {
        'rates': {'input': 0.15, 'output': 0.60},
        'url': 'https://www.together.ai/models/gpt-oss-120b'},
}


def together_reference(model):
    key = model or ''
    for prefix in ('together_ai/', 'together/', 'hosted_vllm/'):
        if key.startswith(prefix):
            key = key[len(prefix):]
            break
    entry = TOGETHER.get(key)
    return {'rates': deepcopy(entry['rates']) if entry else None,
            'source': {'provider': 'together', 'model': key, 'checked_on': CHECKED_ON,
                       'url': entry['url'] if entry else 'https://www.together.ai/pricing',
                       'basis': 'reference_api_equivalent',
                       'status': 'matched' if entry else 'no_verified_match'}}


def freeze(args, numbers):
    """Explicit rates win. Resolve local model keys to their exact HF model ID."""
    from validation.reporting.review_usage import validate_pricing
    explicit = getattr(args, 'review_pricing', None)
    validate_pricing(explicit)
    resolved = getattr(args, 'resolved_review_pricing', None)
    if resolved is None:
        resolved = args.resolved_review_pricing = {}
    for number in numbers:
        if number not in (5, 8) or str(number) in resolved:
            continue
        if explicit is not None:
            resolved[str(number)] = {'rates': deepcopy(explicit), 'source': {'basis': 'user_supplied'}}
            continue
        if getattr(args, 'review_local', False):
            from config.models import resolve
            _, spec = resolve(args.serve_model)
            model = spec.hf_repo
        else:
            model = args.review_model if number == 5 else args.analysis_model
        resolved[str(number)] = together_reference(model)


def for_stage(args, number):
    freeze(args, [number])
    return args.resolved_review_pricing[str(number)]
