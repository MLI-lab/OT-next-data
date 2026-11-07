"""Plot all matched accepted reviews; regenerate with uv run --with matplotlib --with numpy python plot_tokens.py."""
import json
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter

HERE=Path(__file__).resolve().parent
d=json.loads((HERE/'estimates.json').read_text())
fields=['uncached_input','cache_read','cache_write_5m','cache_write_1h','output']
series={name:np.array([sum(r[k] for k in fields) for r in d['reviews'] if r['group'].startswith(prefix)]) for name,prefix in [('Sonnet 5.5','Sonnet'),('Qwen3.8-27B','Qwen')]}
assert all(len(v)==1782 and np.all(v>0) for v in series.values())
# GPT-6.1-sol: labeling only, two reviews per task on the same 297 tasks (estimate_gpt.py)
gpt=json.loads((HERE/'estimates_gpt.json').read_text())
series['GPT-6.1-sol']=np.array([sum(r[k] for k in fields) for r in gpt['reviews']]);assert len(series['GPT-6.1-sol'])==594 and np.all(series['GPT-6.1-sol']>0)
# GLM-5.3-Flash: labeling only, partial (accepted reviews so far; estimate_glm_flash.py)
glm=json.loads((HERE/'estimates_glm_flash.json').read_text())
series['GLM-5.3-Flash']=np.array([r['total_tokens'] for r in glm['reviews']]);assert np.all(series['GLM-5.3-Flash']>0)
values=np.concatenate(list(series.values()));bins=np.geomspace(values.min()*.95,values.max()*1.05,44)
colors={'Sonnet 5.5':'#2563eb','Qwen3.8-27B':'#ea580c','GPT-6.1-sol':'#16a34a','GLM-5.3-Flash':'#9333ea'}
plt.rcParams.update({'font.family':'DejaVu Sans','font.size':11,'axes.spines.top':False,'axes.spines.right':False})
fig,ax=plt.subplots(figsize=(7,4),layout='constrained')
for name,v in series.items():
 ax.hist(v,bins=bins,color=colors[name],alpha=.35,label=f'{name} (n = {len(v):,}'+({'GPT-6.1-sol':', labeling only)','GLM-5.3-Flash':', labeling, partial)'}.get(name,')')),edgecolor=colors[name],linewidth=.6)
 ax.axvline(np.median(v),color=colors[name],linestyle='--',linewidth=1.6)
ax.plot([], [], color='#475569', linestyle='--', linewidth=1.6, label='Dashed lines: median')
ax.set_xscale('log');ax.set_xlabel('Total input + output tokens per review (log scale)');ax.set_ylabel('Number of reviews')
ax.set_title('Token usage per judge review',loc='left',fontweight='bold',fontsize=17,pad=15)
ax.xaxis.set_major_formatter(FuncFormatter(lambda x,pos:f'{x/1e6:g}M' if x>=1e6 else f'{x/1e3:g}k' if x>=1e3 else f'{x:g}'))
ax.grid(axis='y',alpha=.2);ax.set_axisbelow(True);ax.legend(frameon=False,loc='upper right')
for ext in ['png','svg','pdf']:
 fig.savefig(HERE/f'token_distribution.{ext}',dpi=140,bbox_inches='tight',facecolor='white')
stats={name:{'reviews':len(v),'median_total_tokens':float(np.median(v)),'mean_total_tokens':float(np.mean(v)),'p90_total_tokens':float(np.percentile(v,90)),'min':int(v.min()),'max':int(v.max())} for name,v in series.items()}
(HERE/'token_distribution_summary.json').write_text(json.dumps(stats,indent=2)+'\n')
print(json.dumps(stats,indent=2))
