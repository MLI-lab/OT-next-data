#!/usr/bin/env python3
"""Run validation stages and collect revision-aware results for all entry points."""
import sys
import fcntl
import hashlib
import json
import re
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
    from validation.stages.static_gate import prepare as static_gate
    static_gate(args, numbers)
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
    if not args.dry_run:
        from validation.run import export_report
        for _, stage_report in stage_runs.values():
            if stage_report.get('task_content_sha256'):
                export_report(args.out / 'results.parquet', stage_report,
                              stage_report['task_content_sha256'], settings=vars(args))
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



BASE = {'dataset', 'task_id', 'content_sha256', 'revision', 'latest', 'change_reason',
        'task_file', 'updated_at', 'run'}
STATUSES = {'pass', 'fail', 'warning', 'not_run', 'not_recorded'}


def rule_name(name):
    from validation.rules.registry import RULES
    aliases = {r.name.replace('_', '-'): r.name for r in RULES}
    name = name.removeprefix('check-').removesuffix('.sh').removesuffix('.py')
    special = {'reference-solution-leak': 'reference_leaks', 'repository-history-leak': 'repository_history',
               'repository-mtime-leak': 'repository_timestamps'}
    return special.get(name, aliases.get(name, name.replace('-', '_')))


def status(value):
    if value in ('passed', 'completed', 'clean', 'not_evidenced', 'pass'):
        return 'pass'
    if value in ('failed', 'flagged', 'findings', 'fail'):
        return 'fail'
    if value == 'warning':
        return 'warning'
    return 'not_run'


def encode(value):
    return json.dumps(value, sort_keys=True, default=str)


def known_rules():
    from validation.rules.registry import RULES
    return {r.name for r in RULES} | {
        'build', 'reference', 'nop', 'reference_determinism', 'nop_determinism',
        'repository_history', 'repository_timestamps', 'llm_review',
        'trace_metrics', 'trajectory_review', 'adversarial', 'repair_loop',
        'static_input'}


def observations(reports, hashes, dataset='', settings=None):
    """Only emit observed cells; omitted checks must not erase earlier results."""
    rows = {}
    settings = settings or {}
    for stage, report in reports.items():
        if report.get('dry_run'):
            continue
        for item in report.get('items', report.get('tasks', [])):
            task = Path(item.get('task', '')).name
            if task not in hashes:
                continue
            digest = item.get('content_sha256') or report.get('task_content_sha256', {}).get(task) or hashes.get(task)
            if not task or not digest:
                raise ValueError(f'Results need a content hash for task {task!r}')
            row = rows.setdefault((task, digest), {'dataset': dataset or re.sub(r'-\d+$', '', task),
                                                   'task_id': task, 'content_sha256': digest})
            def put(name, record, value=None):
                row[name] = value if value is not None else status(record.get('status'))
                row[name + '_details'] = encode(record)
            if int(stage) != 4:
                name = {1: 'build', 2: 'reference', 3: 'nop', 5: 'llm_review',
                        7: 'trace_metrics', 8: 'trajectory_review', 9: 'adversarial',
                        10: 'repair_loop'}.get(int(stage))
                if name:
                    put(name, item, status(item['normal_baseline_status'])
                        if 'normal_baseline_status' in item else None)
            if int(stage) == 6:
                identity = {k: settings.get(k) for k in ('model', 'agent', 'agent_kwargs', 'attempts')}
                model = re.sub(r'[^a-zA-Z0-9_]+', '_', str(identity['model'] or 'unknown'))
                name = f"passk_{model}_{hashlib.sha256(encode(identity).encode()).hexdigest()[:10]}"
                rewards = item.get('rewards', [])
                solved = sum(isinstance(r, (int, float)) and not isinstance(r, bool) and r == 1 for r in rewards)
                value = 'pass' if solved else 'fail' if item.get('status') == 'completed' and rewards else 'not_run'
                put(name, {'settings': identity, 'result': item}, value)
                row[name + '_solved'] = solved
                row[name + '_attempts'] = len(rewards)
            checks = item.get('checks', [])
            if int(stage) == 4 and not checks:
                put('static_input', item)
            for check in checks:
                put(rule_name(check['check']), check)
            if int(stage) == 4:
                observed = {check['check'] for check in checks}
                for name in report.get('checks', []):
                    if name not in observed:
                        put(rule_name(name), {'status': 'not_run',
                                             'reason': item.get('error') or item.get('reason') or
                                             'Selected check did not produce a result'}, 'not_run')
            for field, name in [('listing_order', 'reference_determinism' if int(stage) == 2 else 'nop_determinism'),
                                ('do_not_modify', 'do_not_modify_dynamic'), ('verifier_tamper', 'verifier_tamper')]:
                record = item.get(field)
                if isinstance(record, dict):
                    value = None
                    if field == 'verifier_tamper' and record.get('status') == 'flagged':
                        value = 'warning'
                    if field == 'verifier_tamper' and 'trials' in record:
                        trials = record['trials']
                        value = ('warning' if any(t.get('status') == 'flagged' for t in trials) else
                                 'not_run' if not trials or any(t.get('status') == 'incomplete' for t in trials) else 'pass')
                    put(name, record, value)
            for scan in item.get('reference_leak_scans', []):
                if scan.get('result'):
                    put('reference_leaks', scan['result'])
            # Keep full environment evidence, including checks not yet given a registry name.
            environment_checks = {}
            for env in item.get('environments', []):
                for check in [*env.get('checks', []), *(env.get('leaks') or {}).get('checks', [])]:
                    if isinstance(check, dict) and check.get('check'):
                        environment_checks.setdefault(rule_name(check['check']), []).append(
                            dict(check, environment=env.get('environment')))
            for name, checks in environment_checks.items():
                values = {status(check.get('status')) for check in checks}
                value = next(v for v in ('fail', 'not_run', 'warning', 'pass') if v in values)
                put(name, {'checks': checks}, value)
    return list(rows.values())


