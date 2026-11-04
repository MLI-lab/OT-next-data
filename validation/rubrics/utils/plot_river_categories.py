"""Plot River false alarms and missed reference defects without conflating label sets."""
import json
from pathlib import Path
from collections import Counter

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Patch

ROOT = Path(__file__).resolve().parents[1] / 'results'
JUDGES = [('sonnet', 'Sonnet 5.5', '#2878b5'), ('qwen', 'Qwen3.8-27B', '#27865e'),
          ('flash', 'GLM-5.3-Flash', '#c57022')]
GROUPS = {'false_negative': 'Tests reject valid solutions',
          'underspecified': 'Underspecified task',
          'false_positive': 'Verifier accepts invalid solutions',
          'instruction_test_mismatch': 'Instruction/test mismatch',
          'scoring_logic': 'Scoring logic', 'answer_leakage': 'Answer leakage'}


def main():
    tasks = json.loads((ROOT / 'river-task-results.json').read_text())['tasks']
    scopes = [('all', 'All 80 tasks'), ('swe_bench', 'SWE · 40 tasks'),
              ('terminal_bench', 'TB · 40 tasks')]
    metrics = {}
    for scope, _ in scopes:
        selected = [t for t in tasks if scope == 'all' or t['benchmark'] == scope]
        support = Counter(c for t in selected if t['gold'] for c in t['reference_defect_categories'])
        fp, fn = {}, {}
        for j, _, _ in JUDGES:
            fp[j] = dict(Counter(t['judges'][j]['category'] for t in selected if t['judges'][j]['error'] == 'FP'))
            fn[j] = dict(Counter(c for t in selected if t['judges'][j]['error'] == 'FN'
                                 for c in t['reference_defect_categories']))
        metrics[scope] = {'unique_tasks': len(selected), 'clean_review_opportunities': 3 * sum(not t['gold'] for t in selected),
                          'reference_group_task_counts': dict(support), 'fp_by_river_category': fp,
                          'fn_by_reference_group': fn,
                          'binary_fp': sum(t['judges'][j]['error'] == 'FP' for t in selected for j, _, _ in JUDGES),
                          'binary_fn': sum(t['judges'][j]['error'] == 'FN' for t in selected for j, _, _ in JUDGES)}
    fpkeys = sorted({c for counts in metrics['all']['fp_by_river_category'].values() for c in counts},
                    key=lambda c: -sum(counts.get(c, 0) for counts in metrics['all']['fp_by_river_category'].values()))
    fnkeys = sorted(GROUPS, key=lambda c: -sum(counts.get(c, 0) for counts in metrics['all']['fn_by_reference_group'].values()))
    fig, axes = plt.subplots(2, 3, figsize=(18, 9))
    fig.subplots_adjust(left=.22, right=.94, top=.80, bottom=.19, wspace=.32, hspace=.6)
    for row, (keys, field) in enumerate([(fpkeys, 'fp_by_river_category'), (fnkeys, 'fn_by_reference_group')]):
        max_total = max(sum(counts.get(c, 0) for counts in metrics['all'][field].values()) for c in keys)
        for col, (scope, title) in enumerate(scopes):
            ax = axes[row, col];m = metrics[scope];left = [0] * len(keys)
            for j, _, color in JUDGES:
                vals = [m[field][j].get(c, 0) for c in keys]
                ax.barh(range(len(keys)), vals, left=left, color=color, height=.65)
                for i, v in enumerate(vals):
                    if v:ax.text(left[i] + v/2, i, str(v), color='white', ha='center', va='center', fontsize=9, weight='bold')
                left = [a + b for a, b in zip(left, vals)]
            for i, (c, total) in enumerate(zip(keys, left)):
                if row:
                    den = 3 * m['reference_group_task_counts'].get(c, 0)
                    label = f'{total}/{den} ({total/den:.0%})' if den else 'no tasks'
                else:label = str(total)
                ax.text(total + .25, i, label, va='center', fontsize=9)
            labels = keys if row == 0 else [GROUPS[c] for c in keys]
            ax.set_yticks(range(len(keys)), labels if col == 0 else [''] * len(keys), fontsize=10)
            ax.invert_yaxis();ax.set_xlim(0, max_total + (8 if row else 3))
            ax.set_xticks(range(0, max_total + 1, 4 if row == 0 else 3))
            ax.set_axisbelow(True);ax.grid(axis='x', color='#e4e7ec')
            ax.tick_params(axis='both', length=0)
            for spine in ax.spines.values():spine.set_visible(False)
            ax.set_xlabel('Judge–task error counts', fontsize=9)
            subtitle = f"{m['binary_fp']} FP / {m['clean_review_opportunities']} clean reviews" if row == 0 else f"{m['binary_fn']} FN; labels may overlap"
            ax.set_title(title + '\n' + subtitle, fontsize=11, loc='left')
    fig.suptitle('River · Which labels produce false alarms, and which defects are missed?', x=.025, ha='left', y=.98, fontsize=17, weight='bold')
    fig.legend(handles=[Patch(facecolor=c, label=n) for _, n, c in JUDGES], loc='upper left', bbox_to_anchor=(.02,.94), ncol=3, frameon=False)
    fig.text(.025,.84, 'FP: reference CLEAN\n→ judge defect category', fontsize=11, weight='bold', va='top')
    fig.text(.025,.47, 'FN: reference defect group\n→ judge CLEAN', fontsize=11, weight='bold', va='top')
    fig.text(.025,.11, 'Top: River categories chosen on clean-labeled tasks. Bottom: original human/source defect groups, not relabeled into River categories.', fontsize=10)
    fig.text(.025,.075, 'FN rates = missed reviews / (3 judges × tasks carrying that group). A task with two reference groups appears twice; 29 FN produce 30 group entries.', fontsize=10)
    fig.text(.025,.04, 'Counts use unchanged reference labels; disputed labels and rubric differences still count as errors. These are descriptive results, not a definitive ranking of criterion difficulty.', fontsize=10)
    fig.savefig(ROOT / 'river-errors-by-category.png', dpi=180)
    svg = ROOT / 'river-errors-by-category.svg';fig.savefig(svg)
    svg.write_text('\n'.join(line.rstrip() for line in svg.read_text().splitlines()) + '\n')
    plt.close(fig)
    (ROOT / 'river-category-counts.json').write_text(json.dumps({'unit': 'judge-task review; three judges per task',
        'reference_group_names': GROUPS, 'notes': 'Native source categories preserved. FN group memberships overlap; binary scores unchanged.',
        'scopes': metrics}, indent=2) + '\n')


if __name__ == '__main__':
    main()
