"""Run one frozen contract as an array of shards and merge the shard reports back into one run.

A contract freezes the full task list. ``plan`` writes one request per shard; each job
validates slice INDEX of COUNT (tasks sorted by ID, taken round-robin) under the same
contract and marks its reports with the slice. ``merge`` checks that the shard reports
belong to that contract and cover every frozen task exactly once, then writes one
report per stage as a single job would have, so the publisher sees one run.
"""
import argparse
import collections
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def parse_shard(value):
    """'INDEX/COUNT' -> (index, count); None when sharding is off."""
    if value in (None, ''):
        return None
    if isinstance(value, (list, tuple)):
        index, count = value
    else:
        try:
            index, count = (int(part) for part in str(value).split('/'))
        except ValueError:
            raise ValueError('--shard must be INDEX/COUNT, e.g. 3/40') from None
    if count < 1 or not 0 <= index < count:
        raise ValueError(f'shard index {index} is outside 0..{count - 1}')
    return int(index), int(count)


def slice_of(items, shard, key):
    """The items of one slice: sorted by key, every COUNT-th starting at INDEX."""
    index, count = shard
    ordered = sorted(items, key=key)
    return [item for position, item in enumerate(ordered) if position % count == index]


def plan(contract_path, count, out, *, time, cpus, memory, partition, array_concurrency, submit):
    from validation.contract import read
    from hpc.validation_submit import snapshot_code
    from validation.stages.runner import save
    contract = read(contract_path)
    if count < 1 or count > len(contract['tasks']):
        raise ValueError(f'shard count must be between 1 and the {len(contract["tasks"])} frozen tasks')
    out = Path(out).resolve()
    if (out / 'shards').exists():
        raise ValueError(f'{out / "shards"} exists; use a new directory')
    out.mkdir(parents=True, exist_ok=True)
    code_root = snapshot_code(out)  # frozen code for every shard, like a single submission
    for index in range(count):
        folder = out / 'shards' / f'{index:04d}'
        folder.mkdir(parents=True)
        payload = dict(contract['arguments'])
        payload.update(contract=str(Path(contract_path).resolve()), prepare_contract=None, submit='never',
                       out=str(folder), shard=f'{index}/{count}', partition=partition, gpus=0, cpus=cpus,
                       time=time, memory=memory)
        save(folder / 'request.json', {'args': payload, 'stages': contract['stages']})
    script = out / 'array.sbatch'
    script.write_text('#!/bin/bash\n#SBATCH --job-name=validation-shards\nset -euo pipefail\n'
                      'shard=$(printf \'%04d\' "${SLURM_ARRAY_TASK_ID:?}")\n'
                      f'exec bash "{code_root}/hpc/helma/validation.sbatch" "{out}/shards/$shard/request.json"\n')
    command = ['sbatch', '--parsable', f'--partition={partition}', '--nodes=1', '--ntasks=1', f'--cpus-per-task={cpus}',
               '--exclusive', f'--mem={memory}', f'--time={time}', f'--array=0-{count - 1}%{array_concurrency}',
               f'--export=ALL,VALIDATION_REPO={code_root}', str(script)]
    save(out / 'plan.json', {'contract': str(Path(contract_path).resolve()), 'contract_sha256': contract['sha256'], 'count': count,
                             'stages': contract['stages'], 'tasks': len(contract['tasks']), 'command': command})
    if submit:
        job = subprocess.check_output(command, text=True).strip()
        (out / 'array-job-id.txt').write_text(job + '\n')
        return job
    return None


def shard_reports(root, stage):
    """(report path, report) of every shard directory holding a report for ``stage``."""
    found = []
    for folder in sorted(Path(root).glob('shards/*')):
        candidates = sorted(folder.glob(f'report/stage-{stage}-*.json'), key=lambda p: p.stat().st_mtime)
        if candidates:
            found.append((candidates[-1], json.loads(candidates[-1].read_text())))
    return found


def merge_summaries(summaries):
    """Add up per-shard outcome-retry summaries: counts are summed, task lists concatenated."""
    merged = {}
    for summary in summaries:
        for key, value in summary.items():
            if isinstance(value, bool) or isinstance(value, str) or value is None:
                merged.setdefault(key, value)
            elif isinstance(value, (int, float)):
                merged[key] = merged.get(key, 0) + value
            elif isinstance(value, list):
                merged.setdefault(key, []).extend(value)
            elif isinstance(value, dict):
                merged[key] = merge_summaries([merged.get(key, {}), value])
    return merged


