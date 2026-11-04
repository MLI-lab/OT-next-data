import sys,json,shutil
from pathlib import Path
sys.path.insert(0,'/home/hpc/y500bb/y500bb12/OT_next_data')
import pyarrow.parquet as pq
from validation.task_inputs.materialize import materialize
from validation.stages.runner import parser
from validation.stages.static_gate import prepare
from validation.rules.static.instruction_suffix import suffix
from validation.rules.dynamic.environment.repair import repair_files
w=Path('/hnvme/workspace/y500bb12-rule-based-checks/bugsinpy-no-repairs-20261101')
out=Path('/home/hpc/y500bb/y500bb12/OT_next_data/validation/results/bugsinpy-stage0-20261102')
tasks=Path('/tmp/y500bb12-bugsinpy-stage0-final/tasks')
out.mkdir(parents=True,exist_ok=False)
sources=['audit-h200.stage4-tasks','r1-h200.stage4-tasks','httpie235.stage4-tasks','httpie.stage4-tasks']
chosen={}
for source in sources:
    for row in pq.read_table(w/source/'0000.parquet',columns=['path']).to_pylist():chosen[row['path']]=source
assert len(chosen)==499,len(chosen)
records=[]
for source in sources:
    selected={name for name,s in chosen.items() if s==source}
    materialize(w/source/'0000.parquet',tasks,selected_ids=selected)
    records.extend(json.loads((tasks.parent/'materialization.json').read_text()))
name='bugsinpy-original-thefuck-3'
materialize(w/'two/tasks.parquet',tasks,selected_ids={name})
records.extend(json.loads((tasks.parent/'materialization.json').read_text()))
t=tasks/name
files={str(p.relative_to(t)):p.read_bytes() for p in t.rglob('*') if p.is_file()}
files['instruction.md']=suffix(files['instruction.md'],files['task.toml'])
files,repair=repair_files(files)
for name_,data in files.items():
 p=t/name_;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data)
chosen[name]='two/tasks.parquet; standard suffix and timestamp setup normalization reconstructed, original prepared copy unavailable'
(out/'materialization.json').write_text(json.dumps(records,indent=2)+'\n')
(out/'provenance.json').write_text(json.dumps({'purpose':'Standalone stage 0 via static_gate.prepare; 499 saved prepared validation inputs plus one reconstructed thefuck-3 input; no containers','sources':chosen,'ai_detection':'Intentionally excluded by user policy (2026-10-26)'},indent=2)+'\n')
a=parser().parse_args([str(tasks),'--out',str(out),'--static-profile','training','--concurrency','4','--cpus','4','--exclude','check_ai_detection.py=AI detection intentionally excluded by user policy (2026-10-26)'])
a.static_gate=True
prepare(a,[])
print('STAGE0_DONE',out,flush=True)
