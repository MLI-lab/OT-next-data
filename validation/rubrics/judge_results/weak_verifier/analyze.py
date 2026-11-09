"""Category-specific confusion matrices from three accepted reviews per task."""
import argparse,collections,csv,hashlib,io,json,urllib.request
from pathlib import Path
ROOT=Path('/hnvme/workspace/y500bb12-rule-based-checks/judge-all-models-20261105')
HERE=Path(__file__).resolve().parent
CAT='VERIFIER-TOO-WEAK'
FILES={('Sonnet 5.5','Labeling'):'claude-sonnet-5-5-labeling-existing',('Qwen3.8-27B','Labeling'):'qwen38-27b-labeling'}
def fetch(url):return urllib.request.urlopen(url,timeout=90).read()
def matrix(truth,pred):
 c=dict.fromkeys(['TP','FN','FP','TN'],0)
 for t,p in zip(truth,pred):c['TP' if t and p else 'FN' if t else 'FP' if p else 'TN']+=1
 return c
def main():
 global CAT
 parser=argparse.ArgumentParser(description=__doc__)
 parser.add_argument('--category',default=CAT,choices=['VERIFIER-TOO-WEAK','INSTR-VERIFIER-MISMATCH','INSTR-ENV-MISMATCH','INSTR-AMBIGUOUS'])
 parser.add_argument('--output',type=Path,default=HERE/'results.json')
 parser.add_argument('--revision',help='Pin the Hugging Face ground-truth revision.')
 args=parser.parse_args();CAT=args.category
 repo='FWeindel/task-defect-ground-truth';rev=args.revision or json.loads(fetch('https://huggingface.co/api/datasets/'+repo))['sha']
 raw=fetch('https://huggingface.co/datasets/'+repo+'/resolve/'+rev+'/curated/2026-11-04-four-categories/category_examples.csv')
 gt=[r for r in csv.DictReader(io.StringIO(raw.decode())) if r['category']==CAT];byid={r['record_id']:r for r in gt};assert len(byid)==len(gt) and gt
 inv=json.loads((ROOT/'inventory.json').read_text())['tasks'];tasks=[]
 for t in inv:
  for l in t['labels']:
   if l['record_id'] in byid:
    assert byid[l['record_id']]['label']==l['label'];tasks.append((t,l))
 assert len(tasks)==len(gt)
 evidence=[];rows=[];sources=[];allvotes={}
 for (judge,fmt),stem in FILES.items():
  path=ROOT/(stem+'-accepted-verdicts.json');payload=path.read_bytes();data=json.loads(payload);sources.append({'path':str(path),'sha256':hashlib.sha256(payload).hexdigest()});votes={};missing=[]
  # Fill incomplete summary entries from registered recovery reports, preserving
  # existing accepted reviews and selecting the first three distinct trials.
  recovery_dirs=list(dict.fromkeys(r['directory'] for r in json.loads((ROOT/'recoveries.json').read_text()) if r['run']==stem))
  wanted={t['task'] for t,l in tasks if len(data.get(t['task'],[]))<3}
  used=set()
  for directory in recovery_dirs:
   for report_path in sorted(Path(directory).glob('run/shards/*/report/stage-5-*.json')):
    report_raw=report_path.read_bytes()
    for item in json.loads(report_raw).get('items',[]):
     task_name=Path(item['task']).name
     if task_name not in wanted:continue
     dest=data.setdefault(task_name,[]);seen={v['trial'] for v in dest}
     for v in item.get('verdicts',[]):
      if len(dest)==3:break
      if v['trial'] not in seen:
       dest.append({'source_report':str(report_path),**v});seen.add(v['trial']);used.add(report_path)
  for report_path in sorted(used):sources.append({'path':str(report_path),'sha256':hashlib.sha256(report_path.read_bytes()).hexdigest(),'purpose':'Fill missing accepted reviews from recovery report'})
  for t,l in tasks:
   vs=data.get(t['task'],[])
   if len(vs)!=3:missing.append({'task_id':t['task_id'],'label':l['label'],'accepted_reviews':len(vs)});continue
   assert len({v['trial'] for v in vs})==3
   if fmt=='Labeling':
    assert all(v['checks'][CAT]['outcome'] in ['pass','fail'] for v in vs)
    pred=[v['checks'][CAT]['outcome']=='fail' for v in vs]
   else:pred=[v['category']==CAT for v in vs]
   votes[t['task']]=(l['label']=='FAIL',pred)
   evidence.append({'judge':judge,'format':fmt,'task_id':t['task_id'],'task':t['task'],'ground_truth':l['label'],'positive_predictions':pred,'reviews':vs})
  allvotes[judge,fmt]=votes
  truth=[v[0] for v in votes.values()];pred=[sum(v[1])>=2 for v in votes.values()]
  pooled=matrix([v[0] for v in votes.values() for _ in range(3)],[p for v in votes.values() for p in v[1]])
  rows.append({'judge':judge,'format':fmt,'tasks':len(votes),'GT_FAIL':sum(truth),'GT_PASS':len(truth)-sum(truth),'majority':matrix(truth,pred),'pooled_3_reviews':pooled,'review_indices':[matrix(truth,[v[1][i] for v in votes.values()]) for i in range(3)],'missing':missing})
 common=set.intersection(*(set(v) for v in allvotes.values()));comparison=[]
 for (j,f),vs in allvotes.items():
  comparison.append({'judge':j,'format':f,'tasks':len(common),'majority':matrix([vs[k][0] for k in sorted(common)],[sum(vs[k][1])>=2 for k in sorted(common)])})
 agreement=[{'judge':j,'tasks':len(vs),'unanimous':sum(len(set(v[1]))==1 for v in vs.values()),'pairwise_agreements':sum(v[1][a]==v[1][b] for v in vs.values() for a,b in [(0,1),(0,2),(1,2)]),'pairwise_comparisons':3*len(vs)} for (j,f),vs in allvotes.items()]
 result={'ground_truth_revision':rev,'ground_truth_category_records':gt,'ground_truth_csv_sha256':hashlib.sha256(raw).hexdigest(),'definition':f'Positive = {CAT} defect. Labeling: criterion FAIL. Majority uses at least 2/3 positive votes. Historical six-criterion rubric, before weak-verifier split.','sources':sources,'rows':rows,'common_complete_cohort':comparison,'within_judge_agreement':agreement,'evidence':evidence}
 args.output.parent.mkdir(parents=True,exist_ok=True)
 args.output.write_text(json.dumps(result,indent=2)+'\n')
 for r in rows:print({k:v for k,v in r.items() if k not in ['missing','review_indices']})
 print('Common cohort:',comparison)
if __name__=='__main__':main()
