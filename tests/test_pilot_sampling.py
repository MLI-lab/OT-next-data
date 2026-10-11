"""Representative pilot sampling from task outcomes and command profiles."""
import json
import math
from pathlib import Path
import random
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from validation.checks.command_metrics import mean_distribution, profile, sampling_distribution
from validation.checks.command_metrics import split_commands
from validation.checks.trace_metrics import summarize
from validation.data.create_pilot import (build_candidates, create, equal_quotas, js_distance,
                                     length_labels, parser, sample)
from validation.data.selection import discover_tasks
from validation.stages.harbor import trial_results


def command_steps(*commands):
    return [{'source': 'agent', 'tool_calls': [
        {'function_name': 'bash_command', 'arguments': {'keystrokes': command}}]}
        for command in commands]


def test_command_profiles_split_shell_but_do_not_count_heredoc_body():
    steps = command_steps("X=1 /usr/bin/python3.12 -m pytest && rg 'a|b' x | head -2",
                          "cat <<'EOF'\nrm -rf /never\nEOF\nprintf ok\n",
                          'for x in a b; do echo "$x"; done', '', 'C-c')
    steps += [{'source': 'agent', 'tool_calls': [
        {'function_name': 'Bash', 'arguments': json.dumps({'command': 'env X=1 ls'})},
        {'function_name': 'Read', 'arguments': {'file_path': '/tmp/file'}},
        {'function_name': 'mark_task_complete', 'arguments': {}}]}]
    result = profile(steps)
    assert result['counts'] == {'python-m:pytest': 1, 'rg': 1, 'head': 1, 'cat': 1,
                                'printf': 1, '[unknown]': 1, 'ls': 1, 'tool:Read': 1}
    assert (result['submissions'], result['polls'], result['controls'], result['unsupported_submissions']) == (4, 1, 1, 1)
    assert split_commands('echo "a;b"; ls 2>&1')[0] == ['echo "a;b"', 'ls 2>&1']
    assert profile(None) is None
    assert sampling_distribution(profile(None)) is None
    assert sampling_distribution(profile([])) == {'[no_commands]': 1}
    assert sampling_distribution(profile(command_steps('if true; then ls; fi'))) is None


def test_task_command_distribution_weights_attempts_equally():
    profiles = [profile(command_steps(*(['ls'] * 100))), profile(command_steps('pytest')), None]
    assert mean_distribution(profiles) == {'ls': .5, 'pytest': .5}
    assert js_distance({'ls': 1}, {'pytest': 1}) == 1
    assert js_distance({'ls': 1}, {'ls': 1}) == 0
    a, b = {'ls': .3, 'pytest': .7}, {'rg': .5, 'ls': .5}
    assert math.isfinite(js_distance(a, b))
    assert js_distance(a, b) == pytest.approx(js_distance(b, a))


def test_equal_quotas_redistribute_empty_and_exhausted_groups():
    assert equal_quotas({'all_solved': 1000, 'all_zero': 500, 'constant_partial': 0, 'varying': 20},
                        120, random.Random(0)) == {'all_solved': 50, 'all_zero': 50, 'constant_partial': 0, 'varying': 20}
    assert equal_quotas({'a': 1, 'b': 2}, 10, random.Random(0)) == {'a': 1, 'b': 2}
    assert sum(equal_quotas({'a': 4, 'b': 4, 'c': 4}, 2, random.Random(0)).values()) == 2


def test_length_ties_and_missing_values():
    rows = [{'length': x} for x in [1, 1, 1, 1, 8, 9, None]]
    assert length_labels(rows) == [1, 1]
    assert [r['length_bucket'] for r in rows] == ['short'] * 4 + ['long', 'long', 'unknown_length']


def candidates(n=12):
    return [{'id': f't{i}', 'task': f'task{i}', 'reward_group': 'all_zero',
             'length_bucket': 'short', 'family': 'python', 'termination': 'task_complete',
             'reward': 0, 'distribution': {'ls': 1} if i < n - 1 else {'pytest': 1}}
            for i in range(n)]


def test_sampling_diversity_is_finite_reproducible_and_keeps_quotas():
    rows = candidates()
    selected, buckets = sample(rows, 3, 2, seed=0)
    assert len(selected) == 3 and sum(b['quota'] for b in buckets) == 3
    assert any(r['id'] == 't11' for r in selected)
    assert any(r['selection_reason'] == 'command_diversity' for r in selected)
    assert sample(rows[::-1], 3, 2, seed=0) == (selected, buckets)
    assert len({r['id'] for r in selected}) == 3


def test_stage8_balances_termination():
    rows = candidates(4)
    rows[0]['termination'] = 'task_timeout'
    chosen, buckets = sample(rows, 2, 8, seed=5)
    assert {r['termination'] for r in chosen} == {'task_complete', 'task_timeout'}
    assert all('family' in b['bucket'] and 'termination' in b['bucket'] for b in buckets)


@pytest.mark.parametrize('stage', [2, 8])
def test_every_pick_after_first_maximizes_minimum_distance(stage):
    rows = candidates(8)
    for index, row in enumerate(rows):
        row['distribution'] = {'ls': index / 7, 'pytest': 1 - index / 7}
    selected, _ = sample(rows, 6, stage, seed=12)
    assert selected[0]['selection_reason'] == 'random'
    for index, row in enumerate(selected[1:], 1):
        previous = selected[:index]
        used = {r['id'] for r in previous}
        best = max(min(js_distance(r['distribution'], p['distribution']) for p in previous)
                   for r in rows if r['id'] not in used)
        assert row['nearest_js_distance'] == pytest.approx(best)
        assert row['selection_reason'] == 'command_diversity'
        assert row['id'] not in used


