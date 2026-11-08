"""Exercise the actual pinned MiniSWE loop; mock only inference and the container."""
import asyncio
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

import minisweagent  # Required pinned runtime dependency.
import litellm
from harbor.models.agent.context import AgentContext
from harbor_patches.mini_swe import MiniSweAgent, request_kwargs
from harbor_patches.terminus_session import active, Policy
from config.models import judge_agent_kwargs


def response(command):
    return litellm.ModelResponse(choices=[{'finish_reason': 'tool_calls', 'message': {
        'role': 'assistant', 'content': 'Executing the next command.',
        'reasoning_content': 'Retained reasoning.', 'tool_calls': [{
            'id': 'call1', 'type': 'function',
            'function': {'name': 'bash', 'arguments': json.dumps({'command': command})}}]}}],
        usage={'prompt_tokens': 20, 'completion_tokens': 10,
               'completion_tokens_details': {'reasoning_tokens': 7}})


class Environment:
    def __init__(self):
        self.commands = []
        self.verdict = ''

    async def exec(self, command, **kwargs):
        self.commands.append(command)
        if command == 'cat /app/verdicts.json':
            return SimpleNamespace(stdout=self.verdict, stderr='', return_code=0 if self.verdict else 1)
        if command == 'write verdict':
            self.verdict = '{"valid": true}'
        return SimpleNamespace(stdout=command.removeprefix('echo '), stderr='', return_code=0)


def validate(text):
    assert json.loads(text)['valid']


def test_native_loop_config_reasoning_metrics_and_task_environment(tmp_path, monkeypatch):
    calls = []
    async def completion(model, messages, kwargs):
        kwargs = dict(kwargs, model=model, messages=messages)
        calls.append(deepcopy(kwargs))
        return response('echo inspecting' if len(calls) == 1 else 'echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT')
    monkeypatch.setattr('harbor_patches.mini_swe.completion', completion)
    monkeypatch.setenv('TOP_SECRET_HOST_VARIABLE', 'never-show-this')
    settings = judge_agent_kwargs('glm-5.3', 'mini-swe-agent')
    agent = MiniSweAgent(logs_dir=tmp_path, model_name='openai/test', **settings)
    context, environment = AgentContext(), Environment()
    asyncio.run(agent.run('Inspect the task.', environment, context))
    assert len(environment.commands) == 2
    assert calls[0]['max_tokens'] == 65536
    assert calls[0]['extra_body']['chat_template_kwargs'] == {'enable_thinking': True, 'reasoning_effort': 'max'}
    assistant = next(m for m in calls[1]['messages'] if m['role'] == 'assistant')
    assert assistant['reasoning_content'] == 'Retained reasoning.'
    assert 'extra' not in assistant
    assert 'never-show-this' not in json.dumps(calls)
    assert context.n_input_tokens == 40 and context.n_output_tokens == 20
    trajectory = json.loads((tmp_path / 'trajectory.json').read_text())
    assert [s for s in trajectory['steps'] if s['source'] == 'agent'][0]['reasoning_content'] == 'Retained reasoning.'
    assert trajectory['final_metrics']['extra']['total_reasoning_tokens'] == 14


def test_judge_rejects_missing_verdict_then_corrects_same_session(tmp_path, monkeypatch):
    calls = []
    commands = iter(['echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT', 'write verdict',
                     'echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT'])
    async def completion(model, messages, kwargs):
        kwargs = dict(kwargs, model=model, messages=messages)
        calls.append(deepcopy(kwargs))
        return response(next(commands))
    monkeypatch.setattr('harbor_patches.mini_swe.completion', completion)
    agent = MiniSweAgent(logs_dir=tmp_path, model_name='openai/test')
    context = AgentContext()
    with active(Policy(review_sec=60, grace_sec=60, validate=validate)):
        asyncio.run(agent.run('Judge.', Environment(), context))
    assert len(calls) == 3
    assert any('not valid' in str(m['content']) for m in calls[1]['messages'] if m['role'] == 'tool')
    assert context.metadata['review_session']['completion_rejections'] == 1


