"""Read-only structural checks; does not execute tasks or replace training validation."""
import ast,csv,hashlib,json,re,shlex,subprocess,sys,tarfile
from pathlib import Path
from collections import Counter
from harbor.models.task.config import TaskConfig
ROOT=Path('/hnvme/workspace/y500bb12-rule-based-checks/published-groundtruth-harbor-20261104')
rows=json.loads((ROOT/'reports/reconstruction.json').read_text())
manifest=[];checks=[];copies=[];inventory=[]
def digest(f):return hashlib.sha256(f.read_bytes()).hexdigest()
def code_ast(f):
 tree=ast.parse(f.read_bytes())
 for node in ast.walk(tree):
  if hasattr(node, 'body') and isinstance(node.body,list):
   node.body=[v for v in node.body if not (isinstance(v,ast.Expr) and isinstance(v.value,ast.Constant) and isinstance(v.value.value,str))]
 return ast.dump(tree,include_attributes=False)
for row in rows:
 t=Path(row['task_path']);case=row['case_id'];issues=[]
 try:TaskConfig.model_validate_toml((t/'task.toml').read_text());schema='PASS'
 except Exception as e:schema='FAIL';issues.append(str(e))
 shell_count=0;py_count=0
 for f in sorted(t.rglob('*')):
  if not f.is_file():continue
  manifest.append(dict(case_id=case,path=str(f.relative_to(t)),bytes=f.stat().st_size,sha256=digest(f)))
  if f.suffix=='.sh':
   shell_count+=1;r=subprocess.run(['bash','-n',str(f)],capture_output=True,text=True)
   if r.returncode:issues.append(str(f.relative_to(t))+': '+r.stderr)
  if f.suffix=='.py':
   py_count+=1
   try:ast.parse(f.read_bytes())
   except SyntaxError as e:
    if case=='PUB-0085' and f.name=='analyze_climate.py':
     checks.append(dict(case_id=case,check='python3_syntax',status='EXPECTED_SOURCE_FIXTURE',detail='Legacy Python 2 program is the modernization task input; preserved unchanged.'))
    else:issues.append(str(f.relative_to(t))+': '+str(e))
  if f.name=='Dockerfile':
   # Check literal local COPY/ADD paths. Remote and stage sources are recorded,
   # never treated as locally available. Do not execute build instructions.
   content=re.sub(r'\\\n',' ',f.read_text())
   for line in content.splitlines():
    m=re.match(r'\s*(COPY|ADD)\s+(.+)',line,re.I)
    if not m:continue
    rest=m.group(2)
    if '--from=' in rest:continue
    rest=re.sub(r'^--\S+\s+','',rest)
    tokens=json.loads(rest) if rest.startswith('[') else shlex.split(rest)
    for src in tokens[:-1]:
     if '$' in src or '://' in src:status='DYNAMIC_OR_REMOTE'
     else:status='PRESENT' if (src == '.' or list(f.parent.glob(src))) else 'MISSING'
     copies.append(dict(case_id=case,dockerfile=str(f.relative_to(t)),source=src,status=status))
     if status=='MISSING':issues.append('Missing build context: '+src)
 original=Path(row['local_instruction']);actual=t/'instruction.md'
 preservation='BYTE_IDENTICAL' if original.read_bytes()==actual.read_bytes() else 'DIFFERENT'
 if preservation=='DIFFERENT':
  preservation='TERMINAL_WHITESPACE_ONLY' if original.read_text().rstrip()==actual.read_text().rstrip() else 'REVIEW_REQUIRED'
  if preservation=='REVIEW_REQUIRED':issues.append('Instruction differs from inventory copy; examine original source.')
 verifier=[]
 for path in row['local_verifier'].split('; '):
  old=Path(path)
  candidates=list(t.rglob(old.name))
  identical=[x for x in candidates if x.is_file() and digest(x)==digest(old)]
  # Wrench cached files have synthetic names; compare content across tests.
  if not identical: identical=[x for x in (t/'tests').rglob('*') if x.is_file() and digest(x)==digest(old)]
  if identical:verifier.append('BYTE_IDENTICAL:'+str(identical[0].relative_to(t)))
  elif row['benchmark']=='SWE-bench Verified':
   rec=json.loads((t/'tests/config.json').read_text())
   if old.name=='test.patch' and rec['test_patch']==old.read_text():verifier.append('BYTE_IDENTICAL:config.json/test_patch')
   elif old.name=='tests.json':
    obj=json.loads(old.read_text());ok=all(obj[k]==rec[k] or (isinstance(rec[k],str) and obj[k]==json.loads(rec[k])) for k in obj if k in rec)
    verifier.append(('MATCH:' if ok else 'REVIEW:')+'config.json/test_ids')
   else:verifier.append('REVIEW:'+old.name)
  else:
   same=[x for x in candidates if x.suffix=='.py' and code_ast(x)==code_ast(old)]
   if same:verifier.append('SAME_CODE_ORIGINAL_COMMENTS_RESTORED:'+str(same[0].relative_to(t)))
   else:verifier.append('REVIEW:'+old.name)
 checks.append(dict(case_id=case,check='structure',status='PASS' if not issues else 'REVIEW',detail='; '.join(issues),schema=schema,shell_files=shell_count,python_files=py_count,instruction_preservation=preservation,verifier_preservation='; '.join(verifier)))
 row['image_status']='NO_EXACT_MATCH_IN_CHECKED_CACHES'
 row['runtime_validation']='NOT_RUN_IMAGES_UNVERIFIED'
 row['static_check_status']='PASS' if not issues else 'REVIEW'
 row['static_check_notes']='; '.join(issues)
 row['instruction_preservation']=preservation;row['verifier_preservation']='; '.join(verifier)
 row['review_subset']='version_context_review' if row['judge_readiness']=='version_or_context_review' else 'static_inputs_available'
 if case=='PUB-0065':row['adapter_notes']+=' Original MCP/service environment dependencies retained; standalone runtime incomplete.'
 # Slim index retains all qualifiers but omits stale recovery next_action.
 keys=['case_id','criterion','reported_label','benchmark','resolved_task_id','task_path','review_subset','reconstruction_mode','static_check_status','static_check_notes','instruction_preservation','verifier_preservation','image_status','runtime_validation','existing_dataset_status','existing_dataset_ids','label_caveat','human_validation','version_match','minimum_missing','adapter_notes','source_url','source_location','instruction_url','verifier_url','pinned_revision']
 inventory.append({k:row[k] for k in keys})