def merge(previous, incoming):
    """Keep old revisions, preserve unobserved cells, invalidate checks for new content."""
    from datetime import datetime, timezone
    rows = [dict(r) for r in previous]
    index = {(r['dataset'], r['task_id'], r['content_sha256']): r for r in rows}
    if len(index) != len(rows):
        raise ValueError('Duplicate task revisions in results')
    by_task = {}
    for row in rows:
        by_task.setdefault((row['dataset'], row['task_id']), []).append(row)
    columns = known_rules() | {k for r in [*rows, *incoming] for k, v in r.items()
                              if k not in BASE and isinstance(v, str) and v in STATUSES}
    for update in incoming:
        key = (update['dataset'], update['task_id'], update['content_sha256'])
        row = index.get(key)
        old = by_task.setdefault(key[:2], [])
        if row is None:
            row = {c: 'not_run' for c in columns}
            row.update(dataset=key[0], task_id=key[1], content_sha256=key[2],
                       revision=max((r['revision'] for r in old), default=0) + 1,
                       change_reason=update.get('change_reason') or ('Task content changed' if old else 'Initial task'))
            rows.append(row)
            old.append(row)
            index[key] = row
        for other in old:
            other['latest'] = other is row
        row.update({k: v for k, v in update.items() if k not in ('revision', 'latest')})
        row['updated_at'] = datetime.now(timezone.utc).isoformat()
    for row in rows:
        for col in columns:
            if row.get(col) is None:
                row[col] = 'not_run'
    return sorted(rows, key=lambda r: (r['dataset'], r['task_id'], r['revision']))


def read(path):
    import pyarrow.parquet as pq
    return pq.read_table(path).to_pylist() if Path(path).is_file() else []


def write(path, rows):
    import pyarrow as pa
    import pyarrow.parquet as pq
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    names = sorted(BASE | {k for r in rows for k in r})
    integers = {'revision'} | {n for n in names if n.endswith(('_solved', '_attempts'))}
    schema = pa.schema([(n, pa.bool_() if n == 'latest' else pa.int64() if n in integers else pa.string())
                        for n in names], metadata={b'ot.results.version': b'1'})
    temporary = path.with_name(path.name + '.' + uuid4().hex + '.tmp')
    try:
        pq.write_table(pa.Table.from_pylist(rows, schema=schema), temporary)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def update(path, incoming):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        rows = merge(read(path), incoming)
        write(path, rows)
    return rows


def export_report(path, report, hashes, dataset='', settings=None):
    stage = report.get('stage', 4)
    return update(path, observations({stage: report}, hashes, dataset, settings))


if __name__ == '__main__':
    raise SystemExit(main())
