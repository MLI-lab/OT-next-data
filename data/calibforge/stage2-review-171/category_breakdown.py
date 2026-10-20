import json,csv,io,tarfile,tomllib,collections
from pathlib import Path
import pyarrow.parquet as pq
p=Path(__file__).parent
rows=json.loads((p/'assignments.json').read_text())['assignments']; ids={r['task'] for r in rows}; categories={}
for file in ['/tmp/calibforge_tasks.parquet','/tmp/calibforge_archive.parquet']:
 for batch in pq.ParquetFile(file).iter_batches(batch_size=32,columns=['path','task_binary']):
  for row in batch.to_pylist():
   task=row['path'].rstrip('/').split('/')[-1]
   if task not in ids:continue
   with tarfile.open(fileobj=io.BytesIO(row['task_binary'])) as tar:
    members=[m for m in tar.getmembers() if m.isfile() and m.name.split('/')[-1]=='task.toml'];assert len(members)==1
    categories[task]=tomllib.loads(tar.extractfile(members[0]).read().decode())['metadata']['category']
assert set(categories)==ids
sizes=collections.Counter(categories.values()); criteria=list(dict.fromkeys(r['criterion'] for r in rows));counts=collections.defaultdict(collections.Counter)
for r in rows:counts[categories[r['task']],r['criterion']][r['outcome']]+=1
active=[c for c in criteria if any(counts[k,c]['fail'] for k in sizes)]
long=[]
for cat,n in sorted(sizes.items()):
 for c in criteria:
  ct=counts[cat,c]; a=ct['pass']+ct['fail'];assert sum(ct.values())==n
  long.append(dict(category=cat,tasks=n,criterion=c,fail=ct['fail'],pass_count=ct['pass'],not_applicable=ct['not_applicable'],applicable=a,failure_rate_applicable=ct['fail']/a if a else None))
with (p/'category_breakdown.csv').open('w') as f:
 w=csv.DictWriter(f,fieldnames=list(long[0]));w.writeheader();w.writerows(long)
(p/'task_categories.json').write_text(json.dumps(categories,indent=2)+'\n')
lines=['# Review failures by task category','',f'{len(ids)} reviewed tasks across {len(sizes)} categories. Categories come from each task’s `task.toml` metadata, not inferred task names. Counts describe the reviewed sample only.','', '[All reports](README.md) · [Full category × criterion CSV](category_breakdown.csv) · [Task category assignments](task_categories.json)','', 'Each task can fail multiple criteria, so criterion counts must not be added to obtain a task count. Rates below use applicable tasks (fail + pass); Genuine N/A outcomes are excluded from rate denominators; automatically skipped criteria are omitted entirely.','', '## Category totals','', '| Category | Reviewed tasks | Tasks with ≥1 failure | Median failed criteria per task |','| --- | ---: | ---: | ---: |']
import statistics
fails=collections.Counter(r['task'] for r in rows if r['outcome']=='fail')
for cat,n in sorted(sizes.items()):
 tasks=[t for t in ids if categories[t]==cat];lines.append(f'| [{cat}](#{cat}) | {n} | {sum(fails[t]>0 for t in tasks)} | {statistics.median(fails[t] for t in tasks):g} |')
lines+=['','Every task fails `solution_quality` because a reference solution is absent. This makes “at least one failure” 100% in every category; it does not establish that these tasks are unsolvable.','', '## Failure count matrix','', 'Cells are failed task counts. This matrix includes every criterion with at least one failure anywhere in the sample. See each category below for applicable denominators and percentages.','', '| Criterion | '+' | '.join(f'{cat} (n={n})' for cat,n in sorted(sizes.items()))+' |','| --- | '+' | '.join('---:' for _ in sizes)+' |']
for c in active:lines.append(f'| [{c}]({c}_clusters.md) | '+' | '.join(str(counts[cat,c]['fail']) for cat in sorted(sizes))+' |')
for cat,n in sorted(sizes.items()):
 lines+=['',f'<a id="{cat}"></a>',f'## {cat}','',f'**{n} tasks reviewed.**','', '| Criterion | Failed | Passed | N/A | Fail / applicable | Failure rate |','| --- | ---: | ---: | ---: | ---: | ---: |']
 for c in sorted(criteria,key=lambda c:(-counts[cat,c]['fail'],c)):
  ct=counts[cat,c];a=ct['fail']+ct['pass'];rate=f"{ct['fail']/a:.1%}" if a else '—'
  lines.append(f"| [{c}]({c}_clusters.md) | {ct['fail']} | {ct['pass']} | {ct['not_applicable']} | {ct['fail']}/{a} | {rate} |")
 lines+=['','<details>','<summary>Show tasks in this category and their failed criteria</summary>','', '| Task | Failed criteria |','| --- | --- |']
 for task in sorted(t for t in ids if categories[t]==cat):
  fc=[r['criterion'] for r in rows if r['task']==task and r['outcome']=='fail']
  lines.append(f"| {task} | "+', '.join(f'[{c}]({c}_clusters.md)' for c in fc)+' |')
 lines+=['','</details>']
(p/'category_breakdown.md').write_text('\n'.join(lines)+'\n')
readme=p/'README.md';s=readme.read_text();link='[Breakdown by task category](category_breakdown.md)'
if link not in s:s=s.replace('## Criteria',link+'\n\n## Criteria')
readme.write_text(s)
assert sum(sizes.values())==171
assert sum(r['fail'] for r in long)==1088
print(json.dumps(sizes,indent=2));print('Verified category totals and all 1,088 failures reconcile with criterion reports.')
