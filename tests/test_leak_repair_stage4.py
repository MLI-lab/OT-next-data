import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from validation.leak_repair import stage4


def test_stage_4_is_in_the_layout_and_later_stages_run_separately():
    from validation.stages.layout import NAMES
    assert NAMES[4] == 'leak_repair'
    stage4.check_selection([0, 1, 2, 3, 4])
    stage4.check_selection([4])
    with pytest.raises(ValueError, match='stage 4 runs in its own job'):
        stage4.check_selection([0, 1, 2, 3, 4, 5])


def test_dataset_dir_defaults_to_the_dataset_folder():
    args = SimpleNamespace(leak_repair_dataset_dir=None)
    assert stage4.dataset_dir(args, {'dataset': {'source': 'CalibForge'}}) == (stage4.ROOT / 'data/calibforge').resolve()
    assert stage4.dataset_dir(SimpleNamespace(leak_repair_dataset_dir=Path('/x/y')), {}) == Path('/x/y')
    with pytest.raises(ValueError):
        stage4.dataset_dir(args, {'dataset': {}})


def rows(names, status):
    return [{'dataset': 'd', 'task_id': n, 'content_sha256': f'h-{n}', 'repository_history': status} for n in names]


def test_replace_rows_takes_the_rerun_rows_of_the_changed_tasks(tmp_path):
    from validation.run import read, write
    write(tmp_path / 'run.parquet', rows('abc', 'warning'))
    write(tmp_path / 'rerun.parquet', rows('b', 'pass') + rows('z', 'pass'))
    assert stage4.replace_rows(tmp_path / 'run.parquet', tmp_path / 'rerun.parquet', {'b'}, tmp_path / 'out.parquet') == 1
    got = {r['task_id']: r['repository_history'] for r in read(tmp_path / 'out.parquet')}
    assert got == {'a': 'warning', 'b': 'pass', 'c': 'warning'}


def test_rerun_args_reuse_the_run_settings_for_the_subset(tmp_path):
    from validation.stages.runner import parser
    arguments = {k: (str(v) if isinstance(v, Path) else v) for k, v in vars(parser().parse_args([])).items()}
    arguments.update(tasks='/data/all.parquet', concurrency=64, network_mode='simulated', out='/runs/x',
                     path_resolution_report=['/a/report.json'], stages='0,1,2,3,4')
    contract = {'arguments': arguments}
    import pyarrow as pa
    import pyarrow.parquet as pq
    pq.write_table(pa.table({'path': ['a', 'b', 'c', 'd']}), tmp_path / 'tasks.parquet')
    prepare = stage4.rerun_args(contract, tmp_path / 'tasks.parquet', tmp_path, 8, prepare=True)
    assert prepare.tasks == tmp_path / 'tasks.parquet' and prepare.prepare_contract == tmp_path / 'contract.json'
    assert prepare.contract is None and prepare.submit == 'never' and prepare.concurrency == 4
    assert prepare.network_mode == 'simulated' and prepare.stages == '0,1,2,3'
    assert prepare.path_resolution_report == [Path('/a/report.json')]
    run = stage4.rerun_args(contract, tmp_path / 'tasks.parquet', tmp_path, 8, prepare=False)
    assert run.contract == tmp_path / 'contract.json' and run.prepare_contract is None and run.tasks is None
    assert prepare.min_tasks is None  # the run's --min-tasks counts the whole run, not the changed subset


