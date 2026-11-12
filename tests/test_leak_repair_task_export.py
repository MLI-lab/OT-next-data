import json
from pathlib import Path
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from validation.automatic_repairs import archive_files
from validation.contract import files_digest
from validation.leak_repair.task_export import export_tasks
from validation.publishing.publish import pack_task
from validation.run import write


def setup_run(tmp_path, repaired=False):
    source=tmp_path/'tasks'
    binaries={}; hashes={}
    for name in ('a','b'):
        folder=source/name;folder.mkdir(parents=True)
        (folder/'instruction.md').write_text(name)
        binaries[name]=pack_task(folder)
        hashes[name]=files_digest(archive_files(binaries[name]).items())
    out=tmp_path/'out';out.mkdir()
    original=out/'baseline';original.mkdir()
    result_hashes=dict(hashes)
    if repaired:
        result_hashes['a']=files_digest({'instruction.md':b'fixed'}.items())
        record={'from':hashes['a'],'to':result_hashes['a'],'rules':{'agent_editable_test':{
            'applied':True,'details':'fixed: replace instruction','evidence':'test evidence'}}}
        (original/'stage-0.json').write_text(json.dumps({'stage':0,'complete':True,'leak_repairs':{'a':record}}))
    dataset=tmp_path/'data/d';(dataset/'leak_repairs').mkdir(parents=True)
    (dataset/'leak_repairs/agent_editable_test.py').write_text("DATASET='d'\ndef repair_agent_editable_test(files,evidence):\n return {'decision':'fixed','reason':'test'},dict(files,**{'instruction.md':b'fixed'})\n")
    write(out/'results.parquet',[{'task_id':n,'dataset':'d','content_sha256':h,'agent_editable_test':'pass'} for n,h in result_hashes.items()])
    request={'tasks':str(source),'out':str(out),'dataset':'d','dataset_dir':str(dataset),'results':str(original/'results.parquet')}
    return request,{'changed':[],'input_results':request['results']},binaries


def test_export_includes_all_tasks_and_preserves_input_bytes(tmp_path):
    request,report,binaries=setup_run(tmp_path)
    # Parquet source exercises the same archive format as a real dataset.
    source=tmp_path/'input.parquet'
    pq.write_table(pa.Table.from_pylist([{'path':n,'task_binary':b} for n,b in binaries.items()]),source)
    original=source.read_bytes();request['tasks']=str(source)
    result=export_tasks(request,report)
    rows=pq.read_table(result['path']).to_pylist()
    assert {r['path']:r['task_binary'] for r in rows}==binaries
    assert source.read_bytes()==original
    assert result['tasks']==2 and result['hashes_verified']


def test_replays_recorded_repairs_and_hashes_actual_export(tmp_path):
    request,report,binaries=setup_run(tmp_path,True)
    result=export_tasks(request,report)
    rows={r['path']:r for r in pq.read_table(result['path']).to_pylist()}
    assert archive_files(rows['a']['task_binary'])['instruction.md']==b'fixed'
    assert archive_files(rows['b']['task_binary'])==archive_files(binaries['b'])
    assert (Path(request['tasks'])/'a/instruction.md').read_text()=='a'
    for r in rows.values():assert files_digest(archive_files(r['task_binary']).items())==r['content_sha256']
    assert result['rewritten_payloads']==['a']


def test_changed_tasks_come_from_rerun_contract_and_reports(tmp_path,monkeypatch):
    request,report,_=setup_run(tmp_path,True)
    out=Path(request['out']);merged=out/'rerun/merged';merged.mkdir(parents=True)
    (merged/'stage-0-merged.json').write_bytes((out/'baseline/stage-0.json').read_bytes())
    (out/'baseline/stage-0.json').unlink()
    import validation.contract
    monkeypatch.setattr(validation.contract,'read',lambda p:{'arguments':{'tasks':request['tasks']}})
    report['changed']=['a']
    assert export_tasks(request,report)['rewritten_payloads']==['a']


@pytest.mark.parametrize('failure',['missing','hash','drift'])
def test_export_fails_closed_and_keeps_previous_export(tmp_path,failure):
    request,report,_=setup_run(tmp_path,True)
    target=Path(request['out'])/'repaired-tasks.parquet';target.write_bytes(b'previous-good-export')
    if failure=='missing': (Path(request['tasks'])/'a/instruction.md').unlink();(Path(request['tasks'])/'a').rmdir()
    elif failure=='hash':
        (Path(request['out'])/'baseline/stage-0.json').unlink()
    else:
        p=Path(request['dataset_dir'])/'leak_repairs/agent_editable_test.py'
        p.write_text(p.read_text().replace("b'fixed'","b'drifted'"))
    with pytest.raises(ValueError):export_tasks(request,report)
    assert target.read_bytes()==b'previous-good-export'
    assert not list(target.parent.glob('.repaired-tasks-*'))
