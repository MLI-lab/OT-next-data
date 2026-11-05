"""CPU accounting follows the pinned Harbor lifecycle, including judge-only runs."""
import asyncio
from types import SimpleNamespace

import pytest

from hpc.validation_worker import trial_capacity, capacity_for_stages
from validation.stages.runner import parser


def task(tmp_path):
    (tmp_path / 'instruction.md').write_text('Test task.')
    (tmp_path / 'task.toml').write_text('''schema_version = "1.1"
[environment]
cpus = 2
memory_mb = 4096
[verifier]
environment_mode = "separate"
[verifier.environment]
cpus = 8
memory_mb = 8192
''')
    return tmp_path


def test_sequential_peak_and_host_verdict_use_different_budgets(tmp_path):
    source = task(tmp_path)
    args = parser().parse_args(['--cpus', '32', '--serve-model', 'qwen38-27b',
                                '--serve-cpus', '4', '--concurrency', '14',
                                '--review-agent', 'mini-swe-agent', '--review-evidence', 'image'])
    solver = capacity_for_stages([source], args, [6])
    assert solver['reserved_cpus_per_trial'] == 8  # max(2, 8), not 2 + 8
    judge = capacity_for_stages([source], args, [5])
    assert judge['reserved_cpus_per_trial'] == 2
    assert judge['max_concurrency'] == 14
    args.review_agent = 'codex'
    inspector = capacity_for_stages([source], args, [5])
    assert inspector['reserved_cpus_per_trial'] == 10  # Both environments live for inspection.


def test_single_step_releases_agent_before_separate_verifier(tmp_path):
    from harbor.models.task.task import Task
    from harbor.trial.single_step import SingleStepTrial
    source = task(tmp_path)
    events = []
    def event(name):
        async def record():
            events.append(name)
        return record
    trial = SimpleNamespace(task=Task(source),
        _run_agent=event('agent'), _upload_agent_logs=event('logs'),
        _collect_artifacts=event('artifacts'), _stop_agent_environment=event('stop'),
        _run_verifier=event('verifier'))
    asyncio.run(SingleStepTrial._run(trial))
    assert events == ['agent', 'logs', 'artifacts', 'stop', 'verifier']


@pytest.mark.parametrize('concurrency', [1, 6, 14])
def test_requested_concurrency_is_configurable(tmp_path, concurrency):
    source = task(tmp_path)
    args = parser().parse_args(['--concurrency', str(concurrency), '--cpus', '32'])
    assert trial_capacity([source], args, verifier_disabled=True)['requested_concurrency'] == concurrency
