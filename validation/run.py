#!/usr/bin/env python3
"""Run selected stages, collect findings, and continue after individual failures."""
import sys
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from validation.stages.runner import parser, run_stage, save


def main():
    ap = parser()
    ap.add_argument('--stages', default='1', help='comma-separated stage numbers or all (default: 1)')
    args = ap.parse_args()
    try:
        numbers = [1, 2, 3, 4, 5, 6, 9, 7, 8, 10] if args.stages == 'all' else [int(n) for n in args.stages.split(',')]
        if not numbers or any(n not in range(1, 11) for n in numbers) or len(set(numbers)) != len(numbers):
            raise ValueError('choose unique stages from 1 through 10')
    except ValueError as exc:
        ap.error(str(exc))
    from hpc.validation_submit import maybe_submit
    try:
        from validation.contract import bind
        numbers = bind(args, numbers)
        if numbers is None:
            return 0
        submitted = maybe_submit(args, numbers)
    except (ValueError, RuntimeError, OSError) as exc:
        ap.error(str(exc))
    if submitted is not None:
        return submitted
    return run_selected(args, numbers)


def run_selected(args, numbers):
    if getattr(args, 'prebuild_only', False):
        raise ValueError('--prebuild-only must run through the Slurm image preparation worker')
    from validation.stages.normalize_paths import prepare as normalize_paths
    from validation.rules.static.pip_pinning.repair import prepare as normalize_pip_pins
    normalize_pip_pins(args, numbers)
    defer_paths = (1 in numbers and 4 in numbers
                   and all(numbers.index(n) < numbers.index(4) for n in (1, 2, 3) if n in numbers)
                   and not getattr(args, 'shard', None))
    if not defer_paths:
        normalize_paths(args, numbers)
    from validation.stages.layout import LAYOUT
    report = {'stage_layout': LAYOUT, 'stages': [], 'complete': False, 'dry_run': args.dry_run}
    if getattr(args, 'contract', None):
        from validation.contract import read
        report['contract_sha256'] = read(args.contract)['sha256']
    summary = args.out.resolve() / f'pipeline-{uuid4().hex[:12]}.json'
    analysis_jobs, given_trials = [], args.trials
    save(summary, report)
    args._build_failures = {}
    stage_runs = {}
    retried, retried_before_paths = None, False
    for number in numbers:
        if number == 4 and defer_paths:
            # Settle environment/reference/no-op retries against their original
            # task contract before instruction normalization derives another one.
            retried = retry_unsettled(args, stage_runs, numbers)
            retried_before_paths = True
            normalize_paths(args, numbers)
            if getattr(args, 'contract', None):
                from validation.contract import read
                report['contract_sha256'] = read(args.contract)['sha256']
        # With all, review both ordinary and adversarial trajectories. An explicit
        # --trials remains authoritative for a standalone analysis selection.
        jobs = analysis_jobs if number in (7, 8) and not given_trials else [given_trials]
        if number in (7, 8) and not jobs:
            report['stages'].append({'stage': number, 'status': 'skipped', 'reason': 'no trial jobs available for analysis'})
        for job in jobs:
            if number in (7, 8):
                args.trials = Path(job) if job else None
            try:
                path, result = run_stage(number, args)
                report['stages'].append({'stage': number, 'report': str(path),
                                         'status': 'findings' if result['has_findings'] else 'completed'})
                stage_runs[number] = (path, result)
                if number == 1 and not args.dry_run:
                    args._stage1_path_reports = [path]
                    args._build_failures = {i['task']: {'report': str(path), 'reason': i.get('reason', 'build validation failed')}
                                            for i in result['items'] if i['status'] != 'passed'}
                if number in (6, 9):
                    analysis_jobs.extend(i['job_dir'] for i in result['items'] if i.get('job_dir') and i['job_dir'] not in analysis_jobs)
            except Exception as exc:
                report['stages'].append({'stage': number, 'status': 'error', 'reason': str(exc)})
            save(summary, report)
    if not retried_before_paths:
        retried = retry_unsettled(args, stage_runs, numbers)
    if retried:
        report['outcome_retries'] = {str(stage): {k: v for k, v in s.items() if k != 'history'}
                                     for stage, s in retried.items()}
        for entry in report['stages']:
            if entry.get('stage') in stage_runs and 'report' in entry:
                entry['status'] = 'findings' if stage_runs[entry['stage']][1]['has_findings'] else 'completed'
    report['complete'] = True
    save(summary, report)
    print(f'Pipeline report: {summary}')
    return int(any(s['status'] in ('findings', 'error') or (s['status'] == 'skipped' and not args.dry_run) for s in report['stages']))


def retry_unsettled(args, stage_runs, numbers):
    """One rule after the run: rerun stages 1-3 tasks without a settled outcome (see outcome_retries)."""
    gated = {n: stage_runs[n] for n in (1, 2, 3) if n in stage_runs}
    if not gated or args.dry_run or not int(getattr(args, 'outcome_retries', 0) or 0):
        return None
    import asyncio
    from validation.contract import read
    from validation.stages.environment_preflight import run_build_stage
    from validation.stages.outcome_retries import retry_round
    from validation.stages.runner import checkout, run_trial_batch
    contract = read(args.contract) if getattr(args, 'contract', None) else None
    upstream = checkout('terminal-bench')

    def batch_for(number):
        if number == 1:
            def build(sources, out):
                return run_build_stage(sources, out, args)
            return build
        return lambda sources, out: run_trial_batch(number, sources, out, args, upstream, gated[number][1], contract)
    return retry_round(gated, args, contract, batch_for, save)


if __name__ == '__main__':
    raise SystemExit(main())
