"""Judge-only routing. Serialized arguments contain credential names, never keys."""
from contextlib import contextmanager
from copy import copy
import os
from pathlib import Path
import re


def apply_defaults(args):
    from config.models import judge_defaults
    model = getattr(args, 'judge_model', None) or (
        (getattr(args, 'serve_model', None) or getattr(args, 'model', None))
        if getattr(args, 'review_local', False) else
        (getattr(args, 'review_model', None) or getattr(args, 'analysis_model', None)))
    defaults = judge_defaults(model)
    if getattr(args, 'timeout_minutes', None) is None:
        args.timeout_minutes = defaults.review_minutes
    if getattr(args, 'review_grace_minutes', None) is None:
        args.review_grace_minutes = defaults.grace_minutes
    serving_judge = (getattr(args, 'judge_mode', None) in ('serve', 'local') or
                     (getattr(args, 'review_local', False) and getattr(args, 'serve_model', None)))
    if serving_judge and getattr(args, 'serve_context', None) is None and defaults.context:
        args.serve_context = defaults.context


def normalize(args):
    apply_defaults(args)
    mode = getattr(args, 'judge_mode', None)
    fields = ('judge_model', 'judge_api_base', 'judge_api_key_env', 'judge_cli')
    if not mode:
        if any(getattr(args, field, None) for field in fields):
            raise ValueError('--judge-* options require --judge-mode serve, api or cli')
        return
    if mode == 'local':
        args.judge_mode = mode = 'serve'
    if not getattr(args, 'judge_model', None):
        raise ValueError('--judge-mode requires --judge-model')
    if getattr(args, 'review_local', False):
        raise ValueError('choose --judge-mode or legacy --review-local')
    if mode == 'cli':
        if not getattr(args, 'judge_cli', None):
            raise ValueError('--judge-mode cli requires --judge-cli')
        if getattr(args, 'judge_api_base', None) or getattr(args, 'judge_api_key_env', None):
            raise ValueError('CLI judges use CLI authentication, not --judge-api-*')
        agent, model = args.judge_cli, args.judge_model
    else:
        if getattr(args, 'judge_cli', None):
            raise ValueError('--judge-cli requires --judge-mode cli')
        agent, model = 'terminus-2', args.judge_model
        if mode == 'serve':
            if getattr(args, 'judge_api_key_env', None):
                raise ValueError('--judge-api-key-env requires --judge-mode api')
            if getattr(args, 'judge_api_base', None) and not getattr(args, '_local_server_ready', False):
                raise ValueError('--judge-api-base requires --judge-mode api')
            from config.models import resolve
            key, _ = resolve(model)
            if getattr(args, 'serve_model', None) and resolve(args.serve_model)[0] != key:
                raise ValueError('judge and solver cannot start different servers in one allocation; use --judge-mode api')
            args.serve_model = key
            model = 'openai/' + key
    key_env = getattr(args, 'judge_api_key_env', None)
    if key_env and not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', key_env):
        raise ValueError('--judge-api-key-env must be an environment variable name')
    for field, value in [('review_agent', agent), ('review_model', model),
                         ('analysis_agent', agent), ('analysis_model', model)]:
        previous = getattr(args, field, None)
        if previous and previous != value:
            raise ValueError(f'--{field.replace("_", "-")} conflicts with --judge-mode/--judge-model')
        setattr(args, field, value)


def review_args(args):
    """Give the reviewer its own endpoint without changing solver arguments."""
    normalize(args)
    if not getattr(args, 'judge_mode', None):
        return args
    result = copy(args)
    result.api_base = getattr(args, 'judge_api_base', None)
    if args.judge_mode != 'cli':
        result.agent = 'terminus-2'
        result.agent_kwargs = getattr(args, 'judge_agent_kwargs', {})
    if args.judge_mode == 'serve' and not result.api_base:
        raise ValueError('judge server has not started; use cluster submission or --judge-mode api for an existing server')
    return result


def provider_key(model):
    provider = model.split('/', 1)[0]
    return {'openai': 'OPENAI_API_KEY', 'hosted_vllm': 'OPENAI_API_KEY',
            'anthropic': 'ANTHROPIC_API_KEY', 'gemini': 'GEMINI_API_KEY',
            'together_ai': 'TOGETHERAI_API_KEY', 'groq': 'GROQ_API_KEY',
            'deepseek': 'DEEPSEEK_API_KEY', 'mistral': 'MISTRAL_API_KEY',
            'cohere': 'COHERE_API_KEY', 'openrouter': 'OPENROUTER_API_KEY'}.get(provider)


@contextmanager
def credentials(args, number):
    """Scope credentials to a judge stage; never put resolved secrets in configs."""
    mode = getattr(args, 'judge_mode', None)
    if number not in (5, 8) or mode not in ('api', 'serve', 'local'):
        yield
        return
    from dotenv import dotenv_values
    # Same home default as hpc/validation.sh; a project .env overrides that
    # fallback, while explicitly exported variables always win.
    explicit = os.environ.get('OT_SECRETS_FILE')
    paths = [Path(explicit).expanduser()] if explicit else [Path.home() / '.env', Path.cwd() / '.env']
    values = {}
    for path in paths:
        if path.is_file():
            values.update({k: v for k, v in dotenv_values(path).items() if v is not None})
    effective = {**values, **os.environ}
    selected = getattr(args, 'judge_api_key_env', None)
    updates = {k: v for k, v in values.items() if k not in os.environ}
    if mode in ('serve', 'local'):
        updates['OPENAI_API_KEY'] = 'EMPTY'
    elif selected:
        if not effective.get(selected):
            raise ValueError(f'judge credential variable {selected} is missing or empty')
        target = provider_key(args.judge_model)
        if not target:
            raise ValueError('custom judge credentials require a supported provider prefix; '
                             'use its standard environment variables instead')
        updates[target] = effective[selected]
    previous = {k: os.environ.get(k) for k in updates}
    try:
        os.environ.update(updates)
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
