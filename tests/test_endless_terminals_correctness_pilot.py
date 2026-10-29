"""The pilot must fail closed on invalid model edits and missing runtime proof."""
import copy
import hashlib
from pathlib import Path
import pytest
from data.endless_terminals import correctness_pilot as pilot


def proposal():
    return {'summary': 'test', 'assessment': {k: {'outcome': 'pass', 'reason': 'checked'} for k in pilot.CORE},
            'findings': [], 'edits': [], 'needs_independent_review': False, 'unresolved': [],
            'probes': [{'id': 'valid', 'script': 'true', 'run_reference': True, 'expected_reward': 1, 'original_reward': None, 'reason': 'valid'},
                       {'id': 'invalid', 'script': 'true', 'run_reference': True, 'expected_reward': 0, 'original_reward': None, 'reason': 'invalid'}]}


@pytest.mark.parametrize('path', ['../instruction.md', '/tmp/evil', 'tests/../../evil', 'task.toml', 'environment/Dockerfile', 'setup_files/setup.sh', 'solution/o3_summary.json'])
def test_no_scope_escape(path):
    p = proposal();p['edits'] = [{'path': path, 'before_sha256': None, 'content': 'x', 'reason': 'fix'}]
    with pytest.raises(ValueError): pilot.validate_proposal(p, {})


def test_requires_original_hash_and_valid_python():
    p = proposal();p['edits'] = [{'path': 'tests/check.py', 'before_sha256': 'wrong', 'content': 'assert True', 'reason': 'fix'}]
    with pytest.raises(ValueError, match='hash'): pilot.validate_proposal(p, {'tests/check.py': b'assert False'})
    p['edits'][0].update(before_sha256=pilot.sha(b'assert False'), content='def invalid(')
    with pytest.raises(SyntaxError): pilot.validate_proposal(p, {'tests/check.py': b'assert False'})


def test_confirmed_finding_requires_falsifiable_regression():
    p = proposal();p['findings'] = [{'id': 'bad', 'criterion': 'verifiable', 'disposition': 'confirmed',
                                    'reason': 'accepts bad output', 'evidence': ['tests/check.py'], 'probe_ids': ['invalid']}]
    with pytest.raises(ValueError, match='regression'): pilot.validate_proposal(p, {'tests/check.py': b'assert x'})
    p['probes'][1]['original_reward'] = 1
    assert pilot.validate_proposal(p, {'tests/check.py': b'assert x'}) is p


def test_solvable_and_positive_negative_coverage_required():
    p = proposal();del p['assessment']['solvable']
    with pytest.raises(ValueError, match='four'): pilot.validate_proposal(p, {})
    p = proposal();p['probes'][1]['expected_reward'] = 1
    with pytest.raises(ValueError, match='Both'): pilot.validate_proposal(p, {})


@pytest.mark.parametrize('script', ['rm /tests/test.py', 'echo 1 > /logs/verifier/reward.txt', 'rm /opt/et-verifier/venv/bin/pytest', 'pip install evil'])
def test_probe_does_not_attack_infrastructure(script):
    p = proposal();p['probes'][1]['script'] = script
    with pytest.raises(ValueError): pilot.validate_proposal(p, {})


def test_zero_reward_needs_completed_probe_and_real_assertions():
    c = {'expected_reward': 0, 'marker': 'EXACT_MARKER'}
    i = {'status': 'failed', 'rewards': [0.0], 'findings': ['000: expected reward 1, got 0.0']}
    assert pilot.case_passed(c, i, ['hello\nEXACT_MARKER\n'])
    assert not pilot.case_passed(c, i, ['hello'])
    assert not pilot.case_passed(c, i, ['prefix EXACT_MARKER'])
    i['findings'].append('000: verifier did not execute tests')
    assert not pilot.case_passed(c, i, ['EXACT_MARKER'])
    assert not pilot.case_passed(c, None, ['EXACT_MARKER'])


def test_probe_runs_reference_and_writes_marker_only_after_success(tmp_path):
    source = tmp_path/'original';(source/'solution').mkdir(parents=True)
    (source/'solution/solve.sh').write_text('#!/bin/bash\nprintf original')
    (source/'setup_files').mkdir();(source/'setup_files/setup.sh').write_text('#!/bin/bash\ntrue\n')
    dest = tmp_path/'variant'
    c = proposal()['probes'][0]
    record = pilot.probe_variant(source, dest, c, 'after')
    assert (dest/'solution/pilot_reference.sh').read_text() == '#!/bin/bash\nprintf original'
    wrapper = (dest/'solution/solve.sh').read_text()
    assert wrapper.index('bash /solution/pilot_case.sh') < wrapper.index(record['marker'])
    assert 'bash /setup_files/setup.sh' in wrapper
    from validation.stages.task_setup import detect
    assert detect(dest)=='bash /setup_files/setup.sh'


def test_prior_review_cannot_follow_different_task_bytes(tmp_path):
    from validation.contract import task_digest
    task=tmp_path/'task';task.mkdir();(task/'instruction.md').write_text('old')
    contract={'sha256':'contract', 'tasks':[{'task_id':'task','sha256':task_digest(task)}]}
    report={'contract_sha256':'contract','items':[{'task':'/expired/task','verdicts':[{'checks':{'verifiable':{'outcome':'fail'},'typos':{'outcome':'fail'}}}]}]}
    r=pilot.prior_findings(report,contract,[task]);assert set(r['task'][0])=={'verifiable'}
    (task/'instruction.md').write_text('changed')
    with pytest.raises(ValueError,match='bytes'):pilot.prior_findings(report,contract,[task])


