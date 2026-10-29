from validation.publishing.evaluation_settings import from_contract, with_trial, markdown


def test_frozen_settings_override_current_registry_and_preserve_overrides():
    contract = {'sha256': 'old', 'arguments': {'serve_model': 'qwen38-27b', 'serve_context': 16384,
        'agent_kwargs': {'llm_call_kwargs': {'max_tokens': 1234, 'extra_body': {'top_p': .7}}}},
        'execution_profile': {'local_model_assets': {'spec': {'max_output_tokens': 8192,
            'thinking': True, 'sampling': {'temperature': 1., 'top_p': .95}}}}}
    s = from_contract(contract)
    assert s['context_tokens'] == 16384
    assert s['intended_output_tokens_per_call'] == 1234
    assert s['sampling'] == {'temperature': 1., 'top_p': .7}
    assert s['request_output_tokens'] is None
    assert 'not verified' in s['output_limit_status']


def test_historical_top_level_limit_is_not_claimed_as_enforced():
    s = with_trial({}, {'agent': {'name': 'terminus-2', 'kwargs': {'max_tokens': 8192}}})
    assert s['request_output_tokens'] is None
    assert 'not forwarded' in s['output_limit_status']
    s = with_trial(s, {'agent': {'name': 'terminus-2', 'kwargs': {'llm_call_kwargs': {'max_tokens': 8192}}}})
    assert s['request_output_tokens'] == 8192
    assert 'HTTP payload not audited' in s['output_limit_status']


def test_missing_frozen_spec_never_uses_live_model_defaults():
    s = from_contract({'arguments': {'serve_model': 'qwen38-27b'}})
    assert s['intended_output_tokens_per_call'] is None
    assert s['sampling'] is None
    text = markdown([s])
    assert 'not recorded' in text
    assert 'does not mean unlimited' in text


def test_table_names_models_by_hugging_face_id():
    assert '| Qwen/Qwen3-Coder-30B-A3B-Instruct |' in markdown([{'model': 'openai/coder-30b'}])
    assert '| Qwen/Qwen3-30B-A3B |' in markdown([{'model': 'qwen3-30b-thinking'}])
    assert '| Qwen/Qwen3.8-27B |' in markdown([{'model': 'x', 'hf_repo': 'Qwen/Qwen3.8-27B'}])
    assert '| Qwen/Qwen3.8-27B |' in markdown([{'model': 'qwen3.8-27b'}])
    assert '| Qwen/Qwen3-30B-A3B-Instruct-2507 |' in markdown([{'model': 'qwen3-30b-a3b-instruct-2507'}])
