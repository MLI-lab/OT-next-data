"""Record how agent trials were run, so rewards are comparable between runs."""
import hashlib
import inspect
from pathlib import Path
import tomllib

from validation.upstream import PINS


def agent_settings(name, kwargs):
    """Constructor defaults of the pinned agent, overridden by the given kwargs."""
    try:
        from harbor.agents.factory import AgentFactory
        from harbor.models.agent.name import AgentName
        cls = AgentFactory._AGENT_MAP[AgentName(name)]
    except Exception as exc:
        return {'settings': dict(kwargs), 'defaults_resolved': False, 'reason': f'{type(exc).__name__}: {exc}'}
    defaults = {p.name: p.default for p in inspect.signature(cls.__init__).parameters.values()
                if p.default is not inspect.Parameter.empty and isinstance(p.default, (str, int, float, bool, type(None)))}
    # Prompt templates and response parsers define what the model is asked to emit.
    module = Path(inspect.getsourcefile(cls))
    files = [module, *sorted(p for p in module.parent.glob('*parser*.py')),
             *sorted(p for p in (module.parent / 'templates').rglob('*') if p.is_file())]
    return {'settings': {**defaults, **kwargs}, 'defaults_resolved': True,
            'source_sha256': {str(p.relative_to(module.parent)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}}


def task_settings(task):
    from validation.contract import task_digest
    config = tomllib.loads((task / 'task.toml').read_text())
    return {'task_id': task.name, 'agent_timeout_sec': config.get('agent', {}).get('timeout_sec'),
            'verifier_timeout_sec': config.get('verifier', {}).get('timeout_sec'),
            'tests_sha256': task_digest(task / 'tests')}


def record(args, config, tasks, contract=None):
    from validation.contract import local_model_assets
    agent = config['agents'][0]
    resolved = agent_settings(agent['name'], agent.get('kwargs', {}))
    settings = resolved['settings']
    info = settings.get('model_info') or {}
    profile = (contract or {}).get('execution_profile', {})
    return {
        'agent': {'name': agent['name'], 'harbor_commit': PINS['harbor-validation']['commit'], **resolved},
        'model': {'name': agent.get('model_name'), 'api_base': settings.get('api_base'),
                  # Served model: sampling, reasoning parser and chat-template hash from config/models.py.
                  'local_serving': profile.get('local_model_assets') or local_model_assets(args)},
        'sampling': {'temperature': settings.get('temperature'), 'extra_body': settings.get('extra_body'),
                     'reasoning_effort': settings.get('reasoning_effort')},
        'limits': {'context_tokens': info.get('max_input_tokens') or (args.serve_context if args.serve_model else None),
                   'output_tokens': settings.get('max_tokens') or info.get('max_output_tokens'),
                   'max_turns': settings.get('max_turns')},
        'attempts': config['n_attempts'], 'concurrency': config['n_concurrent_trials'],
        'reward': {'key': args.reward_key, 'solved_if': 'reward >= 1',
                   'implementation': 'each task\'s tests directory; see tests_sha256'},
        'unset_values': 'null means not set here: the agent\'s or model provider\'s default applies',
        'tasks': [task_settings(task) for task in tasks],
    }
