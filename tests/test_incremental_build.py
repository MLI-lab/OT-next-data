from copy import deepcopy
import hashlib
import json
from pathlib import Path
import pytest
from validation.patch_repair_loop import incremental as inc, controller as core
from validation import contract as contracts


def contract(tasks):
    return {'tasks': [{'task_id': t, 'sha256': h} for t,h in tasks.items()],
            'sha256': 'contract', 'stage_layout': 'environment-first-v2', 'stages': [1],
            'dataset': {'source': 'source', 'revision': 'pinned'},
            'success_criteria': {'minimum_tasks': len(tasks), 'gate': 'strict'},
            **{k: {k:'same'} for k in ('execution_profile','runtime_dependencies','upstream','implementation')}}


def report(c, states):
    return {'stage_layout': 'environment-first-v2', 'stage':1, 'name':'build_validation',
            'contract_sha256':c['sha256'], 'complete':True, 'contract_findings':[],
            'items':[{'task':'/old/'+t,'status':s} for t,s in states.items()]}


def test_only_changed_failed_restored_and_new_tasks_execute():
    old=contract({'pass':'a','changed':'b','failed':'c','restored':'d','removed':'e'})
    new=contract({'pass':'a','changed':'changed','failed':'c','restored':'d','new':'f'})
    reused,execute=inc.partition(old,new,old['tasks'],new['tasks'],
        report(old,{'pass':'passed','changed':'passed','failed':'error','restored':'passed','removed':'passed'}),['restored'])
    assert reused==['pass']
    assert execute==['changed','failed','new','restored']


@pytest.mark.parametrize('key',['stage_layout','stages','execution_profile','runtime_dependencies','upstream','implementation','expected_outcomes_sha256'])
def test_runtime_or_protocol_changes_invalidate_everything(key):
    old=contract({'a':'a'});new=deepcopy(old);new[key]='changed'
    assert inc.partition(old,new,old['tasks'],new['tasks'],report(old,{'a':'passed'}))==([],['a'])


@pytest.mark.parametrize('change',['revision','gates','force','findings'])
def test_other_reuse_guards(change):
    old=contract({'a':'a'});new=deepcopy(old);r=report(old,{'a':'passed'})
    if change=='revision':new['dataset']['revision']='other'
    if change=='gates':new['success_criteria']['gate']='different'
    if change=='force':new['arguments']={'force_build':True}
    if change=='findings':r['contract_findings']=['bad']
    assert inc.partition(old,new,old['tasks'],new['tasks'],r)==([],['a'])


def test_execute_submits_only_dirty_and_keeps_failed_results(tmp_path,monkeypatch):
    old=contract({'good':'a','fixed':'old'});current=contract({'good':'a','fixed':'new'})
    fresh=contract({'fixed':'new'});fresh['sha256']='fresh'
    previous=tmp_path/'previous.json';previous.write_text(json.dumps(report(old,{'good':'passed','fixed':'failed'})))
    fresh_dir=tmp_path/'actual/report';fresh_dir.mkdir(parents=True)
    (fresh_dir/'stage-1-build.json').write_text(json.dumps(report(fresh,{'fixed':'failed'})))
    monkeypatch.setattr(contracts,'read',lambda p: fresh if str(p)=='fresh' else current)
    calls=[]
    def submit(cfg,source,ids,directory,**kwargs):
        calls.append(ids);return {'contract':'fresh','job_id':'123','submission':str(fresh_dir.parent)}
    monkeypatch.setattr(core,'submit',submit)
    monkeypatch.setattr(core,'wait_for_job',lambda *a:fresh_dir)
    plan={'carrier':{'contract':'current','selected_ids':['good','fixed']},'previous_job':{'job_id':'122'},
          'previous_report':str(previous),'previous_report_sha256':hashlib.sha256(previous.read_bytes()).hexdigest(),
          'previous_contract_sha256':old['sha256'],'reused_ids':['good'],'execute_ids':['fixed']}
    job,path,outcome=inc.execute({'poll_seconds':1},{'review_setup':True},tmp_path,['good','fixed'],tmp_path,plan)
    assert calls==[['fixed']]
    assert outcome['passed_all']==1 and outcome['failures'][0]['task_id']=='fixed'
    result=json.loads((path/'stage-1-incremental.json').read_text())
    assert result['report_kind']=='incremental_build_aggregate'
    assert 'timing' not in result
    assert next(i for i in result['items'] if i['task'].endswith('good'))['reuse_provenance']['source_report']==str(previous)
    assert job['derived_evidence'] is True
    inc.execute({'poll_seconds':1},{'review_setup':True},tmp_path,['good','fixed'],tmp_path,plan)
    assert len(calls)==1  # Resume never submits the subset twice.


