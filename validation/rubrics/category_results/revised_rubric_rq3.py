"""RQ3 rows for the revised rubric (8 criteria) with Qwen3.8-27B and GLM-5.3-Flash labeling, 3 reviews.

Runs: judge-rubric-v2-20261107/{qwen38-27b,glm-5.3-flash}-labeling (HF FWeindel/task-defect-ground-truth@c92211d).
Cohorts are the README's: the 59 clean tasks and the defect tasks of each old criterion
(prompt_format_comparison.json evidence, matched by task_id). Split criteria are grouped back:
weak verifier = ACCEPTS-INCOMPLETE or ACCEPTS-SHORTCUTS; mismatch = CONTRADICTION or
UNSTATED-REQUIREMENT, per review, then majority (>= 2 of 3). Clean is reported strict and lenient
(lenient ignores ACCEPTS-INCOMPLETE); each half is also reported separately. Tasks without three
complete reviews are left out and counted.
    python validation/rubrics/category_results/revised_rubric_rq3.py
"""
import glob, json, re, urllib.request
from pathlib import Path
import recompute_clean_cohort as base

HERE = Path(__file__).resolve().parent
W = '/hnvme/workspace/y500bb12-rule-based-checks/judge-rubric-v2-20261107'
GROUPS = {'VERIFIER-TOO-WEAK': ['VERIFIER-ACCEPTS-INCOMPLETE', 'VERIFIER-ACCEPTS-SHORTCUTS'],
          'INSTR-VERIFIER-MISMATCH': ['INSTR-VERIFIER-CONTRADICTION', 'INSTR-VERIFIER-UNSTATED-REQUIREMENT'],
          'INSTR-AMBIGUOUS': ['INSTR-AMBIGUOUS'], 'INSTR-ENV-MISMATCH': ['INSTR-ENV-MISMATCH']}
HALVES = [h for g in GROUPS.values() if len(g) == 2 for h in g]
TUTANOTA = 'instance_tutao__tutanota-d1aa0ecec288bfc800cfb9133b087c4f81ad8b38-vbc0d9ba8f0071fbe982809910959a6ff8884dbbf'
OLD_INV = '/hnvme/workspace/y500bb12-rule-based-checks/judge-all-models-20261105/inventory.json'
MANIFEST = 'https://huggingface.co/datasets/FWeindel/task-defect-ground-truth/resolve/c92211dd9d703e716069ecda64cf0c5e7640386c/curated/2026-11-04-four-categories/download_manifest.json'
inv = json.load(open(W + '/inventory.json'))
# Several task_ids name two different packages (mteb-retrieve, install-windows-3.11, pytorch-model-cli,
# mteb-leaderboard), so tasks are keyed by package: old evidence -> old package -> record_id -> new package.
by_id = {}
for t in inv['tasks']:
    by_id.setdefault(t['task_id'], []).append(t['task'])
new_name = {t['harbor_sha256']: t['task'] for t in inv['tasks']}
record_new = {r['record_id']: new_name[r['harbor_sha256']]
              for r in json.loads(urllib.request.urlopen(MANIFEST, timeout=300).read())['records']}
old_records = {t['package_key']: {l['category']: l['record_id'] for l in t['labels']}
               for t in json.load(open(OLD_INV))['tasks']}


def package(e):
    """New package name for one README evidence row (criterion of the row picks the record)."""
    keys = [re.match(r'\d+-.+__([0-9a-f]{20})$', x).group(1) for x in e['reviews'][0]['trial'].split('/')
            if re.match(r'\d+-.+__[0-9a-f]{20}$', x)]
    if not keys:  # pilot trials carry no package key; only unambiguous task_ids may use the fallback
        assert len(by_id[e['task_id']]) == 1, e['task_id']
        return by_id[e['task_id']][0]
    return record_new[old_records[keys[0]][e['criterion']]]


def reviews(model):
    out = {}
    # original run first, then the 2026-11-08 retries of reviews without a valid verdict; first three valid reviews count
    for f in sorted(glob.glob(W + f'/{model}-labeling/run/shards/*/report/stage-5-*.json')) + \
            sorted(glob.glob(W + f'/{model}-labeling-retry-n*/run/shards/*/report/stage-5-*.json')):
        for it in json.load(open(f))['items']:
            tid = it['task'].rsplit('/', 1)[1]
            ok = [v['checks'] for v in it.get('verdicts', []) if len(v.get('checks') or {}) == 8
                  and all(c.get('outcome') in ('pass', 'fail') for c in v['checks'].values())]
            out[tid] = (out.get(tid, []) + ok)[:3]
    return out


