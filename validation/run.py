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
    from hpc.helma.validation_submit import maybe_submit
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
    report = {'stages': [], 'complete': False, 'dry_run': args.dry_run}
    if getattr(args, 'contract', None):
        from validation.contract import read
        report['contract_sha256'] = read(args.contract)['sha256']
    summary = args.out.resolve() / f'pipeline-{uuid4().hex[:12]}.json'
    analysis_jobs, given_trials = [], args.trials
    save(summary, report)
    bundled = {n for n in numbers if n in (3, 4, 5)} if getattr(args, 'reuse_validation_containers', False) else set()
    if len(bundled) < 2:
        bundled = set()  # Standalone stages retain ordinary fresh-start behavior.
    bundle_finished = False
    for number in numbers:
        if number in bundled:
            if not bundle_finished:
                from validation.stages.paired import run_validation_bundle
                try:
                    for stage, path, result in run_validation_bundle(args, bundled):
                        report['stages'].append({'stage': stage, 'report': str(path),
                                                'status': 'findings' if result['has_findings'] else 'completed'})
                except Exception as exc:
                    for stage in sorted(bundled):
                        report['stages'].append({'stage': stage, 'status': 'error', 'reason': str(exc)})
                bundle_finished = True
                save(summary, report)
            continue
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
                if number in (6, 9):
                    analysis_jobs.extend(i['job_dir'] for i in result['items'] if i.get('job_dir') and i['job_dir'] not in analysis_jobs)
            except Exception as exc:
                report['stages'].append({'stage': number, 'status': 'error', 'reason': str(exc)})
            save(summary, report)
    report['complete'] = True
    save(summary, report)
    print(f'Pipeline report: {summary}')
    return int(any(s['status'] in ('findings', 'error') or (s['status'] == 'skipped' and not args.dry_run) for s in report['stages']))


if __name__ == '__main__':
    raise SystemExit(main())