def test_task_log_directories_are_not_grader_logs():
    p=proposal();p['probes'][1]['script']="printf invalid > /home/user/helpdesk/logs/report.txt"
    assert pilot.validate_proposal(p,{}) is p


def test_file_prefixed_citations_allowed_but_unknown_files_rejected():
    p=proposal();p['findings']=[{'id':'F1','criterion':'verifiable','disposition':'dismissed',
      'reason':'runtime setup exists','evidence':['instruction.md: exact requirement'], 'probe_ids':[]}]
    assert pilot.validate_proposal(p,{'instruction.md':b'task'}) is p
    p['findings'][0]['evidence']=['other.md: nonexistent source']
    with pytest.raises(ValueError):pilot.validate_proposal(p,{'instruction.md':b'task'})


def test_submit_resumes_after_contract_prepared_before_submission(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from validation import contract as contracts
    root=tmp_path/'run';folder=root/'round-1'
    (folder/'candidates').mkdir(parents=True);(folder/'contracts').mkdir()
    (folder/'contracts/candidates.json').write_text('{}')
    staged=tmp_path/'runtime';staged.mkdir();(staged/'staging.json').write_text('{"bundle":{"sha256":"same"}}')
    bundle=tmp_path/'bundle.json';bundle.write_text('{"sha256":"same"}')
    monkeypatch.setattr(pilot.sys,'prefix',str(staged/'env'))
    monkeypatch.setenv('OT_RUNTIME_BUNDLE',str(bundle))
    monkeypatch.setattr(pilot,'assert_frozen',lambda r:{})
    monkeypatch.setattr(contracts,'read',lambda p:{'runtime_dependencies':contracts.dependencies(),
        'arguments':{'concurrency':8},'stages':[1,2,3,4]})
    calls=[]
    def execute(cmd, **kw):
        calls.append(cmd)
        sub=folder/'validation/candidates/submissions/one';sub.mkdir(parents=True)
        (sub/'submission.json').write_text('{"status":"submitted","job_id":"123"}')
        return SimpleNamespace(returncode=0,stdout='',stderr='')
    monkeypatch.setattr(pilot.subprocess,'run',execute)
    pilot.submit(SimpleNamespace(run=root,round=1,concurrency=8))
    assert len(calls)==1 and '--contract' in calls[0]
    assert '--prepare-contract' not in calls[0]


def test_probe_without_setup_does_not_invent_a_setup_call(tmp_path):
    from validation.stages.task_setup import detect
    source=tmp_path/'source';(source/'solution').mkdir(parents=True)
    (source/'solution/solve.sh').write_text('#!/bin/bash\ntrue\n')
    dest=tmp_path/'probe';pilot.probe_variant(source,dest,proposal()['probes'][0],'after')
    assert detect(dest) is None


def test_independent_review_must_resolve_every_pending_note():
    note='Probes have not run yet.'
    assert not pilot.review_accepts({'outcome':'accept'},[note])
    review={'outcome':'accept','note_resolutions':[{'note':note,'resolved':True,'reason':'Recorded probes now pass.'}]}
    assert pilot.review_accepts(review,[note])
    review['note_resolutions'][0]['resolved']=False
    assert not pilot.review_accepts(review,[note])
    review['outcome']='uncertain';review['note_resolutions'][0]['resolved']=True
    assert not pilot.review_accepts(review,[note])


def test_independent_review_cannot_substitute_a_different_note():
    r={'outcome':'accept','note_resolutions':[{'note':'different','resolved':True,'reason':'claimed'}]}
    assert not pilot.review_accepts(r,['stdout action unverified'])
    assert pilot.review_accepts({'outcome':'accept','note_resolutions':[]},[])


def test_batched_oracle_logs_use_full_result_identity(tmp_path,monkeypatch):
    import io,json,tarfile
    from validation import contract as contracts
    sub=tmp_path/'submissions/one';(sub/'report').mkdir(parents=True)
    (sub/'submission.json').write_text('{}')
    (sub/'request.json').write_text(json.dumps({'args':{'contract':'unused','tasks':'unused'}}))
    full='task--a-long-probe-name--before'
    (sub/'report/stage-3-one.json').write_text(json.dumps({'stage':3,'complete':True,'contract_sha256':'bound','items':[{'task':'/expired/'+full}]}))
    records=[{'task_id':full,'sha256':'hash'}]
    monkeypatch.setattr(contracts,'read',lambda p:{'sha256':'bound','tasks':records})
    monkeypatch.setattr(contracts,'inventory',lambda p:(records,[]))
    prefix='results/stage_3_oracle_validation/uuid/jobs/job/truncated__random/attempts/000/'
    with tarfile.open(sub/'evidence.tar.gz','w:gz') as t:
        for name,data in [('agent/oracle.txt',b'EXACT_MARKER\n'),('result.json',json.dumps({'task_name':full}).encode())]:
            info=tarfile.TarInfo(prefix+name);info.size=len(data);t.addfile(info,io.BytesIO(data))
    reports,logs,_=pilot.read_validation(tmp_path)
    assert logs=={full:['EXACT_MARKER\n']} and 3 in reports
