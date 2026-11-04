"""Render the saved River comparison: python plot_river_errors.py (matplotlib required)."""
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.colors import ListedColormap
from matplotlib.patches import Patch

ROOT = Path(__file__).resolve().parents[1] / 'results'
JUDGES = [('sonnet', 'Sonnet 5.5'), ('flash', 'GLM-5.3-Flash'), ('qwen', 'Qwen3.8-27B')]


def main():
    tasks = json.loads((ROOT / 'river-task-results.json').read_text())['tasks']
    colors = ['#edf0f3', '#dc8b22', '#7654b3']
    for benchmark, title, slug in [('swe_bench', 'SWE-bench', 'swe'),
                                    ('terminal_bench', 'Terminal-Bench', 'tb')]:
        rows = sorted((t for t in tasks if t['benchmark'] == benchmark),
                      key=lambda t: (t['gold'], -sum(bool(j['error']) for j in t['judges'].values()), t['task_name']))
        assert len(rows) == 40
        values = [[{None: 0, 'FP': 1, 'FN': 2}[t['judges'][j]['error']] for t in rows] for j, _ in JUDGES]
        fig, ax = plt.subplots(figsize=(19, 6.2))
        fig.subplots_adjust(left=.105, right=.99, top=.69, bottom=.48)
        ax.imshow(values, cmap=ListedColormap(colors), vmin=0, vmax=2, aspect='auto')
        ax.set_yticks(range(3), [n for _, n in JUDGES], fontsize=11)
        names = [t['task_name'].split('__')[-1] for t in rows]
        ax.set_xticks(range(40), names, rotation=65, ha='right', fontsize=8)
        ax.tick_params(axis='both', length=0, pad=8)
        ax.set_xticks([i-.5 for i in range(41)], minor=True)
        ax.set_yticks([i-.5 for i in range(4)], minor=True)
        ax.grid(which='minor', color='white', linewidth=2)
        ax.tick_params(which='minor', length=0)
        ax.axvline(19.5, color='#344054', linewidth=2)
        for y, vals in enumerate(values):
            for x, val in enumerate(vals):
                if val:
                    ax.text(x, y, ['','FP','FN'][val], ha='center', va='center', fontsize=8,
                            color='white' if val == 2 else '#302000', weight='bold')
        ax.text(9.5, -.85, 'Reference: CLEAN (20 tasks)', ha='center', fontsize=11)
        ax.text(29.5, -.85, 'Reference: DEFECTIVE (20 tasks)', ha='center', fontsize=11)
        for spine in ax.spines.values():
            spine.set_visible(False)
        fig.suptitle(f'River · {title} · Which tasks do the judges misclassify?', x=.105, ha='left', y=.96, fontsize=17, weight='bold')
        fig.legend(handles=[Patch(facecolor=c, label=l) for c,l in zip(colors,
                   ['Correct', 'FP: clean task flagged defective', 'FN: defect missed'])],
                   loc='upper left', bbox_to_anchor=(.1,.9), ncol=3, frameon=False, fontsize=11)
        fig.text(.105,.035, 'Each column is one task. Aligned colored cells show shared errors. Within each label group, tasks are ordered by number of judges wrong, then name.', fontsize=10, color='#475467')
        fig.savefig(ROOT / f'river-errors-{slug}.png', dpi=180)
        svg = ROOT / f'river-errors-{slug}.svg'
        fig.savefig(svg)
        svg.write_text('\n'.join(line.rstrip() for line in svg.read_text().splitlines()) + '\n')
        plt.close(fig)


if __name__ == '__main__':
    main()