def fails(checks, crits):
    return any(checks[c]['outcome'] == 'fail' for c in crits)


clean_ids = []
for e in base.clean_set('Sonnet 5.5', 'labeling', base.EXCLUDED):
    assert len(by_id[e['task_id']]) == 1, e['task_id']  # clean tasks are SWE tasks with unique ids
    clean_ids.append(by_id[e['task_id']][0])
clean_ids = sorted(clean_ids)
defect_ids = {cat: sorted({package(e) for e in base.comp['evidence'] if e['judge'] == 'Sonnet 5.5'
                           and e['format'] == 'Labeling' and e['truth'] == 'FAIL' and e['criterion'] == cat})
              for cat in base.CATS}
result = {'definition': __doc__.strip(), 'clean_tasks': len(clean_ids),
          'defect_tasks': {k: len(v) for k, v in defect_ids.items()}, 'models': {}}
for model, label in [('qwen38-27b', 'Qwen3.8-27B'), ('glm-5.3-flash', 'GLM-5.3-Flash')]:
    rv = reviews(model)
    full = lambda ids: [i for i in ids if len(rv.get(i, [])) == 3]
    cl = full(clean_ids)
    m = {'clean_covered': len(cl), 'missing': {'clean': len(clean_ids) - len(cl)}, 'rows': {}, 'halves': {}, 'clean_overall': {}}
    for cat, crits in GROUPS.items():
        ds = full(defect_ids[cat]); m['missing'][cat] = len(defect_ids[cat]) - len(ds)
        pv = [[fails(r, crits) for r in rv[i]] for i in ds]
        cv = [[fails(r, crits) for r in rv[i]] for i in cl]
        row = {'defects': len(ds), 'detected': sum(sum(v) >= 2 for v in pv), 'clean': len(cl),
               'clean_passed': sum(sum(v) < 2 for v in cv), 'agreement': sum(len(set(v)) == 1 for v in pv + cv),
               'tasks': len(pv) + len(cv)}
        if cat == 'VERIFIER-TOO-WEAK':
            lv = [[fails(r, ['VERIFIER-ACCEPTS-SHORTCUTS']) for r in rv[i]] for i in cl]
            row['clean_passed_lenient'] = sum(sum(v) < 2 for v in lv)
            nt = [i for i in ds if not i.startswith(TUTANOTA.replace('/', '_') + '__')]
            row['without_tutanota'] = {'defects': len(nt), 'detected': sum(sum(fails(r, crits) for r in rv[i]) >= 2 for i in nt)}
        m['rows'][cat] = row
    for h in HALVES:
        cat = next(c for c, g in GROUPS.items() if h in g)
        ds = full(defect_ids[cat])
        m['halves'][h] = {'defects_detected': sum(sum(r[h]['outcome'] == 'fail' for r in rv[i]) >= 2 for i in ds),
                          'defects': len(ds), 'clean_flagged': sum(sum(r[h]['outcome'] == 'fail' for r in rv[i]) >= 2 for i in cl),
                          'clean': len(cl)}
    three = [c for g in list(GROUPS.values())[:3] for c in g]
    for name, crits in [('strict', three), ('lenient', [c for c in three if c != 'VERIFIER-ACCEPTS-INCOMPLETE'])]:
        m['clean_overall'][name] = {'tasks_passed': sum(sum(fails(r, crits) for r in rv[i]) < 2 for i in cl),
                                    'reviews_passed': sum(not fails(r, crits) for i in cl for r in rv[i]), 'reviews': 3 * len(cl)}
    result['models'][label] = m
(HERE / 'revised_rubric_rq3.json').write_text(json.dumps(result, indent=1) + '\n')
for label, m in result['models'].items():
    print('==', label, 'missing', m['missing'])
    for cat, r in m['rows'].items():
        extra = f" lenient clean {r['clean_passed_lenient']}/{r['clean']}, w/o tutanota {r['without_tutanota']['detected']}/{r['without_tutanota']['defects']}" if 'clean_passed_lenient' in r else ''
        print(f"  {cat:24s} detect {r['detected']}/{r['defects']}  clean pass {r['clean_passed']}/{r['clean']}  agree {r['agreement']}/{r['tasks']} ({100*r['agreement']/r['tasks']:.1f}%){extra}")
    for h, r in m['halves'].items():
        print(f"    {h:36s} defects {r['defects_detected']}/{r['defects']}  clean flagged {r['clean_flagged']}/{r['clean']}")
    print('  clean overall', m['clean_overall'])