for name,data in [('checks.json',checks),('file_manifest.json',manifest),('docker_copy_checks.json',copies)]:
 (ROOT/'reports'/name).write_text(json.dumps(data,indent=2)+'\n')
def writecsv(name,data):
 with (ROOT/name).open('w',newline='') as f:
  w=csv.DictWriter(f,fieldnames=list(data[0]));w.writeheader();w.writerows(data)
writecsv('task_inventory.csv',inventory)
counts=[]
for criterion in ['test_coverage','instruction_verifier_alignment','task_quality']:
 subset=[r for r in inventory if r['criterion']==criterion]
 counts.append(dict(criterion=criterion,total=len(subset),static_inputs_available=sum(r['review_subset']=='static_inputs_available' for r in subset),version_context_review=sum(r['review_subset']=='version_context_review' for r in subset),existing_dataset_id_match=sum(r['existing_dataset_status']!='no_id_match' for r in subset),runtime_verified=0))
writecsv('counts_by_criterion.csv',counts)
(ROOT/'reports/annotations.json').write_text(json.dumps(rows,indent=2)+'\n')
print(json.dumps(counts,indent=2));print('Checks',Counter(c['status'] for c in checks));print('COPY',Counter(c['status'] for c in copies));print('verifier_review',[(r['case_id'],r['verifier_preservation']) for r in inventory if 'REVIEW' in r['verifier_preservation']]);print('instruction_diff',[(r['case_id'],r['instruction_preservation']) for r in inventory if r['instruction_preservation']!='BYTE_IDENTICAL']);print('files',len(manifest),'bytes',sum(x['bytes'] for x in manifest))
