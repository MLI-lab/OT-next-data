"""Plot teacher results and trace metrics; CLI compares stage-6 pass rates."""
import argparse
from collections import Counter
import json
from pathlib import Path

from validation.checks.reward_metrics import group_of, pass_at_k


def rates(path, ks):
    report = json.loads(Path(path).read_text())
    if report.get('stage') != 6 or not report.get('complete') or report.get('dry_run'):
        raise ValueError(f'{path}: expected a completed stage-6 report')
    groups = {}
    for item in report['items']:
        metrics = item.get('metrics')
        if not metrics:
            raise ValueError(f'{path}: missing attempt metrics for {item["task"]}')
        groups.setdefault(group_of(Path(item['task']).name), []).append(metrics)
    if not groups:
        raise ValueError(f'{path}: no task metrics')
    groups['All tasks'] = [m for rows in groups.values() for m in rows]
    # A bar covers every task in its group, never just tasks with enough attempts.
    return {name: {k: sum(pass_at_k(m['attempts'], m['solved'], k) for m in rows) / len(rows)
                   for k in ks if all(m['attempts'] >= k for m in rows)}
            for name, rows in groups.items()}


def plot_rates(panels, ks, out):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    labels = sorted({name for _, groups in panels for name in groups if name != 'All tasks'}) + ['All tasks']
    fig, axes = plt.subplots(1, len(panels), squeeze=False, sharex=True, sharey=True,
                             figsize=(5 * len(panels), max(3, len(labels) * .7)), layout='constrained')
    colors = plt.colormaps['Blues']([.4 + .5 * i / max(1, len(ks) - 1) for i in range(len(ks))])
    for ax, (name, groups) in zip(axes[0], panels):
        for j, k in enumerate(ks):
            entries = [(i, groups[label][k]) for i, label in enumerate(labels) if k in groups.get(label, {})]
            bars = ax.barh([i + (j - (len(ks) - 1) / 2) * .8 / len(ks) for i, _ in entries],
                           [100 * value for _, value in entries], height=.8 / len(ks),
                           color=colors[j], label=f'pass@{k}')
            ax.bar_label(bars, fmt='%.1f', padding=3, fontsize=8)
        ax.set(title=name, xlim=(0, 110), xlabel='Pass rate (%)', yticks=range(len(labels)), yticklabels=labels)
        ax.spines[['top', 'right']].set_visible(False)
        ax.legend(frameon=False)
    fig.savefig(out, dpi=180)
    plt.close(fig)



def solved_counts(path):
    """Keep different attempt budgets separate, including empty count buckets."""
    report = json.loads(Path(path).read_text())
    if report.get('stage') != 6 or not report.get('complete') or report.get('dry_run'):
        raise ValueError(f'{path}: expected a completed stage-6 report')
    groups = {}
    for item in report['items']:
        metrics = item.get('metrics')
        if not metrics:
            raise ValueError(f'{path}: missing attempt metrics for {item["task"]}')
        attempts, solved = metrics['attempts'], metrics['solved']
        if (type(attempts) is not int or type(solved) is not int
                or attempts < 1 or not 0 <= solved <= attempts):
            raise ValueError(f'{path}: invalid solved/attempt counts for {item["task"]}')
        groups.setdefault(attempts, Counter())[solved] += 1
    if not groups:
        raise ValueError(f'{path}: no task metrics')
    return {k: [counts[n] for n in range(k + 1)] for k, counts in sorted(groups.items())}


def plot_solved_counts(groups, out):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(len(groups), 1, squeeze=False,
                             figsize=(max(6, min(18, max(groups) * .45)), 3.5 * len(groups)),
                             layout='constrained')
    for ax, (k, counts) in zip(axes[:, 0], groups.items()):
        bars = ax.bar(range(k + 1), counts, color='#3979ae')
        ax.bar_label(bars, padding=3, fontsize=8)
        ax.set(title=f'{sum(counts)} tasks with {k} attempts each',
               xlabel='Solved attempts / total attempts', ylabel='Number of tasks',
               xticks=range(k + 1), xticklabels=[f'{n}/{k}' for n in range(k + 1)])
        if k > 12:
            ax.tick_params(axis='x', labelrotation=60)
        ax.yaxis.get_major_locator().set_params(integer=True)
        ax.set_ylim(0, max(counts) * 1.2 + 1)
        ax.spines[['top', 'right']].set_visible(False)
    fig.suptitle('Tasks by solved count (ungraded attempts count as unsolved)')
    fig.savefig(out, dpi=180)
    plt.close(fig)


