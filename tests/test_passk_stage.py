"""Stages 6/7: calibrated pass@k over simulated Harbor jobs (validation.stages.passk_stage)."""
import json
import tomllib
from pathlib import Path
from types import SimpleNamespace

from validation.stages import passk_stage as ps


def make_task(root, name, environment='cpus = 1\nmemory = "2G"\nstorage = "10G"\n'):
    task = root / name
    (task / 'environment').mkdir(parents=True)
    (task / 'environment/Dockerfile').write_text('FROM ubuntu:24.04\n')
    (task / 'tests').mkdir()
    (task / 'tests/test.sh').write_text('#!/bin/bash\n')
    (task / 'instruction.md').write_text('Do it.\n')
    (task / 'task.toml').write_text(f'version = "1.0"\n\n[agent]\ntimeout_sec = 900\n\n[environment]\n{environment}'
                                    '\n[verifier]\ntimeout_sec = 600\n')
    return task


def args(tmp_path, **extra):
    from validation.stages.runner import parser
    (tmp_path / 'tasks').mkdir(exist_ok=True)
    a = parser().parse_args([str(tmp_path / 'tasks'), '--out', str(tmp_path / 'out'), '--attempts', '4',
                             '--model', 'hosted_vllm/qwen35-35b-a3b', '--agent', 'mini-swe-agent'])
    for key, value in extra.items():
        setattr(a, key, value)
    return a


def fake_harbor(behaviour, calls):
    """behaviour(name, allocation, attempt_index, call_index) -> dict of trial outcome."""
    def execute(config):
        calls.append(config)
        job_dir = Path(config['jobs_dir']) / 'job'
        job_dir.mkdir(parents=True, exist_ok=True)
        for task in config['tasks']:
            path = Path(task['path'])
            env = tomllib.loads((path / 'task.toml').read_text())['environment']
            allocation = {'cpus': env['cpus'], 'memory_mb': env['memory_mb'], 'storage_mb': env['storage_mb']}
            for i in range(config['n_attempts']):
                outcome = behaviour(path.name, allocation, i, len(calls))
                trial = job_dir / f'{path.name}__{len(calls)}{i}'
                trial.mkdir()
                result = {'task_name': path.name, 'finished_at': '2026-11-08T20:00:10Z',
                          'agent_execution': {'started_at': '2026-11-08T20:00:00Z', 'finished_at': '2026-11-08T20:00:05Z'},
                          'verifier': {'started_at': '2026-11-08T20:00:05Z', 'finished_at': '2026-11-08T20:00:10Z'},
                          'resources': {'agent': {'memory_peak_mb': outcome.get('memory', 1200), 'oom_kills': outcome.get('oom', 0),
                                                  'disk_peak_mb': 200, 'cpu_seconds': 4, 'step_seconds': 10,
                                                  'cpus': allocation['cpus']}}}
                if 'reward' in outcome:
                    result['verifier_result'] = {'rewards': {'reward': outcome['reward']}}
                if 'error' in outcome:
                    result['exception_info'] = {'exception_type': outcome['error'][0], 'exception_message': outcome['error'][1]}
                (trial / 'result.json').write_text(json.dumps(result))
        return job_dir
    return execute


def behaviour(name, allocation, index, call):
    if name == 'a':  # OOM on one attempt at 8 GiB and no solve there, fine at 16 GiB
        if allocation['memory_mb'] < 16384:
            if index == 1:
                return {'oom': 1, 'memory': 8192, 'error': ('RuntimeError', 'Task memory limit exceeded outside a recoverable agent terminal interaction')}
            return {'reward': 0, 'memory': 900}
        return {'reward': 1 if index == 0 else 0, 'memory': 9500}
    if name == 'b':  # one infrastructure failure in the first job only
        if call == 1 and index == 2:
            return {'error': ('RuntimeError', 'BridgeOutage: worker died')}
        return {'reward': 0}
    if name == 'c':  # needs more memory than the maximum
        return {'oom': 1, 'memory': allocation['memory_mb'], 'error': ('RuntimeError', 'Task memory limit exceeded')}
    raise AssertionError(name)


