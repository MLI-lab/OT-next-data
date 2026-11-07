#!/usr/bin/env python3
"""Run validation stages and collect revision-aware results for all entry points."""
import sys
import fcntl
import hashlib
import math
import json
import re
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from validation.stages.runner import parser, run_stage, save


def main():
    ap = parser()
    ap.add_argument('--stages', default='1', help='comma-separated stage numbers (0-3, 5-10), or all (default: 1)')
    args = ap.parse_args()
    from validation.stages.layout import NAMES
    try:
        numbers = [0, 1, 2, 3, 5, 6, 9, 7, 8, 10] if args.stages == 'all' else [int(n) for n in args.stages.split(',')]
        if not numbers or any(n not in NAMES for n in numbers) or len(set(numbers)) != len(numbers):
            raise ValueError('choose unique stages from ' + ', '.join(map(str, sorted(NAMES)))
                             + ' (static checks are stage 0 since layout v3)')
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
    for number in numbers:
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
                    args._build_failures = {i['task']: {'report': str(path), 'reason': i.get('reason', 'build validation failed')}
                                            for i in result['items'] if i.get('build_status', i['status']) != 'passed'}
                if number in (6, 9):
                    analysis_jobs.extend(i['job_dir'] for i in result['items'] if i.get('job_dir') and i['job_dir'] not in analysis_jobs)
            except Exception as exc:
                report['stages'].append({'stage': number, 'status': 'error', 'reason': str(exc)})
            save(summary, report)
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
        # Automatic path and pip repairs from the stage-1 and stage-2 evidence.
        from validation.automatic_repairs import files_from, plan
        plans = ({} if getattr(args, 'git_checks_only', False) else
                 plan({number: report for number, (_, report) in stage_runs.items()},
                      lambda tasks: files_from(args.tasks, tasks)))
        apply_repairs(args.out / 'results.parquet', plans)
        report['automatic_repairs'] = {task: {k: v for k, v in p.items() if k != 'files'} for task, p in plans.items()}
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



BASE = {'dataset', 'task_id', 'content_sha256', 'run', 'details'}
VERSION = b'3'
from validation.outcomes import RESULTS, NO_RESULT
STATUSES = {*RESULTS, *NO_RESULT}
# Checks that run their own containers. Each cell records the check's cost over
# all its runs: wall-clock seconds and container CPU seconds (summed) and peak
# container memory (largest). Checks evaluated inside another check's container
# (declared_*, repository_*, verifier_tamper, reference_leaks) add no runs.
TIMED = ('build', 'reference', 'reference_determinism', 'do_not_modify_dynamic', 'nop', 'nop_determinism')
USAGE = ('wall_seconds', 'cpu_seconds', 'memory_peak_mb')


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _runs(name, record):
    """(wall seconds, [per-environment resources]) for each container run of a check."""
    if name in ('reference', 'nop'):
        return list(zip(record.get('trial_seconds') or [], record.get('resources') or []))
    if name in ('reference_determinism', 'nop_determinism'):
        usage = [record.get('shuffled_usage') or {}, record.get('repeat_usage') or {}]
        return [pair for u in usage for pair in zip(u.get('trial_seconds') or [], u.get('resources') or [])]
    if name == 'build':
        history = record.get('preparation_runs') or [record]
        return [(sum(v for k, v in (env.get('timings_seconds') or {}).items()
                     if k in ('start', 'inspect', 'stop') and _number(v)), {'agent': env.get('resources')})
                for run in history for env in run.get('environments') or []]
    if name == 'do_not_modify_dynamic':
        found = []
        def walk(value):
            if isinstance(value, dict):
                if isinstance(value.get('trial_seconds'), list) and isinstance(value.get('resources'), list):
                    found.extend(zip(value['trial_seconds'], value['resources']))
                    return
                for item in value.values():
                    walk(item)
            elif isinstance(value, list):
                for item in value:
                    walk(item)
        walk(record.get('mutations'))
        return found
    return []


def usage(name, record):
    """Wall time, CPU time and peak memory of one check over all its runs."""
    if not record or name not in TIMED:
        return {}
    if not any(k in record for k in ('trial_seconds', 'shuffled_usage', 'repeat_usage', 'environments',
                                     'preparation_runs', 'mutations')):
        # Already reduced (read back from results.parquet).
        return {k: record[k] for k in USAGE if _number(record.get(k))}
    runs = _runs(name, record)
    walls = [w for w, _ in runs if _number(w)]
    environments = [e for _, envs in runs for e in (envs or {}).values() if isinstance(e, dict)]
    cpu = [e['cpu_seconds'] for e in environments if _number(e.get('cpu_seconds'))]
    memory = [e['memory_peak_mb'] for e in environments if _number(e.get('memory_peak_mb'))]
    out = {}
    if walls:
        out['wall_seconds'] = round(sum(walls), 1)
    if cpu:
        out['cpu_seconds'] = round(sum(cpu), 2)
    if memory:
        out['memory_peak_mb'] = max(memory)
    return out


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


