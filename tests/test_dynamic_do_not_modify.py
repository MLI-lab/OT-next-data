import asyncio
import json

import pytest

from validation.rules.dynamic.do_not_modify import stage
from validation.stages import harbor as runtime


def make_task(root, instruction, test_text):
    task = root / 'task'
    (task / 'tests').mkdir(parents=True)
    (task / 'instruction.md').write_text(instruction)
    (task / 'tests/test_outputs.py').write_text(test_text)
    return task


def observation(task, path='/app/config.yaml'):
    from validation.rules.dynamic.do_not_modify.check import protected_task_digest
    return {'schema': 'protected-paths-v1', 'status': 'completed', 'environment': 'agent',
            'observation_phase': 'after-task-setup-and-healthcheck-before-agent',
            'task_sha256': protected_task_digest(task),
            'findings': [{'candidate': path, 'status': 'exists', 'matches': [path], 'method': 'exact'}]}


def trials(tmp_path, report):
    trial = tmp_path / 'trial-0'
    trial.mkdir(exist_ok=True)
    (trial / stage.OBSERVATION).write_text(json.dumps(report))
    return [(trial, {})]


def fake_reference(monkeypatch, rewards):
    """Each mutation rerun returns the next reward; record which mutations ran."""
    ran = []
    monkeypatch.setenv('APPTAINER_BRIDGE_URL', 'http://test')

    async def execute(config):
        job = config['jobs_dir']
        mutation = stage.active_mutation.get()
        ran.append(mutation['mutation'])
        directory = runtime.Path(job) / str(len(ran))
        directory.mkdir(parents=True)
        (directory / stage.MUTATION).write_text(json.dumps(dict(mutation, status='applied')))
        return directory
    monkeypatch.setattr(runtime, 'execute_job', execute)
    monkeypatch.setattr(runtime, 'trial_results', lambda job: [job])
    monkeypatch.setattr(runtime, 'assess_trials', lambda rows, *a, **k: {'status': 'completed', 'rewards': [rewards[len(ran) - 1]]})
    return ran


from validation.stages.runner import parser
ARGS = parser().parse_args(['tasks'])


def test_plan_mutates_content_or_deletes_for_do_not_remove(tmp_path):
    task = make_task(tmp_path, 'Do not modify /app/config.yaml.', '')
    assert [m['mutation'] for m in stage.plan(task, observation(task))] == ['newline', 'empty', 'space']
    (task / 'instruction.md').write_text('Do not remove /app/config.yaml.')
    assert [m['mutation'] for m in stage.plan(task, observation(task))] == ['delete']


def test_existing_file_the_verifier_never_mentions_is_flagged_without_mutations(tmp_path, monkeypatch):
    task = make_task(tmp_path, 'Do not modify /app/config.yaml.', 'def test_x():\n    assert True\n')
    ran = fake_reference(monkeypatch, [])
    result = asyncio.run(stage.evaluate(task, {'rewards': [1, 1, 1]}, trials(tmp_path, observation(task)), tmp_path, ARGS))
    assert result['status'] == 'fail' and ran == []


@pytest.mark.parametrize('rewards, status, runs', [([1], 'fail', 1), ([0, 0, 0], 'pass', 3)])
def test_mentioned_file_is_mutated_after_a_passing_reference(tmp_path, monkeypatch, rewards, status, runs):
    task = make_task(tmp_path, 'Do not modify /app/config.yaml.', 'CONFIG = "/app/config.yaml"\n')
    ran = fake_reference(monkeypatch, rewards)
    result = asyncio.run(stage.evaluate(task, {'rewards': [1, 1, 1]}, trials(tmp_path, observation(task)), tmp_path, ARGS))
    # A verifier that still scores 1 after the protected file changed accepted the violation.
    assert result['status'] == status and len(ran) == runs


def test_without_passing_reference_the_mention_scan_decides(tmp_path, monkeypatch):
    task = make_task(tmp_path, 'Do not modify /app/config.yaml.', 'CONFIG = "/app/config.yaml"\n')
    ran = fake_reference(monkeypatch, [])
    result = asyncio.run(stage.evaluate(task, {'rewards': [1, 0, 1]}, trials(tmp_path, observation(task)), tmp_path, ARGS))
    # Without a passing reference the result is that of the mention scan, without mutations.
    assert result['status'] == 'pass' and ran == [] and 'did not pass' in result['mutation_step']
    no_reference = asyncio.run(stage.evaluate(task, {'rewards': [0, 0, 0]}, trials(tmp_path, observation(task)), tmp_path, ARGS, reference=False))
    assert no_reference['status'] == 'pass' and no_reference['mutation_step'] == 'not run: no reference solution'
    missing = asyncio.run(stage.evaluate(task, {'rewards': [1, 1, 1]}, [], tmp_path, ARGS))
    assert missing['status'] == 'incomplete'


def test_publishing_archives_a_flagged_reference_item():
    from validation.publishing.outcome_labels import labels
    from validation.publishing.publish import judge
    from validation.stages.layout import REFERENCE
    item = {'status': 'passed', 'rewards': [1, 1, 1], 'do_not_modify': {'status': 'fail', 'reason': 'mutation accepted'}}
    assert judge(REFERENCE, item)[0] == 'archive'
    assert labels(REFERENCE, item) == ['dynamic:do-not-modify']