def test_no_pending_tasks_needs_no_submission(tmp_path,monkeypatch):
    current=contract({'good':'a'});p=tmp_path/'previous.json';p.write_text(json.dumps(report(current,{'good':'passed'})))
    monkeypatch.setattr(contracts,'read',lambda p:current)
    monkeypatch.setattr(core,'submit',lambda *a,**k:pytest.fail('nothing to submit'))
    plan={'carrier':{'contract':'current'},'previous_job':{'job_id':'122'},'previous_report':str(p),
          'previous_report_sha256':hashlib.sha256(p.read_bytes()).hexdigest(),
          'previous_contract_sha256':current['sha256'],'reused_ids':['good'],'execute_ids':[]}
    _,_,outcome=inc.execute({}, {},tmp_path,['good'],tmp_path,plan)
    assert outcome['passed_all']==1


def test_plan_uses_real_history_evidence_and_freezes_full_contract(tmp_path,monkeypatch):
    old=contract({'good':'a','fixed':'old'});old['sha256']='old'
    current=contract({'good':'a','fixed':'new'})
    previous_dir=tmp_path/'prior/report';previous_dir.mkdir(parents=True)
    (previous_dir/'stage-1-build.json').write_text(json.dumps(report(old,{'good':'passed','fixed':'failed'})))
    evidence=tmp_path/'evidence.json';evidence.write_text(json.dumps({'job':{'contract':'old','submission':str(previous_dir.parent),'job_id':'123'}}))
    state={'history':[{'phase':'build','size':None,'generation':0,'evidence':str(evidence)}],
           'generation':1,'review_setup':True}
    generation=tmp_path/'generation-0001';generation.mkdir()
    monkeypatch.setattr(contracts,'read',lambda p:old if str(p)=='old' else current)
    calls=[]
    def submit(*args,**kwargs):
        calls.append(kwargs);return {'contract':'current','selected_ids':['good','fixed']}
    monkeypatch.setattr(core,'submit',submit)
    planned=inc.plan({},state,tmp_path,['good','fixed'],generation)
    assert planned['reused_ids']==['good'] and planned['execute_ids']==['fixed']
    assert calls[0]['prepare_only'] is True
    assert inc.plan({},state,tmp_path,['good','fixed'],generation)==planned
    assert len(calls)==1


def test_existing_submission_is_not_replanned(tmp_path,monkeypatch):
    p=tmp_path/'stage3-10';p.mkdir();(p/'job.json').write_text('{}')
    monkeypatch.setattr(core,'submit',lambda *a,**k:pytest.fail('existing work'))
    assert inc.plan({}, {'history':[]},tmp_path,['a'],tmp_path) is None


def test_controller_skips_pilots_when_incremental_plan_exists(tmp_path,monkeypatch):
    dataset=tmp_path/'dataset';dataset.mkdir()
    patcher=dataset/'patch.py';patcher.write_text('# unchanged\n')
    cfg={'dataset':'org/example','patcher':str(patcher),'source':'/source','source_revision':'pin',
         'work_root':str(tmp_path),'seed':42,'poll_seconds':1,'publish':False,'pilot_sizes':[10,50,200]}
    config_path=tmp_path/'config.json';config_path.write_text(json.dumps(cfg))
    ids=['a','b'];calls=[]
    monkeypatch.setattr(core,'call',lambda *a,**k:{'changes_needed':False,'reason':'ready'})
    monkeypatch.setattr(core,'materialize_source',lambda *a:tmp_path)
    monkeypatch.setattr(core,'task_ids',lambda *a:ids)
    monkeypatch.setattr(inc,'plan',lambda *a:{'reused_ids':['a'],'execute_ids':['b']})
    outcome={'passed_all':2,'passed_task_ids':ids,'failures':[],'contract_sha256':'c'}
    def execute(*args):
        calls.append('incremental')
        p=tmp_path/'aggregate/report';p.mkdir(parents=True)
        return {'job_id':'123','contract':'contract','submission':str(p.parent)},p,outcome
    monkeypatch.setattr(inc,'execute',execute)
    def submit(cfg,source,selected,directory,**kw):
        calls.append(kw['stages']);assert kw['reuse_stage3']
        p=directory/'submission/report';p.mkdir(parents=True)
        return {'job_id':'124','contract':'contract','submission':str(p.parent)}
    monkeypatch.setattr(core,'submit',submit)
    monkeypatch.setattr(core,'wait_for_job',lambda j,*a:Path(j['submission'])/'report')
    monkeypatch.setattr(core,'summarize',lambda *a,**k:outcome)
    state=core.drive(cfg,config_path,tmp_path/'loop-state.json')
    assert state['status']=='complete'
    assert calls==['incremental',[1,2,3,4]]


def test_prebuild_uses_only_repair_subset(tmp_path,monkeypatch):
    calls=[]
    def submit(config,source,selected,directory,**kw):
        calls.append((selected,kw))
        return {'job_id':'123','submission':str(directory/'submission')}
    monkeypatch.setattr(core,'submit',submit)
    monkeypatch.setattr(core,'wait_for_prebuild',lambda *a:{'passed':True})
    core.prebuild_gate({'poll_seconds':1},tmp_path,['unchanged','fixed'],tmp_path,selected=['fixed'])
    assert calls[0][0]==['fixed'] and calls[0][1]['full'] is False
    core.prebuild_gate({'poll_seconds':1},tmp_path,['unchanged','fixed'],tmp_path,selected=['fixed'])
    assert len(calls)==1
