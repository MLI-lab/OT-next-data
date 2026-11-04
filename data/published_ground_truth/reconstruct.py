"""Materialize selected published cases without modifying their defect-bearing tests.
Run with the existing prep Python. Network downloads are bounded task source files.
"""
import ast, csv, hashlib, json, re, shutil, tarfile, zipfile, subprocess, sys
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import requests

ROOT = Path('/hnvme/workspace/y500bb12-rule-based-checks/published-groundtruth-harbor-20261104')
SRC = ROOT/'sources'
OLD = Path('/home/hpc/y500bb/y500bb12/rl-env-quality-review')
rows = json.loads((SRC/'selection.json').read_text())
TASKS = ROOT/'tasks'
TASKS.mkdir(exist_ok=True)

def put(path, content):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_bytes(content if isinstance(content,bytes) else content.encode())

def copy(src,dst): put(dst,Path(src).read_bytes())
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def safe(p):
    p=Path(p)
    if p.is_absolute() or '..' in p.parts: raise ValueError(p)
    return p

def config(task):
    put(task/'task.toml', 'version = "1.0"\n[agent]\ntimeout_sec = 1800\n[verifier]\ntimeout_sec = 1200\n[environment]\nbuild_timeout_sec = 1800\ncpus = 2\nmemory_mb = 4096\nstorage_mb = 10240\n')

logs={}
for fn in ['recover_harbor_log.json','recover_harbor_rev1_log.json']:
    for x in json.loads((SRC/fn).read_text()): logs[x['case_id']]=x
images={x['task_id']:x['image'] for x in json.loads((SRC/'tmax_images.json').read_text())}
source_log=[]

def harbor_file(job):
    case,entry,existing,base=job
    rel=safe(entry['path']); dst=TASKS/case/rel
    old=existing.get(str(rel))
    url=(old or {}).get('url') or base+'/'+str(rel)
    if old and (OLD/old['local_path']).is_file():
        content=(OLD/old['local_path']).read_bytes()
        if old.get('sha256') and hashlib.sha256(content).hexdigest()!=old['sha256']: raise ValueError('hash '+str(rel))
    else:
        r=requests.get(url,timeout=90);r.raise_for_status();content=r.content
    if len(content)>20_000_000: raise ValueError('unexpected size')
    if entry.get('size') is not None and len(content)!=entry['size']: raise ValueError('size mismatch '+url)
    put(dst,content)
    return dict(case_id=case,path=str(rel),url=url,sha256=sha(dst),bytes=len(content))

jobs=[]
for row in rows:
    c=row['case_id']
    if c not in logs: continue
    x=logs[c];ex={f['path']:f for f in x['files'] if f.get('local_path')}
    base=x.get('file_url_base') or x['files'][0]['url'].split('/files/')[0]+'/files'
    for f in x['manifest']: jobs.append((c,f,ex,base))
with ThreadPoolExecutor(max_workers=6) as pool: source_log.extend(pool.map(harbor_file,jobs))
print('Harbor source files',len(jobs),flush=True)

# Official SWE-bench 4.0.3 constants are pure Python definitions. Extract the
# published directive helper and Harbor adapter, excluding heavyweight imports.
constants={};exec((SRC/'swe_constants_python.py').read_text(),constants)
wheel=zipfile.ZipFile(SRC/'swebench-4.0.3-py3-none-any.whl')
ct=ast.parse(wheel.read('swebench/harness/constants/__init__.py'))
non_test=None
for node in ct.body:
    if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='NON_TEST_EXTS' for t in node.targets): non_test=ast.literal_eval(node.value)
ns={'re':re,'SWEbenchInstance':dict,'NON_TEST_EXTS':non_test}
tr=ast.parse((SRC/'swe_test_spec_python.py').read_text())
helper=next(n for n in tr.body if isinstance(n,ast.FunctionDef) and n.name=='get_test_directives')
exec(compile(ast.Module(body=[helper],type_ignores=[]),'swebench_directives','exec'),ns)
u=ast.parse((SRC/'swe_utils.py').read_text());u.body=[n for n in u.body if not isinstance(n,ast.ImportFrom) or not n.module.startswith('swebench')]
ns.update(LATEST='latest',MAP_REPO_VERSION_TO_SPECS=constants['MAP_REPO_VERSION_TO_SPECS_PY'])
exec(compile(u,'harbor_swe_utils','exec'),ns)

