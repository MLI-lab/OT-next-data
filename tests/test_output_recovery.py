import asyncio
import logging
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest
from harbor.llms.base import LLMResponse, OutputLengthExceededError
from harbor.agents.terminus_2.terminus_2 import Terminus2
from harbor_patches import output_recovery as patch


@pytest.fixture
def agent(monkeypatch):
    for name in ('_query_llm', '_recover_output_overflow', '_reset_per_run_state'):
        monkeypatch.setattr(Terminus2, name, getattr(Terminus2, name))
    monkeypatch.setattr(Terminus2, '_ot_output_recovery', False, raising=False)
    patch.install()
    obj = object.__new__(Terminus2)
    obj.logger = logging.getLogger('output-recovery-test')
    obj._parser = NS(salvage_truncated_response=lambda _: None,
                     parse_response=lambda text: NS(error=None if text == 'valid' else 'incomplete', warning=None))
    obj._llm = NS(get_model_output_limit=lambda: 8192)
    obj._check_proactive_summarization = AsyncMock(return_value=None)
    obj._dispatch_llm_request = AsyncMock()
    obj._ot_output_truncations = 0
    return obj


def context():
    return NS(original_instruction='solve task', session=object(),
              logging_paths=NS(debug=None, prompt=None, response=None))


class Chat:
    def __init__(self):
        self.saved = []
    def append_response(self, prompt, response):
        self.saved.append((prompt, response.content))
    def reset_provider_response_chain(self):
        pass


def overflow():
    return OutputLengthExceededError('too long', LLMResponse(content='partial'))


def test_three_unusable_outputs_stop_without_fourth_request(agent):
    agent._dispatch_llm_request.side_effect = [overflow(), overflow(), overflow()]
    chat = Chat()
    with pytest.raises(patch.RepeatedOutputTruncationError):
        asyncio.run(agent._query_llm(chat, 'initial', context()))
    assert agent._dispatch_llm_request.await_count == 3
    assert agent._check_proactive_summarization.await_count == 2
    assert len(chat.saved) == 3
    assert agent._stop_reason == 'repeated_output_truncation'


def test_summary_replaces_retry_prompt_and_preserves_counter(agent):
    agent._check_proactive_summarization.return_value = ('summary handoff', ['ref'])
    agent._dispatch_llm_request.side_effect = [overflow(), overflow(), overflow()]
    with pytest.raises(patch.RepeatedOutputTruncationError):
        asyncio.run(agent._query_llm(Chat(), 'initial', context()))
    assert [c.args[1] for c in agent._dispatch_llm_request.await_args_list] == [
        'initial', 'summary handoff', 'summary handoff']
    assert agent._ot_output_truncations == 3
    assert agent._pending_handoff_prompt == 'summary handoff'
    assert agent._pending_subagent_refs == ['ref']


def test_valid_response_resets_counter(agent):
    agent._dispatch_llm_request.side_effect = [overflow(), overflow(), LLMResponse('valid'),
                                              overflow(), overflow(), LLMResponse('valid')]
    async def run():
        for _ in range(2):
            result = await agent._query_llm(Chat(), 'initial', context())
            assert result.content == 'valid'
            assert agent._ot_output_truncations == 0
    asyncio.run(run())
    assert agent._dispatch_llm_request.await_count == 6


def test_salvaged_response_preserves_response_type_and_resets_counter(agent):
    agent._ot_output_truncations = 2
    agent._parser.salvage_truncated_response = lambda _: 'valid'
    agent._dispatch_llm_request.side_effect = [overflow()]
    result = asyncio.run(agent._query_llm(Chat(), 'initial', context()))
    assert isinstance(result, LLMResponse) and result.content == 'valid'
    assert agent._ot_output_truncations == 0


def test_verifier_still_runs_after_output_stop(monkeypatch):
    from harbor.trial.single_step import SingleStepTrial
    trial = object.__new__(SingleStepTrial)
    monkeypatch.setattr(SingleStepTrial, 'result', property(lambda _: NS()))
    # Isolate the actual exception handling in SingleStepTrial._run_agent.
    monkeypatch.setattr(SingleStepTrial, 'task', property(lambda _: NS(
        instruction='task', config=NS(agent=NS(user=None)))), raising=False)
    monkeypatch.setattr(SingleStepTrial, '_agent_timeout_sec', property(lambda _: 30), raising=False)
    trial._run_agent_phase = AsyncMock(side_effect=patch.RepeatedOutputTruncationError('limit'))
    trial._record_exception = AsyncMock()
    trial._sync_agent_output = AsyncMock()
    from harbor.models.task.verifier_mode import VerifierEnvironmentMode
    monkeypatch.setattr('harbor.trial.single_step.resolve_task_verifier_mode',
                        lambda _: VerifierEnvironmentMode.SHARED)
    for name in ('_upload_agent_logs', '_collect_artifacts', '_run_verifier', '_stop_agent_environment'):
        setattr(trial, name, AsyncMock())
    asyncio.run(trial._run())
    trial._run_verifier.assert_awaited_once()
    trial._record_exception.assert_awaited_once()
    trial._sync_agent_output.assert_awaited_once()


def test_report_classifies_new_stop_and_budget_failure(tmp_path):
    from validation.metrics.trace_metrics import trial_metrics
    for kind, expected in [('RepeatedOutputTruncationError', 'repeated_output_truncation'),
                           ('ContextBudgetExceededError', 'harness_error')]:
        r = trial_metrics(tmp_path, {'exception_info': {'exception_type': kind},
                                    'verifier_result': {'rewards': {'reward': 1}}})
        assert r['termination'] == expected
        assert r['reward'] == 1
