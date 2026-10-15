"""Submit bounded arrays using the shared frozen-contract shard implementation."""

import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile

from validation import shards
from validation.contract import read
from validation.patch_repair_loop import controller as core


def submit(config, contract, selected, directory):
    root = directory / 'array'
    contract_data = read(contract)
    if set(selected) != set(shards.slice_ids(contract_data, (0, 1))):
        raise ValueError('Array selection differs from its frozen contract')
    record_path = root / 'submission.json'
    if record_path.exists():
        record = json.loads(record_path.read_text())
        if record.get('status') != 'submitted':
            raise RuntimeError(f'Uncertain array submission; reconcile before retry: {record_path}')
    else:
        count = math.ceil(len(selected) / config['shard_size'])
        if not (root / 'plan.json').exists():
            shards.plan(contract, count, root, time=config['time'], cpus=config['cpus'],
                        memory=config['memory'], partition=config['partition'],
                        array_concurrency=config.get('shard_concurrency', 1), submit=False)
        plan = json.loads((root / 'plan.json').read_text())
        if plan['contract_sha256'] != read(contract)['sha256'] or plan['count'] != count:
            raise ValueError('Existing array plan differs from this frozen submission')
        command = plan['command'][:-1] + ['--signal=B:USR1@120',
                    f'--output={root}/slurm-%A_%a.out', plan['command'][-1]]
        core.save(record_path, {'status': 'submitting', 'command': command})
        # A crash after sbatch may have submitted the array. Never blindly resubmit.
        from huggingface_hub import get_token
        environment = {**os.environ, 'OT_WORKSPACE': config['workspace'], 'OT_CLUSTER': 'helma'}
        if not environment.get('HF_TOKEN') and (token := get_token()):
            environment['HF_TOKEN'] = token
        result = subprocess.check_output(command, text=True, stderr=subprocess.STDOUT,
                    env=environment).strip()
        job_id = result.split(';')[0]
        if not re.fullmatch(r'\d+', job_id):
            raise RuntimeError(f'Unexpected sbatch response; reconcile {record_path}')
        record = {'status': 'submitted', 'job_id': job_id, 'command': command, 'count': count,
                  'array': True, 'code_snapshot': str(root / 'code')}
        core.save(record_path, record)
    # Publication reads the aggregate request and frozen snapshot just as for a
    # single submission. Each actual execution remains recorded in its shard.
    core.save(root / 'request.json', {'args': dict(contract_data['arguments']),
                                     'stages': contract_data['stages']})
    children = []
    for index in range(record['count']):
        child = {'job_id': f'{record["job_id"]}_{index}',
                 'submission': str(root / 'shards' / f'{index:04d}'),
                 'contract': str(contract), 'shard': [index, record['count']],
                 'selected_ids': shards.slice_ids(contract_data, (index, record['count']))}
        core.save(Path(child['submission']) / 'job.json', child)
        children.append(child)
    return {'job_id': record['job_id'], 'submission': str(root), 'contract': str(contract),
            'selected_ids': selected, 'shards': children}


def collect(job, poll_seconds, max_wait_hours, *, destination=None):
    """Keep partial observations explicit and merge only contract-checked slices."""
    from validation.patch_repair_loop.retries import collect as collect_child
    root = Path(destination) if destination is not None else Path(job['submission'])
    contract = read(job['contract'])
    effective = root / 'effective'
    for child in job['shards']:
        folder = Path(child['submission'])
        report, _ = collect_child({'poll_seconds': poll_seconds, 'max_wait_hours': max_wait_hours},
                                 child, child['selected_ids'], contract['stages'], folder)
        target = effective / 'shards' / folder.name / 'report'
        target.mkdir(parents=True, exist_ok=True)
        for stage in contract['stages']:
            paths = list(report.glob(f'stage-{stage}-*.json'))
            if len(paths) != 1:
                raise ValueError(f'Ambiguous shard report: {report}')
            payload = json.loads(paths[0].read_text())
            payload['shard'] = {'index': child['shard'][0], 'count': child['shard'][1],
                                'tasks': child['selected_ids']}
            payload['source_submission'] = child['submission']
            payload['evidence_archive'] = str(folder / 'evidence.tar.gz')
            core.save(target / f'stage-{stage}-effective.json', payload)
        if (folder / 'execution.json').exists():
            shutil.copyfile(folder / 'execution.json', target.parent / 'execution.json')
    shards.merge(job['contract'], effective, root / 'report')
    return root / 'report'


def collect_prebuild(job, poll_seconds, max_wait_hours):
    """Preserve failed and unfinished image preparation for the fixer."""
    results = []
    for child in job['shards']:
        try:
            result = core.wait_for_prebuild(child, poll_seconds, max_wait_hours)
        except RuntimeError as exc:
            folder = Path(child['submission'])
            records = []
            archive = folder / 'evidence.tar.gz'
            if archive.exists():
                with tarfile.open(archive) as tar:
                    try:
                        records = json.load(tar.extractfile('image-preparation.json'))
                    except KeyError:
                        pass
            result = {'passed': False, 'complete': False, 'images': records,
                      'failures': [{'error': str(exc), 'execution_missing': True,
                                    'task_ids': child['selected_ids']}]}
        results.append({**result, 'job': child,
                        'evidence_archive': str(Path(child['submission']) / 'evidence.tar.gz')})
    result = {'operation': 'image-prebuild-only', 'complete': all(r.get('complete', True) for r in results),
              'passed': all(r['passed'] for r in results), 'shards': results,
              'failures': [dict(failure, submission=r['job']['submission'])
                           for r in results for failure in r.get('failures', [])],
              'validation_stages_run': []}
    core.save(Path(job['submission']) / 'image-prebuild.json', result)
    return result
