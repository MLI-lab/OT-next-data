import json
from pathlib import Path

import pytest

from hpc import shards
from validation.contract import assess_stage

TASKS = [f'task-{i:02d}' for i in range(7)]
SHA = '0' * 64


def fake_contract(monkeypatch, stages=(2, 3)):
    contract = {'sha256': 'c' * 64, 'stages': list(stages), 'tasks': [{'task_id': t, 'sha256': SHA} for t in TASKS],
                'success_criteria': {'minimum_tasks': len(TASKS)}, 'arguments': {}}
    monkeypatch.setattr(shards, 'sys', shards.sys)
    import validation.contract as contract_module
    monkeypatch.setattr(contract_module, 'read', lambda path: contract)
    monkeypatch.setattr(contract_module, 'task_records', lambda c: c['tasks'])
    return contract


@pytest.mark.parametrize('site,partition,model,expected_gpus', [
    ('helma', 'cpu', None, 0),
    ('zih', 'barnard', None, 0),
    ('zih', 'capella', 'qwen3-30b-instruct-2507', 1),
])
@pytest.mark.parametrize('nodelist', [None, 'cpu-[001,002]'])
def test_array_plan_uses_site_resources_and_keeps_task_slices(tmp_path, monkeypatch, site, partition, model, expected_gpus, nodelist):
    from hpc import validation_submit
    contract = fake_contract(monkeypatch, stages=(6, 7) if model else (4, 5))
    contract['arguments'].update(submit=site, serve_model=model, gpus=None, serve_cpus=4)
    def snapshot(folder):
        target = folder / 'code'
        target.mkdir()
        return target
    monkeypatch.setattr(validation_submit, 'snapshot_code', snapshot)
    monkeypatch.setattr(shards, 'snapshot_matches', lambda *args: True)
    out = tmp_path / 'run with spaces'
    shards.plan(tmp_path / 'contract.json', 3, out, time='01:00:00', cpus=12, memory='128G',
                partition=partition, array_concurrency=2, submit=False, nodelist=nodelist)
    plan = json.loads((out / 'plan.json').read_text())
    command = plan['command']
    assert plan['allocation']['nodelist'] == nodelist
    assert [c for c in command if c.startswith('--nodelist=')] == ([f'--nodelist={nodelist}'] if nodelist else [])
    assert plan['allocation']['cluster'] == site
    assert plan['allocation']['gpus'] == expected_gpus
    assert f'--partition={partition}' in command and '--array=0-2%2' in command
    assert f'OT_CLUSTER={site}' in next(c for c in command if c.startswith('--export='))
    script = (out / 'array.sbatch').read_text()
    assert f'hpc/{site}/validation.sbatch' in script
    # One packed snapshot on shared storage, unpacked per job on node-local disk.
    assert (out / 'code.tar.gz').is_file() and not (out / 'code').exists()
    assert 'tar -xzf' in script and 'VALIDATION_REPO="$code/code"' in script
    assert 'VALIDATION_REPO' not in next(c for c in command if c.startswith('--export='))
    if site == 'zih':
        assert '--account=p_agents_finetuning' in command
    assert ('--gres=gpu:1' in command) == bool(expected_gpus)
    requests = [json.loads(p.read_text()) for p in sorted(out.glob('shards/*/request.json'))]
    assert [r['args']['shard'] for r in requests] == ['0/3', '1/3', '2/3']
    assert all(r['args']['gpus'] == expected_gpus and r['args']['serve_model'] == model for r in requests)
    assert all(r['args']['cpus'] == (48 if site == 'helma' else 12) for r in requests)


def test_gpu_array_rejects_cpu_partition_before_writing(tmp_path, monkeypatch):
    contract = fake_contract(monkeypatch)
    contract['arguments'].update(submit='zih', serve_model='qwen3-30b-thinking', gpus=None)
    with pytest.raises(ValueError, match='CPU partition cannot serve'):
        shards.plan('contract.json', 3, tmp_path / 'run', time='01:00:00', cpus=12, memory='128G',
                    partition='barnard', array_concurrency=2, submit=False)
    assert not (tmp_path / 'run').exists()