def test_submit_writes_the_request_and_waits_for_the_stage_0_3_job(tmp_path, monkeypatch):
    import validation.contract
    contract = {'arguments': {'tasks': '/data/all.parquet', 'concurrency': 32, 'network_mode': 'simulated'},
                'dataset': {'source': 'CalibForge'}}
    monkeypatch.setattr(validation.contract, 'read', lambda path: contract)
    monkeypatch.setenv('OT_WORKSPACE', str(tmp_path))
    calls = []
    monkeypatch.setattr(stage4.subprocess, 'check_output', lambda command, **kw: calls.append(command) or '777\n')
    args = SimpleNamespace(leak_repair_model='claude-sonnet-5-5', leak_repair_minutes=120, leak_repair_concurrency=None,
                           leak_repair_dataset_dir=None, leak_repair_partition='cpu', leak_repair_time='10:00:00',
                           leak_repair_memory='128G', concurrency=4, dry_run=False)
    assert stage4.submit(args, tmp_path / 'contract.json', tmp_path / 'run', shards_root=tmp_path / 'run',
                         after='555') == '777'
    request = json.loads((tmp_path / 'run/leak-repair/request.json').read_text())
    assert request['concurrency'] == 32 and request['network_mode'] == 'simulated' and request['minutes'] == 120
    assert request['dataset'] == 'CalibForge' and request['dataset_dir'].endswith('data/calibforge')
    assert '--dependency=afterany:555' in calls[0] and calls[0][-2:] == ['--stage4', request['out'] + '/request.json']
    with pytest.raises(ValueError, match='already has a stage-4 job'):
        stage4.submit(args, tmp_path / 'contract.json', tmp_path / 'run', after='555')


def fake_request(tmp_path, results):
    path = tmp_path / 'leak-repair' / 'request.json'
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({'results': str(results), 'out': str(path.parent), 'tasks': str(tmp_path / 'tasks'),
                                'dataset': 'd', 'dataset_dir': str(tmp_path / 'data/d'), 'minutes': 30,
                                'concurrency': 2, 'runtime_cache': None, 'contract': 'c.json'}))
    return path


def test_run_without_changed_tasks_keeps_the_results(tmp_path, monkeypatch):
    monkeypatch.setattr(stage4, 'export_tasks', lambda request, report: {'path': 'repaired-tasks.parquet'})
    from validation.leak_repair import launch
    from validation.run import read, write
    write(tmp_path / 'results.parquet', rows('ab', 'warning'))
    monkeypatch.setattr(launch, 'repair_dataset', lambda *a, **k: {'repository_history': None})
    monkeypatch.setattr(stage4, 'changed_tasks', lambda rows, request: {})
    monkeypatch.setattr(stage4, 'task_plans', lambda rows, request: {'a': FALSE_POSITIVE})
    report = stage4.run(fake_request(tmp_path, tmp_path / 'results.parquet'), None)
    assert report['complete'] and report['changed'] == [] and report['settled'] == 1
    # the false positive passes in the results, the other task keeps its warning
    assert {r['task_id']: r['repository_history'] for r in read(Path(report['results']))} == {'a': 'pass', 'b': 'warning'}


FALSE_POSITIVE = {'from': 'h-a', 'to': 'h-a', 'rules': {'repository_history': {
    'status': 'pass', 'applied': True, 'details': 'false positive: the history is the deliverable'}}}


