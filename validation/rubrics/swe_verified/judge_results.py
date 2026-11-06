"""Summarize the existing three accepted judge reviews on the 66 audit PASS tasks."""
import csv
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = Path('/hnvme/workspace/y500bb12-rule-based-checks/judge-all-models-20261105')
CATS = ('INSTR-AMBIGUOUS', 'INSTR-VERIFIER-MISMATCH')
FILES = {
    ('Sonnet 5.5', 'labeling'): 'claude-sonnet-5-5-labeling-existing',
    ('Qwen3.8-27B', 'labeling'): 'qwen38-27b-labeling',
    ('Sonnet 5.5', 'classification'): 'claude-sonnet-5-5-classification',
    ('Qwen3.8-27B', 'classification'): 'qwen38-27b-classification',
}


def main():
    annotation_rows = list(csv.DictReader((HERE / 'task_agreement.csv').open()))
    ids = set.intersection(*[
        {r['instance_id'] for r in annotation_rows
         if r['criterion'] == criterion and r['labels'] == '0;0;0'
         and r['min_confidence'] == '5' and r['complete'] == 'True'}
        for criterion in ('underspecified', 'false_negative')
    ])
    inventory = json.loads((ROOT / 'inventory.json').read_text())['tasks']
    tasks = sorted((t for t in inventory if t['task_id'] in ids), key=lambda t: t['task_id'])
    assert len(ids) == len(tasks) == len({t['task_id'] for t in tasks}) == 66
    assert all({(l['category'], l['label']) for l in t['labels']} ==
               {(c, 'PASS') for c in CATS} for t in tasks)
    evidence, rows, predictions, sources = [], [], {}, []
    for (judge, fmt), filename in FILES.items():
        path = ROOT / (filename + '-accepted-verdicts.json')
        raw = path.read_bytes()
        data = json.loads(raw)
        sources.append({'path': str(path), 'sha256': hashlib.sha256(raw).hexdigest()})
        selected = [data.get(t['task'], []) for t in tasks]
        assert all(len(vs) == 3 and len({v['trial'] for v in vs}) == 3 for vs in selected)
        for task, vs in zip(tasks, selected):
            evidence.append({'judge': judge, 'format': fmt, 'task_id': task['task_id'],
                             'task': task['task'], 'reviews': vs})
        for scope, cats in [('both human-labeled criteria', CATS),
                            ('verifier fairness', (CATS[1],)),
                            ('issue clarity', (CATS[0],)), ('whole rubric', None)]:
            votes = []
            for vs in selected:
                task_votes = []
                for v in vs:
                    if fmt == 'classification':
                        clean = v['category'] == 'CLEAN' if cats is None else v['category'] not in cats
                    else:
                        checks = v['checks']
                        keys = cats or tuple(checks)
                        assert keys and all(checks[k]['outcome'] in ('pass', 'fail') for k in keys)
                        clean = all(checks[k]['outcome'] == 'pass' for k in keys)
                    task_votes.append(int(clean))
                votes.append(task_votes)
            predictions[judge, fmt, scope] = votes
            counts = [sum(v[i] for v in votes) for i in range(3)]
            rows.append({'judge': judge, 'format': fmt, 'scope': scope,
                         'pass_by_review_index': counts, 'pass_rate': sum(counts)/198,
                         'TN': sum(counts) if cats and fmt == 'labeling' else None,
                         'FP': 198-sum(counts) if cats and fmt == 'labeling' else None,
                         'TP': 0 if cats and fmt == 'labeling' else None,
                         'FN': 0 if cats and fmt == 'labeling' else None,
                         'accuracy': sum(counts)/198 if cats and fmt == 'labeling' else None,
                         'majority_pass': sum(sum(v) >= 2 for v in votes),
                         'unanimous': sum(len(set(v)) == 1 for v in votes),
                         'within_pairwise_agreement': sum(sum(v[i] == v[j] for i,j in ((0,1),(0,2),(1,2))) for v in votes)/198,
                         'pass_vote_histogram': dict(Counter(sum(v) for v in votes))})
    between = []
    for fmt in ('labeling', 'classification'):
        for scope in ('both human-labeled criteria', 'whole rubric'):
            a, b = [predictions[j, fmt, scope] for j in ('Sonnet 5.5', 'Qwen3.8-27B')]
            between.append({'format': fmt, 'scope': scope,
                            'majority_agree': sum((sum(x)>=2) == (sum(y)>=2) for x,y in zip(a,b)),
                            'all_pairs_agree': sum(u == v for x,y in zip(a,b) for u in x for v in y),
                            'all_pairs_total': 66*9})
    result = {'generated_at': datetime.now(timezone.utc).isoformat(), 'sources': sources,
              'rows': rows, 'between_judges': between, 'evidence': evidence}
    (HERE / 'judge_results.json').write_text(json.dumps(result, indent=2) + '\n')
    for r in rows:
        print({k:v for k,v in r.items() if k != 'pass_vote_histogram'})
    print('Between judges:', between)


if __name__ == '__main__':
    main()