for row in rows:
    case=row['case_id']; task=TASKS/case; task.mkdir(exist_ok=True); family=row['benchmark']; note='Original task files restored; no verifier repairs.'
    if case in logs:
        mode='native_harbor'
    elif family=='SETA':
        mode='native_harbor'
        for f in (SRC/'wrench'/row['resolved_task_id']).rglob('*'):
            if f.is_file() and f.name not in ['analysis.toml','variants.json']:
                copy(f,task/f.relative_to(SRC/'wrench'/row['resolved_task_id']))
        note+=' Upstream analysis.toml and variants.json withheld from judge input. Optional solution for 637 not recovered.'
    elif family=='TMax':
        mode='tmax_image_adapter';config(task)
        copy(row['local_instruction'],task/'instruction.md');copy(row['local_verifier'],task/'tests/test.sh')
        put(task/'environment/Dockerfile',f"FROM {images[row['resolved_task_id']]}\nWORKDIR /home/user\n")
        # Setup script is an alternative source recipe, not rerun over the
        # task-specific initialized image. Native source remains outside tasks.
        source=SRC/'tmax_native'/row['resolved_task_id']
        with zipfile.ZipFile(SRC/'tmax_tasks.zip') as z:
            prefix=row['resolved_task_id']+'/'
            for name in z.namelist():
                if name.startswith(prefix) and not name.endswith('/') and '/solutions/' not in name and not name.endswith('task_summary.txt'):
                    put(source/safe(name[len(prefix):]),z.read(name))
        note='Published per-task Docker image; original training instruction and test.sh unchanged. Image tag is not digest-pinned. Default adapter resource/time limits; native setup retained separately, not rerun. Runtime initialization unverified.'
    elif family=='SWE-bench Verified':
        mode='swebench_adapter';config(task)
        gid=row['existing_dataset_ids'].split(';')[0]; archive=OLD/'recovered_artifacts'/('existing_'+gid+'.tar')
        with tarfile.open(archive) as t:
            rec=json.load(t.extractfile('tasks/'+gid+'/original/task.json'))
            for m in t.getmembers():
                pref='tasks/'+gid+'/original/attachments/'
                if m.isfile() and m.name.startswith(pref): put(task/'attachments'/safe(m.name[len(pref):]),t.extractfile(m).read())
        copy(row['local_instruction'],task/'instruction.md')
        put(task/'tests/config.json',json.dumps(rec,indent=2)+'\n')
        cmds=ns['get_test_commands'](rec['test_patch'],rec['repo'],rec['version'],rec['base_commit'])
        put(task/'tests/test.sh',(SRC/'swe_test.sh').read_text().replace('{test_commands}',cmds))
        image='swebench/sweb.eval.x86_64.'+rec['instance_id'].replace('__','_1776_')+':latest'
        put(task/'environment/Dockerfile',f'FROM {image}\nWORKDIR /testbed\nRUN curl -LsSf https://astral.sh/uv/install.sh | sh\nRUN mkdir -p /logs/verifier\n')
        put(task/'solution/solve.sh','#!/bin/bash\nset -e\ncd /testbed\ngit apply /solution/reference.patch\n')
        put(task/'solution/reference.patch',rec['patch'])
        note='Original record and test patch; official Harbor adapter at ac533ec72bc506c8a6bf6f296ba6ab80707fad89 with SWE-bench 4.0.3 test commands. Mutable image tag and online uv dependencies; execution unverified. Adapter resource defaults. Issue attachments included.'
    elif family=='Toolathlon':
        mode='toolathlon_static_adapter';config(task)
        copy(row['local_instruction'],task/'instruction.md');copy(row['local_verifier'],task/'tests/evaluate.py')
        copy(row['local_context'],task/'environment/personal_info.md')
        helper_src=(SRC/'extra_2.py').read_text();tree=ast.parse(helper_src)
        fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='normalize_str')
        put(task/'tests/utils/general/helper.py','import re\n\n'+ast.get_source_segment(helper_src,fn)+'\n')
        put(task/'tests/test.sh','#!/bin/bash\nmkdir -p /logs/verifier\npython3 /tests/evaluate.py --agent_workspace /workspace\nstatus=$?\nif [ "$status" -eq 0 ]; then echo 1; else echo 0; fi > /logs/verifier/reward.txt\nexit "$status"\n')
        put(task/'environment/Dockerfile','FROM python:3.11-slim\nWORKDIR /workspace\nCOPY personal_info.md /workspace/personal_info.md\n')
        note='Static judge bundle only: original task, fixture and evaluator; exact normalize_str helper extracted. Browser/MCP tool services and research corpus not reconstructed. Minimal filesystem adapter is not the original agent runtime.'
    else:
        mode='native_harbor_archive';gid=Path(row['local_context']).name
        with tarfile.open(OLD/'recovered_artifacts'/('existing_'+gid+'.tar')) as t:
            pref='tasks/'+gid+'/original/'
            for m in t.getmembers():
                if m.isfile() and m.name.startswith(pref):
                    rel=safe(m.name[len(pref):])
                    if rel.parts[0] in ['instruction.md','task.toml','environment','tests','solution']:put(task/rel,t.extractfile(m).read())
    row.update(task_path=str(task),reconstruction_mode=mode,adapter_notes=note,runtime_validation='NOT_RUN',image_status='PENDING_CACHE_LOOKUP')
    for f in task.rglob('*.sh'):f.chmod(0o755)

put(ROOT/'reports/source_files.json',json.dumps(source_log,indent=2)+'\n')
put(ROOT/'reports/reconstruction.json',json.dumps(rows,indent=2)+'\n')
print('Materialized tasks',len(rows),flush=True)