def test_changed_tasks_are_rerun_as_a_sharded_run_then_finalized(tmp_path, monkeypatch):
    monkeypatch.setattr(stage4, 'export_tasks', lambda request, report: {'path': 'repaired-tasks.parquet'})
    from validation.leak_repair import launch
    from validation.run import read, write
    write(tmp_path / 'results.parquet', rows('ab', 'warning'))
    path = fake_request(tmp_path, tmp_path / 'results.parquet')
    request = json.loads(path.read_text())
    request.update(repo=str(tmp_path / 'repo'), submit_env={'OT_WORKSPACE': '/w', 'PATH': '/bin'})
    path.write_text(json.dumps(request))
    monkeypatch.setattr(launch, 'repair_dataset', lambda *a, **k: {'repository_history': Path('f.py')})
    monkeypatch.setattr(stage4, 'changed_tasks', lambda rows, request: {'b': {}})
    monkeypatch.setattr(stage4, 'task_plans', lambda rows, request: {
        'a': FALSE_POSITIVE, 'b': {'from': 'h-b', 'to': 'h-b2', 'rules': {}}})
    monkeypatch.setattr(stage4, 'subset_parquet', lambda tasks, names, target: target)
    monkeypatch.setattr(stage4, 'publish_images', lambda request, images: [])
    import validation.contract
    monkeypatch.setattr(validation.contract, 'read', lambda p: {'arguments': {'partition': 'cpu'}})
    out = tmp_path / 'leak-repair'
    commands = []

    def fake_run(command, cwd, env, check):
        commands.append((command, cwd, env))
        if 'plan' in command:
            (out / 'rerun/run').mkdir(parents=True)
            (out / 'rerun/run/array-job-id.txt').write_text('111\n')
    monkeypatch.setattr(stage4.subprocess, 'run', fake_run)
    monkeypatch.setattr(stage4.subprocess, 'check_output', lambda command, cwd, env, text: commands.append(
        (command, cwd, env)) or '222\n')
    report = stage4.run(path, None)
    prepare, plan, final = commands
    assert prepare[0][-2:] == ['prepare-rerun', str(path)] and prepare[1] == tmp_path / 'repo'
    assert prepare[2] == {'OT_WORKSPACE': '/w', 'PATH': '/bin', 'PYTHONPATH': str(tmp_path / 'repo')}
    assert '--submit' in plan[0] and plan[0][plan[0].index('--count') + 1] == '1'
    assert plan[0][plan[0].index('--contract') + 1] == str(out / 'rerun/contract.json')
    assert '--dependency=afterany:111' in final[0] and final[0][-2:] == ['--finalize', str(path)]
    assert report['rerun']['array_job'] == '111' and report['rerun']['finalize_job'] == '222'
    assert not report['complete'] and not (out / 'results.parquet').exists()
    # the finalize job
    import hpc.shards
    monkeypatch.setattr(hpc.shards, 'merge', lambda contract, root, target: write(Path(target) / 'results.parquet',
                                                                                  rows('b', 'pass')))
    final = stage4.finalize(path)
    assert final['complete'] and final['rerun']['rows'] == 1
    assert {r['task_id']: r['repository_history'] for r in read(out / 'results.parquet')} == {'a': 'pass', 'b': 'pass'}
    assert final['settled'] == 1
    assert json.loads((out / 'stage-4.json').read_text())['complete']


def test_rerun_allocation_keeps_shards_two_per_node():
    assert stage4.rerun_allocation(2) == (1, 48, '128G')
    assert stage4.rerun_allocation(100) == (1, 48, '128G')
    assert stage4.rerun_allocation(101) == (1, 192, '256G')
    assert stage4.rerun_allocation(4800) == (9, 192, '256G')


def test_submit_environment_drops_secrets_and_slurm_variables():
    env = stage4.submit_environment({'OT_WORKSPACE': '/w', 'HF_TOKEN': 'x', 'OPENAI_API_KEY': 'y',
                                     'SLURM_JOB_ID': '1', 'PATH': '/bin', 'CLAUDE_CODE_OAUTH_TOKEN': 'z',
                                     'OT_SECRETS_FILE': '/home/u/.env-judge-cli'})
    assert env == {'OT_WORKSPACE': '/w', 'PATH': '/bin', 'OT_SECRETS_FILE': '/home/u/.env-judge-cli'}


def test_sessions_write_to_the_work_folder_and_are_packed_into_out(tmp_path, monkeypatch):
    monkeypatch.setattr(stage4, 'export_tasks', lambda request, report: {'path': 'repaired-tasks.parquet'})
    import tarfile
    from validation.leak_repair import launch
    from validation.run import write
    write(tmp_path / 'results.parquet', rows('ab', 'warning'))
    work = tmp_path / 'local'
    monkeypatch.setenv('OT_STAGE4_WORK', str(work))
    seen = {}

    def fake_repair(results, tasks, dataset_dir, dataset, sessions, *a, **k):
        seen['sessions'] = sessions
        (sessions / 'verifier_tamper').mkdir(parents=True)
        (sessions / 'verifier_tamper/x.txt').write_text('x')
        return {'verifier_tamper': None}
    monkeypatch.setattr(launch, 'repair_dataset', fake_repair)
    monkeypatch.setattr(stage4, 'changed_tasks', lambda rows, request: {})
    stage4.run(fake_request(tmp_path, tmp_path / 'results.parquet'), None)
    out = tmp_path / 'leak-repair'
    assert seen['sessions'] == work / 'sessions' and not (out / 'sessions').exists()
    with tarfile.open(out / 'sessions.tar.gz') as tar:
        assert 'sessions/verifier_tamper/x.txt' in tar.getnames()
    assert json.loads((out / 'stage-4.json').read_text())['packed']['sessions'] == str(out / 'sessions.tar.gz')


