"""GLM-5.3-Flash labeling cost per accepted review (failed attempts excluded, as for Sonnet/Qwen) (partial: corrected kpool cohort, tasks with three complete reviews).

Rates checked 2026-11-07 at https://docs.z.ai/guides/overview/pricing: input $0.15, cached input $0.03,
output $0.50 per 1M tokens (cached-input storage free for a limited time). Our runs were self-hosted, so
cache hits are local prefix-cache hits used as an estimate of API caching.
"""
import glob, json, statistics
from pathlib import Path
HERE = Path(__file__).resolve().parent
K = '/hnvme/workspace/y500bb12-rule-based-checks/judge-runtime-repair-20261106/kpool-both-fixes'
RATE = {'input': 0.15, 'cache_read': 0.03, 'output': 0.50}
common = set(json.loads((HERE / 'estimates.json').read_text())['common_tasks'])
rows = []
for r in sorted(glob.glob(K + '/glm-5.3-flash-labeling-kpool-fixed-*/**/report/stage-5-*.json', recursive=True)):
    for it in json.load(open(r))['items']:
        task = it['task'].rsplit('/', 1)[1]
        if task not in common:
            continue
        good = [v for v in it.get('verdicts', []) if len(v.get('checks') or {}) == 6]
        trials = it.get('usage', {}).get('trials', [])
        if len(good) != 3 or not trials:
            continue
        accepted = {v['trial'] for v in good}
        for u in trials:
            if u['trial'] not in accepted or u.get('input_tokens') is None:
                continue
            inp, cr, out = u['input_tokens'], u.get('cache_read_input_tokens') or 0, u['output_tokens']
            rows.append({'task': task, 'trial': u['trial'], 'uncached_input': inp - cr, 'cache_read': cr, 'output': out,
                         'total_tokens': inp + out,
                         'cost_usd': ((inp - cr) * RATE['input'] + cr * RATE['cache_read'] + out * RATE['output']) / 1e6})
n = len(rows)
summary = {'reviews': n, 'tasks': len({r['task'] for r in rows}),
           'mean_cost_usd': sum(r['cost_usd'] for r in rows) / n,
           'mean_total_tokens': sum(r['total_tokens'] for r in rows) / n,
           'median_total_tokens': statistics.median(r['total_tokens'] for r in rows),
           'cache_read_share': sum(r['cache_read'] for r in rows) / sum(r['cache_read'] + r['uncached_input'] for r in rows)}
(HERE / 'estimates_glm_flash.json').write_text(json.dumps({'rates_usd_per_million': RATE, 'pricing_checked': '2026-11-07',
    'pricing_source': 'https://docs.z.ai/guides/overview/pricing', 'summary': summary, 'reviews': rows}, indent=1) + '\n')
print(json.dumps(summary, indent=1))