def write_trace_plots(path):
    """Plot observed per-trajectory measurements, retaining missing-data counts."""
    import math
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    path = Path(path)
    report = json.loads(path.read_text())
    metrics = report.get('trace_metrics') or {}
    rows = metrics.get('trajectories') or []
    if not rows:
        raise ValueError(f'{path}: no trajectory metrics')
    distributions = path.with_name(path.stem + '-trace-distributions.png')
    outcomes = path.with_name(path.stem + '-trace-outcomes.png')
    fields = [('turns', 'Agent turns'), ('input_tokens', 'Input tokens'),
              ('output_tokens', 'Output tokens'), ('peak_context_fraction', 'Peak context / limit'),
              ('agent_seconds', 'Agent runtime (seconds)'), ('verifier_seconds', 'Verifier runtime (seconds)')]
    fig, axes = plt.subplots(2, 3, figsize=(13, 7), layout='constrained')
    try:
        for ax, (key, label) in zip(axes.flat, fields):
            values = [r[key] for r in rows if isinstance(r.get(key), (int, float))
                      and math.isfinite(r[key])]
            if values:
                ax.hist(values, bins=min(30, max(1, math.ceil(math.sqrt(len(values))))), color='#3979ae')
            else:
                ax.text(.5, .5, 'No measurements', ha='center', transform=ax.transAxes)
            ax.set(title=f'{len(values)} measured, {len(rows) - len(values)} missing',
                   xlabel=label, ylabel='Trajectories')
            ax.yaxis.get_major_locator().set_params(integer=True)
            ax.spines[['top', 'right']].set_visible(False)
        fig.suptitle('Trace measurements per attempt')
        fig.savefig(distributions, dpi=180)
    finally:
        plt.close(fig)

    terminations = Counter(r.get('termination') or 'not_recorded' for r in rows)
    error_kinds = sorted({kind for r in rows for kind in (r.get('errors') or {})})
    errors = {kind: sum(bool((r.get('errors') or {}).get(kind)) for r in rows) for kind in error_kinds}
    fig, axes = plt.subplots(1, 2, figsize=(12, max(4, .4 * max(len(terminations), len(errors)))),
                             layout='constrained')
    try:
        for ax, counts, title in zip(axes, (terminations, errors),
                                    ('Termination reasons', 'Errors (an attempt can appear in multiple groups)')):
            entries = sorted(counts.items())
            if entries:
                bars = ax.barh([k.replace('_', ' ') for k, _ in entries], [v for _, v in entries], color='#3979ae')
                ax.bar_label(bars, padding=3)
                ax.set_xlim(0, max(v for _, v in entries) * 1.2 + 1)
            else:
                ax.text(.5, .5, 'No measurements', ha='center', transform=ax.transAxes)
            ax.set(title=title, xlabel='Trajectories')
            ax.xaxis.get_major_locator().set_params(integer=True)
            ax.spines[['top', 'right']].set_visible(False)
        fig.savefig(outcomes, dpi=180)
    finally:
        plt.close(fig)
    return {'trace_distributions': distributions.name, 'trace_outcomes': outcomes.name}


def write_stage_plots(path):
    """Write figures beside an exported stage report and return their paths."""
    path = Path(path)
    if json.loads(path.read_text()).get('stage') == 7:
        return write_trace_plots(path)
    counts = solved_counts(path)
    ks = sorted({1, *counts})
    panels = [('Teacher results', rates(path, ks))]
    pass_rates = path.with_name(path.stem + '-pass-rates.png')
    distribution = path.with_name(path.stem + '-solved-counts.png')
    plot_rates(panels, ks, pass_rates)
    plot_solved_counts(counts, distribution)
    return {'pass_rates': pass_rates.name, 'solved_counts': distribution.name}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('models', nargs='+', metavar='NAME=STAGE6.json')
    parser.add_argument('--k', nargs='+', type=int, default=[1, 4, 16])
    parser.add_argument('-o', '--out', type=Path, default=Path('pass_rates.png'))
    args = parser.parse_args()
    if any(k < 1 for k in args.k):
        parser.error('k must be positive')
    ks = sorted(set(args.k))
    try:
        panels = [(name, rates(path, ks)) for name, path in (s.split('=', 1) for s in args.models)]
    except (ValueError, KeyError, OSError) as exc:
        parser.error(str(exc))
    if not any(values for _, groups in panels for values in groups.values()):
        parser.error('no group has enough attempts for the requested k values')
    plot_rates(panels, ks, args.out)
    print(f'Wrote {args.out}')


if __name__ == '__main__':
    main()
