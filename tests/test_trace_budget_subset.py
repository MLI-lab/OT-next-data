from validation.metrics.trace_budget_subset import attempt_metrics


def row(prompt=100, output=200, summaries=0, reward=1):
    return {'task_id': 'task', 'attempt_id': 'a', 'verifier_reward': reward,
            'trajectory': {'steps': [{'source': 'agent', 'metrics': {'prompt_tokens': prompt, 'completion_tokens': output}}]},
            'result': {'agent_result': {'metadata': {'summarization_count': summaries}}}}


def test_boundaries_include_prompt_and_completion_and_strict_output():
    assert attempt_metrics(row(20000, 5000))['eligible_no_summary']
    assert not attempt_metrics(row(20001, 5000))['eligible_no_summary']
    assert not attempt_metrics(row(1, 8192))['eligible_no_summary']
    assert attempt_metrics(row(1, 8191))['eligible_no_summary']


def test_missing_steps_counts_or_summary_metadata_are_not_invented():
    r = row(); r['trajectory']['steps'].append({'source': 'agent'})
    assert not attempt_metrics(r)['eligible_observed_limits']
    assert not attempt_metrics(row(summaries=None))['eligible_no_summary']
    r = row(); r['trajectory']['steps'] = []
    assert not attempt_metrics(r)['eligible_no_summary']
    assert not attempt_metrics(row(output=float('nan')))['eligible_no_summary']


def test_summaries_and_unsolved_attempts_are_separate():
    m = attempt_metrics(row(summaries=1))
    assert m['eligible_observed_limits'] and not m['eligible_no_summary']
    assert not attempt_metrics(row(reward=0))['eligible_observed_limits']
    assert not attempt_metrics(row(reward=None))['solved']
