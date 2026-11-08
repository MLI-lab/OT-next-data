"""Stage 5 judges only tasks without a failed check of stages 0-3.

A task is left out when any stage 0-3 check has status `fail` in the results available
to the run: this run's `results.parquet`, any `--judge-results` files, and the stages
0-3 that ran earlier in the same invocation. Warnings and checks without a result
(execution_error, timeout, incomplete, not_run, blocked) do not exclude a task.
Results are matched by task id. When a results file holds the same task file (same
content hash), only that row counts; otherwise every row for the task id counts, so a
recorded failure is not silently ignored because a later normalization changed the hash.
`--judge-failed-tasks include` judges every task.
"""
from pathlib import Path


def failed_by_task(paths, rows=()):
    from validation.run import known_rules, read
    rules = known_rules()
    found = {}
    for row in [r for path in paths for r in read(path)] + list(rows):
        failed = sorted(c for c in rules if row.get(c) == 'fail')
        found.setdefault(row['task_id'], []).append((row.get('content_sha256'), failed))
    return found


def exclusions(sources, hashes, found):
    excluded = {}
    for source in sources:
        rows = found.get(source.name, [])
        exact = [failed for digest, failed in rows if digest == hashes.get(source.name)]
        failed = sorted({check for checks in (exact or [f for _, f in rows]) for check in checks})
        if failed:
            excluded[source.name] = failed
    return excluded


def prepare(args, stage_runs):
    """Called before stage 5: collect the failures it must respect."""
    if getattr(args, 'judge_failed_tasks', 'skip') == 'include':
        args._judge_failed = None
        return
    from validation.run import observations
    paths = [p for p in [Path(args.out) / 'results.parquet', *getattr(args, 'judge_results', [])] if Path(p).is_file()]
    rows = []
    for number, (_, report) in stage_runs.items():
        if number in (0, 1, 2, 3) and report.get('task_content_sha256'):
            rows += observations({number: report}, report['task_content_sha256'])
    import hashlib
    args._judge_failed = {'results': [{'path': str(p), 'sha256': hashlib.sha256(Path(p).read_bytes()).hexdigest()} for p in paths],
                          'stages_this_run': sorted(n for n in stage_runs if n <= 3),
                          'failed': failed_by_task(paths, rows)}