def merge(contract_path, root, out):
    from validation.contract import read, task_records
    contract = read(contract_path)
    expected = sorted(t['task_id'] for t in task_records(contract))
    out = Path(out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    written, jobs = {}, []
    for stage in contract['stages']:
        reports = shard_reports(root, stage)
        if not reports:
            raise ValueError(f'no shard reports for stage {stage} under {root}')
        count = {r.get('shard', {}).get('count') for _, r in reports}
        if len(count) != 1 or None in count:
            raise ValueError(f'stage {stage}: shard reports disagree on the shard count or lack shard markers')
        count = count.pop()
        seen, items, summaries, timings, incomplete = {}, [], [], [], []
        for path, report in reports:
            if report.get('contract_sha256') != contract['sha256']:
                raise ValueError(f'{path} belongs to another contract')
            if report.get('dry_run'):
                raise ValueError(f'{path} is a dry run')
            index = report['shard']['index']
            if index in seen:
                raise ValueError(f'stage {stage}: shard {index} reported twice ({seen[index]} and {path})')
            seen[index] = path
            wanted = slice_of(expected, (index, count), key=lambda t: t)
            got = sorted(Path(i['task']).name for i in report['items'] if i.get('task'))
            if got != wanted:
                raise ValueError(f'{path}: tasks differ from slice {index}/{count} of the contract')
            if not report.get('complete'):
                incomplete.append(index)
            items.extend(report['items'])
            if report.get('outcome_retries'):
                summaries.append(report['outcome_retries'])
            if report.get('timing'):
                timings.append({'shard': index, **report['timing']})
                jobs.append(report['timing'].get('slurm_job_id'))
        missing = sorted(set(range(count)) - set(seen))
        if missing:
            raise ValueError(f'stage {stage}: shards {missing} of {count} have no report')
        first = reports[0][1]
        merged = {k: v for k, v in first.items() if k not in ('items', 'shard', 'timing', 'outcome_retries', 'complete', 'has_findings',
                                                                'contract_findings', 'reward_metrics', 'implementation_review_groups')}
        merged['items'] = sorted(items, key=lambda i: Path(i.get('task', '')).name)
        merged['complete'] = not incomplete
        merged['has_findings'] = any(i['status'] in ('failed', 'findings', 'error') for i in items)
        merged['contract_findings'] = sorted({f for _, r in reports for f in r.get('contract_findings', [])})
        merged['shards'] = {'count': count, 'root': str(Path(root).resolve()), 'incomplete': incomplete,
                            'reports': {str(i): str(p) for i, p in sorted(seen.items())}, 'timing': timings}
        if summaries:
            merged['outcome_retries'] = merge_summaries(summaries)
        if any('reward_metrics' in r for _, r in reports):
            from validation.checks.reward_metrics import summarize
            merged['reward_metrics'] = summarize(merged['items'])
        target = out / f'stage-{stage}-merged.json'
        target.write_text(json.dumps(merged, indent=2, default=str) + '\n')
        written[stage] = str(target)
    executions = [json.loads(p.read_text()) for p in sorted(Path(root).glob('shards/*/execution.json'))]
    first = executions[0] if executions else {}
    (out.parent / 'execution.json').write_text(json.dumps({
        'status': 'findings' if any(json.loads(Path(p).read_text()).get('has_findings') for p in written.values()) else 'completed',
        'contract_sha256': contract['sha256'], 'merged_from_shards': len(executions),
        'job_ids': sorted({str(j) for j in jobs if j}), 'runtime': first.get('runtime'),
        'apptainer_version': first.get('apptainer_version')}, indent=2) + '\n')
    resources = sorted(Path(root).glob('shards/*/resources.json'))
    if resources:
        (out.parent / 'resources.json').write_text(resources[0].read_text())
    summary = {'contract_sha256': contract['sha256'], 'stages': written, 'tasks': len(expected), 'complete': all(
        json.loads(Path(p).read_text()).get('complete') for p in written.values())}
    (out / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    return summary


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='mode', required=True)
    p = sub.add_parser('plan', help='write one request per shard and the array script; --submit runs sbatch')
    p.add_argument('--contract', type=Path, required=True)
    p.add_argument('--count', type=int, required=True)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--time', required=True, help='Slurm time limit per shard, HH:MM:SS')
    p.add_argument('--cpus', type=int, default=48)
    p.add_argument('--memory', default='128G')
    p.add_argument('--partition', default='cpu')
    p.add_argument('--array-concurrency', type=int, default=16)
    p.add_argument('--submit', action='store_true')
    m = sub.add_parser('merge', help='merge the shard reports under ROOT into one results directory')
    m.add_argument('root', type=Path)
    m.add_argument('--contract', type=Path, required=True)
    m.add_argument('--out', type=Path, required=True, help='results directory for the merged stage reports (e.g. ROOT/merged/report)')
    a = ap.parse_args(argv)
    if a.mode == 'plan':
        job = plan(a.contract, a.count, a.out, time=a.time, cpus=a.cpus, memory=a.memory, partition=a.partition,
                   array_concurrency=a.array_concurrency, submit=a.submit)
        print(json.dumps({'planned': a.count, 'out': str(a.out), 'job': job}))
    else:
        print(json.dumps(merge(a.contract, a.root, a.out), indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main())