def test_a_failed_session_still_packs_and_records_the_report(tmp_path, monkeypatch):
    from validation.leak_repair import launch
    from validation.run import write
    write(tmp_path / 'results.parquet', rows('ab', 'warning'))
    work = tmp_path / 'local'
    monkeypatch.setenv('OT_STAGE4_WORK', str(work))

    def fake_repair(results, tasks, dataset_dir, dataset, sessions, *a, **k):
        (sessions / 'x').mkdir(parents=True)
        raise RuntimeError('session broke')
    monkeypatch.setattr(launch, 'repair_dataset', fake_repair)
    with pytest.raises(RuntimeError, match='session broke'):
        stage4.run(fake_request(tmp_path, tmp_path / 'results.parquet'), None)
    report = json.loads((tmp_path / 'leak-repair/stage-4.json').read_text())
    assert not report['complete'] and 'sessions' in report['packed']


def test_network_env_sets_the_bridge_network_mode():
    from hpc.leak_repair_job import network_env
    base = {'https_proxy': 'http://p', 'APPTAINERENV_x': '1', 'PATH': '/bin'}
    simulated = network_env(base, 'simulated')
    assert simulated['OT_NET_SIMULATED'] == '1' and simulated['OT_NET_ISOLATION'] == '0'
    assert 'https_proxy' not in simulated and 'APPTAINERENV_x' not in simulated
    host = network_env(base, 'host')
    assert host['https_proxy'] == 'http://p' and 'OT_NET_SIMULATED' not in host
    assert network_env(base, 'isolated')['OT_NET_ISOLATION'] == '1'


def stage4_args(**overrides):
    values = dict(leak_repair_model='claude-sonnet-5-5', leak_repair_minutes=600, leak_repair_concurrency=None,
                  leak_repair_dataset_dir=None, leak_repair_partition=None, leak_repair_time='24:00:00',
                  leak_repair_memory='128G', leak_repair_harness='mini-swe-agent', leak_repair_serve_weights=None,
                  concurrency=4, dry_run=False)
    values.update(overrides)
    return SimpleNamespace(**values)


def test_claude_runs_on_cpus_and_a_local_model_gets_its_gpus(monkeypatch):
    monkeypatch.setenv('OT_CLUSTER', 'helma')
    assert stage4.local_model('claude-sonnet-5-5') is None
    assert stage4.local_model('glm-5.3-flash')[0] == 'glm-5.3-flash'
    options, gpus = stage4.allocation(stage4_args(), None)
    assert gpus == 0 and '--partition=cpu' in options and '--mem=128G' in options and '--cpus-per-task=48' in options
    options, _ = stage4.allocation(stage4_args(leak_repair_cpus=192, leak_repair_memory='512G'), None)
    assert '--cpus-per-task=192' in options and '--mem=512G' in options
    options, gpus = stage4.allocation(stage4_args(), stage4.local_model('glm-5.3-flash'))
    assert gpus == 4 and '--partition=h200' in options and '--gres=gpu:h200:4' in options
    assert not any(o.startswith('--mem') for o in options)  # Helma GPU jobs get memory per GPU
    options, _ = stage4.allocation(stage4_args(leak_repair_partition='preempt'), stage4.local_model('glm-5.3-flash'))
    assert '--partition=preempt' in options and '--gres=gpu:h200:4' in options


