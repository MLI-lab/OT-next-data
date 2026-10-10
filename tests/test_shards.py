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
