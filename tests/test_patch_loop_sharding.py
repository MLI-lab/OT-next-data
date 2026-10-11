import json
from pathlib import Path

import pytest

from validation.patch_repair_loop import controller as c, recovery, resources, sharding
from validation import contract as contracts, shards


@pytest.fixture
def frozen(monkeypatch):
    value = {'sha256': 'frozen', 'stages': [3], 'arguments': {},
             'tasks': [{'task_id': name, 'sha256': name} for name in ('a', 'b', 'c', 'd', 'e')]}
    monkeypatch.setattr(contracts, 'read', lambda path: value)
    monkeypatch.setattr(sharding, 'read', lambda path: value)
    monkeypatch.setattr(contracts, 'task_records', lambda contract: contract['tasks'])
    return value


def test_array_submission_is_bounded_resumable_and_preserves_publication_inputs(tmp_path, monkeypatch, frozen):
    config = {'shard_size': 2, 'shard_concurrency': 2, 'time': '24:00:00',
              'cpus': 48, 'memory': '128G', 'partition': 'cpu', 'workspace': str(tmp_path)}
    calls = []
    def plan(contract, count, out, **kwargs):
        assert count == 3 and kwargs['array_concurrency'] == 2 and not kwargs['submit']
        assert kwargs['time'] == '24:00:00'
        c.save(out / 'plan.json', {'count': count, 'contract_sha256': 'frozen',
                                 'command': ['sbatch', '--array=0-2%2', '/array.sbatch']})
    monkeypatch.setattr(shards, 'plan', plan)
    monkeypatch.setattr(sharding.subprocess, 'check_output', lambda cmd, **kw: calls.append(cmd) or '123;helma\n')
    ids = [t['task_id'] for t in frozen['tasks']]
    first = sharding.submit(config, '/contract', ids, tmp_path)
    assert sharding.submit(config, '/contract', ids, tmp_path) == first
    assert len(calls) == 1 and '--signal=B:USR1@120' in calls[0]
    assert [s['selected_ids'] for s in first['shards']] == [['a', 'd'], ['b', 'e'], ['c']]
    assert [s['job_id'] for s in first['shards']] == ['123_0', '123_1', '123_2']
    assert json.loads((tmp_path / 'array/request.json').read_text())['stages'] == [3]
    assert json.loads((tmp_path / 'array/submission.json').read_text())['code_snapshot'] == str(tmp_path / 'array/code')
    c.save(tmp_path / 'array/submission.json', {'status': 'submitting'})
    with pytest.raises(RuntimeError, match='Uncertain array'):
        sharding.submit(config, '/contract', ids, tmp_path)
    assert len(calls) == 1


def test_merge_retains_finished_shards_and_marks_only_unfinished_tasks(tmp_path, monkeypatch, frozen):
    children = []
    for index in range(2):
        folder = tmp_path / 'shards' / f'{index:04d}'
        ids = shards.slice_ids(frozen, (index, 2))
        children.append({'job_id': f'123_{index}', 'submission': str(folder),
                         'selected_ids': ids, 'shard': [index, 2], 'contract': '/contract'})
        items = [{'task': t, 'status': 'passed'} for t in (ids if index == 0 else ids[:1])]
        c.save(folder / 'report/stage-3-observed.json', {
            'stage': 3, 'complete': index == 0, 'contract_sha256': 'frozen',
            'dry_run': False, 'items': items})
    def wait(job, *args):
        if job['job_id'] == '123_1':
            raise RuntimeError('job ended TIMEOUT without complete report')
        return Path(job['submission']) / 'report'
    monkeypatch.setattr(c, 'wait_for_job', wait)
    root_job = {'contract': '/contract', 'submission': str(tmp_path), 'shards': children}
    result = sharding.collect(root_job, 5, 72)
    summary = c.summarize(result, ['a', 'b', 'c', 'd', 'e'], [3])
    assert summary['passed_all'] == 4
    assert {f['task_id'] for f in summary['failures']} == {'d'}
    merged = json.loads((result / 'stage-3-merged.json').read_text())
    assert len(merged['shards']['reports']) == 2
    unfinished = next(item for item in merged['items'] if item['task'] == 'd')
    assert unfinished['execution_missing']


