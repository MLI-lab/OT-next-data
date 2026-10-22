"""Plot teacher results and trace metrics; CLI compares stage-6 pass rates."""
import argparse
from collections import Counter
import json
from pathlib import Path

from validation.checks.reward_metrics import group_of, pass_at_k
from validation.checks.trace_outcomes import classify_outcome, OUTCOME_LABELS


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


def histogram_upper(values):
    """Zoom past extreme tails, reporting every observation outside the view."""
    import math
    ordered = sorted(values)
    upper = ordered[-1]
    p99 = ordered[math.ceil(.99 * len(ordered)) - 1]
    if len(ordered) >= 100 and p99 > 0 and upper > 1.5 * p99:
        upper = p99
    return max(upper, 1), sum(v > max(upper, 1) for v in values)


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
    fields = [('turns', 'Agent turns per attempt', 1),
              ('input_tokens', 'Total input tokens per attempt', 1),
              ('output_tokens', 'Total output tokens per attempt', 1),
              ('peak_context_tokens', 'Largest recorded turn\n(input + output tokens)', 1),
              ('agent_seconds', 'Agent elapsed time per attempt (minutes)', 1 / 60),
              ('verifier_seconds', 'Verifier elapsed time per attempt (seconds)', 1)]
    fig, axes = plt.subplots(2, 3, figsize=(13, 7), layout='constrained')
    try:
        for ax, (key, label, scale) in zip(axes.flat, fields):
            values = [r[key] * scale for r in rows if isinstance(r.get(key), (int, float))
                      and math.isfinite(r[key])]
            beyond = 0
            if values:
                upper, beyond = histogram_upper(values)
                ax.hist(values, bins=min(30, max(1, math.ceil(math.sqrt(len(values))))),
                        range=(0, upper), color='#3979ae')
                ax.set_xlim(0, upper)
            if key in ('input_tokens', 'output_tokens', 'peak_context_tokens'):
                ax.ticklabel_format(axis='x', style='sci', scilimits=(0, 0), useOffset=False)
            ax.xaxis.get_major_locator().set_params(nbins=4)
            if not values:
                ax.text(.5, .5, 'No measurements', ha='center', transform=ax.transAxes)
            ax.set(title=f'{len(values):,} measured; {len(rows) - len(values):,} missing'
                         + (f'\n{beyond:,} beyond shown range (99th percentile)' if beyond else ''),
                   xlabel=label, ylabel='Trajectories')
            ax.yaxis.get_major_locator().set_params(integer=True)
            ax.spines[['top', 'right']].set_visible(False)
        fig.suptitle('Trace measurements per attempt')
        fig.savefig(distributions, dpi=180)
    finally:
        plt.close(fig)

    terminations = Counter(classify_outcome(r.get('termination'), r.get('errors')) for r in rows)
    fig, ax = plt.subplots(figsize=(10, max(4, .5 * len(terminations))), layout='constrained')
    try:
        entries = sorted(terminations.items())
        bars = ax.barh([OUTCOME_LABELS.get(k, k.replace('_', ' ')) for k, _ in entries],
                       [v for _, v in entries], color='#3979ae')
        ax.bar_label(bars, padding=3)
        ax.set(xlim=(0, max(v for _, v in entries) * 1.15 + 1),
               title='Trace outcomes · each attempt counted once', xlabel='Attempts')
        ax.xaxis.get_major_locator().set_params(integer=True)
        ax.spines[['top', 'right']].set_visible(False)
        fig.savefig(outcomes, dpi=180)
    finally:
        plt.close(fig)
    plots = {'trace_distributions': distributions.name, 'trace_outcomes': outcomes.name}
    counts = Counter()
    unknown = 0
    for row in rows:
        command_profile = row.get('command_profile') or {}
        if isinstance(command_profile, str):
            command_profile = json.loads(command_profile)
        for name, count in (command_profile.get('counts') or {}).items():
            if name.startswith('['):
                unknown += count
            else:
                counts[name] += count
    if counts:
        commands = path.with_name(path.stem + '-trace-commands.png')
        entries = counts.most_common(100)
        total = sum(counts.values())
        fig, axes = plt.subplots(1, 4, figsize=(19, 10), layout='constrained', sharex=True)
        try:
            for i, ax in enumerate(axes):
                panel = entries[i * 25:(i + 1) * 25]
                if not panel:
                    ax.set_visible(False)
                    continue
                names, values = zip(*panel)
                labels = [name if len(name) <= 42 else name[:39] + '...' for name in names]
                ax.barh(range(len(panel)), [100 * value / total for value in values], color='#3979ae')
                ax.set(yticks=range(len(panel)), yticklabels=labels,
                       title=f'Ranks {i * 25 + 1}–{i * 25 + len(panel)}',
                       xlabel='Share of recognized commands (%) · log scale')
                ax.invert_yaxis()
                ax.set_xscale('log')
                ax.tick_params(axis='y', labelsize=8)
                ax.spines[['top', 'right']].set_visible(False)
            fig.suptitle(f'Top {len(entries)} command/tool labels · {total:,} recognized occurrences\n'
                         f'{unknown:,} unclassified/assignment labels excluded; counts are submissions, not verified executions')
            fig.savefig(commands, dpi=180)
        finally:
            plt.close(fig)
        plots['trace_commands'] = commands.name
    return plots


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