def details(row):
    """Per-check evidence of an in-memory row, keyed by check name."""
    value = row.get('details')
    return dict(json.loads(value) if isinstance(value, str) else value or {})


def normalize_details(row):
    row = dict(row)
    shared = details(row)
    from validation.outcomes import normalize
    for name, record in shared.items():
        if name in row and isinstance(record, dict):
            row[name], shared[name] = normalize(name, row[name], record)
    for name, value in row.items():
        if value == 'not_run' and name not in shared:
            # A check without a result and without evidence: classify it like the others.
            row[name], _ = normalize(name, value, {})
    if shared or row.get('details') is not None:
        row['details'] = encode(shared)
    return row


# Dynamic checks that run inside each stage's containers.
STAGE_CHECKS = {1: ('declared_workdir', 'declared_internet_access', 'repository_history', 'repository_timestamps'),
                2: ('reference', 'reference_determinism', 'do_not_modify_dynamic'),
                3: ('nop', 'nop_determinism', 'verifier_tamper', 'reference_leaks', 'verifier_paths')}


def known_rules():
    from validation.rules.registry import RULES
    # The checks of stages 0-3. Later stages (review, metrics, evaluations) add
    # their columns only when they run.
    return {r.name for r in RULES} | {
        'build', 'reference', 'nop', 'reference_determinism', 'nop_determinism',
        'repository_history', 'repository_timestamps', 'declared_workdir', 'declared_internet_access'}


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
            from validation.automatic_repairs import for_item
            repairs = for_item(report, item, task, digest)
            if repairs and not settings.get('git_checks_only'):
                from validation.automatic_repairs import combine
                row['automatic_repairs'] = combine(row.get('automatic_repairs'), encode(repairs))
            def put(name, record, value=None):
                if settings.get('git_checks_only') and name not in ('repository_history', 'repository_timestamps'):
                    return
                from validation.outcomes import normalize
                row[name], record = normalize(name, value if value is not None else status(record.get('status')), record)
                row['details'] = encode({**details(row), name: record})
            static = int(stage) == 0
            if not static:
                name = {1: 'build', 2: 'reference', 3: 'nop', 5: 'llm_review',
                        7: 'trace_metrics', 8: 'trajectory_review', 9: 'adversarial',
                        10: 'repair_loop'}.get(int(stage))
                if name:
                    value = (status(item['normal_baseline_status'])
                             if 'normal_baseline_status' in item else None)
                    if int(stage) == 1 and 'build_status' in item:
                        value = status(item['build_status'])
                    # Trial infrastructure can report "failed" without ever
                    # obtaining a reward. Keep that absence distinct from an
                    # observed wrong reward, preserving the original details.
                    if int(stage) in (2, 3) and item.get('rewards') == []:
                        value = 'not_run'
                    put(name, item, value)
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
            if static and not checks:
                put('static_input', item)
            for check in checks:
                put(rule_name(check['check']), check)
            if static:
                observed = {check['check'] for check in checks}
                for name in report.get('checks', []):
                    if name not in observed:
                        put(rule_name(name), {'status': 'not_run',
                                             'reason': item.get('error') or item.get('reason') or
                                             'Selected check did not produce a result'}, 'not_run')
            if int(stage) == 1 and isinstance(item.get('setup_speed'), dict):
                put('setup_speed', item['setup_speed'])
            for field, name in [('listing_order', 'reference_determinism' if int(stage) == 2 else 'nop_determinism'),
                                ('do_not_modify', 'do_not_modify_dynamic'), ('verifier_tamper', 'verifier_tamper'),
                                ('verifier_paths', 'verifier_paths')]:
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
            for env in [*item.get('environments', []),
                        *(dict(v.get('inspection') or {}, environment=v.get('environment'))
                          for v in item.get('verifier_preparation', []) if v.get('inspection'))]:
                for check in [*env.get('checks', []), *(env.get('leaks') or {}).get('checks', [])]:
                    if isinstance(check, dict) and check.get('check'):
                        environment_checks.setdefault(rule_name(check['check']), []).append(
                            dict(check, environment=env.get('environment')))
            for name, checks in environment_checks.items():
                values = {status(check.get('status')) for check in checks}
                value = next(v for v in ('fail', 'not_run', 'warning', 'pass') if v in values)
                put(name, {'checks': checks}, value)
            # A failed build blocks the dynamic checks that need its container or a trial.
            blocked = (item.get('blocked_by_stage') is not None or
                       (int(stage) == 1 and item.get('build_status', item.get('status')) not in ('passed', 'completed')))
            if blocked and int(stage) in STAGE_CHECKS:
                blocked_checks = list(STAGE_CHECKS[int(stage)])
                if int(stage) == 3 and settings.get('dynamic_do_not_modify', True):
                    # Tasks without a reference receive this check in their no-op
                    # trials. A failed build prevents it; it was not deselected.
                    blocked_checks.append('do_not_modify_dynamic')
                for name in blocked_checks:
                    if row.get(name) in (None, 'not_requested'):
                        put(name, {'status': 'skipped', 'blocked_by_stage': 1,
                                   'reason': 'The task did not build'})
    return list(rows.values())