def test_prebuild_terminal_failure_keeps_other_shards_and_evidence(tmp_path, monkeypatch):
    children = [{'job_id': str(i), 'submission': str(tmp_path / str(i)), 'selected_ids': [str(i)]}
                for i in range(2)]
    def wait(job, *args):
        if job['job_id'] == '1':
            raise RuntimeError('allocation interrupted')
        return {'passed': True, 'complete': True, 'failures': []}
    monkeypatch.setattr(c, 'wait_for_prebuild', wait)
    result = sharding.collect_prebuild({'submission': str(tmp_path), 'shards': children}, 5, 72)
    assert not result['passed'] and not result['complete']
    assert result['shards'][0]['passed']
    assert result['failures'][0]['task_ids'] == ['1']
    assert result['shards'][1]['evidence_archive'].endswith('1/evidence.tar.gz')


def test_supervisor_resources_are_audited_and_cannot_change_validation():
    decision = {'action': 'restart', 'kind': 'controller', 'cause': 'walltime exhausted',
                'evidence': ['/slurm.log'], 'restart_from': 'stage3', 'restart_prompt': 'retry',
                'resource_changes': {'time': '24:00:00', 'shard_size': 200}}
    assert recovery.validate_decision(decision, 'supervisor') == decision
    with pytest.raises(ValueError, match='Only a supervisor'):
        recovery.validate_decision(decision, 'recovery')
    state = {}
    resources.approve(state, decision['resource_changes'], decision='/decision.json', evidence=decision['evidence'])
    assert resources.effective({'time': '06:00:00', 'required_stages': [1, 3, 5]}, state) == {
        'time': '24:00:00', 'shard_size': 200, 'required_stages': [1, 3, 5]}
    assert state['resource_history'][0]['evidence'] == ['/slurm.log']
    for changes in ({'required_stages': [3]}, {'max_recoveries': 20}, {'shard_size': 0},
                    {'validation_args': ['--preparation-max-seconds', '1000']}, {'time': '0:00:00'}):
        with pytest.raises(ValueError):
            resources.validate(changes)


def test_scheduler_tracks_array_elements_instead_of_parent(tmp_path, monkeypatch):
    root = tmp_path / 'generation-0001'
    c.save(root / 'job.json', {'job_id': '123', 'shards': [{}]})
    c.save(root / 'array/submission.json', {'job_id': '123', 'array': True})
    for index in range(2):
        c.save(root / f'array/shards/{index}/job.json', {'job_id': f'123_{index}'})
    def sacct(command, **kwargs):
        assert command[command.index('-j') + 1] == '123_0,123_1'
        assert 'JobID,State' in command
        return type('Result', (), {'stdout': '123_0|COMPLETED|\n123_1|RUNNING|\n'})()
    monkeypatch.setattr(recovery.subprocess, 'run', sacct)
    assert recovery.jobs(tmp_path) == [
        {'job_id': '123_0', 'state': 'COMPLETED', 'finished': True},
        {'job_id': '123_1', 'state': 'RUNNING', 'finished': False}]


def test_uncertain_array_submission_blocks_a_fresh_generation(tmp_path):
    path = tmp_path / 'generation-0000/array/submission.json'
    c.save(path, {'status': 'submitting'})
    assert recovery.jobs(tmp_path) == [{'job_id': None, 'state': 'UNKNOWN',
                                       'finished': False, 'submission': str(path)}]


def test_resource_override_preserves_preparation_thresholds():
    config = {'validation_args': ['--preparation-max-seconds', '30', '--image-build-concurrency', '2']}
    state = {'resource_overrides': {'validation_args': ['--image-build-concurrency', '4']}}
    assert resources.effective(config, state)['validation_args'] == [
        '--preparation-max-seconds', '30', '--image-build-concurrency', '4']


