"""Reprice accepted reviews on the common complete cohort; read archives without extracting."""
import json,tarfile,collections,hashlib
from pathlib import Path
ROOT=Path('/hnvme/workspace/y500bb12-rule-based-checks/judge-all-models-20261105')
HERE=Path(__file__).resolve().parent
STEMS={'Sonnet labeling':'claude-sonnet-5-5-labeling-existing','Sonnet classification':'claude-sonnet-5-5-classification','Qwen labeling':'qwen38-27b-labeling','Qwen classification':'qwen38-27b-classification'}
data={k:json.loads((ROOT/(v+'-accepted-verdicts.json')).read_text()) for k,v in STEMS.items()}
common=set.intersection(*[{k for k,v in d.items() if len(v)==3} for d in data.values()]);assert len(common)==300
def model_usage_row(obj):
 totals=dict.fromkeys(['uncached_input','cache_read','cache_write_5m','cache_write_1h','output','cost_usd','no_cache_cost_usd'],0)
 models=obj['modelUsage']
 for name,u in models.items():
  assert name in ['claude-sonnet-5-5','claude-sonnet-5'],name
  base=u['inputTokens'];cr=u['cacheReadInputTokens'];w5=u['cacheCreationEphemeral5mInputTokens'];w1=u['cacheCreationEphemeral1hInputTokens'];out=u['outputTokens'];assert w5+w1==u['cacheCreationInputTokens']
  rate=.1 if name=='claude-sonnet-5-5' else .2
  vals={'uncached_input':base,'cache_read':cr,'cache_write_5m':w5,'cache_write_1h':w1,'output':out,'cost_usd':(base*2+cr*rate+w5*2.5+w1*4+out*10)/1e6,'no_cache_cost_usd':((base+cr+w5+w1)*2+out*10)/1e6}
  assert abs(vals['cost_usd']-u['costUSD'])<1e-6,(name,vals['cost_usd'],u['costUSD'])
  for k,v in vals.items():totals[k]+=v
 assert abs(totals['cost_usd']-obj['total_cost_usd'])<1e-6
 return {**totals,'per_model_usage':models}
rows=[];missing=[]
for group,d in data.items():
 vs=[dict(v,task=task) for task in sorted(common) for v in d[task]]
 assert len({v['trial'] for v in vs})==900
 byreport=collections.defaultdict(list)
 for v in vs:byreport[v['source_report']].append(v)
 for report,selected in byreport.items():
  rp=Path(report)
  if group.startswith('Qwen'):
   rd=json.loads(rp.read_text());usage={u['trial']:u for item in rd['items'] for u in item.get('usage',{}).get('trials',[])}
   for v in selected:
    u=usage.get(v['trial'],{});inp=u.get('input_tokens');cached=u.get('cache_read_input_tokens');out=u.get('output_tokens')
    if any(x is None for x in [inp,cached,out]):missing.append({'group':group,'trial':v['trial'],'reason':'missing Qwen usage'});continue
    assert 0<=cached<=inp
    rows.append({'group':group,'task':v['task'],'trial':v['trial'],'source':report,'uncached_input':inp-cached,'cache_read':cached,'cache_write_5m':0,'cache_write_1h':0,'output':out,'cost_usd':((inp-cached)*.5+cached*.1+out*3)/1e6,'no_cache_cost_usd':(inp*.5+out*3)/1e6})
  else:
   archive=rp.parent.parent/'evidence.tar.gz'
   wanted={'results/'+v['trial'].split('/results/',1)[1]+'/agent/claude-code.txt':v for v in selected}
   found=set()
   if archive.exists():
    with tarfile.open(archive,'r|gz') as tf:
     for m in tf:
      name=m.name.removeprefix('./') if hasattr(str,'removeprefix') else m.name[2:] if m.name.startswith('./') else m.name
      if name not in wanted:continue
      v=wanted[name];found.add(name);text=tf.extractfile(m).read().decode(errors='replace');records=[]
      for line in text.splitlines():
       try:obj=json.loads(line)
       except ValueError:continue
       if isinstance(obj,dict) and obj.get('type')=='result':records.append(obj)
      if len(records)!=1:missing.append({'group':group,'trial':v['trial'],'reason':f'{len(records)} result envelopes'});continue
      obj=records[0];u=obj.get('usage',{});base=u.get('input_tokens');cr=u.get('cache_read_input_tokens');cw=u.get('cache_creation_input_tokens');out=u.get('output_tokens');detail=u.get('cache_creation',{})
      if any(x is None for x in [base,cr,cw,out]):missing.append({'group':group,'trial':v['trial'],'reason':'missing Sonnet usage'});continue
      w5=detail.get('ephemeral_5m_input_tokens',0);w1=detail.get('ephemeral_1h_input_tokens',0)
      if w5+w1!=cw:missing.append({'group':group,'trial':v['trial'],'reason':'unknown cache write lifetime'});continue
      rows.append({'group':group,'task':v['task'],'trial':v['trial'],'source':str(archive)+'::'+name,'uncached_input':base,'cache_read':cr,'cache_write_5m':w5,'cache_write_1h':w1,'output':out,'cost_usd':(base*2+cr*.1+w5*2.5+w1*4+out*10)/1e6,'no_cache_cost_usd':((base+cr+cw)*2+out*10)/1e6,'reported_cost_usd':obj.get('total_cost_usd'),**model_usage_row(obj)})
   for name in wanted.keys()-found:missing.append({'group':group,'trial':wanted[name]['trial'],'reason':'archive/log missing'})
 print(group,'done',sum(r['group']==group for r in rows),'usage records',flush=True)
