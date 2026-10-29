"""Trajectory statistics for a published v2 folder, computed from its trace Parquets.

Writes the `trace_stats/` folder: README.md, data/{summary.json, provenance.json,
per_attempt.parquet, per_task.parquet} and plots/. Metrics come from stage 7
(validation.metrics.trace_metrics) and plots from the report exporter
(validation.publishing.plot_results); this module only feeds them the stored
trajectories, one attempt at a time.

    python -m validation.publishing.trace_stats V2_FOLDER OUT_DIR
"""
from collections import defaultdict
from datetime import datetime, timezone
import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import tempfile

from validation.metrics.trace_metrics import aggregate, distribution, trial_metrics
from validation.metrics import trace_outcomes
from validation.metrics import trace_metrics
from validation.publishing import plot_results

OUTCOME_POLICY = 'exclusive-outcomes-v1'
# Nested metrics are stored as JSON strings in the per-attempt table.
NESTED = ('tool_calls', 'tool_failures', 'tool_calls_with_known_outcome', 'command_profile', 'errors')


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def slug(model):
    return re.sub(r'[^a-z0-9.-]+', '-', model.rsplit('/', 1)[-1].lower()).strip('-')


def attempts(trace_files, sources):
    """Each attempt once, keyed by (model, task_id, attempt_id); per-file counts go to `sources`."""
    import pyarrow.parquet as pq
    seen = set()
    for path in trace_files:
        rows = added = 0
        for batch in pq.ParquetFile(path).iter_batches(batch_size=64):
            for row in batch.to_pylist():
                rows += 1
                key = (row['model'], row['task_id'], row['attempt_id'])
                if key in seen:
                    continue
                seen.add(key)
                added += 1
                yield row
        sources.append({'file': f'traces/{Path(path).name}', 'rows': rows, 'unique_rows_added': added,
                        'sha256': sha256(path)})


def metrics(row, scratch):
    """Stage-7 metrics of one stored attempt; the trajectory is written to a temporary trial folder."""
    # The v2 trace schemas differ: SWE-style traces have no `trial` column, CalibForge
    # names the hash `evaluated_content_sha256`. The attempt ID identifies every row.
    trial = Path(scratch) / (row.get('trial') or row['attempt_id'])
    if row.get('trajectory_found', True) and row['trajectory']:
        (trial / 'agent').mkdir(parents=True, exist_ok=True)
        (trial / 'agent/trajectory.json').write_text(row['trajectory'])
    try:
        result = json.loads(row['result']) if row['result'] else {}
        values = trial_metrics(trial, result)
    finally:
        shutil.rmtree(trial, ignore_errors=True)
    values.pop('trial_path')
    values.update(model=row['model'], task_id=row['task_id'], attempt_id=row['attempt_id'],
                  content_sha256=row.get('content_sha256') or row.get('evaluated_content_sha256'),
                  kept_v2=row.get('retained_v2'))
    return values


def write_plots(groups, plots, scratch):
    """Run the report exporter on one stage-7 report per model and on all models together."""
    written = {}
    for name, rows in groups.items():
        report = Path(scratch) / f'{name}.json'
        # The plots do not use per-call latencies; leaving them out keeps the report small.
        rows = [{k: v for k, v in row.items() if k != 'model_call_seconds'} for row in rows]
        report.write_text(json.dumps({'stage': 7, 'trace_metrics': {'trajectories': rows}}))
        for kind, file in plot_results.write_trace_plots(report).items():
            shutil.move(report.with_name(file), plots / file)
            written.setdefault(name, {})[kind] = f'plots/{file}'
        report.unlink()
    return written


def write_tables(rows, data):
    import pyarrow as pa
    import pyarrow.parquet as pq
    table = []
    for row in rows:
        row = dict(row)
        row['model_call_latency'] = json.dumps(distribution(row.pop('model_call_seconds') or []))
        row.update({key: json.dumps(row[key]) for key in NESTED})
        table.append(row)
    pq.write_table(pa.Table.from_pylist(table), data / 'per_attempt.parquet', compression='zstd')
    by_task = defaultdict(list)
    for row in rows:
        by_task[(row['model'], row['task_id'])].append(row)
    tasks = [{'model': model, 'task_id': task, 'kept_v2': any(r['kept_v2'] for r in group),
              'metrics_json': json.dumps(aggregate(group))} for (model, task), group in sorted(by_task.items())]
    pq.write_table(pa.Table.from_pylist(tasks), data / 'per_task.parquet', compression='zstd')


def median(stats, *keys):
    for key in keys:
        stats = (stats or {}).get(key)
    return f"{stats['median']:,.0f}" if stats else 'n/a'


