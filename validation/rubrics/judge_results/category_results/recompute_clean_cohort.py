"""Recompute every clean-cohort table used by judge_results/README.md.

Updates structured_comparison.json (overall, by_criterion), rq2_per_criterion.json,
pooled_prompt_agreement.json and gpt61sol_labeling_rq3.json in place.
Definitions match the original scripts: majority = at least 2 of 3 reviews (GPT: both of 2),
agreement = all reviews give the same binary decision, overall flag = weak verifier,
mismatch or ambiguity (environment mismatch, answer leakage and triviality excluded).

    python validation/rubrics/judge_results/category_results/recompute_clean_cohort.py [--check-old]
"""
import argparse, collections, json, re
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OLD_EXCLUDED = {'django__django-12039', 'django__django-13513', 'django__django-14376',
                'django__django-14534', 'scikit-learn__scikit-learn-14983', 'sympy__sympy-13878'}
# 2026-11-07: gold gets reward 0 in our runtime and the graded FAIL_TO_PASS tests do not cover the fix
# (swe_verified/django10097_runtime_audit).
EXCLUDED = OLD_EXCLUDED | {'django__django-10097'}
CATS = ['VERIFIER-TOO-WEAK', 'INSTR-VERIFIER-MISMATCH', 'INSTR-AMBIGUOUS', 'INSTR-ENV-MISMATCH']
JUDGES = ['Sonnet 5.5', 'Qwen3.8-27B']
FORMATS = ['Labeling', 'Classification']


def load(p):
    return json.loads((ROOT / p).read_text())


comp = load('category_results/prompt_format_comparison.json')
clean = load('swe_verified/judge_results.json')


def outcome(r, fmt, cat):
    return r['checks'][cat]['outcome'] == 'fail' if fmt == 'Labeling' else r['category'] == cat


def flag(r, fmt):
    return any(outcome(r, fmt, c) for c in CATS[:3])


def clean_set(judge, fmt, excluded):
    return [e for e in clean['evidence'] if e['judge'] == judge and e['format'].lower() == fmt.lower()
            and e['task_id'] not in excluded]


def compute(excluded):
    n = 66 - len(excluded)
    overall, rows = [], []
    for j in JUDGES:
        for fmt in FORMATS:
            ce = clean_set(j, fmt, excluded); assert len(ce) == n, (j, fmt, len(ce))
            pos = [e for e in comp['evidence'] if e['judge'] == j and e['format'] == fmt and e['truth'] == 'FAIL']
            base = [e for e in pos if e['criterion'] in CATS[:3]]; assert len(base) == 146
            overall.append({'judge': j, 'format': fmt,
                            'clean_correct': sum(not flag(r, fmt) for e in ce for r in e['reviews']),
                            'clean_reviews': 3 * n,
                            'defect_flagged': sum(flag(r, fmt) for e in base for r in e['reviews']),
                            'defect_reviews': 3 * len(base)})
            for cat in CATS:
                pv = [[outcome(r, fmt, cat) for r in e['reviews']] for e in pos if e['criterion'] == cat]
                cv = [[outcome(r, fmt, cat) for r in e['reviews']] for e in ce]
                rows.append({'judge': j, 'format': fmt, 'criterion': cat, 'defects': len(pv),
                             'detected': sum(sum(v) >= 2 for v in pv), 'clean_tasks': n,
                             'false_flags': sum(sum(v) >= 2 for v in cv),
                             'agreement': sum(len(set(v)) == 1 for v in pv + cv), 'tasks': len(pv) + n})
    return overall, rows


def pooled(excluded):
    ces = {f: {e['task_id']: e for e in clean_set('Sonnet 5.5', f, excluded)} for f in ['labeling', 'classification']}
    out = {'definition': 'Sonnet: all six binary decisions agree, combining three labeling and three classification reviews per task case.',
           'excluded_clean_ids': sorted(excluded), 'clean': [], 'criteria': []}
    for skip in [set(), {'ANSWER-LEAK', 'TASK-TRIVIAL'}]:
        ev = []
        for tid in sorted(ces['labeling']):
            l, c = ces['labeling'][tid]['reviews'], ces['classification'][tid]['reviews']
            votes = [any(ch['outcome'] == 'fail' for k, ch in r['checks'].items() if k not in skip) for r in l] + \
                    [r['category'] not in skip | {'CLEAN'} for r in c]
            ev.append({'task_id': tid, 'six_defect_votes': votes})
        out['clean'].append({'excluded_categories': sorted(skip), 'tasks': len(ev),
                             'agreement': sum(len(set(e['six_defect_votes'])) == 1 for e in ev), 'evidence': ev})
    for cat in CATS:
        sets = {f: [e for e in comp['evidence'] if e['judge'] == 'Sonnet 5.5' and e['format'] == f
                    and e['criterion'] == cat and e['truth'] == 'FAIL'] for f in FORMATS}
        pairs = list(zip(sets['Labeling'], sets['Classification'])) + \
            [(ces['labeling'][t], ces['classification'][t]) for t in sorted(ces['labeling'])]
        ev = []
        for l, c in pairs:
            assert l['task_id'] == c['task_id']
            ev.append({'task_id': l['task_id'], 'six_defect_votes': [r['checks'][cat]['outcome'] == 'fail' for r in l['reviews']]
                       + [r['category'] == cat for r in c['reviews']]})
        out['criteria'].append({'criterion': cat, 'tasks': len(pairs),
                                'agreement': sum(len(set(e['six_defect_votes'])) == 1 for e in ev), 'evidence': ev})
    return out


