"""Setup detection and preparation before NOP verification."""
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from validation.stages.harbor import job_config
from validation.stages.task_setup import detect
from validation.stages.runner import run_trial_batch
from validation.stages import harbor as runtime


def test_nop_setup_is_detected_from_oracle_and_is_not_the_solution(tmp_path):
    task = tmp_path / 'task'
    (task / 'setup_files').mkdir(parents=True)
    (task / 'setup_files/setup.sh').write_text('#!/bin/sh\n')
    (task / 'solution').mkdir()
    (task / 'solution/solve.sh').write_text('bash /setup_files/setup.sh || exit $?\necho answer > /app/answer\n')
    args = SimpleNamespace(agent='terminus-2', agent_kwargs={}, api_base=None,
        environment_kwargs={}, backend='apptainer', dry_run=True, force_build=False,
        trial_cpus=None, trial_memory_mb=None, attempts=1, concurrency=1)
    command = detect(task)
    assert command == 'bash /setup_files/setup.sh'
    config = job_config(task, tmp_path / 'jobs', args, 'nop')
    assert config['agents'] == [{'name': 'nop', 'kwargs': {}}]
    (task / 'solution/solve.sh').write_text('echo answer > /app/answer\n')
    assert detect(task) is None
    (task / 'solution/solve.sh').write_text('bash /setup_files/setup.sh\n/app/broken_setup.sh\n')
    assert detect(task) == 'bash /setup_files/setup.sh'
    (task / 'solution/solve.sh').write_text('bash /setup_files/setup.sh && echo answer\n')
    with pytest.raises(ValueError, match='needs review'):
        detect(task)
    (task / 'solution/solve.sh').write_text('bash /setup_files/setup.sh\n')
    (task / 'setup_files/setup.sh').unlink()
    with pytest.raises(ValueError, match='referenced setup script is missing'):
        detect(task)


def test_stage_five_previews_only_post_setup_nop(tmp_path):
    task = tmp_path / 'task'
    (task / 'setup_files').mkdir(parents=True)
    (task / 'setup_files/setup.sh').write_text('#!/bin/sh\n')
    (task / 'solution').mkdir()
    (task / 'solution/solve.sh').write_text('bash /setup_files/setup.sh || exit $?\necho answer\n')
    (task / 'task.toml').write_text('[environment]\ncpus = 1\n')
    args = SimpleNamespace(agent='terminus-2', agent_kwargs={}, api_base=None,
        environment_kwargs={}, backend='apptainer', dry_run=True, force_build=False,
        trial_cpus=None, trial_memory_mb=None, attempts=1, concurrency=1,
        reward_key='reward')
    out = tmp_path / 'out'
    out.mkdir()
    [item] = run_trial_batch(5, [task], out, args, None)
    assert item['status'] == 'previewed'
    assert item['setup_detection'] == 'found'
    assert item['setup_command'] == 'bash /setup_files/setup.sh'
    assert 'raw_nop' not in item and 'prepared_nop' not in item
    assert not (out / 'job.json').exists()
    assert len(list(out.glob('*.json'))) == 1
    assert (out / 'nop-job-0.json').is_file()
    from harbor.models.job.config import JobConfig
    prepared = JobConfig.model_validate_json((out / 'nop-job-0.json').read_text())
    assert prepared.agents[0].name == 'nop'
    assert prepared.agents[0].override_setup_timeout_sec is None


@pytest.mark.parametrize('prepared_reward,expected', [(0, 'passed'), (1, 'failed')])
def test_stage_five_runs_only_post_setup_nop(tmp_path, monkeypatch, prepared_reward, expected):
    task = tmp_path / 'task'
    (task / 'setup_files').mkdir(parents=True)
    (task / 'setup_files/setup.sh').write_text('#!/bin/sh\n')
    (task / 'solution').mkdir()
    (task / 'solution/solve.sh').write_text('bash /setup_files/setup.sh || exit $?\necho answer\n')
    (task / 'task.toml').write_text('[environment]\ncpus = 1\n')
    args = SimpleNamespace(agent='terminus-2', agent_kwargs={}, api_base=None,
        environment_kwargs={'bridge_url': 'http://localhost', 'sif_cache': str(tmp_path)},
        backend='apptainer', dry_run=False, force_build=False, trial_cpus=None,
        trial_memory_mb=None, attempts=1, concurrency=1, reward_key='reward')
    monkeypatch.setattr(runtime, 'check_runtime_task', lambda *_: None)

    calls = []
    async def execute(config):
        calls.append(config)
        assert config['agents'][0] == {'name': 'nop', 'kwargs': {}}
        return Path('prepared')

    def results(job):
        reward = prepared_reward if job.name == 'prepared' else 0
        return [(tmp_path / 'trial', {'task_name': 'task',
            'verifier_result': {'rewards': {'reward': reward}}})]

    monkeypatch.setattr(runtime, 'execute_job', execute)
    monkeypatch.setattr(runtime, 'trial_results', results)
    out = tmp_path / 'out'
    out.mkdir()
    [item] = run_trial_batch(5, [task], out, args, None)
    assert item['status'] == expected
    assert len(calls) == 1
    assert 'raw_nop' not in item and 'prepared_nop' not in item
    assert item['rewards'] == [prepared_reward]


@pytest.mark.parametrize('setup_kind,expected_calls', [('none', 1), ('ambiguous', 0), ('fails', 1)])
def test_nop_does_not_fall_back_to_unprepared_run(tmp_path, monkeypatch, setup_kind, expected_calls):
    task = tmp_path / 'task'
    (task / 'solution').mkdir(parents=True)
    (task / 'setup_files').mkdir()
    (task / 'setup_files/setup.sh').write_text('exit 1')
    script = {'none': 'echo answer', 'ambiguous': 'bash /setup_files/setup.sh && echo answer',
              'fails': 'bash /setup_files/setup.sh'}[setup_kind]
    (task / 'solution/solve.sh').write_text(script)
    args = SimpleNamespace(agent='terminus-2', agent_kwargs={}, api_base=None,
        environment_kwargs={'bridge_url': 'http://localhost', 'sif_cache': str(tmp_path)},
        backend='apptainer', dry_run=False, force_build=False, trial_cpus=None,
        trial_memory_mb=None, attempts=1, concurrency=1, reward_key='reward')
    monkeypatch.setattr(runtime, 'check_runtime_task', lambda *_: None)
    calls = []
    async def execute(config):
        calls.append(config)
        if setup_kind == 'fails':
            raise RuntimeError('setup failed')
        assert config['agents'][0]['name'] == 'nop'
        return tmp_path / 'job'
    monkeypatch.setattr(runtime, 'execute_job', execute)
    monkeypatch.setattr(runtime, 'trial_results', lambda _: [(tmp_path / 'trial', {
        'task_name': 'task', 'verifier_result': {'rewards': {'reward': 0}}})])
    out = tmp_path / 'out'
    out.mkdir()
    [item] = run_trial_batch(5, [task], out, args, None)
    assert len(calls) == expected_calls
    assert item['status'] == ('passed' if setup_kind == 'none' else 'error')
