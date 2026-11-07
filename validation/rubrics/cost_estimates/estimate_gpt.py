"""GPT-6.1-sol labeling cost on the cost cohort of estimate.py (same task packages, 2 reviews per task).
Packages are mapped to tasks with the keymap in ../category_results/gpt61sol_labeling_rq3.json, extended by task-content hash."""
import json,glob,hashlib
from pathlib import Path
HERE=Path(__file__).resolve().parent;R=HERE.parent
W='/hnvme/workspace/y500bb12-rule-based-checks/judge-all-models-20261105'
g=json.loads((R/'stage-5-2f4c27d5171f.json').read_text())
est=json.loads((HERE/'estimates.json').read_text())
keymap=json.loads((R/'category_results/gpt61sol_labeling_rq3.json').read_text())['keymap']
gh={}
for k,v in g['task_content_sha256'].items(): gh.setdefault(v,[]).append(k)
srcs={r['source_report'] for f in glob.glob(f'{W}/*accepted-verdicts.json') for rs in json.load(open(f)).values() for r in rs}
for s in sorted(srcs):
    try: d=json.load(open(s))
    except Exception: continue
    for k,h in (d.get('task_content_sha256') or {}).items():
        if k not in keymap and len(gh.get(h,[]))==1: keymap[k]=gh[h][0]
# same-content duplicate pairs that are not in the RQ3 cohorts
dups={'adaptive-rejection-sampler__0e2dcfc95bd5921ce832':'pkg_1342d0ad9f0fba29513e','adaptive-rejection-sampler__e877d437cc0043bf2ee5':'pkg_ae8204f36161bb437114',
  # content changed between runs; matched from explanations (vm.js MIPS VM; fixed MNIST predictions)
  'make-mips-interpreter__38210d00254a72357cd9':'pkg_18fd804cdf4855c58711','pytorch-model-cli__8d4521165ad9821dccc6':'pkg_e5cdfdadcd0227e68da6'}
for k,v in dups.items(): keymap.setdefault(k,v)
items={i['task'].rsplit('/',1)[1]:i for i in g['items']}
# Rates checked 2026-11-07, https://developers.openai.com/api/docs/pricing, standard tier (<=272K input per request)
RATE={'input':2.0,'cache_read':0.10,'output':10.0}
rows=[];missing=[]
for task in est['common_tasks']:
    p=keymap.get(task)
    if not p: missing.append(task);continue
    trials=items[p]['usage']['trials'];assert len(trials)==2
    for u in trials:
        inp,cr,out=u['input_tokens'],u['cache_read_input_tokens'],u['output_tokens'];assert u['cache_write_input_tokens']==0 and 0<=cr<=inp
        rows.append({'group':'GPT labeling','task':task,'package':p,'trial':u['trial'],'uncached_input':inp-cr,'cache_read':cr,'cache_write_5m':0,'cache_write_1h':0,'output':out,
          'cost_usd':((inp-cr)*RATE['input']+cr*RATE['cache_read']+out*RATE['output'])/1e6,'no_cache_cost_usd':(inp*RATE['input']+out*RATE['output'])/1e6})
n=len(rows);sums={k:sum(r[k] for r in rows) for k in ['uncached_input','cache_read','output','cost_usd','no_cache_cost_usd']}
out={'scope':'GPT-6.1-sol labeling via Codex, all 2 reviews per task on the estimate.py common tasks; Codex reported no cache writes. Standard OpenAI tier assumed: no single request exceeds 272K input (per-review totals sum many calls).',
 'pricing_checked':'2026-11-07','pricing_source':'https://developers.openai.com/api/docs/pricing','rates_usd_per_million':RATE,
 'source':{'path':str(R/'stage-5-2f4c27d5171f.json'),'sha256':hashlib.sha256((R/'stage-5-2f4c27d5171f.json').read_bytes()).hexdigest()},
 'missing_tasks':missing,'summary':{'reviews':n,'totals':sums,'means':{k:v/n for k,v in sums.items()},'cache_read_share':sums['cache_read']/(sums['uncached_input']+sums['cache_read'])},'reviews':rows}
(HERE/'estimates_gpt.json').write_text(json.dumps(out,indent=2)+'\n')
print(json.dumps({k:out[k] for k in ['missing_tasks','summary']},indent=2))
