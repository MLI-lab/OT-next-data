import hashlib

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from validation.publishing import combine as combine_module, patch_provenance, publish


def parquet(path, rows):
    pq.write_table(pa.table({'path': list(rows), 'task_binary': list(rows.values())}), path)
    return path


def sha(data):
    return hashlib.sha256(data).hexdigest()


def setup(tmp_path, monkeypatch):
    final = parquet(tmp_path / 'final.parquet', {'t1': b'one-new', 't2': b'two'})
    contracts = {
        'c0': {'sha256': 'c0', 'stage_layout': 'v2', 'success_criteria': {'static_checks': ['a.sh'], 'static_exclusions': {}},
               'tasks': [{'task_id': 't1', 'sha256': 'n1-old'}, {'task_id': 't2', 'sha256': 'n2'}],
               'arguments': {'tasks': str(parquet(tmp_path / 'c0.parquet', {'t1': b'n1-old', 't2': b'n2'}))},
               'dataset': {'automation_changes': {'t2': ['suffix']}}},
        'c1': {'sha256': 'c1', 'stage_layout': 'v2', 'success_criteria': {'static_checks': ['a.sh'], 'static_exclusions': {}},
               'tasks': [{'task_id': 't1', 'sha256': 'n1-new'}],
               'arguments': {'tasks': str(parquet(tmp_path / 'c1.parquet', {'t1': b'n1-new'}))},
               'dataset': {'automation_changes': {'t1': ['suffix']}}},
    }
    originals = {'c0': {'t1': sha(b'one-old'), 't2': sha(b'two')}, 'c1': {'t1': sha(b'one-new')}}
    item = lambda task, status, contract: {'task': f'/tmp/{task}', 'status': status, 'from': contract}
    reports = {
        'r0': {1: {'stage': 1, 'contract_sha256': 'c0', 'complete': True,
                   'items': [item('t1', 'passed', 'c0'), item('t2', 'passed', 'c0')]}},
        'r1': {1: {'stage': 1, 'contract_sha256': 'c1', 'complete': True, 'items': [item('t1', 'passed', 'c1')]}},
    }
    monkeypatch.setattr(combine_module, 'read', lambda path: contracts[str(path)])
    monkeypatch.setattr(patch_provenance, 'source_binaries', lambda contract: originals[contract['sha256']])
    monkeypatch.setattr(publish, 'stage_reports', lambda path: reports[str(path)])
    # Payloads here are plain bytes; the part before '|' stands for everything but instruction.md.
    monkeypatch.setattr(combine_module, 'without_instruction', lambda binary: binary.split(b'|')[0])
    return final, reports


def test_each_task_takes_results_over_its_own_content(tmp_path, monkeypatch):
    final, _ = setup(tmp_path, monkeypatch)
    view, reports = combine_module.combine([('r0', 'c0'), ('r1', 'c1')], final, tmp_path / 'out')
    by_task = {item['task'].rsplit('/', 1)[1]: item['from'] for item in reports[1]['items']}
    # t1 changed after run 0, so only run 1 counts for it.
    assert by_task == {'t1': 'c1', 't2': 'c0'}
    assert {t['task_id']: t['sha256'] for t in view['tasks']} == {'t1': 'n1-new', 't2': 'n2'}
    assert dict(publish.task_rows(view['arguments']['tasks'])) == {'t1': b'n1-new', 't2': b'n2'}
    assert view['dataset']['automation_changes'] == {'t1': ['suffix'], 't2': ['suffix']}
    assert reports[1]['contract_sha256'] == view['sha256']


def test_missing_results_and_different_rules_are_refused(tmp_path, monkeypatch):
    final, _ = setup(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match='no run over their exact content'):
        combine_module.combine([('r0', 'c0')], final, tmp_path / 'out')
    final, reports = setup(tmp_path, monkeypatch)
    reports['r1'][4] = {'stage': 4, 'contract_sha256': 'c1', 'complete': True, 'items': [{'task': 't1', 'status': 'passed'}]}
    with pytest.raises(ValueError, match='stage results are missing'):
        combine_module.combine([('r0', 'c0'), ('r1', 'c1')], final, tmp_path / 'out')
    final, reports = setup(tmp_path, monkeypatch)
    reports['r0'][4] = {'stage': 4, 'contract_sha256': 'c0', 'complete': True,
                        'items': [{'task': t, 'status': 'passed', 'checks': [{'check': 'a.sh'}]} for t in ('t1', 't2')]}
    reports['r1'][4] = {'stage': 4, 'contract_sha256': 'c1', 'complete': True,
                        'items': [{'task': 't1', 'status': 'passed', 'checks': [{'check': 'a.sh'}, {'check': 'b.sh'}]}]}
    with pytest.raises(ValueError, match='not judged the same way'):
        combine_module.combine([('r0', 'c0'), ('r1', 'c1')], final, tmp_path / 'out')


def test_stages_that_ignore_the_instruction_accept_instruction_only_changes(tmp_path, monkeypatch):
    final, reports = setup(tmp_path, monkeypatch)
    # Run 1 changed only t1's instruction: its build result from run 0 still counts,
    # but stage 4 must come from a run over the edited instruction.
    for name, payload in (('c0', {'t1': b'env|old', 't2': b'n2'}), ('c1', {'t1': b'env|new'})):
        parquet(tmp_path / f'{name}.parquet', payload)
    reports['r1'] = {4: {'stage': 4, 'contract_sha256': 'c1', 'complete': True, 'items': [{'task': 't1', 'status': 'passed', 'from': 'c1'}]}}
    reports['r0'][4] = {'stage': 4, 'contract_sha256': 'c0', 'complete': True,
                        'items': [{'task': t, 'status': 'passed', 'from': 'c0'} for t in ('t1', 't2')]}
    view, merged = combine_module.combine([('r0', 'c0'), ('r1', 'c1')], final, tmp_path / 'out')
    by_stage = {s: {i['task'].rsplit('/', 1)[-1]: i['from'] for i in r['items']} for s, r in merged.items()}
    assert by_stage == {1: {'t1': 'c0', 't2': 'c0'}, 4: {'t1': 'c1', 't2': 'c0'}}
    assert merged[1]['combined_instruction_only_difference'] == ['t1']
    assert merged[4]['combined_instruction_only_difference'] == []


def test_reviewed_nop_zero_is_a_manual_pass_and_keeps_the_original_verdict():
    from validation.publishing.combine import manually_reviewed_nop
    item = {'task': '/tmp/x/format-code-task-000605', 'status': 'failed', 'rewards': [0.0],
            'findings': ['000: verifier execution evidence is missing; runner instrumentation did not report an invocation']}
    passed = manually_reviewed_nop(item)
    assert passed['status'] == 'passed' and passed['findings'] == []
    assert passed['manual_review']['previous_findings'] == item['findings']
    # A wrong reward, another finding or an unreviewed task is never overruled.
    assert manually_reviewed_nop(dict(item, rewards=[1.0]))['status'] == 'failed'
    assert manually_reviewed_nop(dict(item, findings=["000: exception: {'exception_type': 'VerifierTimeoutError'}"]))['status'] == 'failed'
    assert manually_reviewed_nop(dict(item, task='/tmp/x/format-code-task-999999'))['status'] == 'failed'