def readme(summary, plots, dataset):
    models = summary['models']
    lines = ['# Trajectory statistics', '',
             f"{summary['unique_attempts']:,} attempts across {summary['unique_tasks']:,} tasks of {dataset}, "
             'kept and excluded, each counted once. Task success counts are in the [dataset overview](../README.md).', '',
             '| Metric | ' + ' | '.join(summary.get('model_ids', {}).get(m, m) for m in models) + ' |', '| --- |' + ' ---: |' * len(models),
             '| Recorded attempts | ' + ' | '.join(f"{m['trajectories']:,}" for m in models.values()) + ' |']
    for label, keys in (('Median agent turns', ('turns',)), ('Median input tokens per attempt', ('input_tokens',)),
                        ('Median output tokens per attempt', ('output_tokens',)),
                        ('Median agent runtime (seconds)', ('latency_seconds', 'agent')),
                        ('Median verifier runtime (seconds)', ('latency_seconds', 'verifier'))):
        lines.append(f'| {label} | ' + ' | '.join(median(m, *keys) for m in models.values()) + ' |')
    overall = plots['all-models']
    lines += ['', 'Medians use recorded measurements only.', '', '## Plots', '',
              f"![Trace measurements]({overall['trace_distributions']})", '',
              f"![Trace outcomes]({overall['trace_outcomes']})", '']
    if 'trace_commands' in overall:
        lines += [f"![Most frequent commands]({overall['trace_commands']})", '',
                  'Commands are counted as submitted, not as successful executions.', '']
    per_model = [f"[{summary.get('model_ids', {}).get(name, name)} {kind.removeprefix('trace_')}]({path})"
                 for name, files in plots.items() if name != 'all-models'
                 for kind, path in sorted(files.items())]
    lines += ['Per-model plots: ' + ', '.join(per_model) + '.', '', '## Data', '',
              '- [Summary](data/summary.json): overall and per-model statistics.',
              '- [Per-task metrics](data/per_task.parquet) and [per-attempt metrics](data/per_attempt.parquet), '
              'with nested metrics stored as JSON.',
              '- [Provenance](data/provenance.json): source files and deduplication.', '',
              f"Attempts without a saved conversation: {summary['missing_trajectories']:,}; without a verifier reward: "
              f"{summary['null_verifier_rewards']:,}. Their missing measurements stay empty.", '']
    settings = summary.get('evaluation_settings')
    if settings:
        from validation.publishing.evaluation_settings import markdown
        lines += ['', markdown(settings)]
    else:
        lines += ['', 'Evaluation budgets: see the [dataset evaluation setup](../README.md). '
                  'Per-call context and output-limit enforcement are not established by token-usage statistics.', '']
    return '\n'.join(lines)


def export(folder, out):
    folder, out = Path(folder), Path(out)
    trace_files = sorted((folder / 'traces').glob('*.parquet'))
    if not trace_files:
        raise ValueError(f'{folder}: no trace Parquets')
    data, plots = out / 'data', out / 'plots'
    data.mkdir(parents=True, exist_ok=True)
    plots.mkdir(exist_ok=True)
    sources = []
    from validation.publishing.trace_outcome_stats import OutcomeStats, readme_section
    from validation.publishing.evaluation_settings import model_id
    outcome_stats = OutcomeStats()
    with tempfile.TemporaryDirectory(prefix='trace-stats-') as scratch:
        rows = []
        for row in attempts(trace_files, sources):
            outcome_stats.add(row)
            rows.append(metrics(row, scratch))
        groups = defaultdict(list)
        for row in rows:
            groups[slug(row['model'])].append(row)
        groups = {'all-models': rows, **dict(sorted(groups.items()))}
        figures = write_plots(groups, plots, scratch)
    summary = {
        'scope': 'All attempts in the published trace Parquets of this folder.',
        'created_at': datetime.now(timezone.utc).isoformat(),
        'input_rows': sum(s['rows'] for s in sources), 'duplicate_records_removed': sum(s['rows'] - s['unique_rows_added'] for s in sources),
        'unique_attempts': len(rows), 'unique_tasks': len({r['task_id'] for r in rows}),
        'missing_trajectories': sum(not r['trajectory_found'] for r in rows),
        'null_verifier_rewards': sum(r['verifier_reward'] is None for r in rows),
        'all_attempts': aggregate(rows),
        'models': {name: aggregate(group) for name, group in groups.items() if name != 'all-models'},
        'model_ids': {name: model_id({'model': group[0]['model']}) for name, group in groups.items() if name != 'all-models'},
        'by_outcome': {
            model: {outcome: aggregate([
                row for row in group
                if ('No verifier score' if row['verifier_reward'] is None or not math.isfinite(row['verifier_reward'])
                    else 'Successful' if row['verifier_reward'] >= 1 else 'Unsuccessful (graded)') == outcome
            ]) for outcome in ('Successful', 'Unsuccessful (graded)', 'No verifier score')}
            for model, group in groups.items()
        },
        'outcome_policy': OUTCOME_POLICY,
    }
    settings_path = folder / 'metadata' / 'evaluation-settings-audit.json'
    if settings_path.is_file():
        settings = json.loads(settings_path.read_text())
        summary['evaluation_settings'] = settings['evaluation_settings']
    (data / 'summary.json').write_text(json.dumps(summary, indent=1) + '\n')
    (data / 'provenance.json').write_text(json.dumps({
        'deduplication_key': ['model', 'task_id', 'attempt_id'], 'sources': sources, 'figures': figures,
        'metric_implementation_sha256': sha256(trace_metrics.__file__),
        'outcome_implementation_sha256': sha256(trace_outcomes.__file__),
        'plotting': {'implementation_sha256': sha256(plot_results.__file__)}, 'outcome_policy': OUTCOME_POLICY,
    }, indent=1) + '\n')
    write_tables(rows, data)
    outcome_stats.write(out)
    (out / 'README.md').write_text(readme(summary, figures, folder.name) + '\n\n' + readme_section())
    return summary


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('folder', type=Path, help='a v2 folder with traces/*.parquet')
    ap.add_argument('out', type=Path, help='the trace_stats folder to write')
    a = ap.parse_args()
    summary = export(a.folder, a.out)
    print(f"{summary['unique_attempts']:,} attempts, {summary['unique_tasks']:,} tasks -> {a.out}")


if __name__ == '__main__':
    main()
