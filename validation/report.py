#!/usr/bin/env python3
"""Summarize archived validation evidence without unpacking thousands of files."""
import argparse
from collections import Counter
import json
from pathlib import Path
import sys
import tarfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from validation.verify.check_terminal_bench import POLICY


def review_finding_reason(item):
    """Keep the run summary short; full explanations are in the stage report."""
    failed = sorted({name for trial in item.get('verdicts', [])
                     for name, check in trial.get('checks', {}).items()
                     if check.get('outcome') == 'fail'})
    decision = (item.get('proposal_review') or {}).get('decision')
    parts = []
    if item.get('status') in ('error', 'failed'):
        parts.extend(str(value) for value in item.get('findings', []))
        if item.get('reason'):
            parts.append(str(item['reason']))
    if failed:
        parts.append('failed criteria: ' + ', '.join(failed))
    if decision:
        parts.append('proposal: ' + decision)
    if not parts:
        parts.extend(str(value) for value in item.get('findings', []))
    return '; '.join(parts) or str(item.get('reason') or item.get('status', 'unknown'))


def review_task(item):
    """Keep the judgments and written reasons, omitting transient trial paths."""
    proposal = item.get('proposal_review') or {}
    return {
        'task_id': Path(item.get('task', '')).name,
        'status': item.get('status'),
        'implementation': {
            'attempts': [{'criteria': trial.get('checks', {})} for trial in item.get('verdicts', [])],
        },
        'proposal': {
            'status': proposal.get('status'),
            'decision': proposal.get('decision'),
            'reviews': [{'decision': review.get('decision'), 'review': review.get('review')}
                        for review in proposal.get('reviews', [])],
        },
        'findings': item.get('findings', []),
        'error': item.get('reason'),
    }


def summarize(archive):
    stages = []
    failures = []
    contract_hash = None
    network_status = None
    instruction_fixup = None
    pipelines = []
    with tarfile.open(archive, mode='r|gz') as tar:
        for member in tar:
            path = Path(member.name)
            if len(path.parts) == 2 and path.parts[0] == 'results' and path.name.startswith('pipeline-'):
                pipelines.append(json.load(tar.extractfile(member)))
                continue
            if member.name == 'network-status.json':
                network_status = json.load(tar.extractfile(member))
                continue
            if member.name == 'contract.json':
                contract = json.load(tar.extractfile(member))
                contract_hash = contract.get('sha256')
                instruction_fixup = contract.get('dataset', {}).get('normalization')
                continue
            # Stage summary is results/stage_N_name/<uuid>/summary.json.
            if path.name != 'summary.json' or len(path.parts) != 4 or path.parts[0] != 'results':
                continue
            report = json.load(tar.extractfile(member))
            number = report['stage']
            items = report['items']
            counts = Counter(item['status'] for item in items)
            stages.append({'stage': number, 'name': report['name'], 'complete': report['complete'],
                           'tasks': len(items), 'counts': dict(counts), 'report': str(path),
                           'contract_sha256': report.get('contract_sha256')})
            if number == 2:
                from validation.checks.review_results import group_reviews
                stages[-1]['implementation_review_groups'] = report.get('implementation_review_groups') or group_reviews(items)
                stages[-1]['proposal_review_groups'] = report.get('proposal_review_groups', {})
            if report.get('trace_metrics'):
                # Per-task and per-trajectory rows stay in the stage report.
                stages[-1]['trace_metrics'] = {k: report['trace_metrics'][k] for k in ('all_tasks', 'families', 'model_server_requests', 'inference_resources')}
            if report.get('reward_metrics'):
                stages[-1]['reward_metrics'] = report['reward_metrics']
            for finding in report.get('contract_findings', []):
                failures.append({'stage': number, 'task': '', 'status': 'failed', 'check': 'validation-contract',
                    'benchmark_policy': False, 'reason': finding, 'evidence': str(path)})
            for item in items:
                task = Path(item.get('task', '')).name
                if number == 1:
                    for check in item.get('checks', []):
                        if check['status'] != 'passed' and not (check['status'] == 'skipped' and check.get('optional')):
                            failures.append({'stage': number, 'task': task, 'status': check['status'],
                                'check': check['check'], 'benchmark_policy': check['check'] in POLICY,
                                'reason': f"exit_code={check['exit_code']}", 'evidence': check['log']})
                    if item['status'] == 'error':
                        failures.append({'stage': number, 'task': task, 'status': 'error', 'check': '',
                            'benchmark_policy': False, 'reason': item.get('error',''), 'evidence': str(path)})
                elif item['status'] not in ('passed', 'completed', 'previewed'):
                    failures.append({'stage': number, 'task': task, 'status': item['status'], 'check': '',
                        'benchmark_policy': False,
                        'reason': review_finding_reason(item) if number == 2 else json.dumps(
                            item.get('findings') or item.get('reason') or item.get('environments') or item.get('upstream_summary') or item),
                        'evidence': item.get('output') or item.get('job_dir') or str(path)})
    invalidations = archive.parent / 'invalidated-stages.json'
    if invalidations.exists():
        for stage_id, reason in json.loads(invalidations.read_text()).items():
            for stage in stages:
                if stage['stage'] == int(stage_id):
                    stage['invalidated'] = reason
                    failures.append({'stage': int(stage_id), 'task': '', 'status': 'invalidated',
                        'check': 'evidence-integrity', 'benchmark_policy': False, 'reason': reason, 'evidence': str(archive)})
    request = archive.parent / 'request.json'
    requested = json.loads(request.read_text()).get('stages', []) if request.exists() else []
    missing = sorted(set(requested) - {s['stage'] for s in stages})
    stage_two = next((stage for stage in stages if stage['stage'] == 2), None)
    review_groups = ({'implementation': stage_two['implementation_review_groups'],
                      'proposal': stage_two['proposal_review_groups']} if stage_two else None)
    return {'archive': str(archive.resolve()), 'contract_sha256': contract_hash, 'network_status': network_status,
            'instruction_fixup': instruction_fixup,
            'contract_status': 'bound' if contract_hash and all(s['contract_sha256'] == contract_hash for s in stages) else 'absent-or-unbound-exploratory',
            'requested_stages': requested,
            'missing_stages': missing, 'complete': bool(stages) and not missing and all(s['complete'] for s in stages),
            'stages': stages, 'review_groups': review_groups, 'pipelines': pipelines, 'failures': failures}


