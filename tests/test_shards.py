import json
from pathlib import Path

import pytest

from validation import shards
from validation.contract import assess_stage

TASKS = [f'task-{i:02d}' for i in range(7)]
SHA = '0' * 64


def fake_contract(monkeypatch, stages=(4, 5)):
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
def test_array_plan_uses_site_resources_and_keeps_task_slices(tmp_path, monkeypatch, site, partition, model, expected_gpus):
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
                partition=partition, array_concurrency=2, submit=False)
    plan = json.loads((out / 'plan.json').read_text())
    command = plan['command']
    assert plan['allocation']['cluster'] == site
    assert plan['allocation']['gpus'] == expected_gpus
    assert f'--partition={partition}' in command and '--array=0-2%2' in command
    assert f'OT_CLUSTER={site}' in next(c for c in command if c.startswith('--export='))
    assert f'hpc/{site}/validation.sbatch' in (out / 'array.sbatch').read_text()
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
    report = {'stage': stage, 'name': 'x', 'backend': 'apptainer', 'dry_run': False, 'complete': complete,
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
    report = report_for(4, 1, 3)
    assess_stage(contract, 4, report)
    assert report['contract_findings'] == [] and report['contract_sha256'] == contract['sha256']
    report['items'].pop()
    assess_stage(contract, 4, report)
    assert any('coverage mismatch' in f for f in report['contract_findings'])


@pytest.mark.parametrize('count', [1, 3, 7])
def test_merge_rebuilds_one_report_per_stage_with_provenance(tmp_path, monkeypatch, count):
    contract = fake_contract(monkeypatch)
    write_shards(tmp_path, count, {4: lambda i: report_for(4, i, count, retries={'retried': 1, 'recovered': ['t'] if i == 0 else [], 'mode': 'expected'}),
                                   5: lambda i: report_for(5, i, count, status='failed' if i == 0 else 'passed')})
    summary = shards.merge('contract.json', tmp_path, tmp_path / 'merged' / 'report')
    assert summary['complete'] and set(summary['stages']) == {4, 5}
    merged4 = json.loads(Path(summary['stages'][4]).read_text())
    assert [Path(i['task']).name for i in merged4['items']] == TASKS
    assert merged4['complete'] and not merged4['has_findings'] and merged4['contract_sha256'] == contract['sha256']
    assert merged4['shards']['count'] == count and len(merged4['shards']['timing']) == count
    assert merged4['outcome_retries'] == {'retried': count, 'recovered': ['t'], 'mode': 'expected'}
    merged5 = json.loads(Path(summary['stages'][5]).read_text())
    assert merged5['has_findings']
    execution = json.loads((tmp_path / 'merged' / 'execution.json').read_text())
    assert execution['status'] == 'findings' and execution['merged_from_shards'] == count and execution['apptainer_version'] == 'apptainer 1.5'


def test_merge_rejects_missing_duplicate_foreign_and_wrong_slices(tmp_path, monkeypatch):
    fake_contract(monkeypatch, stages=(4,))
    write_shards(tmp_path, 2, {4: lambda i: report_for(4, i, 3)})  # shard 2 of 3 missing
    with pytest.raises(ValueError, match=r'shards \[2\] of 3 have no report'):
        shards.merge('contract.json', tmp_path, tmp_path / 'm1' / 'report')
    extra = tmp_path / 'shards' / '0002' / 'report'; extra.mkdir(parents=True)
    (extra / 'stage-4-x.json').write_text(json.dumps(report_for(4, 1, 3)))  # claims slice 1 again
    with pytest.raises(ValueError, match='reported twice'):
        shards.merge('contract.json', tmp_path, tmp_path / 'm2' / 'report')
    (extra / 'stage-4-x.json').write_text(json.dumps(report_for(4, 2, 3, contract_sha='d' * 64)))
    with pytest.raises(ValueError, match='another contract'):
        shards.merge('contract.json', tmp_path, tmp_path / 'm3' / 'report')
    wrong = report_for(4, 2, 3); wrong['items'] = wrong['items'][:-1]
    (extra / 'stage-4-x.json').write_text(json.dumps(wrong))
    with pytest.raises(ValueError, match='differ from slice'):
        shards.merge('contract.json', tmp_path, tmp_path / 'm4' / 'report')


def test_incomplete_shard_marks_the_merged_report_incomplete(tmp_path, monkeypatch):
    fake_contract(monkeypatch, stages=(4,))
    write_shards(tmp_path, 2, {4: lambda i: report_for(4, i, 2, complete=(i == 0))})
    summary = shards.merge('contract.json', tmp_path, tmp_path / 'merged' / 'report')
    assert not summary['complete']
    assert json.loads(Path(summary['stages'][4]).read_text())['shards']['incomplete'] == [1]


def test_shard_job_verifies_only_its_slice_of_the_materialized_tasks(tmp_path, monkeypatch):
    import validation.contract as contract_module
    from types import SimpleNamespace
    contract = fake_contract(monkeypatch)
    contract['implementation'] = {}
    monkeypatch.setattr(contract_module, 'implementation', lambda: {})
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


def test_a_shard_refuses_to_derive_its_own_path_contract(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from validation.stages import normalize_paths
    import validation.contract, validation.data.selection
    # Import first: a module first imported while the names below are patched would keep the patched functions.
    import validation.stages.runner, validation.checks.instruction_suffix, data.utils.resolve_absolute_paths  # noqa: F401
    task = tmp_path / 'task-a'
    task.mkdir()
    monkeypatch.setattr(validation.contract, 'verify_materialized', lambda args: None)
    monkeypatch.setattr(validation.data.selection, 'discover_tasks', lambda tasks: [task])
    monkeypatch.setattr(validation.data.selection, 'select_paths', lambda tasks, args: tasks)
    args = SimpleNamespace(fix_instruction_paths=True, dry_run=False, tasks=tmp_path, out=tmp_path / 'out',
                           shard='0/2', _path_candidates=[task])
    with pytest.raises(ValueError, match='resolve paths before preparing the contract of a sharded run'):
        normalize_paths.prepare(args, [1, 3])
