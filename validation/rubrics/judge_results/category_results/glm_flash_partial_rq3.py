"""Partial RQ3 rows for GLM-5.3-Flash labeling (corrected kpool cohort, river_selected, 3 reviews).

Uses only tasks with three complete reviews; tasks are matched to the Sonnet evidence by task_content_sha256.
    python validation/rubrics/judge_results/category_results/glm_flash_partial_rq3.py
"""
import functools, glob, json, re
from pathlib import Path
import recompute_clean_cohort as base

HERE = Path(__file__).resolve().parent
K = '/hnvme/workspace/y500bb12-rule-based-checks/judge-runtime-repair-20261106/kpool-both-fixes'
hash_to_key = {}


def load(fmt):
    reps = sorted(glob.glob(K + f'/glm-5.3-flash-{fmt}-kpool-fixed-*/**/report/stage-5-*.json', recursive=True))
    out = {}
    for r in reps:
        d = json.load(open(r))
        assert d['review_model'] == 'openai/glm-5.3-flash' and d['review_format'] == fmt
        for k, h in d['task_content_sha256'].items():
            hash_to_key.setdefault(h, k)
        for it in d['items']:
            k = it['task'].rsplit('/', 1)[1]
            for v in it.get('verdicts', []):
                ch = v.get('checks') or {}
                ok = (len(ch) == 6 and all(c.get('outcome') in ('pass', 'fail') for c in ch.values())) if fmt == 'labeling' else bool(v.get('category'))
                if ok:
                    out.setdefault(k, []).append(v)
    return reps, out


reports, reviews = load('labeling')
class_reports, class_reviews = load('classification')


@functools.lru_cache(None)
def rep(p):
    return json.load(open(p))


def glm(e, pool=None):
    r = e['reviews'][0]
    k = re.search(r'/\d{5}-([^/]+)/', r['trial']).group(1)
    h = rep(r['source_report'])['task_content_sha256'].get(k)
    key = hash_to_key.get(h)
    if key is None:  # package rebuilt since the Sonnet run (content hash differs): match by a unique task_id
        same = {x for x in hash_to_key.values() if x.rsplit('__', 1)[0] == e['task_id'].replace('/', '_')}
        key = same.pop() if len(same) == 1 else None
        if key:
            id_matched.add(key)
    v = (reviews if pool is None else pool).get(key, [])
    return v[:3] if len(v) >= 3 else None


id_matched = set()


cat_rows = {}
ce = base.clean_set('Sonnet 5.5', 'labeling', base.EXCLUDED)
cv_all = [glm(e) for e in ce]
for cat in base.CATS:
    pos = [e for e in base.comp['evidence'] if e['judge'] == 'Sonnet 5.5' and e['format'] == 'Labeling'
           and e['truth'] == 'FAIL' and e['criterion'] == cat]
    pv = [[x['checks'][cat]['outcome'] == 'fail' for x in g] for g in map(glm, pos) if g]
    cv = [[x['checks'][cat]['outcome'] == 'fail' for x in g] for g in cv_all if g]
    cat_rows[cat] = {'defects_covered': len(pv), 'defects_total': len(pos), 'detected': sum(sum(v) >= 2 for v in pv),
                     'clean_covered': len(cv), 'clean_total': len(ce), 'clean_passed': sum(sum(v) < 2 for v in cv),
                     'agreement': sum(len(set(v)) == 1 for v in pv + cv), 'tasks': len(pv) + len(cv)}
# RQ2 rows: mean over individual reviews, same definitions as rq2_per_criterion.json
def outcome(v, fmt, cat):
    return v['checks'][cat]['outcome'] == 'fail' if fmt == 'Labeling' else v.get('category') == cat
rq2 = {}
for fmt, pool in [('Labeling', reviews), ('Classification', class_reviews)]:
    cvs = [g for g in (glm(e, pool) for e in ce) if g]
    row = {'CLEAN': {'correct': sum(not any(outcome(v, fmt, c) for c in base.CATS[:3]) for g in cvs for v in g), 'reviews': 3 * len(cvs)}}
    for cat in base.CATS:
        pos = [e for e in base.comp['evidence'] if e['judge'] == 'Sonnet 5.5' and e['format'] == 'Labeling'
               and e['truth'] == 'FAIL' and e['criterion'] == cat]
        gs = [g for g in (glm(e, pool) for e in pos) if g]
        row[cat] = {'correct': sum(outcome(v, fmt, cat) for g in gs for v in g), 'reviews': 3 * len(gs)}
    rq2[fmt] = row
out_rq2 = rq2
out = {'definition': 'GLM-5.3-Flash labeling, corrected kpool cohort (judge-runtime-repair-20261106/kpool-both-fixes); '
                     'partial: only tasks with three complete reviews. Same cohorts and majority rule as structured_comparison.json.',
       'reports': reports, 'tasks_with_reviews': len(reviews),
       'tasks_with_three_reviews': sum(len(v) == 3 for v in reviews.values()), 'rows': cat_rows,
       'classification_reports': class_reports, 'rq2_rows': out_rq2}
(HERE / 'glm_flash_partial_rq3.json').write_text(json.dumps(out, indent=1) + '\n')
for c, r in cat_rows.items():
    print(c, r)
print(json.dumps(out_rq2))