def merge(previous, incoming):
    """One row per task. Unobserved cells are kept; new task files replace the row."""
    rows = [normalize_details(r) for r in previous]
    incoming = [normalize_details(r) for r in incoming]
    index = {(r['dataset'], r['task_id']): r for r in rows}
    if len(index) != len(rows):
        raise ValueError('Duplicate tasks in results')
    columns = known_rules() | {k for r in [*rows, *incoming] for k, v in r.items()
                              if k not in BASE and isinstance(v, str) and v in STATUSES}
    for update in incoming:
        key = (update['dataset'], update['task_id'])
        row = index.get(key)
        if row is None or row['content_sha256'] != update['content_sha256']:
            # Results for other task files are not results for these files.
            if row is not None:
                rows.remove(row)
            row = {'dataset': key[0], 'task_id': key[1], 'content_sha256': update['content_sha256']}
            rows.append(row)
            index[key] = row
        if update.get('details') is not None:
            update = dict(update, details=encode({**details(row), **details(update)}))
        if update.get('automatic_repairs'):
            from validation.automatic_repairs import combine
            update = dict(update, automatic_repairs=combine(row.get('automatic_repairs'), update['automatic_repairs']))
        row.update(update)
    for row in rows:
        for col in columns:
            if row.get(col) is None:
                row[col] = 'not_requested'
    return sorted(rows, key=lambda r: (r['dataset'], r['task_id']))


def summary(record):
    """One short text from a check record: its reason or error, as plain text."""
    parts = []
    for key in ('reason', 'error', 'findings', 'exception_info', 'exception_type', 'stderr'):
        value = record.get(key)
        if value:
            parts.append(value if isinstance(value, str) else
                         '; '.join(map(str, value)) if isinstance(value, list) else encode(value))
    return '\n'.join(dict.fromkeys(parts)) or None


def cell(name, status, record, repair):
    """The Parquet cell of one check: status, repaired (repairable checks only) and one
    short details text."""
    from validation.automatic_repairs import RULES as repairable
    value = {'status': status}
    if name in repairable:
        value['repaired'] = (repair or {}).get('applied')
    text = None
    if name == 'instruction-suffix':
        # Always the same timeout and anti-cheating sentence, so no text.
        record, repair = None, None
    if value.get('repaired'):
        text = (repair or {}).get('details')
    if not text and status != 'pass' and record:
        # Rewards are 0 or 1, so a failed no-op or reference result needs no text.
        if not (name in ('nop', 'reference') and status == 'fail'):
            text = record.get('details') if isinstance(record.get('details'), str) else None
            if not text:
                text = summary(record)
    if text:
        value['details'] = text if len(text) <= 1000 else text[:997] + '...'
    if name in TIMED:
        value.update(usage(name, record))
    return value


def uncell(row):
    """Back to the in-memory form: status strings, per-check details, repair records."""
    row = dict(row)
    records, repairs = {}, {}
    for name, value in list(row.items()):
        if not (isinstance(value, dict) and value.get('status') in STATUSES):
            continue
        row[name] = value['status']
        record = {k: value[k] for k in ('details', *USAGE) if value.get(k) is not None}
        if record:
            records[name] = record
        if 'repaired' in value:
            repairs[name] = {'applied': value['repaired'], 'evidence': []}
            if value['repaired'] and value.get('details'):
                repairs[name]['details'] = value['details']
    if records:
        row['details'] = encode({**details(row), **records})
    if repairs:
        row['automatic_repairs'] = encode(repairs)
    return row