def test_submit_with_a_local_model_records_the_server(tmp_path, monkeypatch):
    import validation.contract
    monkeypatch.setenv('OT_CLUSTER', 'helma')
    monkeypatch.setenv('OT_WORKSPACE', str(tmp_path))
    monkeypatch.setattr(validation.contract, 'read', lambda path: {
        'arguments': {'tasks': '/t.parquet', 'concurrency': 8, 'network_mode': 'simulated'}, 'dataset': {'source': 'X'}})
    calls = []
    monkeypatch.setattr(stage4.subprocess, 'check_output', lambda command, **kw: calls.append(command) or '9\n')
    stage4.submit(stage4_args(leak_repair_model='glm-5.3-flash'), tmp_path / 'c.json', tmp_path / 'run')
    request = json.loads((tmp_path / 'run/leak-repair/request.json').read_text())
    assert request['serve']['model'] == 'glm-5.3-flash' and request['serve']['gpus'] == 4
    assert request['serve']['harness'] == 'mini-swe-agent' and request['serve']['context'] == 262144
    assert '--export=ALL,OT_LEAK_REPAIR_LOCAL=1' in calls[0] and '--gres=gpu:h200:4' in calls[0]


def test_a_usage_limit_reruns_the_session_seeded_with_its_work(tmp_path, monkeypatch):
    import asyncio
    from validation.leak_repair import launch
    from validation.stages import harbor as runtime
    import validation.stages.env_review as env_review
    import validation.stages.image_review as image_review
    task = tmp_path / 'task'
    (task / 'setup_files/work-seed').mkdir(parents=True)
    jobs = []

    async def execute_job(config):
        job = tmp_path / f"job-{len(jobs)}"
        trial = job / 'trial'
        (trial / 'agent').mkdir(parents=True)
        (trial / 'agent/claude-code.txt').write_text('limit')
        (trial / 'artifacts/work').mkdir(parents=True)
        (trial / 'artifacts/work/leak_repairs.py').write_text(f'# attempt {len(jobs)}\n')
        jobs.append(config['job_name'])
        return job
    monkeypatch.setattr(runtime, 'execute_job', execute_job)
    monkeypatch.setattr(runtime, 'trial_results', lambda job: [(job / 'trial', {})])
    monkeypatch.setattr(env_review, 'usage_limited', lambda trial: trial.parent.name == 'job-0')
    monkeypatch.setattr(image_review, 'seconds_until_reset', lambda text: 0)
    job, limits = asyncio.run(launch.execute_with_limits({'job_name': 'session'}, task, tmp_path / 'out'))
    assert jobs == ['session', 'session-after-limit-1'] and job.name == 'job-1'
    assert limits == [{'job': str(tmp_path / 'job-0'), 'wait_seconds': 0, 'seeded': True}]
    assert (task / 'setup_files/work-seed/leak_repairs.py').read_text() == '# attempt 0\n'


def test_link_images_links_plain_images_and_reports_missing_ones(tmp_path):
    from hpc.leak_repair_job import link_images
    source = tmp_path / 'images'
    (source / 'bundles-v1').mkdir(parents=True)
    (source / 'build_a-aaaaaaaaaaaa.sif').write_bytes(b'a')
    (source / 'build_a-aaaaaaaaaaaa.deferred.json').write_text('{}')
    (source / 'bundles-v1/build_b-bbbbbbbbbbbb.sif').write_bytes(b'b')
    target = tmp_path / 'local'  # created by link_images
    assert link_images({'aaaaaaaaaaaa', 'bbbbbbbbbbbb', 'cccccccccccc'}, source, target) == ['cccccccccccc']
    assert (target / 'build_a-aaaaaaaaaaaa.sif').is_symlink() and (target / 'build_a-aaaaaaaaaaaa.sif').read_bytes() == b'a'
    assert (target / 'build_a-aaaaaaaaaaaa.deferred.json').is_symlink()
    assert (target / 'build_b-bbbbbbbbbbbb.sif').read_bytes() == b'b'