def report_for(stage, shard, count, status='passed', complete=True, retries=None, contract_sha='c' * 64):
    tasks = shards.slice_of(TASKS, (shard, count), key=lambda t: t)
    report = {'stage_layout': 'environment-first-v3', 'stage': stage, 'name': {2: 'oracle_validation', 3: 'nop_validation', 4: 'static_checks', 5: 'llm_rubric_review'}[stage], 'backend': 'apptainer', 'dry_run': False, 'complete': complete,
              'contract_sha256': contract_sha, 'shard': {'index': shard, 'count': count, 'tasks': tasks},
              'items': [{'task': f'/tasks/{t}', 'status': status, 'rewards': [1.0]} for t in tasks],
              'has_findings': status != 'passed', 'timing': {'slurm_job_id': f'{stage}{shard}', 'wall_seconds': 10}}
    if retries:
        report['outcome_retries'] = retries
    return report


def write_shards(root, count, stage_reports):
    for index in range(count):
        folder = root / 'shards' / f'{index:04d}' / 'report'
        folder.mkdir(parents=True)
        for stage, make in stage_reports.items():
            (folder / f'stage-{stage}-{index}.json').write_text(json.dumps(make(index)))
        (root / 'shards' / f'{index:04d}' / 'execution.json').write_text(json.dumps({'runtime': {'python': '3.12'}, 'apptainer_version': 'apptainer 1.5'}))


def test_parse_and_slice():
    assert shards.parse_shard('2/3') == (2, 3) and shards.parse_shard(None) is None
    with pytest.raises(ValueError):
        shards.parse_shard('3/3')
    slices = [shards.slice_of(TASKS, (i, 3), key=lambda t: t) for i in range(3)]
    assert sorted(sum(slices, [])) == TASKS and all(slices)


def test_assess_stage_judges_only_the_shard_slice(monkeypatch):
    contract = fake_contract(monkeypatch)
    report = report_for(2, 1, 3)
    assess_stage(contract, 3, report)
    assert report['contract_findings'] == [] and report['contract_sha256'] == contract['sha256']
    report['items'].pop()
    assess_stage(contract, 3, report)
    assert any('coverage mismatch' in f for f in report['contract_findings'])


@pytest.mark.parametrize('count', [1, 3, 7])
def test_merge_rebuilds_one_report_per_stage_with_provenance(tmp_path, monkeypatch, count):
    contract = fake_contract(monkeypatch)
    write_shards(tmp_path, count, {2: lambda i: report_for(2, i, count, retries={'retried': 1, 'recovered': ['t'] if i == 0 else [], 'mode': 'expected'}),
                                   3: lambda i: report_for(3, i, count, status='failed' if i == 0 else 'passed')})
    summary = shards.merge('contract.json', tmp_path, tmp_path / 'merged' / 'report')
    assert summary['complete'] and set(summary['stages']) == {3, 2}
    merged4 = json.loads(Path(summary['stages'][2]).read_text())
    assert [Path(i['task']).name for i in merged4['items']] == TASKS
    assert merged4['complete'] and not merged4['has_findings'] and merged4['contract_sha256'] == contract['sha256']
    assert merged4['shards']['count'] == count and len(merged4['shards']['timing']) == count
    assert merged4['outcome_retries'] == {'retried': count, 'recovered': ['t'], 'mode': 'expected'}
    merged5 = json.loads(Path(summary['stages'][3]).read_text())
    assert merged5['has_findings']
    execution = json.loads((tmp_path / 'merged' / 'execution.json').read_text())
    assert execution['status'] == 'findings' and execution['merged_from_shards'] == count and execution['apptainer_version'] == 'apptainer 1.5'


