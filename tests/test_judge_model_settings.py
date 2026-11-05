"""Judge settings must reach requests, regardless of how the endpoint was started."""
import asyncio
import json

import httpx
import pytest
from openai import AsyncOpenAI

from config.models import judge_agent_kwargs, judge_cli_flags, judge_defaults, resolve, serving_agent_kwargs
from validation.stages.env_review import judge_config
from validation.stages.runner import parser


@pytest.mark.parametrize('model,effort', [
    ('glm-5.3', 'max'), ('glm-5.3-flash', 'max'), ('qwen38-27b', 'xhigh')])
@pytest.mark.parametrize('route', ['serve', 'api', 'experiment'])
def test_judge_settings_reach_http_request(tmp_path, model, effort, route):
    from harbor.agents.terminus_2.terminus_2 import Terminus2
    from harbor.llms.chat import Chat
    spec = resolve(model)[1]
    name = 'openai/' + model if route == 'serve' else 'hosted_vllm/' + spec.hf_repo
    kwargs = serving_agent_kwargs(spec, judge_defaults(model).context, {}) if route == 'serve' else {}
    config = judge_config({'agents': [{'name': 'terminus-2', 'model_name': name, 'kwargs': kwargs}]},
                          parser().parse_args([]))
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={
            'id': 'test', 'object': 'chat.completion', 'created': 1, 'model': 'test',
            'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': 'ok'},
                         'finish_reason': 'stop'}],
            'usage': {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2}})

    async def run():
        agent = Terminus2(logs_dir=tmp_path, model_name='openai/test',
                          api_base='http://test/v1', **config['agents'][0]['kwargs'])
        async with AsyncOpenAI(api_key='test', base_url='http://test/v1',
                               http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond))) as client:
            agent._llm._openai_client = client
            await agent._dispatch_llm_request(Chat(agent._llm), 'hello', None, None)

    asyncio.run(run())
    request = requests[0]
    assert request['chat_template_kwargs'] == {'enable_thinking': True, 'reasoning_effort': effort}
    for key, value in spec.sampling.items():
        assert request[key] == value
    assert request['max_tokens'] == 65536
    assert config['agents'][0]['kwargs']['model_info']['max_output_tokens'] == 65536
    assert config['agents'][0]['kwargs']['model_info']['max_input_tokens'] == 262144


def test_explicit_nested_overrides_survive_defaults_and_reapplication():
    overrides = {'llm_call_kwargs': {'temperature': .3, 'top_p': .8, 'extra_body': {
        'top_k': 7, 'chat_template_kwargs': {'reasoning_effort': 'medium'}}}}
    original = json.dumps(overrides)
    result = judge_agent_kwargs('Qwen/Qwen3.8-27B', 'terminus-2', overrides)
    assert result['llm_call_kwargs']['temperature'] == .3
    assert 'temperature' not in result
    assert result['extra_body']['top_p'] == .8
    assert result['extra_body']['top_k'] == 7
    assert result['extra_body']['chat_template_kwargs'] == {'enable_thinking': True, 'reasoning_effort': 'medium'}
    assert result == judge_agent_kwargs('qwen38-27b', 'terminus-2', result)
    assert json.dumps(overrides) == original


def test_sonnet_cli_and_harbor_share_defaults():
    assert judge_cli_flags('anthropic/claude-sonnet-5-5') == ['--effort', 'medium', '--thinking', 'adaptive']
    config = judge_config({'agents': [{'name': 'claude-code', 'model_name': 'claude-sonnet-5-5', 'kwargs': {}}]})
    assert config['agents'][0]['kwargs']['reasoning_effort'] == 'medium'
    assert config['agents'][0]['kwargs']['thinking'] == 'adaptive'
    assert judge_cli_flags('claude-sonnet-5-5', 'high')[1] == 'high'


def test_serving_template_defaults_do_not_erase_nested_effort_override():
    spec = resolve('qwen38-27b')[1]
    serving = serving_agent_kwargs(spec, 131072, {'llm_call_kwargs': {'extra_body': {
        'chat_template_kwargs': {'reasoning_effort': 'medium'}}}})
    result = judge_agent_kwargs('qwen38-27b', 'terminus-2', serving)
    assert result['extra_body']['chat_template_kwargs'] == {'enable_thinking': True, 'reasoning_effort': 'medium'}


def test_unknown_models_leave_provider_defaults_unset():
    assert judge_agent_kwargs('hosted_vllm/example/new-model', 'terminus-2') == {}
    assert judge_cli_flags('future-claude') == []
    assert judge_defaults('future-claude').reasoning_effort is None
    assert parser().parse_args([]).review_reasoning_effort is None


def test_unsupported_effort_fails_instead_of_silently_using_max():
    with pytest.raises(ValueError, match='GLM-5.3 judges support'):
        judge_agent_kwargs('glm-5.3', 'terminus-2', reasoning_effort='medium')


def test_experiment_contains_only_requested_judges():
    from validation.rubrics.utils.evaluation import JUDGES
    assert {j['id'] for j in JUDGES} == {'sonnet-5.5', 'glm-flash', 'glm-5.3', 'qwen'}


@pytest.mark.parametrize('model', ['glm-5.3', 'glm-5.3-flash', 'qwen38-27b'])
def test_judge_budget_overrides_are_preserved(model):
    result = judge_agent_kwargs(model, 'terminus-2', {
        'llm_call_kwargs': {'max_completion_tokens': 4096},
        'model_info': {'max_input_tokens': 131072}})
    assert result['llm_call_kwargs']['max_tokens'] == 4096
    assert 'max_completion_tokens' not in result['llm_call_kwargs']
    assert result['model_info']['max_output_tokens'] == 4096
    assert result['model_info']['max_input_tokens'] == 131072


def test_sonnet_keeps_cli_token_defaults():
    result = judge_agent_kwargs('claude-sonnet-5-5', 'claude-code')
    assert 'model_info' not in result
    assert 'max_tokens' not in result
    assert 'llm_call_kwargs' not in result