def test_resource_reconfigure_preserves_history_and_rejects_active_jobs(tmp_path, monkeypatch):
    from contextlib import nullcontext
    patcher = tmp_path / 'patch.py'
    patcher.write_text('# existing repairs')
    config = {'work_root': str(tmp_path), 'patcher': str(patcher), 'time': '06:00:00'}
    path = tmp_path / 'config.json'
    c.save(path, config)
    state = {'config_sha256': c.config_digest(config), 'generation': 4, 'status': 'blocked',
             'history': [{'passed': 200}], 'repair_count': 3}
    c.save(tmp_path / 'loop-state.json', state)
    monkeypatch.setattr(c, 'load_config', lambda p: config)
    monkeypatch.setattr(recovery, 'controller_locks', lambda cfg: nullcontext())
    monkeypatch.setattr(recovery, 'infrastructure_revision', lambda: 'revision')
    monkeypatch.setattr(recovery, 'jobs', lambda work: [{'finished': False}])
    with pytest.raises(ValueError, match='active or uncertain'):
        resources.reconfigure(path, {'time': '24:00:00'})
    assert json.loads(path.read_text()) == config
    monkeypatch.setattr(recovery, 'jobs', lambda work: [])
    result = resources.reconfigure(path, {'time': '24:00:00', 'shard_size': 200})
    after = json.loads((tmp_path / 'loop-state.json').read_text())
    assert result['generation'] == 5 and after['phase'] == 'build'
    assert after['repair_count'] == 3 and after['history'] == state['history']
    assert after['config_sha256'] == c.config_digest(json.loads(path.read_text()))
    assert json.loads((Path(result['audit']) / 'state-before.json').read_text()) == state


def test_image_preparation_interrupt_does_not_wait_for_queued_builds(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from hpc import image_cache
    task = tmp_path / 'task'
    task.mkdir()
    (task / 'Dockerfile').write_text('FROM test\n')
    images = tmp_path / 'images'
    images.mkdir()
    shutdown = []
    class Pool:
        def __init__(self, **kwargs):
            pass
        def submit(self, fn, item):
            return object()
        def shutdown(self, **kwargs):
            shutdown.append(kwargs)
    def interrupted(pending):
        raise KeyboardInterrupt('allocation signal')
    monkeypatch.setattr(image_cache, 'ThreadPoolExecutor', Pool)
    monkeypatch.setattr(image_cache, 'as_completed', interrupted)
    args = SimpleNamespace(image_build_memory_mb=8192, image_build_cpus=4,
                           image_build_concurrency=2, image_build_timeout_sec=3600, cpus=48)
    with pytest.raises(KeyboardInterrupt):
        image_cache.prepare_images([task], images, tmp_path / 'shared', args)
    assert shutdown == [{'wait': False, 'cancel_futures': True}]


@pytest.mark.parametrize('active', [False, True])
def test_supervisor_applies_resources_only_after_reconciling_jobs(tmp_path, monkeypatch, active):
    patcher = tmp_path / 'dataset/patch.py'
    patcher.parent.mkdir()
    patcher.write_text('# repaired dataset')
    work = tmp_path / 'work'
    config = {'work_root': str(work), 'patcher': str(patcher), 'source': '/source',
              'max_supervisor_reviews': 6, 'time': '06:00:00'}
    state_path = work / 'loop-state.json'
    c.save(state_path, {'status': 'blocked', 'phase': 'build', 'generation': 4})
    answer = {'action': 'restart', 'restart_from': 'stage3', 'restart_prompt': 'Use smaller jobs',
              'kind': 'controller', 'cause': 'allocation exhausted', 'evidence': ['/slurm.log'],
              'resource_changes': {'time': '24:00:00', 'shard_size': 200}}
    def agent(cfg, role, context, *a, **kw):
        assert role == 'supervisor' and 'time' in context['allowed_resource_changes']
        return json.dumps(answer)
    monkeypatch.setattr(c, 'agent_call', agent)
    monkeypatch.setattr(recovery, 'jobs', lambda work: [{'finished': not active}])
    result = recovery.recover_locked(config, state_path, work / 'failure', role='supervisor')
    state = json.loads(state_path.read_text())
    if active:
        assert result == 'wait' and 'resource_overrides' not in state
    else:
        assert result == 'restart' and state['generation'] == 5
        assert state['resource_overrides'] == answer['resource_changes']
        assert state['resource_history'][0]['decision'].endswith('decision.json')