def test_only_the_selected_checks_get_sessions(tmp_path, monkeypatch):
    monkeypatch.setattr(stage4, 'export_tasks', lambda request, report: {'path': 'repaired-tasks.parquet'})
    from validation.leak_repair import launch
    from validation.run import write
    write(tmp_path / 'results.parquet', rows('ab', 'warning'))
    path = fake_request(tmp_path, tmp_path / 'results.parquet')
    request = json.loads(path.read_text())
    request['checks'] = ['verifier_tamper']
    path.write_text(json.dumps(request))
    seen = {}
    monkeypatch.setattr(launch, 'repair_dataset', lambda *a, **k: seen.update(k) or {})
    monkeypatch.setattr(stage4, 'changed_tasks', lambda rows, request: {})
    stage4.run(path, None)
    assert seen['checks'] == ('verifier_tamper',)


def test_the_job_starter_works_for_the_shared_model_startup(tmp_path):
    """hpc.model_startup calls start(command, name) and waits on the returned process."""
    import os
    from hpc import leak_repair_job
    start = leak_repair_job.starter(tmp_path)
    process = start(['sh', '-c', 'echo served'], 'model')
    assert process.wait() == 0 and process in leak_repair_job.PROCESSES
    assert (tmp_path / 'model.log').read_text() == 'served\n'
    assert start(['sh', '-c', 'echo $X'], 'bridge', {'X': 'y', 'PATH': os.environ['PATH']}).wait() == 0
    assert (tmp_path / 'bridge.log').read_text() == 'y\n'


def test_model_steps_ask_for_the_gpu_type_of_the_job_partition(monkeypatch):
    from hpc.leak_repair_job import gpu_type
    from hpc.validation_worker import gpu_request
    monkeypatch.setenv('OT_CLUSTER', 'helma')
    monkeypatch.setenv('SLURM_JOB_PARTITION', 'preempt')
    assert gpu_request(gpu_type('preempt'), 4) == '--gres=gpu:h200:4'
    monkeypatch.setenv('SLURM_JOB_PARTITION', 'h100')
    assert gpu_request(gpu_type('preempt'), 4) == '--gres=gpu:h100:4'


def test_a_not_fixable_decision_stays_in_the_cell_once_through_rewrites(tmp_path):
    from validation.run import read, repair_rows, write
    write(tmp_path / 'r.parquet', [{'dataset': 'd', 'task_id': 'a', 'content_sha256': 'h',
                                    'verifier_tamper': 'warning',
                                    'details': json.dumps({'verifier_tamper': {'details': 'grading ran python'}})}])
    plan = {'a': {'from': 'h', 'to': 'h', 'rules': {'verifier_tamper': {
        'status': 'warning', 'applied': False, 'details': 'not fixable: tests run python', 'evidence': []}}}}
    write(tmp_path / 'r.parquet', repair_rows(read(tmp_path / 'r.parquet'), plan))
    expected = 'not fixable: tests run python; check finding: grading ran python'
    for _ in range(2):
        row = read(tmp_path / 'r.parquet')[0]
        assert row['verifier_tamper'] == 'warning'
        assert json.loads(row['details'])['verifier_tamper']['details'] == expected
        write(tmp_path / 'r.parquet', repair_rows([row], plan))


def test_export_failure_does_not_mark_stage4_complete(tmp_path, monkeypatch):
    from validation.leak_repair import launch
    from validation.run import write
    write(tmp_path / 'results.parquet', rows('ab', 'warning'))
    monkeypatch.setattr(launch, 'repair_dataset', lambda *a, **k: {})
    monkeypatch.setattr(stage4, 'changed_tasks', lambda *a: {})
    monkeypatch.setattr(stage4, 'task_plans', lambda *a: {})
    def reject(request, report):
        raise ValueError('exported task hash differs from final results')
    monkeypatch.setattr(stage4, 'export_tasks', reject)
    path = fake_request(tmp_path, tmp_path / 'results.parquet')
    with pytest.raises(ValueError, match='exported task hash'):
        stage4.run(path, None)
    assert json.loads((path.parent / 'stage-4.json').read_text())['complete'] is False
