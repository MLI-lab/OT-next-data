"""Host-side MiniSWE v2 loop with commands confined to Harbor's task environment.

No agent packages are installed in task images. Native tool calls and reasoning
stay in MiniSWE's history; Harbor retains ownership of setup and hard deadlines.
"""
from __future__ import annotations

import asyncio
from concurrent.futures import CancelledError, ThreadPoolExecutor
from contextvars import copy_context
from copy import deepcopy
from importlib.metadata import version
import json
import os
from pathlib import Path
import shlex
import threading
import time

from harbor.agents.base import BaseAgent, TurnCapExhaustedError
from harbor_patches.oom_recovery import TERMINAL, RecoveredTaskOOM
from harbor_patches.terminus_session import POLICY, TIME_LIMIT, INVALID_VERDICT, _minutes


class Stopped(BaseException):
    """Cancellation must bypass MiniSWE's API retries and error recovery."""


class Bridge:
    def __init__(self):
        self.loop = asyncio.get_running_loop()
        self.stopped = threading.Event()
        self.pending = set()
        self.lock = threading.Lock()

    def call(self, coroutine):
        with self.lock:
            if self.stopped.is_set():
                coroutine.close()
                raise Stopped()
            future = asyncio.run_coroutine_threadsafe(coroutine, self.loop)
            self.pending.add(future)
        try:
            return future.result()
        except CancelledError:
            raise Stopped() from None
        finally:
            with self.lock:
                self.pending.discard(future)

    def stop(self):
        with self.lock:
            self.stopped.set()
            for future in self.pending:
                future.cancel()


def request_kwargs(kwargs):
    """Translate the shared model configuration without inventing sampling defaults."""
    result = deepcopy(kwargs.get('llm_call_kwargs') or {})
    for key in ('api_base', 'temperature', 'top_p', 'max_tokens', 'reasoning_effort'):
        if key in kwargs:
            result.setdefault(key, kwargs[key])
    extra = dict(result.pop('extra_body', {}) or {})
    extra.update(deepcopy(kwargs.get('extra_body') or {}))
    if extra:
        result['extra_body'] = extra
    return result


# Per-request limit for one model call. The OpenAI SDK default (600 s) aborted
# long reasoning turns on slow local servers, and each MiniSWE retry restarted
# the turn from scratch. The review session limit stays the binding deadline.
REQUEST_TIMEOUT_SEC = 3600.0

# A judge that keeps exploring eventually fills the context and every request
# fails. Ask for the verdict once the prompt passes this share of the context,
# and halve the reply budget (down to MIN_REPLY_TOKENS) when the server says
# prompt plus reply no longer fit.
CONTEXT_WARNING_FRACTION = 0.7
MIN_REPLY_TOKENS = 4096
CONTEXT_LIMIT = ('The conversation is close to the model context limit ({used:,} of {limit:,} tokens). '
                 'Do not investigate further. Write {artifact} now from the evidence you have, '
                 'then mark the task complete.')


def request_timeout(options):
    value = options.pop('timeout', None) or os.environ.get('OT_MODEL_REQUEST_TIMEOUT_SEC')
    return float(value) if value else REQUEST_TIMEOUT_SEC


async def completion(model, messages, kwargs):
    """Use native OpenAI transport for local endpoints: LiteLLM strips reasoning there."""
    import litellm
    from openai import AsyncOpenAI
    if model.startswith(('hosted_vllm/', 'openai/')):
        options = deepcopy(kwargs)
        base = options.pop('api_base', None) or os.environ.get('OPENAI_API_BASE') or os.environ.get('OPENAI_BASE_URL')
        key = os.environ.get('OPENAI_API_KEY') or ('EMPTY' if base else None)
        timeout = request_timeout(options)
        async with AsyncOpenAI(base_url=base, api_key=key, max_retries=0, timeout=timeout) as client:
            response = await client.chat.completions.create(
                model=model.split('/', 1)[1], messages=messages, **options)
        from harbor_patches.reasoning_field import with_reasoning_content
        return litellm.ModelResponse(**with_reasoning_content(response.model_dump()))
    return await litellm.acompletion(model=model, messages=messages, **kwargs)


