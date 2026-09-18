#!/usr/bin/env python3
"""Pass@k per language for one or more finished pilot runs of the same model.

Runs are merged per task (e.g. two Coder jobs of 8 attempts each -> 16 attempts).
pass@k uses the unbiased estimator 1 - C(n-c, k) / C(n, k) (Chen et al. 2021),
averaged over tasks; timeouts and infrastructure errors count as failed attempts.
Reported for the full selection and for the strict subset in
data/selection1000_strict_subset.json.

Usage: analyze_full.py <run dir> [<run dir> ...] [--k 1 4 16]
"""
import argparse
import json
import re
from collections import defaultdict
from math import comb
from pathlib import Path

HERE = Path(__file__).resolve().parent


def pass_at_k(n, c, k):
    if n < k:
        return None
    return 1.0 if n - c < k else 1.0 - comb(n - c, k) / comb(n, k)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('runs', nargs='+', type=Path)
    ap.add_argument('--k', nargs='+', type=int, default=[1, 4, 16])
    a = ap.parse_args()
    attempts = defaultdict(lambda: [0, 0, 0])  # task -> [n, successes, timeouts]
    for run in a.runs:
        for task, x in json.loads((run / 'validated_attempt_summary.json').read_text())['tasks'].items():
            n = len(x['trials']) or 1
            attempts[task][0] += n
            attempts[task][1] += x['n_success']
            attempts[task][2] += x['n_timeouts']
    strict = json.loads((HERE / 'data/selection1000_strict_subset.json').read_text())['tasks']
    ns = sorted({v[0] for v in attempts.values()})
    print(f"runs: {', '.join(r.name for r in a.runs)} | tasks {len(attempts)} | attempts per task {ns}")
    for subset in ('all', 'strict'):
        rows = defaultdict(list)
        for task, (n, c, t) in attempts.items():
            if subset == 'strict' and not strict.get(task, {}).get('strict_keep', True):
                continue
            lang = re.search(r'crosscodeeval-(\w+)-', task).group(1)
            rows[lang].append((n, c, t))
            rows['total'].append((n, c, t))
        print(f"\n[{subset} tasks]")
        header = f"{'language':11}{'tasks':>6}" + ''.join(f"{'pass@' + str(k):>9}" for k in a.k) + f"{'timeouts':>10}"
        print(header)
        for lang in ('csharp', 'java', 'python', 'typescript', 'total'):
            r = rows.get(lang, [])
            if not r:
                continue
            cells = []
            for k in a.k:
                vals = [pass_at_k(n, c, k) for n, c, _ in r]
                vals = [x for x in vals if x is not None]
                cells.append(f"{100 * sum(vals) / len(vals):8.1f}%" if vals else f"{'-':>9}")
            print(f"{lang:11}{len(r):6}" + ''.join(cells) + f"{sum(t for _, _, t in r):10}")


if __name__ == '__main__':
    main()
