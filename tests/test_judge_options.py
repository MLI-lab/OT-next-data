"""Judge transport, solver isolation and credential persistence regressions."""
import json
import os
from pathlib import Path

import pytest

from validation.stages.runner import parser, resolve_review_defaults
from validation.stages.judge import normalize, review_args, credentials


def arguments(*flags):
    return parser().parse_args(list(flags))


@pytest.mark.parametrize('mode', ['api', 'cli', 'serve', 'local'])
def test_modes_keep_solver_model_and_endpoint(mode):
    flags = ['--judge-mode', mode, '--judge-model', 'org/model' if mode in ('serve', 'local') else 'openai/judge',
             '--model', 'openai/solver', '--api-base', 'http://solver/v1']
    if mode == 'cli':
        flags += ['--judge-cli', 'codex']
    if mode == 'api':
        flags += ['--judge-api-base', 'http://judge/v1']
    args = arguments(*flags)
    normalize(args)
    normalize(args)  # replay and per-stage checks must be idempotent
    if mode in ('serve', 'local'):
        args._local_server_ready = True
        args.judge_api_base = 'http://judge/v1'
    judged = review_args(args)
    assert args.model == 'openai/solver'
    assert args.api_base == 'http://solver/v1'
    assert judged.api_base == (None if mode == 'cli' else 'http://judge/v1')
    assert args.review_agent == ('codex' if mode == 'cli' else 'mini-swe-agent')
    assert args.analysis_model == args.review_model


@pytest.mark.parametrize('flags', [
    ['--judge-model', 'x'],
    ['--judge-mode', 'api'],
    ['--judge-mode', 'cli', '--judge-model', 'x'],
    ['--judge-mode', 'api', '--judge-model', 'openai/x', '--judge-cli', 'codex'],
    ['--judge-mode', 'api', '--judge-model', 'openai/x', '--review-local'],
    ['--judge-mode', 'api', '--judge-model', 'openai/x', '--review-model', 'different'],
    ['--judge-mode', 'api', '--judge-model', 'openai/x', '--judge-api-key-env', 'secret-value'],
    ['--judge-mode', 'serve', '--judge-model', 'org/model', '--judge-api-base', 'http://server/v1'],
])
def test_invalid_combinations(flags):
    with pytest.raises(ValueError):
        normalize(arguments(*flags))


def test_legacy_args_unchanged():
    args = arguments('--review-local', '--model', 'openai/legacy', '--api-base', 'http://legacy/v1')
    before = vars(args).copy()
    assert review_args(args) is args
    before.update(timeout_minutes=300, review_grace_minutes=60)
    assert vars(args) == before


def test_hf_resolution_preserves_registered_defaults():
    from config.models import resolve
    key, spec = resolve('Qwen/Qwen3-Coder-30B-A3B-Instruct')
    assert key == 'coder-30b'
    assert spec.sampling
    key, spec = resolve('example/new-model')
    assert key == spec.hf_repo == 'example/new-model'
    assert spec.dtype == 'auto'


def test_credentials_scoped_and_never_serialized(tmp_path, monkeypatch):
    env = tmp_path / '.env'
    env.write_text('MY_KEY=dotenv-secret\nANTHROPIC_API_KEY=file-default\n')
    monkeypatch.setenv('OT_SECRETS_FILE', str(env))
    monkeypatch.setenv('MY_KEY', 'exported-secret')
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'solver-secret')
    args = arguments('--judge-mode', 'api', '--judge-model', 'anthropic/test', '--judge-api-key-env', 'MY_KEY')
    normalize(args)
    before = json.dumps(vars(args), default=str)
    with pytest.raises(RuntimeError):
        with credentials(args, 5):
            assert os.environ['ANTHROPIC_API_KEY'] == 'exported-secret'
            assert json.dumps(vars(args), default=str) == before
            raise RuntimeError('review failed')
    assert os.environ['ANTHROPIC_API_KEY'] == 'solver-secret'
    assert 'secret' not in before
    with credentials(args, 6):
        assert os.environ['ANTHROPIC_API_KEY'] == 'solver-secret'


def test_dotenv_fallback_and_environment_precedence(tmp_path, monkeypatch):
    env = tmp_path / '.env'
    env.write_text('OPENAI_API_KEY=file-secret\n')
    monkeypatch.setenv('OT_SECRETS_FILE', str(env))
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    args = arguments('--judge-mode', 'api', '--judge-model', 'openai/test')
    with credentials(args, 5):
        assert os.environ['OPENAI_API_KEY'] == 'file-secret'
    assert 'OPENAI_API_KEY' not in os.environ
    monkeypatch.setenv('OPENAI_API_KEY', 'exported-secret')
    with credentials(args, 8):
        assert os.environ['OPENAI_API_KEY'] == 'exported-secret'