def resolve_archive(source, wait=False, timeout_hours=24):
    """Accept an archive or a submission directory; optionally wait for evidence."""
    if timeout_hours <= 0:
        raise ValueError('--timeout-hours must be positive')
    archive = source / 'evidence.tar.gz' if source.is_dir() else source
    deadline = time.monotonic() + timeout_hours * 3600
    while not archive.is_file():
        if not wait:
            raise FileNotFoundError(f'No evidence archive: {archive}; use --wait for a running job')
        if time.monotonic() >= deadline:
            raise TimeoutError('Timed out waiting for evidence; the Slurm job was not cancelled.')
        time.sleep(min(30, max(0, deadline - time.monotonic())))
    return archive


def write_report(archive, out):
    """Save a compact run summary and task reviews beside the evidence archive."""
    result = summarize(archive)
    out.mkdir(parents=True, exist_ok=True)
    (out / 'summary.json').write_text(json.dumps(result, indent=2) + '\n')
    reviews = []
    with tarfile.open(archive, mode='r|gz') as tar:
        for member in tar:
            path = Path(member.name)
            if len(path.parts) != 4 or path.parts[0] != 'results' or path.name != 'summary.json' or not member.isfile():
                continue
            content = tar.extractfile(member).read()
            if path.parts[1].startswith('stage_2_'):
                stage = json.loads(content)
                reviews.extend(review_task(item) for item in stage.get('items', []))
            else:
                (out / ('stage-' + path.parts[1].split('_')[1] + '-' + path.parts[2] + '.json')).write_bytes(content)
    if result['review_groups'] is not None:
        (out / 'reviews.json').write_text(json.dumps({'stage': 2, 'tasks': reviews}, indent=2) + '\n')
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('archive', type=Path, help='evidence archive or Slurm submission directory')
    ap.add_argument('--out', type=Path, required=True)
    ap.add_argument('--wait', action='store_true', help='wait for the evidence archive before reporting')
    ap.add_argument('--timeout-hours', type=float, default=24)
    args = ap.parse_args()
    args.archive = resolve_archive(args.archive, args.wait, args.timeout_hours)
    result = write_report(args.archive, args.out)
    if result['network_status']:
        print('Effective network: ' + json.dumps(result['network_status']))
    if result['instruction_fixup']:
        print('Instruction suffix fix-up: ' + json.dumps(result['instruction_fixup']))
    for stage in result['stages']:
        validity = ' INVALIDATED: ' + stage['invalidated'] if stage.get('invalidated') else ''
        print(f"Stage {stage['stage']} {stage['name']}: {stage['counts']} (complete={stage['complete']}){validity}")
    if result['missing_stages']:
        print(f"Stages without reports: {result['missing_stages']}")
    print(f"Run summary and findings: {args.out / 'summary.json'}")


if __name__ == '__main__':
    main()
