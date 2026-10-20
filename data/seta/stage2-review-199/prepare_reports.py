from pathlib import Path
import json,re,urllib.request,concurrent.futures
P=Path(__file__).parent
base='https://raw.githubusercontent.com/MLI-lab/OT-next-data/main/data/seta/stage2-review-199/'
items=json.loads((P/'reviews.json').read_text())['items']
(P/'tasks').mkdir(exist_ok=True)
def fetch(item):
 task=Path(item['task']).name;file=P/'tasks'/f'{task}.md'
 if not file.exists():
  for attempt in range(3):
   try:file.write_bytes(urllib.request.urlopen(base+f'tasks/{task}.md',timeout=30).read());break
   except Exception:
    if attempt==2:raise
 text=file.read_text();part=text.split('## Task instructions',1)[1].split('## Judge verdicts',1)[0].strip();m=re.fullmatch(r'(`{3,})markdown\n(.*)\n\1',part,re.S);assert m,task
 return task,m.group(2)
with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:instructions=dict(pool.map(fetch,items))
(P/'instructions.json').write_text(json.dumps(instructions,indent=2))
source=(P.parents[1]/'calibforge/stage2-review-171/build_reports.py').read_text()
source=source.replace("SOURCE=OUT.parent/'reviews.json'","SOURCE=OUT/'reviews.json'")
start=source.index(' reviews=json.loads(SOURCE.read_text()); instructions={}; location={}')
end=source.index(' rows=[]; counts=',start)
source=source[:start]+''' raw=json.loads(SOURCE.read_text()); reviews={Path(v['task']).name:v for v in raw['items']}
 instructions=json.loads((OUT/'instructions.json').read_text())
 assert set(reviews)==set(instructions)
 skipped_criteria={c for c in next(iter(reviews.values()))['verdicts'][0]['checks'] if all(c in v.get('automatic_skips',{}) for v in reviews.values())}
 for c in skipped_criteria:(OUT/f'{c}_clusters.md').unlink(missing_ok=True)
 original=json.loads((OUT/'original_instruction_concision_clusters.json').read_text())
 concision={r['task_id']:r for r in original['tasks']}
 RULES['solution_quality']=[('Hardcoded or copied answers instead of computation',r'hardcod|echo|cat |pre.comput|literal|copy|copied'),('Solution does not reliably solve or verify the task',r'.')]
 RULES['ctrf_reporting']=[('Missing per-test CTRF report',r'.')]
 RULES['solvable']=[('Reference solution fails or task is not solvable as specified',r'.')]
 RULES['task_security']=[('Unsafe task behavior',r'.')]
''' +source[end:]
source=source.replace("   rows.append(dict(task=task", "   if criterion=='instruction_concision' and outcome=='fail':\n    primary=concision[task]['cluster_label'];matches=[primary]+concision[task]['reason_tags']\n   rows.append(dict(task=task")
source=source.replace('# CalibForge review explanation clusters','# SETA review explanation clusters')
source=source.replace('These reports describe this sample only, not all 5,317 kept tasks.','These are the first 199 completed reviews of a seed-42 sample of 1,000 stage-1 survivors, not a separately random sample. The saved run is incomplete; unfinished tasks are excluded.')
source=source.replace('https://huggingface.co/datasets/FWeindel/validated-tasks/tree/main/calibforge_v1','https://github.com/MLI-lab/OT-next-data/tree/main/data/seta/stage2-review-199')
start=source.index(" 'The judge marks all 171 tasks")
end=source.index("'## Criteria'",start)
source=source[:start]+" 'The existing SETA manual instruction-concision clusters are preserved exactly. Other criteria use provisional text-pattern grouping; consult original explanations. No judge verdicts have been changed.','', "+source[end:]
source=source.replace('Grouping uses explicit, ordered text patterns', 'Except for the preserved manual instruction-concision assignments, grouping uses explicit, ordered text patterns')
source=source.replace('Clusters group the judge explanations, not independently verified defects.', 'Clusters group the judge explanations, not independently verified defects. Instruction-concision assignments preserve the original SETA manual clusters; other criteria use the text-pattern method below.')
source=source.replace('Instructions were retrieved from the current Hugging Face `main` snapshot on 2026-10-19; the review JSON does not identify its dataset revision, so exact revision correspondence cannot be guaranteed. Task archives are read in memory without executing or extracting their code.', 'Instructions and rubric definitions were retrieved from the matching SETA stage2-review-199 reports on GitHub on 2026-10-19. Original task reports are saved under `tasks/`.')
source=source.replace('Rerun `python3 build_reports.py` with PyArrow installed and the downloaded parquet files at the paths in the script.', 'Rerun `python3 build_reports.py` using the saved reviews, instructions and original concision assignments in this directory. `prepare_reports.py` downloads inputs and refreshes the generator from the CalibForge report template.')
source=source.replace('import pyarrow.parquet as pq','')
(P/'build_reports.py').write_text(source)
# Definitions stripped of SETA example verdicts, which are already in the reports.
defs={}
s=urllib.request.urlopen(base+'criteria.md').read().decode()
for part in re.split(r'^## ',s,flags=re.M)[1:]:
 name,body=part.split('\n',1);body=re.split(r'^### ',body,flags=re.M)[0];defs[name.replace('-','_')]=re.sub(r'^Pass:.*\n','',body,flags=re.M).strip()
active={c for v in items for c in v['verdicts'][0]['checks'] if c not in v.get('automatic_skips',{})}
(P/'criterion_definitions.json').write_text(json.dumps({c:defs[c] for c in sorted(active)},indent=2))
enrich=(P.parents[1]/'calibforge/stage2-review-171/add_descriptions.py').read_text()
enrich=enrich.replace('The review JSON does not include a rubric revision, so these are reference definitions rather than a verified snapshot of the exact rubric used for this run.', 'These definitions accompany this saved SETA review snapshot.')
enrich=enrich.replace('The exact rubric revision used for these reviews is not recorded.', 'Definitions come from the matching SETA review snapshot.')
(P/'add_descriptions.py').write_text(enrich)
print('Prepared',len(instructions),'instructions and',len(active),'active criteria')