def test_identical_distributions_fill_without_duplicates():
    rows = candidates(5)
    for row in rows:
        row['distribution'] = {'ls': 1}
    chosen, _ = sample(rows, 5, 2, seed=4)
    assert len({r['id'] for r in chosen}) == 5
    assert all(r['nearest_js_distance'] == 0 for r in chosen[1:])


@pytest.mark.parametrize('distribution', [None, {}, {'ls': float('nan')}, {'ls': -1}, {'ls': .5}])
def test_missing_or_invalid_distributions_fail(distribution):
    rows = candidates(5)
    rows[0]['distribution'] = distribution
    with pytest.raises(ValueError, match='missing or invalid command distribution'):
        sample(rows, 2, 2)


def dataset(tmp_path):
    tasks, job = tmp_path / 'tasks', tmp_path / 'job'
    results = []
    for name, rewards in [('set-python-0001', [1, 0]), ('set-java-0002', [0, None])]:
        task = tasks / name
        task.mkdir(parents=True)
        (task / 'instruction.md').write_text('Solve the task.')
        (task / 'task.toml').write_text('schema_version = "1.0"\n')
        for index, reward in enumerate(rewards):
            trial = f'{name}__{index}'
            evidence = job / trial / 'attempts/000'
            (evidence / 'agent').mkdir(parents=True)
            (evidence / 'agent/trajectory.json').write_text(json.dumps({'steps': command_steps('ls', *(['pytest'] * index))}))
            data = {'task_name': name, 'trial_relpath': f'{trial}/attempts/000',
                    'verifier_result': {'rewards': {'reward': reward}},
                    'agent_result': {'metadata': {'n_episodes': 2 + index * 4, 'stop_reason': 'task_complete'}}}
            (job / trial / 'result.json').write_text(json.dumps(data))
            results.append((evidence, data))
    metrics = summarize(results, lambda path, result: result['task_name'])
    metrics_path = tmp_path / 'trace-metrics.json'
    metrics_path.write_text(json.dumps(metrics))
    return tasks, job, metrics_path, metrics


def test_stage7_means_family_and_missing_rewards(tmp_path):
    tasks, _, _, metrics = dataset(tmp_path)
    unknown = tasks / 'untried'
    unknown.mkdir()
    (unknown / 'instruction.md').write_text('Untried')
    rows, _ = build_candidates(discover_tasks(tasks), metrics, 2, {}, True)
    by_name = {r['task']: r for r in rows}
    assert by_name['set-python-0001']['length'] == 4
    assert metrics['tasks']['set-python-0001']['turns']['mean'] == 4
    assert by_name['set-python-0001']['family'] == 'python'
    assert by_name['set-python-0001']['reward_group'] == 'varying'
    assert by_name['set-java-0002']['reward_group'] == 'all_zero'
    assert by_name['set-java-0002']['ungraded_attempts'] == 1
    assert by_name['untried']['reward_group'] == 'no_reward'
    assert by_name['untried']['length_bucket'] == 'unknown_length'
    rows, _ = build_candidates(discover_tasks(tasks), metrics, 2, {'set-python-0001': 'custom'}, False)
    assert next(r for r in rows if r['task'] == 'set-python-0001')['family'] == 'custom'
    assert next(r for r in rows if r['task'] == 'set-java-0002')['family'] == 'unknown'


@pytest.mark.parametrize('stage', [2, 8])
def test_exported_pilot_is_consumable_and_source_unchanged(tmp_path, stage):
    tasks, job, metrics_path, metrics = dataset(tmp_path)
    before = metrics_path.read_bytes()
    out = tmp_path / 'pilot'
    args = parser().parse_args([str(tasks), '--trace-metrics', str(metrics_path), '--trials', str(job),
                               '--stage', str(stage), '--size', '3', '--family-from-id', '--out', str(out)])
    manifest = create(args)
    assert manifest['selected_size'] == (2 if stage == 2 else 3)
    assert {p.name for p in discover_tasks(out)} == {r['task'] for r in manifest['selected']}
    assert metrics_path.read_bytes() == before
    assert json.loads((out / 'pilot.json').read_text()) == manifest
    assert not any(p.is_symlink() for p in out.rglob('*'))
    if stage == 8:
        exported = trial_results(out / 'trials')
        assert len(exported) == 3
        assert all((p / 'agent/trajectory.json').is_file() for p, _ in exported)
        assert {r['task_name'] for _, r in exported} <= {p.name for p in discover_tasks(out)}
    with pytest.raises(ValueError, match='already exists'):
        create(args)


@pytest.mark.parametrize('stage', [2, 8])
def test_missing_metrics_fail_even_with_trials(tmp_path, stage):
    tasks, job, metrics_path, metrics = dataset(tmp_path)
    del metrics['trajectories'][0]['command_profile']
    metrics_path.write_text(json.dumps(metrics))
    args = parser().parse_args([str(tasks), '--trace-metrics', str(metrics_path), '--stage', str(stage),
                               '--trials', str(job), '--size', '1', '--out', str(tmp_path / 'pilot')])
    with pytest.raises(ValueError, match='missing usable command distribution'):
        create(args)
    assert not args.out.exists()


def test_wrong_trial_reward_rejection(tmp_path):
    tasks, job, metrics_path, metrics = dataset(tmp_path)
    metrics['trajectories'][0]['reward'] = .25
    metrics_path.write_text(json.dumps(metrics))
    args = parser().parse_args([str(tasks), '--trace-metrics', str(metrics_path), '--stage', '8',
                               '--trials', str(job), '--size', '1', '--out', str(tmp_path / 'pilot')])
    with pytest.raises(ValueError, match='reward mismatch'):
        create(args)
    assert not args.out.exists()
