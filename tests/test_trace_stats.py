import json

import pyarrow as pa
import pyarrow.parquet as pq


def row(task, model, attempt, reward, steps=None, stop='task_complete', exception=None):
    result = {'started_at': '2026-01-01T10:00:00Z', 'finished_at': '2026-01-01T10:10:00Z',
              'agent_execution': {'started_at': '2026-01-01T10:00:00Z', 'finished_at': '2026-01-01T10:05:00Z'},
              'agent_result': {'n_input_tokens': 1000, 'n_output_tokens': 100,
                               'metadata': {'n_episodes': len(steps or []), 'stop_reason': stop}},
              'verifier_result': {'rewards': {'reward': reward}} if reward is not None else None,
              'exception_info': {'exception_type': exception} if exception else None}
    return {'task_id': task, 'content_sha256': 'c' * 64, 'model': model, 'attempt_id': attempt,
            'trial': f'{task}__{attempt}', 'reward': reward, 'verifier_reward': reward, 'retained_v2': reward == 1,
            'trajectory_found': steps is not None,
            'trajectory': json.dumps({'steps': steps}) if steps is not None else None, 'result': json.dumps(result)}


def step(command):
    return {'source': 'agent', 'metrics': {'prompt_tokens': 300, 'completion_tokens': 50},
            'tool_calls': [{'function_name': 'bash_command', 'arguments': {'keystrokes': command + '\n'}}],
            'observation': {'results': [{'content': 'ok'}]}}


def test_trace_stats_counts_each_attempt_once_and_keeps_missing_trajectories(tmp_path):
    from validation.publishing.trace_stats import export
    folder = tmp_path / 'example_v2'
    (folder / 'traces').mkdir(parents=True)
    rows = [row('t1', 'org/Model-A', 'a1', 1.0, [step('ls'), step('cat notes.txt')]),
            row('t1', 'org/Model-A', 'a2', 0.0, None, exception='AgentTimeoutError'),
            row('t2', 'org/Model-B', 'b1', None, [step('ls')], exception='RewardFileNotFoundError')]
    pq.write_table(pa.Table.from_pylist(rows), folder / 'traces/000.parquet')
    # The same attempt again in a second file is counted once.
    pq.write_table(pa.Table.from_pylist(rows[:1]), folder / 'traces/001.parquet')
    out = tmp_path / 'trace_stats'
    summary = export(folder, out)
    assert summary['input_rows'] == 4 and summary['duplicate_records_removed'] == 1
    assert summary['unique_attempts'] == 3 and summary['missing_trajectories'] == 1
    assert summary['null_verifier_rewards'] == 1 and set(summary['models']) == {'model-a', 'model-b'}
    assert summary['all_attempts']['terminations'] == {'task_complete': 1, 'task_timeout': 1, 'verifier_error': 1}
    attempts = pq.read_table(out / 'data/per_attempt.parquet').to_pylist()
    assert sorted(a['attempt_id'] for a in attempts) == ['a1', 'a2', 'b1']
    assert json.loads(next(a for a in attempts if a['attempt_id'] == 'a1')['command_profile'])['counts'] == {'cat': 1, 'ls': 1}
    assert pq.read_table(out / 'data/per_task.parquet').num_rows == 2
    provenance = json.loads((out / 'data/provenance.json').read_text())
    assert [s['unique_rows_added'] for s in provenance['sources']] == [3, 0]
    for files in provenance['figures'].values():
        for path in files.values():
            assert (out / path).read_bytes().startswith(b'\x89PNG')
    text = (out / 'README.md').read_text()
    assert '3 attempts across 2 tasks' in text and 'Attempts without a saved conversation: 1;' in text


def test_trace_stats_reads_trace_schemas_without_trial_column(tmp_path):
    from validation.publishing.trace_stats import export
    folder = tmp_path / 'example_v2'
    (folder / 'traces').mkdir(parents=True)
    row_ = row('t1', 'org/Model-A', 'a1', 1.0, [step('ls')])
    row_['evaluated_content_sha256'] = row_.pop('content_sha256')
    for name in ('trial', 'verifier_reward'):
        del row_[name]
    pq.write_table(pa.Table.from_pylist([row_]), folder / 'traces/instruct.parquet')
    summary = export(folder, tmp_path / 'trace_stats')
    attempt, = pq.read_table(tmp_path / 'trace_stats/data/per_attempt.parquet').to_pylist()
    assert summary['unique_attempts'] == 1 and attempt['trial'] == 'a1' and attempt['content_sha256'] == 'c' * 64