def test_calibrated_passk_workflow_and_resume(tmp_path, monkeypatch):
    monkeypatch.setenv('APPTAINER_BRIDGE_URL', 'http://test')
    tasks = [make_task(tmp_path / 'tasks', n) for n in 'abc']
    selected = [(t, {'task': str(t)}) for t in tasks]
    calls, report = [], {}
    a = args(tmp_path)
    items = ps.run(6, selected, tmp_path / 'stage6', a, None, report, None, execute=fake_harbor(behaviour, calls))
    by = {Path(i['task']).name: i for i in items}
    # a: 8 GiB OOM -> whole batch reruns at 16 GiB; pass@4 from the 16 GiB batch only (1 success).
    assert [r['allocation']['memory_mb'] for r in by['a']['calibration']['rounds']] == [8192, 16384]
    assert by['a']['passk'].items() >= {'outcome': 'solved', 'successes': 1, 'valid_attempts': 4}.items()
    assert by['a']['calibration']['final_allocation'] == {'cpus': 2, 'memory_mb': 16384, 'storage_mb': 20480}
    # Requirements from the one solved attempt: ceil(9500 MB)+1 = 11 GiB; disk peak + 2 GiB = 3 GiB; ceil(4 CPU-seconds/10 seconds) = 1 vCPU.
    assert by['a']['calibration']['requirements'].items() >= {'cpus': 1, 'memory_mb': 11 * 1024, 'storage_mb': 3 * 1024}.items()
    # b: only the failed attempt reran, at the same allocation; one round.
    assert len(by['b']['calibration']['rounds']) == 1 and by['b']['passk']['outcome'] == 'unsolved'
    assert by['b']['calibration']['earlier_attempts'] == 1
    # c: OOM at the maximum -> inconclusive, not unsolved, no rewards counted.
    assert by['c']['passk']['outcome'] == 'inconclusive' and by['c']['status'] == 'inconclusive' and by['c']['rewards'] == []
    # Upstream values are kept for comparison.
    assert by['c']['calibration']['upstream'] == {'cpus': 1, 'memory_mb': 2048, 'storage_mb': 10240}
    # Job allocations were written into the task copies; each job ran only the attempts it needed.
    # Tasks with the same allocation share a job: a+b+c at 8 GiB, a+c at 16 GiB, then b's single retry.
    assert sorted(c['n_attempts'] for c in calls) == [1, 4, 4] and [len(c['tasks']) for c in calls] == [3, 2, 1]
    # Resume: a second run of the same stage starts no new trials.
    again = []
    ps.run(6, [(t, {'task': str(t)}) for t in tasks], tmp_path / 'stage6-again', a, None, {}, None,
           execute=fake_harbor(behaviour, again))
    assert again == []


def test_capped_teacher_task_never_goes_up(tmp_path, monkeypatch):
    monkeypatch.setenv('APPTAINER_BRIDGE_URL', 'http://test')
    task = make_task(tmp_path / 'tasks', 'b')
    start = {'b': {'cpus': 2, 'memory_mb': 4096, 'storage_mb': 10240, 'capped': True}}
    oom = lambda *a: {'oom': 1, 'memory': 4096, 'error': ('RuntimeError', 'Task memory limit exceeded')}
    items = ps.run(7, [(task, {'task': str(task)})], tmp_path / 'stage7', args(tmp_path), None, {}, None,
                   start_allocations=start, execute=fake_harbor(oom, []))
    rounds = items[0]['calibration']['rounds']
    assert len(rounds) == 1 and rounds[0]['allocation'] == {'cpus': 2, 'memory_mb': 4096, 'storage_mb': 10240}
    assert items[0]['passk'].items() >= {'outcome': 'unsolved', 'valid_attempts': 4}.items()


def test_teacher_starts_from_the_target_models_allocation(tmp_path, monkeypatch):
    monkeypatch.setenv('APPTAINER_BRIDGE_URL', 'http://test')
    task = make_task(tmp_path / 'tasks', 'b')
    calls = []
    start = {'b': {'cpus': 4, 'memory_mb': 16384, 'storage_mb': 20480}}
    items = ps.run(7, [(task, {'task': str(task)})], tmp_path / 'stage7', args(tmp_path), None, {}, None,
                   start_allocations=start, execute=fake_harbor(lambda *a: {'reward': 0}, calls))
    assert items[0]['calibration']['rounds'][0]['allocation'] == start['b']
    env = tomllib.loads((Path(calls[0]['tasks'][0]['path']) / 'task.toml').read_text())['environment']
    assert (env['cpus'], env['memory_mb'], env['storage_mb']) == (4, 16384, 20480)


def test_set_environment_rewrites_only_resources():
    text = 'version = "1.0"\n[environment]\ncpus = 1\nmemory = "2G"\nbuild_timeout_sec = 600\n[verifier]\ntimeout_sec = 9\n'
    out = ps.set_environment(text, {'cpus': 2, 'memory_mb': 8192, 'storage_mb': 20480})
    data = tomllib.loads(out)
    assert data['environment'] == {'cpus': 2, 'memory_mb': 8192, 'build_timeout_sec': 600, 'storage_mb': 20480}
    assert data['verifier'] == {'timeout_sec': 9}
    assert tomllib.loads(ps.set_environment('version = "1.0"\n', {'cpus': 2, 'memory_mb': 1, 'storage_mb': 1}))['environment']['cpus'] == 2


def test_run_wide_resource_overrides_are_refused(tmp_path, monkeypatch):
    import pytest
    monkeypatch.setenv('APPTAINER_BRIDGE_URL', 'http://test')
    task = make_task(tmp_path / 'tasks', 'b')
    with pytest.raises(ValueError, match='trial-cpus'):
        ps.run(6, [(task, {'task': str(task)})], tmp_path / 's', args(tmp_path, trial_cpus=8), None, {}, None,
               execute=fake_harbor(lambda *a: {'reward': 0}, []))