def test_missing_custom_key_fails(tmp_path, monkeypatch):
    monkeypatch.setenv('OT_SECRETS_FILE', str(tmp_path / 'missing'))
    monkeypatch.delenv('MISSING_JUDGE_KEY', raising=False)
    args = arguments('--judge-mode', 'api', '--judge-model', 'openai/test', '--judge-api-key-env', 'MISSING_JUDGE_KEY')
    with pytest.raises(ValueError, match='missing or empty'):
        with credentials(args, 5):
            pytest.fail('must not start the judge')


def test_judge_job_uses_judge_endpoint_even_with_different_solver_agent(tmp_path):
    from validation.stages.harbor import job_config
    args = arguments('--judge-mode', 'api', '--judge-model', 'openai/judge',
                     '--judge-api-base', 'http://judge/v1', '--api-base', 'http://solver/v1',
                     '--agent', 'claude-code', '--model', 'solver', '--backend', 'docker')
    scoped = review_args(args)
    config = job_config(tmp_path, tmp_path / 'out', scoped, scoped.review_agent, scoped.review_model)
    assert config['agents'][0]['kwargs']['api_base'] == 'http://judge/v1'
    assert config['agents'][0]['model_name'] == 'openai/judge'
    assert args.agent == 'claude-code'
    assert args.api_base == 'http://solver/v1'


def test_served_judge_sampling_is_separate_from_solver():
    args = arguments('--judge-mode', 'serve', '--judge-model', 'coder-30b',
                     '--agent-kwargs', '{"temperature": 0.2}')
    normalize(args)
    args._local_server_ready = True
    args.judge_api_base = 'http://localhost:8000/v1'
    args.judge_agent_kwargs = {'temperature': 0.7}
    assert review_args(args).agent_kwargs == {'temperature': 0.7}
    assert args.agent_kwargs == {'temperature': 0.2}


def test_unregistered_hf_model_passes_serving_configuration():
    from validation.stages.runner import check_args
    from config.models import resolve, serving_agent_kwargs
    args = arguments('--judge-mode', 'serve', '--judge-model', 'example/new-model',
                     '--model', 'openai/solver', '--api-base', 'http://solver/v1',
                     '--network-mode', 'host')
    check_args(args)
    _, spec = resolve(args.serve_model)
    settings = serving_agent_kwargs(spec, args.serve_context, {})
    assert settings['llm_call_kwargs']['max_tokens'] == 8192
    assert 'chat_template_kwargs' not in settings['extra_body']
    assert args.api_base == 'http://solver/v1'


@pytest.mark.parametrize('model', ['glm-5.3', 'zai-org/GLM-5.3', 'openai/glm-5.3', 'hosted_vllm/zai-org/GLM-5.3', 'anthropic/claude-sonnet-5-5'])
def test_glm_judge_defaults(model):
    args = arguments('--judge-mode', 'api', '--judge-model', model)
    normalize(args)
    assert (args.timeout_minutes, args.review_grace_minutes) == (300, 60)


def test_judge_defaults_preserve_explicit_overrides():
    args = arguments('--judge-mode', 'serve', '--judge-model', 'glm-5.3',
                     '--timeout-minutes', '45', '--review-grace-minutes', '20', '--serve-context', '65536')
    normalize(args)
    assert (args.timeout_minutes, args.review_grace_minutes, args.serve_context) == (45, 20, 65536)
    replay = type(args)(**json.loads(json.dumps(vars(args), default=str)))
    normalize(replay)
    assert (replay.timeout_minutes, replay.review_grace_minutes, replay.serve_context) == (45, 20, 65536)


def test_legacy_local_judge_uses_model_defaults():
    args = arguments('--review-local', '--serve-model', 'glm-5.3')
    normalize(args)
    assert (args.timeout_minutes, args.review_grace_minutes, args.serve_context) == (300, 60, 262144)


@pytest.mark.parametrize('model', ['glm-5.3-flash', 'qwen38-27b'])
def test_judges_share_measurement_budget(model):
    args = arguments('--judge-mode', 'serve', '--judge-model', model)
    normalize(args)
    assert (args.timeout_minutes, args.review_grace_minutes, args.serve_context) == (300, 60, 262144)


def test_solver_model_does_not_select_judge_defaults():
    args = arguments('--model', 'openai/glm-5.3')
    normalize(args)
    assert (args.timeout_minutes, args.review_grace_minutes) == (300, 60)


def test_mini_default_and_independent_terminus_override():
    from validation.rubrics.utils.evaluation import JUDGES
    args = arguments('--judge-mode', 'api', '--judge-model', 'openai/test',
                     '--judge-harness', 'terminus-2')
    normalize(args)
    assert args.agent == 'mini-swe-agent'
    assert args.review_agent == review_args(args).agent == 'terminus-2'
    assert {j['agent'] for j in JUDGES if j.get('needs_endpoint')} == {'mini-swe-agent'}


def test_registered_passk_models_support_native_tool_calls():
    from config.models import MODELS
    assert all(model.tool_call_parser for model in MODELS.values())
