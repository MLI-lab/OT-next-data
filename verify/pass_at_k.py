#!/usr/bin/env python3
"""pass@k per task group, for one or more finished runs of the same model.

It does two things and nothing else:

1. **Gathers.** One run directory holds one job's attempts. A pass@16 is usually
   split over several jobs (four jobs of four attempts, say), so the runs given
   here are merged per task: attempts add up, successes add up.
2. **Computes.** pass@k with the unbiased estimator 1 - C(n-c, k) / C(n, k)
   (Chen et al. 2021), averaged over tasks. Timeouts and infrastructure errors
   count as failed attempts, never as missing data, and the timeout column says
   how many there were - a run with a broken serving path otherwise reads as a
   weak model.

It reads each run's `validated_attempt_summary.json`, so it never disagrees with
what the run itself recorded, and it works for any dataset: tasks are grouped by
the middle part of the task id (`<dataset>-<group>-<number>`, e.g. the language
in `crosscodeeval-python-0001`), or all together if ids are not shaped that way.

  python verify/pass_at_k.py <run dir> [<run dir> ...] [--k 1 4 16]
"""
import argparse
import json
from collections import defaultdict
from math import comb
from pathlib import Path


def pass_at_k(n, c, k):
    if n < k:
        return None
    return 1.0 if n - c < k else 1.0 - comb(n - c, k) / comb(n, k)


def group_of(task):
    parts = task.split('-')
    return parts[1] if len(parts) >= 3 else 'all'


def groups_for_run(run):
    selection = Path(run) / 'selection.json'
    if not selection.exists():
        return {}
    data = json.loads(selection.read_text())
    return {task: group for group, tasks in data.get('groups', data.get('per_language', {})).items()
            for task in tasks}


def table(rows, ks, title):
    print(f'\n[{title}]')
    print(f"{'group':12}{'tasks':>6}" + ''.join(f"{'pass@' + str(k):>9}" for k in ks) + f"{'timeouts':>10}")
    for group in sorted(rows, key=lambda g: (g == 'total', g)):
        r = rows[group]
        cells = []
        for k in ks:
            vals = [v for v in (pass_at_k(n, c, k) for n, c, _ in r) if v is not None]
            cells.append(f'{100 * sum(vals) / len(vals):8.1f}%' if vals else f"{'-':>9}")
        print(f'{group:12}{len(r):6}' + ''.join(cells) + f'{sum(t for _, _, t in r):10}')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('runs', nargs='+', type=Path, help='run directories of the same model, merged per task')
    ap.add_argument('--k', nargs='+', type=int, default=[1, 4, 16])
    a = ap.parse_args()

    attempts = defaultdict(lambda: [0, 0, 0])            # task -> [attempts, successes, timeouts]
    task_groups = {}
    for run in a.runs:
        task_groups.update(groups_for_run(run))
        summary = json.loads((run / 'validated_attempt_summary.json').read_text())['tasks']
        for task, x in summary.items():
            attempts[task][0] += len(x['trials']) or 1
            attempts[task][1] += x['n_success']
            attempts[task][2] += x['n_timeouts']

    print(f"runs: {', '.join(r.name for r in a.runs)} | tasks {len(attempts)} | "
          f"attempts per task {sorted({v[0] for v in attempts.values()})}")

    def rows_for(keep=lambda task: True):
        rows = defaultdict(list)
        for task, (n, c, t) in attempts.items():
            if keep(task):
                rows[task_groups.get(task, group_of(task))].append((n, c, t))
                rows['total'].append((n, c, t))
        return rows

    table(rows_for(), a.k, 'all tasks')


if __name__ == '__main__':
    main()
