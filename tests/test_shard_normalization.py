import hashlib
import json
from pathlib import Path

import pytest
from hpc import shard_normalization, shards
from validation.stages import normalize_paths
from validation.stages.runner import parser
from validation.contract import create, read, task_digest, task_records


@pytest.mark.parametrize('nodelist', [None, 'cpu-001'])
def test_plan_schedules_preparation_without_shard_slices(tmp_path, monkeypatch, nodelist):
    from hpc import validation_submit
    contract = {'sha256': 'a'*64, 'stages': [4], 'tasks': [{'task_id': 'a'}, {'task_id': 'b'}],
                'arguments': {'fix_instruction_paths': True, 'serve_model': None, 'gpus': None}}
    monkeypatch.setattr('validation.contract.read', lambda _: contract)
    monkeypatch.setattr('validation.contract.task_records', lambda c: c['tasks'])
    def snapshot(folder):
        code = folder / 'code'; code.mkdir(); return code
    monkeypatch.setattr(validation_submit, 'snapshot_code', snapshot)
    monkeypatch.setattr(shards, 'snapshot_matches', lambda *a: True)
    commands = []
    monkeypatch.setattr(shard_normalization.subprocess, 'check_output', lambda cmd, **kw: commands.append(cmd) or '123\n')
    out = tmp_path / 'run'
    assert shards.plan(tmp_path/'contract.json', 2, out, time='01:00:00', cpus=12,
                       memory='32G', partition='barnard', array_concurrency=2,
                       submit=True, cluster='zih', nodelist=nodelist) == '123'
    request = json.loads((out/'normalization/request.json').read_text())
    assert request['args']['shard'] is None
    assert request['args']['fix_instruction_paths'] is True
    assert request['shard_plan']['plan']['count'] == 2
    assert request['shard_plan']['plan']['nodelist'] == nodelist
    assert [c for c in commands[0] if c.startswith('--nodelist=')] == ([f'--nodelist={nodelist}'] if nodelist else [])
    assert not (out/'shards').exists()
    assert not any(arg.startswith('--array=') for arg in commands[0])


def fixture(tmp_path):
    for name in ['a', 'b']:
        task = tmp_path/'tasks'/name; task.mkdir(parents=True)
        (task/'instruction.md').write_text('Read `src/data.txt`.\n')
        (task/'task.toml').write_text('[agent]\ntimeout_sec=120\n')
    args = parser().parse_args([str(tmp_path/'tasks'), '--out', str(tmp_path/'results'),
                               '--no-fix-repository-leaks'])
    path = tmp_path/'parent.json'; create(args, [4], path); args.contract = path
    folder = tmp_path/'normalization'; folder.mkdir()
    request = {'parent_contract': str(path), 'folder': str(folder),
               'plan': {'out': str(tmp_path/'array'), 'count': 2, 'time': '01:00:00',
                        'cpus': 12, 'memory': '32G', 'partition': 'barnard',
                        'array_concurrency': 2, 'submit': True, 'cluster': 'zih'}}
    return args, path, folder, request


def test_normalization_exports_one_portable_contract_and_merge_checks_lineage(tmp_path, monkeypatch):
    from validation.task_inputs.materialize import materialize
    args, parent, folder, request = fixture(tmp_path)
    original = parent.read_bytes()
    monkeypatch.setattr('validation.rules.static.task_absolute_path.repair.flagged_paths', lambda _: (['src/data.txt'], {}))
    def discover(tasks, out, args):
        return [{'task': str(task), 'status': 'passed', 'instruction_path_diagnostics': [{
            'agent_baseline': {'status': 'completed', 'task_sha256': task_digest(task),
                'instruction_sha256': hashlib.sha256((task/'instruction.md').read_bytes()).hexdigest(),
                'findings': [{'relative_path': 'src/data.txt', 'status': 'unique',
                              'matches': ['/workspace/src/data.txt']}]},
            'observations': [], 'reference_unavailable': True}]} for task in tasks]
    monkeypatch.setattr(normalize_paths, 'discover', discover)
    launches = []
    monkeypatch.setattr(shard_normalization, 'launch_array', lambda request:
        launches.append((Path(request['folder'])/'normalized-contract.json',
                         {**request['plan'], '_paths_prepared': True})))
    assert shard_normalization.prepare_and_submit(args, [4], request) == 0
    assert parent.read_bytes() == original
    child = read(launches[0][0])
    assert launches[0][1]['_paths_prepared'] is True
    assert child['arguments']['fix_instruction_paths'] is True
    assert [t['task_id'] for t in task_records(child)] == ['a', 'b']
    assert child['arguments']['environment_kwargs'] == {}
    dest = tmp_path/'materialized'; materialize(Path(child['arguments']['tasks']), dest)
    fresh = parser().parse_args([str(dest)])
    assert normalize_paths.candidates(fresh, [dest/'a', dest/'b']) == []
    assert '/workspace/src/data.txt' in (dest/'a/instruction.md').read_text()
    pointer = folder/'prepared.json'
    assert shard_normalization.resolve_prepared(read(parent), pointer) == (launches[0][0], tmp_path/'array')
    record = json.loads(pointer.read_text()); record['parent_contract_sha256'] = 'bad'; pointer.write_text(json.dumps(record))
    with pytest.raises(ValueError, match='another parent'):
        shard_normalization.resolve_prepared(read(parent), pointer)


