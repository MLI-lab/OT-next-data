#!/usr/bin/env python3
"""Horizontal bar chart of pass@k per language, one panel per model.

Reads the same validated_attempt_summary.json files as analyze_full.py, so the
plot cannot drift from the reported numbers.

Usage: plot_pass_rates.py "<model name>=<run dir>[,<run dir>...]" [more models] [-o out.png]
"""
import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.path import Path as MPath
from matplotlib.patches import PathPatch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from pass_at_k import pass_at_k, group_of, groups_for_run  # noqa: E402

LANGS = [('csharp', 'C#'), ('java', 'Java'), ('python', 'Python'), ('typescript', 'TypeScript')]
KS = [1, 4, 16]
# Blue ordinal ramp (steps 250/400/550 of the reference palette), light -> dark with k.
RAMP = {1: '#86b6ef', 4: '#3987e5', 16: '#1c5cab'}
THEMES = {
    'light': dict(surface='#fcfcfb', ink='#0b0b0b', ink2='#52514e', grid='#e3e2de'),
    'dark': dict(surface='#1a1a19', ink='#ffffff', ink2='#c3c2b7', grid='#383835',
                 ramp={1: '#6da7ec', 4: '#3987e5', 16: '#184f95'}),
}


def rates(runs):
    """{language or 'total': {k: pass@k}} merged over the given run dirs, plus attempts per task."""
    attempts = defaultdict(lambda: [0, 0])
    task_groups = {}
    for run in runs:
        task_groups.update(groups_for_run(run))
        for task, x in json.loads((run / 'validated_attempt_summary.json').read_text())['tasks'].items():
            attempts[task][0] += len(x['trials']) or 1
            attempts[task][1] += x['n_success']
    rows = defaultdict(list)
    for task, (n, c) in attempts.items():
        rows[task_groups.get(task, group_of(task))].append((n, c))
        rows['total'].append((n, c))
    out = {}
    for lang, r in rows.items():
        out[lang] = {}
        for k in KS:
            vals = [v for v in (pass_at_k(n, c, k) for n, c in r) if v is not None]
            if vals:
                out[lang][k] = 100 * sum(vals) / len(vals)
    return out, min(n for n, _ in attempts.values()), len(attempts)


def bar(ax, y, width, height, color, rx, ry):
    """Bar with a 4px rounded data-end, square at the baseline."""
    b, t, e = y - height / 2, y + height / 2, max(width - rx, 0)
    ax.add_patch(PathPatch(MPath(
        [(0, b), (e, b), (width, b), (width, y - height / 2 + ry), (width, t - ry),
         (width, t), (e, t), (0, t), (0, b)],
        [MPath.MOVETO, MPath.LINETO, MPath.CURVE3, MPath.CURVE3, MPath.LINETO,
         MPath.CURVE3, MPath.CURVE3, MPath.LINETO, MPath.CLOSEPOLY]),
        facecolor=color, edgecolor='none'))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('models', nargs='+', metavar='NAME=RUNDIR[,RUNDIR...]')
    ap.add_argument('-o', '--out', type=Path, default=Path('pass_rates.png'))
    ap.add_argument('--theme', default='light', choices=sorted(THEMES))
    ap.add_argument('--title', default='Teacher pass@k by task group')
    a = ap.parse_args()
    groups = [(name, [Path(p) for p in runs.split(',')])
              for name, runs in (m.split('=', 1) for m in a.models)]

    t = THEMES[a.theme]
    ramp = t.get('ramp', RAMP)
    ink, ink2 = t['ink'], t['ink2']
    data = [(m, *rates(rs)) for m, rs in groups]
    labels = dict(LANGS)
    task_groups = sorted({g for _, d, _, _ in data for g in d if g != 'total'})
    langs = [(g, labels.get(g, g)) for g in task_groups]
    xmax = max(1.0, max(v for _, d, _, _ in data for r in d.values() for v in r.values()) * 1.18)

    fig, axes = plt.subplots(1, len(data), figsize=(11.5, 3.3), dpi=200, sharex=True,
                             facecolor=t['surface'])
    axes = list(axes) if len(data) > 1 else [axes]
    ypos = {lang: i for i, (lang, _) in enumerate(reversed(langs))}
    ypos['total'] = -1.25  # a gap separates the aggregate from the per-language rows

    for ax, (model, d, n_attempts, n_tasks) in zip(axes, data):
        ks = [k for k in KS if k <= n_attempts]
        ax.set_facecolor(t['surface'])
        ax.set_xlim(0, xmax)
        ax.set_ylim(-2.05, len(langs) - 0.45)
        px_x = xmax / (ax.get_window_extent().width or 1)
        px_y = (len(langs) + 1.6) / (ax.get_window_extent().height or 1)
        step = 0.26 if len(ks) > 1 else 0.36
        for lang, label in langs + [('total', f'All {n_tasks:,} tasks')]:
            base = ypos[lang]
            for j, k in enumerate(ks):
                if k not in d.get(lang, {}):
                    continue
                v = d[lang][k]
                y = base + (len(ks) - 1) / 2 * step - j * step
                bar(ax, y, v, step - 2 * px_y, ramp[k], 4 * px_x, 4 * px_y)
                ax.text(v + 1.2 * px_x * 4, y, f'{v:.1f}', va='center', ha='left',
                        fontsize=7.5, color=ink2)
        ax.set_yticks([ypos[l] for l, _ in langs] + [ypos['total']],
                      [lab for _, lab in langs] + [f'All {n_tasks:,} tasks'],
                      fontsize=9, color=ink)
        for lab in ax.get_yticklabels()[-1:]:
            lab.set_fontweight('semibold')
        ax.set_xticks(range(0, int(xmax) + 1, 10), [f'{x}%' for x in range(0, int(xmax) + 1, 10)],
                      fontsize=8, color=ink2)
        ax.xaxis.grid(True, color=t['grid'], linewidth=0.8)
        ax.set_axisbelow(True)
        ax.tick_params(length=0)
        for s in ax.spines.values():
            s.set_visible(False)
        ax.set_title(f'{model}\n{n_attempts} attempt{"s" if n_attempts > 1 else ""} per task',
                     fontsize=9.5, color=ink, loc='left', pad=8)

    handles = [plt.Rectangle((0, 0), 1, 1, facecolor=ramp[k], edgecolor='none') for k in KS]
    fig.legend(handles, [f'pass@{k}' for k in KS], loc='upper right', bbox_to_anchor=(0.995, 0.99),
               frameon=False, ncol=3, fontsize=8.5, labelcolor=ink2, handlelength=1.1,
               handleheight=1.1, columnspacing=1.2)
    fig.suptitle(a.title, fontsize=11, color=ink, x=0.007, ha='left', y=0.965)
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    fig.savefig(a.out, facecolor=t['surface'])
    print(f'wrote {a.out}')


if __name__ == '__main__':
    main()
