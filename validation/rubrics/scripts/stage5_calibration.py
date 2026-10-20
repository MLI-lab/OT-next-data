"""Score stage-5 reports on the calibration groups that have no comparison script of their own.

Reviews come from the pipeline itself (validation/run.py --stages 5); this only reads
summary.json. tb_human_labeled and tb3_tb4_assumed_positive use their own scripts/.

  hackable:  every task is a reported hack, so anti_cheat_robustness should fail
  stability: run with --attempts 5; agreement of the repeated reviews per task and criterion
"""
import argparse
from collections import Counter
from itertools import combinations
import json
from pathlib import Path

from validation.rubrics.scripts.report_stability import agreement

VALID = ('passed', 'findings')


def hackable(report, criterion='anti_cheat_robustness'):
    rows = []
    for item in report['items']:
        verdicts = item.get('verdicts', []) if item.get('status') in VALID else []
        outcome = verdicts[0]['checks'].get(criterion, {}).get('outcome') if verdicts else None
        rows.append({'task': Path(item['task']).name, 'status': item.get('status'), 'outcome': outcome,
                     'detected': outcome == 'fail' if outcome else None,
                     'repaired': bool(item.get('criteria_repairs')), 'findings': item.get('findings', [])})
    counts = Counter('pending' if r['detected'] is None else 'detected' if r['detected'] else 'missed' for r in rows)
    return {'criterion': criterion, 'expected': 'fail', 'counts': dict(counts), 'tasks': rows}


def stability(report, attempts):
    rows = []
    for item in report['items']:
        verdicts = item.get('verdicts', []) if item.get('status') in VALID else []
        complete = len(verdicts) == attempts
        for criterion in sorted({name for v in verdicts for name in v['checks']}):
            outcomes = [v['checks'].get(criterion, {}).get('outcome') for v in verdicts]
            pairs = list(combinations(outcomes, 2))
            rows.append({'task': Path(item['task']).name, 'criterion': criterion, 'complete': complete,
                         'outcomes': outcomes, 'changed': len(set(outcomes)) > 1, 'pairs': len(pairs),
                         'pairwise_disagreements': sum(a != b for a, b in pairs)})
    return {'attempts': attempts, 'agreement': agreement(rows), 'rows': rows,
            'incomplete_tasks': sorted({r['task'] for r in rows if not r['complete']})}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['hackable', 'stability'])
    parser.add_argument('report', type=Path, help='stage-5 summary.json')
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    report = json.loads(args.report.read_text())
    result = hackable(report) if args.mode == 'hackable' else stability(report, report['review_attempts'])
    args.out.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result.get('counts') or result['agreement'], indent=2))