def test_judge_timeout_delivers_finish_message(tmp_path, monkeypatch):
    calls = []
    async def completion(model, messages, kwargs):
        kwargs = dict(kwargs, model=model, messages=messages)
        calls.append(deepcopy(kwargs))
        if len(calls) == 1:
            await asyncio.sleep(.05)
            return response('write verdict')
        return response('echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT')
    monkeypatch.setattr('harbor_patches.mini_swe.completion', completion)
    agent = MiniSweAgent(logs_dir=tmp_path, model_name='openai/test')
    context = AgentContext()
    with active(Policy(review_sec=.01, grace_sec=1, validate=validate)):
        asyncio.run(agent.run('Judge.', Environment(), context))
    assert any('Do not investigate further' in str(m['content']) for m in calls[1]['messages'])
    assert context.metadata['review_session']['time_limit_message'] is True


def test_cancellation_stops_pending_inference_and_never_runs_commands(tmp_path, monkeypatch):
    cancelled = []
    async def completion(model, messages, kwargs):
        kwargs = dict(kwargs, model=model, messages=messages)
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.append(True)
            raise
    monkeypatch.setattr('harbor_patches.mini_swe.completion', completion)
    agent = MiniSweAgent(logs_dir=tmp_path, model_name='openai/test')
    environment, context = Environment(), AgentContext()
    async def run():
        with pytest.raises(TimeoutError):
            async with asyncio.timeout(.1):
                await agent.run('Task.', environment, context)
        await asyncio.sleep(.01)
    asyncio.run(run())
    assert cancelled == [True]
    assert environment.commands == []


def test_shared_overrides_and_unknown_model_defaults():
    overrides = {'temperature': .2, 'llm_call_kwargs': {'max_tokens': 1000},
                 'extra_body': {'chat_template_kwargs': {'reasoning_effort': 'high'}}}
    settings = request_kwargs(judge_agent_kwargs('glm-5.3', 'mini-swe-agent', overrides))
    assert settings['temperature'] == .2 and settings['max_tokens'] == 1000
    assert settings['extra_body']['chat_template_kwargs']['reasoning_effort'] == 'high'
    assert request_kwargs(judge_agent_kwargs('openai/unknown', 'mini-swe-agent')) == {}


