"""Resolve paths once on a compute node before launching a shared-contract array."""
import copy
import json
import os
import sys
from pathlib import Path
import shlex
import subprocess


def plan_preparation(contract_path, contract, out, code_root, config, *, count, time,
                     cpus, memory, partition, array_concurrency, submit, exclusive,
                     cluster, gpus, nodelist=None):
    from validation.stages.runner import save
    folder = out / 'normalization'
    folder.mkdir()  # Existing requests must be reconciled, never blindly resubmitted.
    payload = dict(contract['arguments'])
    payload.update(contract=str(Path(contract_path).resolve()), prepare_contract=None,
                   submit='never', out=str(folder), shard=None, partition=partition,
                   gpus=gpus, cpus=cpus, time=time, memory=memory)
    followup = dict(count=count, out=str(out / 'array'), time=time, cpus=cpus,
                    memory=memory, partition=partition, array_concurrency=array_concurrency,
                    submit=True, exclusive=exclusive, cluster=cluster, nodelist=nodelist)
    save(folder / 'request.json', {'args': payload, 'stages': contract['stages'],
         'shard_plan': {'parent_contract': str(Path(contract_path).resolve()),
                        'folder': str(folder), 'source_code': str(code_root), 'plan': followup}})
    script = folder / 'prepare.sbatch'
    script.write_text('#!/bin/bash\nset -euo pipefail\nexec bash ' +
        shlex.quote(str(code_root / 'hpc' / config.name / 'validation.sbatch')) + ' ' +
        shlex.quote(str(folder / 'request.json')) + '\n')
    command = ['sbatch', '--parsable', f'--partition={partition}', '--nodes=1', '--ntasks=1',
               f'--cpus-per-task={cpus}', f'--time={time}', '--signal=B:USR1@120',
               *(['--exclusive'] if exclusive else []), *([f'--nodelist={nodelist}'] if nodelist else []),
               *([f'--mem={memory}'] if memory else []),
               *([f'--account={config.account}'] if config.account else []),
               *(['--gres=' + config.gpu_gres.format(partition=partition, n=gpus)] if gpus else []),
               f'--output={folder}/slurm-%j.out',
               f'--export=ALL,VALIDATION_REPO={code_root},OT_CLUSTER={config.name}', str(script)]
    save(out / 'plan.json', {'operation': 'normalize-paths-then-shard',
         'contract': str(Path(contract_path).resolve()), 'contract_sha256': contract['sha256'],
         'count': count, 'stages': contract['stages'], 'command': command,
         'normalization': str(folder), 'array': followup})
    if not submit:
        return None
    job = subprocess.check_output(command, text=True).strip()
    (folder / 'job-id.txt').write_text(job + '\n')
    return job


