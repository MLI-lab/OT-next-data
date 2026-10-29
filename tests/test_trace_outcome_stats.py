from validation.publishing.trace_outcome_stats import OutcomeStats, OUTCOMES


def test_outcomes_missing_and_step_weighting():
    stats = OutcomeStats()
    stats.add({'model': 'model', 'verifier_reward': 1, 'result': {
        'agent_result': {'n_output_tokens': 12, 'n_input_tokens': 30,
                         'metadata': {'summarization_count': 0}}},
        'trajectory': {'steps': [
            {'source': 'agent', 'metrics': {'prompt_tokens': 10, 'completion_tokens': 5}},
            {'source': 'agent', 'metrics': {'completion_tokens': 7}},
            {'source': 'user', 'metrics': {'completion_tokens': 999}}]}})
    stats.add({'model': 'model', 'verifier_reward': 0, 'result': {}, 'trajectory': None})
    stats.add({'model': 'model', 'verifier_reward': None, 'result': {}, 'trajectory': None})
    group = stats.groups['all-models']
    assert [group[k]['attempts'] for k in OUTCOMES] == [1, 1, 1]
    success = group[OUTCOMES[0]]
    assert success['values']['step_output_tokens'] == [5, 7]
    assert success['values']['step_context_tokens'] == [15]
    assert success['missing']['step_context_tokens'] == 1
    assert success['values']['input_plus_output_tokens'] == [42]
    assert success['values']['context_summarizations'] == [0]
    assert group[OUTCOMES[1]]['values']['context_summarizations'] == []
    assert group[OUTCOMES[1]]['missing']['context_summarizations'] == 1
    assert stats.groups['model'][OUTCOMES[0]]['values']['step_output_tokens'] == [5, 7]
    assert stats.context_groups['all-models']['Without summarization'] == [15]
    assert stats.context_groups['all-models']['Unknown summary count'] == [None, None]


def test_peak_context_groups_attempts_by_summarization_not_success():
    stats = OutcomeStats()
    for reward in (0, 1):
        stats.add({'model': 'model', 'verifier_reward': reward,
                   'result': {'agent_result': {'metadata': {'summarization_count': 2}}},
                   'trajectory': {'steps': [
                       {'source': 'agent', 'metrics': {'prompt_tokens': 10, 'completion_tokens': 5}},
                       {'source': 'agent', 'metrics': {'prompt_tokens': 20, 'completion_tokens': 7}}]}})
    assert stats.context_groups['all-models']['With summarization'] == [27, 27]