class MiniSweAgent(BaseAgent):
    SUPPORTS_ATIF = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.options = kwargs
        self.mini = None

    @staticmethod
    def name():
        return 'mini-swe-agent'

    def version(self):
        return version('mini-swe-agent')

    async def setup(self, environment):
        # Check host dependencies before running. Never pip-install inside task images.
        from minisweagent.agents.default import DefaultAgent  # noqa: F401
        if self.mcp_servers or self.skills_dir:
            raise ValueError('MiniSWE does not support MCP servers or injected skills')

    def _save(self, context, data=None):
        if self.mini is None:
            return
        from harbor.agents.installed.mini_swe_agent import convert_mini_swe_agent_to_atif
        data = data if data is not None else deepcopy(self.mini.serialize())
        (self.logs_dir / 'mini.trajectory.json').write_text(json.dumps(data, indent=2))
        trajectory = convert_mini_swe_agent_to_atif(data, str(self.logs_dir))
        messages = [m for m in data['messages'] if m.get('role') == 'assistant']
        steps = [s for s in trajectory.steps if s.source == 'agent']
        for step, message in zip(steps, messages):
            step.reasoning_content = message.get('reasoning_content') or message.get('reasoning')
        (self.logs_dir / 'trajectory.json').write_text(trajectory.model_dump_json(indent=2))
        metrics = trajectory.final_metrics
        context.n_input_tokens = metrics.total_prompt_tokens
        context.n_output_tokens = metrics.total_completion_tokens
        context.n_cache_tokens = metrics.total_cached_tokens or 0
        context.cost_usd = metrics.total_cost_usd

    async def run(self, instruction, environment, context):
        import openai
        import minisweagent
        import yaml
        from minisweagent.agents.default import DefaultAgent
        from minisweagent.environments.local import LocalEnvironment
        from minisweagent.models.litellm_model import LitellmModel
        from minisweagent.models.utils.actions_toolcall import BASH_TOOL

        bridge = Bridge()
        policy = POLICY.get()
        started = time.monotonic()
        state = {'time_limit_message': False, 'completion_rejections': 0, 'events': [],
                 'context_message': False, 'prompt_tokens': 0}
        context_limit = (self.options.get('model_info') or {}).get('max_input_tokens')
        deadline = None
        owner = self
        defaults = yaml.safe_load((Path(minisweagent.__file__).parent / 'config/mini.yaml').read_text())

        def event(kind, **details):
            state['events'].append({'kind': kind, 'elapsed_sec': round(time.monotonic() - started, 1), **details})

        async def before_query(agent):
            if policy and not state['time_limit_message'] and time.monotonic() - started >= policy.review_sec:
                state['time_limit_message'] = True
                deadline.reschedule(asyncio.get_running_loop().time() + policy.grace_sec)
                event('time_limit_message')
                agent.add_messages(agent.model.format_message(role='user', content=TIME_LIMIT.format(
                    artifact=policy.artifact, minutes=_minutes(policy.grace_sec))))
            used = state['prompt_tokens']
            if (policy and context_limit and not state['context_message']
                    and used >= CONTEXT_WARNING_FRACTION * context_limit):
                state['context_message'] = True
                event('context_limit_message', prompt_tokens=used)
                agent.add_messages(agent.model.format_message(role='user', content=CONTEXT_LIMIT.format(
                    artifact=policy.artifact, used=used, limit=context_limit)))

        async def check_verdict():
            if not policy or not policy.validate:
                return None
            read = await environment.exec(f'cat {shlex.quote(policy.artifact)}', timeout_sec=60)
            if read.return_code:
                return f'the file could not be read ({(read.stderr or "missing").strip()[:300]})'
            try:
                policy.validate(read.stdout or '')
            except ValueError as exc:
                return str(exc)
            return None

        class Model(LitellmModel):
            abort_exceptions = [*LitellmModel.abort_exceptions, Stopped, openai.AuthenticationError,
                                openai.PermissionDeniedError, openai.BadRequestError, openai.NotFoundError]

            def _query(self, messages, **kwargs):
                options = self.config.model_kwargs | kwargs
                async def request(limit):
                    response = await completion(self.config.model_name, messages,
                        {**options, **({'max_tokens': limit} if limit else {}), "tools": [BASH_TOOL]})
                    # vLLM versions use either name. Keep reasoning in native history.
                    message = response.choices[0].message
                    if not getattr(message, 'reasoning_content', None) and getattr(message, 'reasoning', None):
                        message.reasoning_content = message.reasoning
                    used = getattr(getattr(response, 'usage', None), 'prompt_tokens', None)
                    if used:
                        state['prompt_tokens'] = used
                    return response
                limit = None
                while True:
                    try:
                        return bridge.call(request(limit))
                    except openai.BadRequestError as exc:
                        current = limit or options.get('max_tokens')
                        if 'maximum context length' not in str(exc) or not current or current // 2 < MIN_REPLY_TOKENS:
                            raise
                        limit = current // 2
                        event('reply_budget_reduced', max_tokens=limit)

        class Environment(LocalEnvironment):
            def execute(self, action, cwd='', *, timeout=None):
                command_limit = timeout or owner.options.get('command_timeout_sec', 30)

                async def agent_command():
                    # Mark the command as an agent terminal interaction, so a task OOM
                    # restarts the container processes and returns feedback (as for
                    # Terminus) instead of ending the review.
                    TERMINAL.set(True)
                    return await environment.exec(action.get('command', ''), cwd=cwd or None,
                                                  timeout_sec=command_limit)
                try:
                    result = bridge.call(agent_command())
                except TimeoutError:
                    return {'output': '', 'returncode': -1,
                            'exception_info': f'Command timed out after {command_limit} seconds.'}
                except RecoveredTaskOOM as exc:
                    event('task_oom_recovered')
                    return {'output': 'TASK_MEMORY_LIMIT: ' + str(exc), 'returncode': 137, 'exception_info': ''}
                output = {'output': (result.stdout or '') + (result.stderr or ''),
                          'returncode': result.return_code, 'exception_info': ''}
                lines = output['output'].lstrip().splitlines()
                finishing = lines and lines[0].strip() == 'COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT' and not result.return_code
                if finishing:
                    error = bridge.call(check_verdict())
                    if error:
                        if state['completion_rejections'] >= policy.max_rejections:
                            raise ValueError(f'Judge verdict remains invalid: {error}')
                        state['completion_rejections'] += 1
                        event('completion_rejected', error=error[:1000])
                        return {'output': INVALID_VERDICT.format(artifact=policy.artifact, error=error),
                                'returncode': 1, 'exception_info': ''}
                self._check_finished(output)
                return output

            def get_template_vars(self, **kwargs):
                # Never expose host environment variables (including API credentials).
                return {'system': 'Linux', 'release': '', 'version': '', 'machine': '', **kwargs}

            def serialize(self):
                return {'info': {'config': {'environment_type': 'harbor-task-environment'}}}

        async def persist(data):
            owner._save(context, data)

        class Agent(DefaultAgent):
            def query(self):
                bridge.call(before_query(self))
                return super().query()

            def save(self, path, *extra_dicts):
                data = self.serialize(*extra_dicts)
                bridge.call(persist(data))
                return data

        model_options = {k: defaults['model'][k] for k in ('observation_template', 'format_error_template')}
        model = Model(model_name=self.model_name, model_kwargs=request_kwargs(self.options),
                      cost_tracking='ignore_errors', **model_options)
        agent_options = defaults['agent']
        agent_options.pop('mode', None)
        agent_options.update(cost_limit=0, step_limit=self.options.get('max_turns', 0),
                             output_path=self.logs_dir / 'mini.trajectory.json')
        # Keep upstream tool/finish instructions, with a task-neutral introduction.
        agent_options['instance_template'] = agent_options['instance_template'].replace(
            'Please solve this issue: {{task}}', '{{task}}')
        self.mini = Agent(model, Environment(), **agent_options)
        self.logs_dir.mkdir(parents=True, exist_ok=True)

        def work():
            try:
                return self.mini.run(instruction)
            except Stopped:
                return {'exit_status': 'Cancelled'}

        # Each synchronous agent waits on async I/O through Bridge.call(). It
        # must not occupy the default executor needed by that I/O (including
        # bridge HTTP calls and DNS), or 32+ agents can deadlock the entire loop.
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='mini-swe')
        worker = asyncio.get_running_loop().run_in_executor(executor, copy_context().run, work)
        try:
            if policy:
                async with asyncio.timeout(policy.review_sec + policy.grace_sec) as deadline:
                    result = await asyncio.shield(worker)
            else:
                result = await asyncio.shield(worker)
            if result.get('exit_status') == 'LimitsExceeded':
                raise TurnCapExhaustedError('MiniSWE reached its turn limit')
            if result.get('exit_status') != 'Submitted':
                raise RuntimeError(f'MiniSWE ended without submitting: {result.get("exit_status")}')
        except TimeoutError as exc:
            if deadline is None or not deadline.expired():
                raise
            from harbor.trial.errors import AgentTimeoutError
            event('grace_timeout' if state['time_limit_message'] else 'finish_instruction_timeout')
            raise AgentTimeoutError('MiniSWE judge exceeded its session time limit') from exc
        finally:
            bridge.stop()
            executor.shutdown(wait=False, cancel_futures=True)
            # Bridge.stop releases blocked calls before we read the final history.
            await asyncio.shield(asyncio.gather(worker, return_exceptions=True))
            # The worker cannot issue further API/environment requests after stop().
            # Backfill even on cancellation; native logs are also written after each step.
            self._save(context)
            context.metadata = dict(context.metadata or {}, mini_swe={
                'version': minisweagent.__version__, 'interleaved_thinking': True,
                'model_info': self.options.get('model_info'), 'exit_status':
                (self.mini.messages[-1].get('extra', {}).get('exit_status') if self.mini.messages else None)})
            if policy:
                context.metadata['review_session'] = dict(state, review_sec=policy.review_sec,
                    grace_sec=policy.grace_sec, finish_wait_sec=policy.grace_sec,
                    grace_starts='finish_instruction_sent')


def install():
    from harbor.agents.factory import AgentFactory
    from harbor.models.agent.name import AgentName
    AgentFactory._AGENT_MAP[AgentName.MINI_SWE_AGENT] = MiniSweAgent