def test_merge_rejects_missing_duplicate_foreign_and_wrong_slices(tmp_path, monkeypatch):
    fake_contract(monkeypatch, stages=(2,))
    write_shards(tmp_path, 2, {2: lambda i: report_for(2, i, 3)})  # shard 2 of 3 missing
    with pytest.raises(ValueError, match=r'shards \[2\] of 3 have no report'):
        shards.merge('contract.json', tmp_path, tmp_path / 'm1' / 'report')
    extra = tmp_path / 'shards' / '0002' / 'report'; extra.mkdir(parents=True)
    (extra / 'stage-2-x.json').write_text(json.dumps(report_for(2, 1, 3)))  # claims slice 1 again
    with pytest.raises(ValueError, match='reported twice'):
        shards.merge('contract.json', tmp_path, tmp_path / 'm2' / 'report')
    (extra / 'stage-2-x.json').write_text(json.dumps(report_for(2, 2, 3, contract_sha='d' * 64)))
    with pytest.raises(ValueError, match='another contract'):
        shards.merge('contract.json', tmp_path, tmp_path / 'm3' / 'report')
    wrong = report_for(2, 2, 3); wrong['items'] = wrong['items'][:-1]
    (extra / 'stage-2-x.json').write_text(json.dumps(wrong))
    with pytest.raises(ValueError, match='differ from slice'):
        shards.merge('contract.json', tmp_path, tmp_path / 'm4' / 'report')


def test_merge_preserves_repair_evidence_from_every_shard(tmp_path, monkeypatch):
    from validation import run as results
    fake_contract(monkeypatch, stages=(2,))
    def make(index):
        report = report_for(2, index, 3)
        tasks = report['shard']['tasks']
        report['task_content_sha256'] = {task: SHA for task in tasks}
        report['automatic_repairs'] = {task: {'content_sha256': SHA, 'rules': {
            'instruction-suffix': {'applied': True, 'evidence': []}}} for task in tasks}
        return report
    write_shards(tmp_path, 3, {2: make})
    out = tmp_path / 'merged' / 'report'
    summary = shards.merge('contract.json', tmp_path, out)
    merged = json.loads(Path(summary['stages'][2]).read_text())
    assert set(merged['automatic_repairs']) == set(TASKS)
    rows = results.read(out / 'results.parquet')
    assert len(rows) == len(TASKS)
    assert all(json.loads(row['automatic_repairs'])['instruction-suffix']['applied'] for row in rows)


def test_incomplete_shard_marks_the_merged_report_incomplete(tmp_path, monkeypatch):
    fake_contract(monkeypatch, stages=(2,))
    write_shards(tmp_path, 2, {2: lambda i: report_for(2, i, 2, complete=(i == 0))})
    summary = shards.merge('contract.json', tmp_path, tmp_path / 'merged' / 'report')
    assert not summary['complete']
    assert json.loads(Path(summary['stages'][2]).read_text())['shards']['incomplete'] == [1]


def test_shard_job_verifies_only_its_slice_of_the_materialized_tasks(tmp_path, monkeypatch):
    import validation.contract as contract_module
    from types import SimpleNamespace
    contract = fake_contract(monkeypatch)
    contract['implementation'] = {}
    monkeypatch.setattr(contract_module, 'implementation', lambda *args, **kwargs: {})
    monkeypatch.setattr(contract_module, 'task_digest', lambda path: SHA)
    mine = shards.slice_ids(contract, (1, 3))
    for name in mine:
        (tmp_path / name).mkdir(); (tmp_path / name / 'task.toml').write_text('')
    args = SimpleNamespace(contract='contract.json', tasks=tmp_path, limit=None, task_id_range=None, shard='1/3')
    assert contract_module.verify_materialized(args) is contract
    args.shard = '0/3'   # another slice's tasks are not here
    with pytest.raises(ValueError, match='differ from frozen contract'):
        contract_module.verify_materialized(args)
    args.shard = None    # an unsharded job needs the whole inventory
    with pytest.raises(ValueError, match='differ from frozen contract'):
        contract_module.verify_materialized(args)


def test_plan_cli_forwards_node_constraint(tmp_path, monkeypatch):
    captured = {}
    def fake_plan(*args, **kwargs):
        captured.update(kwargs)
    monkeypatch.setattr(shards, 'plan', fake_plan)
    assert shards.main(['plan', '--contract', str(tmp_path / 'contract.json'),
                        '--count', '2', '--out', str(tmp_path / 'out'),
                        '--time', '00:30:00', '--nodelist', 'cpu-001']) == 0
    assert captured['nodelist'] == 'cpu-001'