def prepare_and_submit(args, stages, request):
    """Keep the input immutable, export portable inputs, then schedule the array."""
    import pyarrow as pa
    import pyarrow.parquet as pq
    from validation.contract import create, read, task_records, verify_materialized
    from validation.task_inputs.selection import discover_tasks
    from validation.rules.static.task_absolute_path.path_cache import KEY, read as read_cache
    from validation.publishing.publish import pack_task
    from validation.stages import normalize_paths
    from validation.stages.runner import save

    parent = read(request['parent_contract'])
    if verify_materialized(args)['sha256'] != parent['sha256']:
        raise ValueError('path preparation input belongs to another contract')
    normalize_paths.prepare(args, stages)
    verify_materialized(args)
    tasks = discover_tasks(args.tasks)
    if sorted(t.name for t in tasks) != sorted(t['task_id'] for t in task_records(parent)):
        raise ValueError('path preparation changed the frozen task coverage')
    remaining = normalize_paths.candidates(args, tasks)
    if remaining:
        raise ValueError(f'path discovery did not finish for {len(remaining)} tasks, '
                         f'e.g. {remaining[0].name}; array not submitted, inspect resolver evidence')

    folder = Path(request['folder'])
    output = folder / 'normalized-input'
    output.mkdir()
    schema = pa.schema([('path', pa.string()), ('task_binary', pa.binary())],
                       metadata={KEY: json.dumps(read_cache(args.tasks), sort_keys=True).encode()})
    parquet = output / 'tasks.parquet'
    temporary = output / 'tasks.parquet.tmp'
    with pq.ParquetWriter(temporary, schema) as writer:
        for start in range(0, len(tasks), 32):
            rows = [{'path': task.name, 'task_binary': pack_task(task)} for task in tasks[start:start + 32]]
            writer.write_table(pa.Table.from_pylist(rows, schema=schema))
    temporary.replace(parquet)
    source = args.tasks / 'source.json'
    metadata = json.loads(source.read_text()) if source.exists() else {}
    metadata['automation_changes'] = {**parent['dataset'].get('automation_changes', {}),
                                      **metadata.get('automation_changes', {})}
    metadata.setdefault('normalization', {}).update(
        parent_contract_sha256=parent['sha256'], operation='automatic-path-normalization-before-sharding',
        saved_dataset=str(output))
    save(output / 'source.json', metadata)
    from validation.publishing.image_release import copy_references
    copy_references(tasks, output)

    derived = copy.copy(args)
    for key in list(vars(derived)):
        if key.startswith('_'):
            delattr(derived, key)
    derived.tasks, derived.out = output, Path(request['plan']['out'])
    derived.dataset_source = parent['dataset']['source']
    derived.dataset_revision = parent['dataset']['revision']
    derived.contract = derived.prepare_contract = None
    derived.shard = derived.limit = derived.task_id_range = None
    derived.static_resume = derived.reuse_stage3 = None
    derived.path_resolution_report = []
    # Runtime bridge addresses and image paths are node-local, never freeze them.
    derived.environment_kwargs = copy.deepcopy(parent['arguments'].get('environment_kwargs', {}))
    contract_path = folder / 'normalized-contract.json'
    create(derived, stages, contract_path)
    normalized = read(contract_path)
    save(folder / 'prepared.json', {'parent_contract_sha256': parent['sha256'],
         'contract': str(contract_path), 'contract_sha256': normalized['sha256'],
         'array': request['plan']['out']})
    launch_array(request)
    return 0


def resolve_prepared(parent, pointer):
    """Merge the common derived contract only when lineage and coverage match."""
    from validation.contract import read, task_records
    record = json.loads(pointer.read_text())
    child = read(record['contract'])
    lineage = (child.get('dataset', {}).get('normalization') or {}).get('parent_contract_sha256')
    if (record['parent_contract_sha256'] != parent['sha256'] or lineage != parent['sha256'] or
            child['sha256'] != record['contract_sha256'] or child['stages'] != parent['stages']):
        raise ValueError('normalized shard contract belongs to another parent or execution')
    if sorted(t['task_id'] for t in task_records(child)) != sorted(t['task_id'] for t in task_records(parent)):
        raise ValueError('normalized shard contract changed task coverage')
    return Path(record['contract']), Path(record['array'])


def launch_array(request):
    # Plan from persistent frozen code, not node-local ROOT: the array snapshot's
    # external symlink must still exist after this allocation exits.
    subprocess.run([sys.executable, '-m', 'hpc.shard_normalization',
                    str(Path(request['folder']) / 'request.json')],
                   cwd=request['source_code'],
                   env={**os.environ, 'PYTHONPATH': request['source_code']}, check=True)


def resume(request_path):
    from validation.contract import read
    from hpc.shards import plan
    from validation.stages.runner import save
    request = json.loads(Path(request_path).read_text())['shard_plan']
    folder = Path(request['folder'])
    contract_path, _ = resolve_prepared(read(request['parent_contract']), folder / 'prepared.json')
    job = plan(contract_path, _paths_prepared=True, **request['plan'])
    save(folder / 'continuation.json', {'job': job, 'array': request['plan']['out']})


if __name__ == '__main__':
    resume(sys.argv[1])