def read(path):
    import pyarrow.parquet as pq
    if not Path(path).is_file():
        return []
    table = pq.read_table(path)
    if (table.schema.metadata or {}).get(b'ot.results.version') != VERSION:
        raise ValueError(f'{path}: not a results.parquet of format version 3')
    return [normalize_details(uncell(row)) for row in table.to_pylist()]


def write(path, rows):
    import pyarrow as pa
    import pyarrow.parquet as pq
    from validation.automatic_repairs import RULES as repairable
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    out = []
    for row in rows:
        row = normalize_details(row)
        records = details(row)
        repairs = json.loads(row.pop('automatic_repairs', None) or '{}')
        row.pop('details', None)
        for name, value in list(row.items()):
            if isinstance(value, str) and value in STATUSES and name not in BASE:
                row[name] = cell(name, value, records.get(name), repairs.get(name))
        out.append(row)
    names = sorted({k for r in out for k in r} | {'dataset', 'task_id', 'content_sha256'})
    checks = {n for r in out for n, v in r.items() if isinstance(v, dict) and 'status' in v}
    integers = {n for n in names if n.endswith(('_solved', '_attempts')) or n in ('cpus', 'memory_mb')}
    def kind(name):
        if name in checks:
            fields = [('status', pa.string())]
            if name in repairable:
                fields.append(('repaired', pa.bool_()))
            fields.append(('details', pa.string()))
            if name in TIMED:
                fields += [(key, pa.float64()) for key in USAGE]
            return pa.struct(fields)
        return pa.int64() if name in integers else pa.string()
    schema = pa.schema([(n, kind(n)) for n in names], metadata={b'ot.results.version': VERSION})
    temporary = path.with_name(path.name + '.' + uuid4().hex + '.tmp')
    try:
        pq.write_table(pa.Table.from_pylist(out, schema=schema), temporary)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    write_usage_summary(path.with_name('check_resources.json'), out)


def write_usage_summary(path, cells):
    """Mean and median wall time, CPU time and peak memory of each check that runs
    containers, over all tasks, and of the whole validation per task ("overall":
    times summed over those checks, memory the largest)."""
    import statistics
    def stats(values):
        return {'tasks': len(values), 'mean': round(statistics.fmean(values), 1),
                'median': round(statistics.median(values), 1)}
    summary, totals = {}, []
    for name in TIMED:
        measured = [row[name] for row in cells if isinstance(row.get(name), dict)]
        entry = {key: stats(values) for key in USAGE
                 if (values := [cell[key] for cell in measured if _number(cell.get(key))])}
        if entry:
            summary[name] = entry
    for row in cells:
        measured = [row[name] for name in TIMED if isinstance(row.get(name), dict)]
        total = {}
        for key in USAGE:
            values = [cell[key] for cell in measured if _number(cell.get(key))]
            if values:
                total[key] = max(values) if key == 'memory_peak_mb' else sum(values)
        if total:
            totals.append(total)
    overall = {key: stats(values) for key in USAGE if (values := [t[key] for t in totals if key in t])}
    if overall:
        summary['overall'] = overall
    temporary = path.with_name(path.name + '.' + uuid4().hex + '.tmp')
    temporary.write_text(json.dumps(summary, indent=2) + '\n')
    temporary.replace(path)


def update(path, incoming):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        rows = merge(read(path), incoming)
        write(path, rows)
    return rows


def repair_rows(rows, plans):
    """Rows of repaired tasks: the repaired files' hash, each repaired check's status
    after the repair, and the repair record (validation/automatic_repairs.py)."""
    from validation.automatic_repairs import combine
    for row in rows:
        plan = plans.get(row['task_id'])
        if not plan or row['content_sha256'] != plan['from']:
            continue
        row['content_sha256'] = plan['to']
        for rule, repair in plan['rules'].items():
            row[rule] = repair['status']
        row['automatic_repairs'] = combine(row.get('automatic_repairs'), encode({
            rule: {'applied': True, 'evidence': [], 'details': repair['details']}
            for rule, repair in plan['rules'].items()}))
    return rows


def apply_repairs(path, plans):
    """Record the automatic repairs of a finished run in its results.parquet."""
    path = Path(path)
    if not plans or not path.is_file():
        return
    with path.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        write(path, repair_rows(read(path), plans))


def export_report(path, report, hashes, dataset='', settings=None):
    from validation.automatic_repairs import capture
    capture(report, settings)
    stage = report.get('stage', 0)  # the static-rules command writes reports without a stage
    return update(path, observations({stage: report}, hashes, dataset, settings))


if __name__ == '__main__':
    raise SystemExit(main())