@pytest.mark.parametrize('prefix', ['openai/', 'hosted_vllm/'])
def test_local_http_preserves_reasoning_and_budget(tmp_path, monkeypatch, prefix):
    import httpx
    import openai
    from harbor_patches.mini_swe import completion
    bodies = []
    async def handler(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=response('echo done').model_dump())
    client = openai.AsyncOpenAI(api_key='test', base_url='http://example/v1',
                               http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(openai, 'AsyncOpenAI', lambda **kwargs: client)
    settings = request_kwargs(judge_agent_kwargs('glm-5.3', 'mini-swe-agent'))
    settings['api_base'] = 'http://example/v1'
    settings['tools'] = [{'type': 'function', 'function': {'name': 'bash', 'parameters': {'type': 'object'}}}]
    history = [{'role': 'user', 'content': 'Task'},
               {'role': 'assistant', 'content': 'Proceeding', 'reasoning_content': 'Keep this thinking.'}]
    result = asyncio.run(completion(prefix + 'glm', history, settings))
    assert bodies[0]['messages'][-1]['reasoning_content'] == 'Keep this thinking.'
    assert bodies[0]['max_tokens'] == 65536
    assert bodies[0]['chat_template_kwargs']['reasoning_effort'] == 'max'
    assert bodies[0]['model'] == 'glm' and bodies[0]['tools']
    assert result.usage.completion_tokens == 10


@pytest.mark.parametrize('override,env,expected', [
    (None, None, 3600.0), (120, None, 120.0), (None, '900', 900.0)])
def test_local_http_request_timeout_default_and_overrides(monkeypatch, override, env, expected):
    import httpx
    import openai
    from harbor_patches.mini_swe import completion
    created = []
    real = openai.AsyncOpenAI
    async def handler(request):
        return httpx.Response(200, json=response('echo done').model_dump())
    def factory(**kwargs):
        created.append(kwargs)
        return real(api_key='test', base_url='http://example/v1',
                                  http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(openai, 'AsyncOpenAI', factory)
    if env:
        monkeypatch.setenv('OT_MODEL_REQUEST_TIMEOUT_SEC', env)
    else:
        monkeypatch.delenv('OT_MODEL_REQUEST_TIMEOUT_SEC', raising=False)
    settings = {'api_base': 'http://example/v1', 'max_tokens': 8}
    if override:
        settings['timeout'] = override
    asyncio.run(completion('openai/glm', [{'role': 'user', 'content': 'Task'}], settings))
    assert created[0]['timeout'] == expected and created[0]['max_retries'] == 0


def test_factory_and_record_use_host_adapter(monkeypatch):
    from harbor.agents.factory import AgentFactory
    from harbor.models.agent.name import AgentName
    from harbor_patches.mini_swe import install
    from validation.metrics.agent_run import agent_settings
    monkeypatch.setattr(AgentFactory, '_AGENT_MAP', dict(AgentFactory._AGENT_MAP))
    install()
    assert AgentFactory._AGENT_MAP[AgentName.MINI_SWE_AGENT] is MiniSweAgent
    recorded = agent_settings('mini-swe-agent', {'max_turns': 2})
    assert recorded['version'] == '2.4.6'
    assert recorded['settings']['max_turns'] == 2
    assert 'config/mini.yaml' in recorded['source_sha256']
    assert 'ot/mini_swe.py' in recorded['source_sha256']


def test_turn_cap_is_not_successful_submission(tmp_path, monkeypatch):
    from harbor.agents.base import TurnCapExhaustedError
    async def completion(model, messages, kwargs):
        return response('echo still working')
    monkeypatch.setattr('harbor_patches.mini_swe.completion', completion)
    agent = MiniSweAgent(logs_dir=tmp_path, model_name='openai/test', max_turns=1)
    with pytest.raises(TurnCapExhaustedError):
        asyncio.run(agent.run('Task.', Environment(), AgentContext()))


@pytest.mark.parametrize('artifact', ['', '{broken json', '{"checks": {}}'])
def test_invalid_verdict_correction_is_bounded(tmp_path, monkeypatch, artifact):
    calls = []
    async def completion(model, messages, kwargs):
        calls.append(messages)
        return response('echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT')
    def validator(raw):
        parsed = json.loads(raw)
        if 'criterion' not in parsed.get('checks', {}):
            raise ValueError('Missing criterion')
    monkeypatch.setattr('harbor_patches.mini_swe.completion', completion)
    environment, context = Environment(), AgentContext()
    environment.verdict = artifact
    agent = MiniSweAgent(logs_dir=tmp_path, model_name='openai/test')
    with active(Policy(review_sec=60, grace_sec=60, validate=validator)):
        with pytest.raises(ValueError, match='verdict remains invalid'):
            asyncio.run(agent.run('Judge.', environment, context))
    assert len(calls) == 2
    assert context.metadata['review_session']['completion_rejections'] == 1


def test_judge_final_answer_deadline_is_enforced(tmp_path, monkeypatch):
    from harbor.trial.errors import AgentTimeoutError
    calls = []
    async def completion(model, messages, kwargs):
        calls.append(deepcopy(messages))
        if len(calls) == 1:
            await asyncio.sleep(.03)
            return response('echo inspecting')
        await asyncio.sleep(10)
    monkeypatch.setattr('harbor_patches.mini_swe.completion', completion)
    agent = MiniSweAgent(logs_dir=tmp_path, model_name='openai/test')
    context = AgentContext()
    with active(Policy(review_sec=.01, grace_sec=.1, validate=validate)):
        with pytest.raises(AgentTimeoutError):
            asyncio.run(agent.run('Judge.', Environment(), context))
    assert context.n_output_tokens == 10
    assert context.metadata['review_session']['events'][-1]['kind'] == 'grace_timeout'


def test_command_timeout_is_an_observation(tmp_path, monkeypatch):
    calls = []
    async def completion(model, messages, kwargs):
        calls.append(deepcopy(messages))
        return response('slow command' if len(calls) == 1 else 'echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT')
    class SlowEnvironment(Environment):
        async def exec(self, command, **kwargs):
            if command == 'slow command':
                raise TimeoutError()
            return await super().exec(command, **kwargs)
    monkeypatch.setattr('harbor_patches.mini_swe.completion', completion)
    agent = MiniSweAgent(logs_dir=tmp_path, model_name='openai/test')
    asyncio.run(agent.run('Task.', SlowEnvironment(), AgentContext()))
    assert 'Command timed out after 30 seconds' in calls[1][-1]['content']


def test_36_agents_leave_default_executor_available_for_io(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import threading

    async def exercise():
        # Keep the shared I/O pool small: agents must not consume its slots.
        asyncio.get_running_loop().set_default_executor(ThreadPoolExecutor(max_workers=4))
        ready = asyncio.Event()
        started = 0
        io_threads = []

        async def completion(model, messages, kwargs):
            nonlocal started
            started += 1
            if started == 36:
                ready.set()
            await ready.wait()
            io_threads.append(await asyncio.to_thread(lambda: threading.current_thread().name))
            return response('echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT')

        class ThreadedEnvironment(Environment):
            async def exec(self, command, **kwargs):
                await asyncio.to_thread(lambda: None)  # bridge HTTP transport
                return await super().exec(command, **kwargs)

        monkeypatch.setattr('harbor_patches.mini_swe.completion', completion)
        agents = [MiniSweAgent(logs_dir=tmp_path / str(i), model_name='openai/test') for i in range(36)]
        async with asyncio.timeout(15):
            await asyncio.gather(*(a.run('Judge.', ThreadedEnvironment(), AgentContext()) for a in agents))
        assert started == len(io_threads) == 36
        assert not any(name.startswith('mini-swe') for name in io_threads)
        assert all((tmp_path / str(i) / 'trajectory.json').exists() for i in range(36))

    asyncio.run(exercise())


def test_task_oom_in_judge_command_is_feedback_not_failure(tmp_path, monkeypatch):
    from harbor_patches.oom_recovery import TERMINAL, RecoveredTaskOOM
    from harbor_patches.protected_step import FEEDBACK
    commands = iter(['echo big', 'write verdict', 'echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT'])
    seen = []
    async def completion(model, messages, kwargs):
        seen.append(deepcopy(messages))
        return response(next(commands))
    class OOMEnvironment(Environment):
        async def exec(self, command, **kwargs):
            if command == 'echo big':
                assert TERMINAL.get() is True
                raise RecoveredTaskOOM(FEEDBACK)
            if command.startswith('cat '):
                assert TERMINAL.get() is False  # verdict reads are not agent commands
            return await super().exec(command, **kwargs)
    monkeypatch.setattr('harbor_patches.mini_swe.completion', completion)
    agent = MiniSweAgent(logs_dir=tmp_path, model_name='openai/test')
    context = AgentContext()
    with active(Policy(review_sec=60, grace_sec=60, validate=validate)):
        asyncio.run(agent.run('Judge.', OOMEnvironment(), context))
    assert any('TASK_MEMORY_LIMIT: Task memory limit exceeded' in str(m['content'])
               for m in seen[1] if m['role'] == 'tool')
    assert [e['kind'] for e in context.metadata['review_session']['events']] == ['task_oom_recovered']


def test_context_overflow_halves_reply_budget_and_asks_for_verdict(tmp_path, monkeypatch):
    import httpx, openai
    calls = []
    def big(command, prompt):
        r = response(command); r.usage.prompt_tokens = prompt; return r
    async def completion(model, messages, kwargs):
        calls.append(deepcopy(kwargs))
        if len(calls) == 1:
            return big('echo inspecting', 700)  # 70 % of a 1,000-token context
        if kwargs['max_tokens'] > 200:
            raise openai.BadRequestError("This model's maximum context length is 1000 tokens.",
                response=httpx.Response(400, request=httpx.Request('POST', 'http://x')), body=None)
        return big('write verdict' if len(calls) < 6 else 'echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT', 900)
    monkeypatch.setattr('harbor_patches.mini_swe.completion', completion)
    monkeypatch.setattr('harbor_patches.mini_swe.MIN_REPLY_TOKENS', 100)
    agent = MiniSweAgent(logs_dir=tmp_path, model_name='openai/test', max_tokens=800,
                         model_info={'max_input_tokens': 1000})
    context = AgentContext()
    with active(Policy(review_sec=60, grace_sec=60, validate=validate)):
        asyncio.run(agent.run('Judge.', Environment(), context))
    assert [c['max_tokens'] for c in calls[:4]] == [800, 800, 400, 200]
    events = [e['kind'] for e in context.metadata['review_session']['events']]
    assert events[:3] == ['context_limit_message', 'reply_budget_reduced', 'reply_budget_reduced']
    assert events.count('context_limit_message') == 1


def test_context_overflow_below_minimum_reply_budget_still_fails(tmp_path, monkeypatch):
    import httpx, openai
    async def completion(model, messages, kwargs):
        raise openai.BadRequestError("This model's maximum context length is 1000 tokens.",
            response=httpx.Response(400, request=httpx.Request('POST', 'http://x')), body=None)
    monkeypatch.setattr('harbor_patches.mini_swe.completion', completion)
    agent = MiniSweAgent(logs_dir=tmp_path, model_name='openai/test', max_tokens=8192)
    with pytest.raises(Exception):
        with active(Policy(review_sec=60, grace_sec=60, validate=validate)):
            asyncio.run(agent.run('Judge.', Environment(), AgentContext()))