# Recover pilot logs stored directly, rather than in shard archives.
remaining=[]
for m in missing:
 p=Path(m['trial'])/'agent/claude-code.txt'
 if not m['group'].startswith('Sonnet') or not p.exists():remaining.append(m);continue
 records=[]
 for line in p.read_text().splitlines():
  try:obj=json.loads(line)
  except ValueError:continue
  if isinstance(obj,dict) and obj.get('type')=='result':records.append(obj)
 assert len(records)==1
 obj=records[0];u=obj['usage'];base=u['input_tokens'];cr=u['cache_read_input_tokens'];cw=u['cache_creation_input_tokens'];out=u['output_tokens'];detail=u['cache_creation'];w5=detail['ephemeral_5m_input_tokens'];w1=detail['ephemeral_1h_input_tokens'];assert w5+w1==cw
 task=next(t for t,vs in data[m['group']].items() if any(v['trial']==m['trial'] for v in vs))
 rows.append({'group':m['group'],'task':task,'trial':m['trial'],'source':str(p),'uncached_input':base,'cache_read':cr,'cache_write_5m':w5,'cache_write_1h':w1,'output':out,'cost_usd':(base*2+cr*.1+w5*2.5+w1*4+out*10)/1e6,'no_cache_cost_usd':((base+cr+cw)*2+out*10)/1e6,'reported_cost_usd':obj.get('total_cost_usd'),**model_usage_row(obj)})
missing=remaining
# Keep identical full task cohorts in all four estimates.
complete=set.intersection(*[{task for task,n in collections.Counter(r['task'] for r in rows if r['group']==group).items() if n==3} for group in data])
excluded_usage_tasks=sorted(common-complete)
rows=[r for r in rows if r['task'] in complete]
common=complete
summaries=[]
for group in data:
 rs=[r for r in rows if r['group']==group];n=len(rs)
 sums={k:sum(r[k] for r in rs) for k in ['uncached_input','cache_read','cache_write_5m','cache_write_1h','output','cost_usd','no_cache_cost_usd']}
 inp=sum(sums[k] for k in ['uncached_input','cache_read','cache_write_5m','cache_write_1h'])
 summaries.append({'group':group,'expected_reviews':len(common)*3,'reviews_with_usage':n,'totals':sums,'means':{k:v/n for k,v in sums.items()},'cache_read_share':sums['cache_read']/inp})
result={'scope':'Common task packages with complete usage, three accepted reviews per task and format; failed/rejected attempts and GPU/container compute excluded. Qwen local cache hits are an estimate of API implicit caching, not a provider invoice.','pricing_checked':'2026-11-06','rates_usd_per_million':{'Sonnet_5_fallback':{'input':2,'cache_read':.2,'cache_write_5m':2.5,'cache_write_1h':4,'output':10},'Sonnet':{'input':2,'cache_read':.1,'cache_write_5m':2.5,'cache_write_1h':4,'output':10},'Qwen_Singapore_international':{'input':.5,'implicit_cache_read':.1,'output':3}},'pricing_sources':['https://platform.claude.com/docs/en/about-claude/pricing','https://www.alibabacloud.com/help/en/model-studio/qwen3-8-27b'],'common_tasks':sorted(common),'excluded_usage_tasks':excluded_usage_tasks,'sources':[{'path':str(ROOT/(v+'-accepted-verdicts.json')),'sha256':hashlib.sha256((ROOT/(v+'-accepted-verdicts.json')).read_bytes()).hexdigest()} for v in STEMS.values()],'summaries':summaries,'missing':missing,'reviews':rows}
(HERE/'estimates.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps({'summaries':summaries,'missing':missing},indent=2))