def test_failed_discovery_never_submits_array(tmp_path, monkeypatch):
    args, parent, folder, request = fixture(tmp_path)
    monkeypatch.setattr(normalize_paths, 'prepare', lambda *a: None)
    monkeypatch.setattr(normalize_paths, 'candidates', lambda args, tasks: tasks)
    monkeypatch.setattr(shards, 'plan', lambda *a, **kw: pytest.fail('must not submit'))
    with pytest.raises(ValueError, match='array not submitted'):
        shard_normalization.prepare_and_submit(args, [4], request)
    assert not (folder/'prepared.json').exists()


def test_continuation_plans_from_persistent_code(tmp_path, monkeypatch):
    captured = []
    monkeypatch.setattr(shard_normalization.subprocess, 'run', lambda *a, **kw: captured.append((a, kw)))
    request = {'folder': str(tmp_path/'normalization'), 'source_code': str(tmp_path/'frozen-code')}
    shard_normalization.launch_array(request)
    command, options = captured[0]
    assert options['cwd'] == request['source_code']
    assert options['env']['PYTHONPATH'] == request['source_code']
    assert options['check'] is True
    assert command[0][-1] == str(tmp_path/'normalization/request.json')


def test_prepared_array_keeps_automatic_paths_enabled(tmp_path, monkeypatch):
    from hpc import validation_submit
    contract = {'sha256': 'a'*64, 'stages': [4], 'tasks': [{'task_id': 'a'}, {'task_id': 'b'}],
                'arguments': {'fix_instruction_paths': True, 'serve_model': None, 'gpus': None}}
    monkeypatch.setattr('validation.contract.read', lambda _: contract)
    monkeypatch.setattr('validation.contract.task_records', lambda c: c['tasks'])
    def snapshot(folder):
        code = folder / 'code'; code.mkdir(); return code
    monkeypatch.setattr(validation_submit, 'snapshot_code', snapshot)
    monkeypatch.setattr(shards, 'snapshot_matches', lambda *a: True)
    out = tmp_path/'array'
    shards.plan(tmp_path/'contract.json', 2, out, time='01:00:00', cpus=12,
                memory='32G', partition='barnard', array_concurrency=2,
                submit=False, cluster='zih', _paths_prepared=True)
    requests = [json.loads(p.read_text()) for p in sorted(out.glob('shards/*/request.json'))]
    assert len(requests) == 2
    assert all(r['args']['fix_instruction_paths'] is True for r in requests)
    assert len({r['args']['contract'] for r in requests}) == 1
    assert [r['args']['shard'] for r in requests] == ['0/2', '1/2']


def test_merge_rejects_changed_normalized_coverage(tmp_path, monkeypatch):
    parent = {'sha256': 'parent', 'stages': [4], 'tasks': [{'task_id': 'a'}, {'task_id': 'b'}]}
    child = {'sha256': 'child', 'stages': [4], 'tasks': [{'task_id': 'a'}],
             'dataset': {'normalization': {'parent_contract_sha256': 'parent'}}}
    pointer = tmp_path/'prepared.json'
    pointer.write_text(json.dumps({'parent_contract_sha256': 'parent', 'contract_sha256': 'child',
                                   'contract': 'child.json', 'array': 'array'}))
    monkeypatch.setattr('validation.contract.read', lambda _: child)
    monkeypatch.setattr('validation.contract.task_records', lambda c: c['tasks'])
    with pytest.raises(ValueError, match='changed task coverage'):
        shard_normalization.resolve_prepared(parent, pointer)