def rq2(overall):
    rows = []
    for j in JUDGES:
        for fmt in FORMATS:
            o = next(r for r in overall if (r['judge'], r['format']) == (j, fmt))
            counts = {'CLEAN': {'correct': o['clean_correct'], 'reviews': o['clean_reviews']}}
            for cat in CATS:
                es = [e for e in comp['evidence'] if e['judge'] == j and e['format'] == fmt and e['criterion'] == cat and e['truth'] == 'FAIL']
                counts[cat] = {'correct': sum(sum(e['votes']) for e in es), 'reviews': 3 * len(es)}
            rows.append({'judge': j, 'format': fmt, 'counts': counts})
    return rows


def gpt(excluded):
    d = load('category_results/gpt61sol_labeling_rq3.json')
    g = load('raw/gpt-6.1-sol-labeling-report.json'); items = {i['task'].rsplit('/', 1)[1]: i for i in g['items']}
    key = lambda e: re.search(r'/\d{5}-([^/]+)/', e['reviews'][0]['trial']).group(1)
    votes = lambda e, cat: [v['checks'][cat]['outcome'] == 'fail' for v in items[d['keymap'][key(e)]]['verdicts']]
    ce = clean_set('Sonnet 5.5', 'labeling', excluded); n = len(ce)
    rows, per = {}, {}
    for cat in CATS:
        pos = [e for e in comp['evidence'] if e['judge'] == 'Sonnet 5.5' and e['format'] == 'Labeling' and e['truth'] == 'FAIL' and e['criterion'] == cat]
        pv = [votes(e, cat) for e in pos]; cv = [votes(e, cat) for e in ce]
        rows[cat] = dict(defects=len(pv), detected_both=sum(all(x) for x in pv), detected_any=sum(any(x) for x in pv),
                         split_defect=sum(sum(x) == 1 for x in pv), clean_pass_both=sum(not any(x) for x in cv),
                         clean_pass_any=sum(not all(x) for x in cv), split_clean=sum(sum(x) == 1 for x in cv),
                         agreement=sum(len(set(x)) == 1 for x in pv + cv), tasks=len(pv) + n,
                         review_detect=sum(map(sum, pv)), review_total=2 * len(pv))
        per[cat] = {'defect': [(e['task_id'], x) for e, x in zip(pos, pv)], 'clean': [(e['task_id'], x) for e, x in zip(ce, cv)]}
    skip = {'ANSWER-LEAK', 'TASK-TRIVIAL', 'INSTR-ENV-MISMATCH'}
    items_clean = [v for e in ce for v in items[d['keymap'][key(e)]]['verdicts']]
    d.update(rows=rows, per_task=per, excluded_clean_ids=sorted(excluded),
             clean_overall_correct_reviews=sum(not any(ch['outcome'] == 'fail' for k, ch in v['checks'].items() if k not in skip) for v in items_clean),
             clean_overall_reviews=len(items_clean))
    d['definition'] = re.sub(r'\d+ clean \(\d+ excluded\)', f'{n} clean ({len(excluded)} excluded)', d['definition'])
    return d


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--check-old', action='store_true')
    a = ap.parse_args()
    sc = load('category_results/structured_comparison.json')
    if a.check_old:  # reproduce the committed numbers with the old six exclusions
        overall, rows = compute(OLD_EXCLUDED)
        assert overall == sc['overall'], 'overall differs'
        assert rows == sc['by_criterion'], 'by_criterion differs'
        assert pooled(OLD_EXCLUDED)['criteria'][0]['agreement'] == load('category_results/pooled_prompt_agreement.json')['criteria'][0]['agreement']
        assert rq2(overall) == load('category_results/rq2_per_criterion.json')['rows']
        print('old numbers reproduced'); return
    overall, rows = compute(EXCLUDED)
    sc.update(overall=overall, by_criterion=rows, excluded_clean_ids=sorted(EXCLUDED))
    sc['definition'] = sc['definition'].replace('excludes four defective and two subjective tasks',
                                                'excludes four defective, two subjective and one runtime-broken task (django-10097)')
    (HERE / 'structured_comparison.json').write_text(json.dumps(sc, indent=1) + '\n')
    r = load('category_results/rq2_per_criterion.json'); r['rows'] = rq2(overall)
    (HERE / 'rq2_per_criterion.json').write_text(json.dumps(r, indent=2) + '\n')
    (HERE / 'pooled_prompt_agreement.json').write_text(json.dumps(pooled(EXCLUDED), indent=2) + '\n')
    (HERE / 'gpt61sol_labeling_rq3.json').write_text(json.dumps(gpt(EXCLUDED), indent=1) + '\n')
    print('updated')


if __name__ == '__main__':
    main()
