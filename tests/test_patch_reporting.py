import io
import json
import tarfile

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from data.utils.patch_reporting import write_patch_report
from validation.publishing import patch_provenance, publish
from validation.publishing.outcome_labels import labels
from validation.stages.harbor import assess_trials


def blob(text, timestamp=0):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w') as archive:
        info = tarfile.TarInfo('instruction.md')
        info.size, info.mtime = len(text), timestamp
        archive.addfile(info, io.BytesIO(text.encode()))
    return stream.getvalue()


def inputs(tmp_path):
    original = [{'path': 'set-1', 'task_binary': blob('old')},
                {'path': 'set-2', 'task_binary': blob('same')},
                {'path': 'set-3', 'task_binary': blob('dropped')}]
    patched = [{'path': 'set-1', 'task_binary': blob('new')},
               {'path': 'set-2', 'task_binary': blob('same', 100)}]
    for name, rows in [('original', original), ('patched', patched)]:
        pq.write_table(pa.Table.from_pylist(rows), tmp_path / f'{name}.parquet')
    return original, patched


def report(tmp_path, **overrides):
    options = dict(archive=tmp_path / 'archive.parquet', manifest=tmp_path / 'manifest.json',
                   source={'dataset': 'org/source', 'url': 'https://example.org/source', 'revision': 'pinned'},
                   dropped={'set-3': {'category': 'environment-task-mismatch', 'reason': 'Missing required service'}},
                   change_labels={'set-1': ['instruction-clarified', 'instruction-clarified', 'verifier-aligned']})
    options.update(overrides)
    return write_patch_report(tmp_path / 'original.parquet', tmp_path / 'patched.parquet', **options)


def test_source_comparison_and_combined_archive(tmp_path):
    original, patched = inputs(tmp_path)
    document = report(tmp_path)
    assert [t['action'] for t in document['tasks']] == ['changed', 'unchanged', 'dropped']
    assert document['tasks'][0]['labels'] == ['instruction-clarified', 'verifier-aligned']
    archived = pq.read_table(tmp_path / 'archive.parquet').to_pylist()
    assert archived[0]['task_binary'] == original[2]['task_binary']
    # A validated task archived later still contributes to the patch change count.
    tables = {'set': ([dict(patched[1], archive_stage=None)], [dict(patched[0], archive_stage=4)])}
    record = {'data_sources': {'set': {'tasks': 2, 'kept': 1, 'archived': 1}}, 'rules': {}}
    publish.add_conversion_archives(tables, record, 'runs/r.json', [f'set={tmp_path}/archive.parquet'], {})
    patch_provenance.attach(record, tables, [f'set={tmp_path}/manifest.json'])
    evidence = record['patch_provenance']['set']
    assert evidence['counts'] == {'changed': 1, 'unchanged': 1, 'dropped': 1}
    assert evidence['change_labels'] == {'instruction-clarified': 1, 'verifier-aligned': 1}
    assert evidence['retained_changed'] == 0
    assert record['data_sources']['set']['archived'] == 2
    assert tables['set'][1][0]['patch_labels'] == ['instruction-clarified', 'verifier-aligned']
    assert 'https://example.org/source' in patch_provenance.render(record)


@pytest.mark.parametrize('options', [
    {'dropped': {}},
    {'dropped': {'set-2': {'category': 'x', 'reason': 'y'}, 'set-3': {'category': 'x', 'reason': 'y'}}},
    {'change_labels': {'set-2': ['not-actually-changed']}},
    {'change_labels': {'set-1': 'must-be-a-list'}},
])
def test_rejects_unaccounted_drops_and_misleading_labels(tmp_path, options):
    inputs(tmp_path)
    with pytest.raises(ValueError):
        report(tmp_path, **options)
    assert not (tmp_path / 'manifest.json').exists()


def test_manifest_requires_archive_and_matching_output_payloads(tmp_path):
    _, patched = inputs(tmp_path)
    report(tmp_path)
    tables = {'set': ([dict(row, archive_stage=None) for row in patched], [])}
    with pytest.raises(ValueError, match='every patch drop'):
        patch_provenance.attach({}, tables, [f'set={tmp_path}/manifest.json'])
    record = {'data_sources': {'set': {'tasks': 2, 'kept': 2, 'archived': 0}}, 'rules': {}}
    publish.add_conversion_archives(tables, record, 'r', [tmp_path / 'archive.parquet'], {})
    tables['set'][0][0]['task_binary'] = blob('different version')
    with pytest.raises(ValueError, match='payload'):
        patch_provenance.attach(record, tables, [f'set={tmp_path}/manifest.json'])


@pytest.mark.parametrize('stage', [4, 5])
@pytest.mark.parametrize('finding,label', [
    ('exception: BuildTimeoutError', 'build-execution-error'),
    ('exception: BridgeOutageError', 'infrastructure-error'),
    ('verifier did not run: pytest is not installed', 'verifier-execution-error'),
])
def test_execution_failure_never_becomes_reward_failure(stage, finding, label):
    item = {'status': 'failed', 'rewards': [0 if stage == 4 else 1], 'findings': [finding]}
    assert publish.judge(stage, item)[0] == 'not_run'
    assert labels(stage, item) == [label]


def test_real_reward_failures_and_reference_verifier_execution(tmp_path):
    assert labels(4, {'status': 'failed', 'rewards': [0]}) == ['reference-solution-reward-zero']
    assert labels(5, {'status': 'failed', 'rewards': [1]}) == ['no-op-reward-one']
    result = assess_trials([(tmp_path, {'verifier_result': {
        'rewards': {'reward': 0}, 'stdout': '/usr/bin/python3: No module named pytest\n'}})], 1, 1)
    assert labels(4, result) == ['verifier-execution-error']
    assert publish.judge(4, result)[0] == 'not_run'


def test_patch_columns_and_labels_survive_publishing(tmp_path):
    from test_validation_pipeline import publish_fixture
    _, contract, reports = publish_fixture(tmp_path)
    tables, record, run_file = publish.build(contract, reports, {}, run_id='r')
    row = tables['set-python'][0][0]
    row.update(patch_labels=['instruction-clarified'], patch_changed=True, source_task_id=row['path'])
    publish.write(tables, record, run_file, tmp_path / 'out')
    rows = pq.read_table(tmp_path / 'out/set-python/tasks.parquet').to_pylist()
    assert rows[0]['patch_labels'] == ['instruction-clarified']
    text = (tmp_path / 'out/pull-request.md').read_text()
    assert 'reference-solution-reward-zero' in text


def test_no_drops_still_produces_readable_empty_archive(tmp_path):
    _, patched = inputs(tmp_path)
    pq.write_table(pa.Table.from_pylist(patched), tmp_path / 'original.parquet')
    report(tmp_path, dropped={}, change_labels={})
    assert pq.read_table(tmp_path / 'archive.parquet').num_rows == 0
    tables = {'set': ([dict(row, archive_stage=None) for row in patched], [])}
    record = {'data_sources': {'set': {'tasks': 2, 'kept': 2, 'archived': 0}}, 'rules': {}}
    publish.add_conversion_archives(tables, record, 'r', [f'set={tmp_path}/archive.parquet'], {})
    patch_provenance.attach(record, tables, [f'set={tmp_path}/manifest.json'])
    assert record['patch_provenance']['set']['counts']['unchanged'] == 2


def test_patcher_report_defaults_to_patched_output_directory(tmp_path):
    inputs(tmp_path)
    write_patch_report(tmp_path / 'original.parquet', tmp_path / 'patched.parquet',
        source={'dataset': 'org/source', 'url': 'https://example.org/source', 'revision': 'pinned'},
        dropped={'set-3': {'category': 'environment-task-mismatch', 'reason': 'Missing required service'}})
    assert (tmp_path / 'patched.archive.parquet').is_file()
    document = json.loads((tmp_path / 'patched.manifest.json').read_text())
    assert len(document['tasks']) == 3


def test_changed_labels_and_explanation_are_automatic_but_do_not_archive(tmp_path):
    inputs(tmp_path)
    document = report(tmp_path, change_labels={}, file_labels={'instruction.md': 'instruction-clarified'},
                      change_reasons={'set-1': 'Specify the output path'})
    changed, unchanged, dropped = document['tasks']
    assert changed['action'] == 'changed'
    assert changed['labels'] == ['instruction-clarified']
    assert changed['reason'] == 'Specify the output path'
    assert unchanged['labels'] == []
    assert pq.read_table(tmp_path / 'archive.parquet')['path'].to_pylist() == ['set-3']


def test_inferredbugs_reporting_keeps_original_drop_and_pinned_source(tmp_path):
    from data.inferredbugs import patch as patcher
    original, _ = inputs(tmp_path)
    document = patcher.write_reporting(tmp_path / 'original.parquet', tmp_path / 'patched.parquet',
        {'set-3': {'category': 'warning_not_reproduced', 'reason': 'Warning absent in the buggy project'}})
    assert document['source']['revision'] == patcher.TASKTROVE_REVISION
    assert document['tasks'][0]['labels'] == ['compiled-warning-verification']
    assert document['tasks'][0]['reason'] == patcher.PATCH_EXPLANATION
    archived = pq.read_table(tmp_path / 'patched.archive.parquet').to_pylist()
    assert archived[0]['task_binary'] == original[2]['task_binary']
    with pytest.raises(FileExistsError):
        patcher.check_reporting_outputs(tmp_path / 'patched.parquet')


def test_unresolved_is_not_an_archive_decision(tmp_path):
    inputs(tmp_path)
    document = report(tmp_path, dropped={}, unresolved={'set-3': 'Trial did not finish'})
    assert document['complete'] is False
    assert document['tasks'][2]['action'] == 'not_run'
    assert pq.read_table(tmp_path / 'archive.parquet').num_rows == 0
    with pytest.raises(ValueError, match='unresolved'):
        patch_provenance.attach({}, {'set': ([], [])}, [f'set={tmp_path}/manifest.json'])


def test_bugsinpy_project_emits_shared_manifest_and_conversion_labels(tmp_path):
    from data.tasktrove_bugsinpy import patch as patcher
    name = 'bugsinpy-original-demo-1'
    drop_name = 'bugsinpy-original-demo-2'
    rows = [{'path': name, 'task_binary': blob('converted')}]
    archived = [{'path': drop_name, 'task_binary': blob('excluded'),
                 'archive_category': 'environment-task-mismatch',
                 'archive_reason': 'environment-task-mismatch: Required service unavailable'}]
    task = {'task_id': name, 'project': 'demo', 'bug_id': 1,
            'dockerfile_sha256': 'image', 'requirements_lock_sha256': 'lock'}
    excluded = dict(task, task_id=drop_name, bug_id=2)
    project = {'project': 'demo', 'repo': 'https://example.org/demo', 'bug_ids': [1, 2]}
    patcher.write_project(tmp_path, rows, [task], [], {}, 'commit', project, archived,
                          [{'task_id': drop_name, 'task_manifest': excluded}])
    document = json.loads((tmp_path / 'tasks.manifest.json').read_text())
    assert document['comparison_kind'] == 'conversion'
    assert document['tasks'][0]['action'] == 'changed'
    assert document['tasks'][0]['source_task_id'] == 'demo/1'
    assert document['tasks'][0]['labels'] == ['upstream-to-harbor']
    tables = {'demo': ([dict(rows[0], archive_stage=None)], [])}
    record = {'data_sources': {'demo': {'tasks': 1, 'kept': 1, 'archived': 0}}, 'rules': {}}
    publish.add_conversion_archives(tables, record, 'r', [f'demo={tmp_path}/tasks.archive.parquet'], {})
    patch_provenance.attach(record, tables, [f'demo={tmp_path}/tasks.manifest.json'])
    assert record['patch_provenance']['demo']['counts'] == {'changed': 1, 'unchanged': 0, 'dropped': 1}
    assert 'converted/adapted' in patch_provenance.render(record)
    text = patch_provenance.render(record)
    conversion_row = next(line for line in text.splitlines() if line.startswith('| upstream-to-harbor |'))
    assert conversion_row == f'| upstream-to-harbor | 1 | {patcher.CONVERSION_EXPLANATION} |'
    assert text.count(patcher.CONVERSION_EXPLANATION) == 1
    assert 'dependencies-pinned' not in text
    assert 'verifier-adapted' not in text
    assert document['tasks'][0]['reason'] == patcher.CONVERSION_EXPLANATION
    assert 'label_reasons' not in document['tasks'][0]
    assert 'comparison_note' not in document


@pytest.mark.parametrize('project,bug_id', [
    ('ansible', 12), ('black', 23), ('httpie', 4), ('luigi', 8),
    ('thefuck', 3), ('thefuck', 16), ('tornado', 6),
])
def test_bugsinpy_instruction_review_exclusion_survives_publication(tmp_path, project, bug_id):
    from data.tasktrove_bugsinpy import patch as patcher
    name = f'bugsinpy-original-{project}-{bug_id}'
    exclusion = patcher.task_exclusion(project, bug_id)
    binary = blob('original instruction retained for review')
    archived = [{'path': name, 'task_binary': binary,
                 'archive_category': exclusion['category'],
                 'archive_reason': exclusion['category'] + ': ' + exclusion['reason']}]
    details = {'task_id': name, 'project': project, 'bug_id': bug_id,
               'dockerfile_sha256': 'image'}
    patcher.write_project(tmp_path, [], [], [], {}, 'pinned',
                          {'project': project, 'repo': 'https://example.org/source',
                           'bug_ids': [bug_id]}, archived,
                          [{'task_id': name, **exclusion, 'task_manifest': details}])
    tables = {project: ([], [])}
    record = {'data_sources': {project: {'tasks': 0, 'kept': 0, 'archived': 0}}, 'rules': {}}
    publish.add_conversion_archives(tables, record, 'run',
                                   [f'{project}={tmp_path}/tasks.archive.parquet'], {})
    patch_provenance.attach(record, tables, [f'{project}={tmp_path}/tasks.manifest.json'])
    task = record['patch_provenance'][project]['tasks'][0]
    assert task['action'] == 'dropped'
    assert task['labels'] == ['flagged_by_instruction_loop']
    assert task['reason'] == exclusion['reason']
    assert tables[project][1][0]['task_binary'] == binary
    assert record['conversion_exclusions'][0]['category'] == exclusion['category']
    assert 'label_reasons' not in task
    assert patcher.CONVERSION_EXPLANATION not in patch_provenance.render(record)
    assert patcher.task_exclusion(project, 999) is None


def test_crosscodeeval_pin_only_preserves_original_comparison_and_drops(tmp_path):
    from data.crosscodeeval import patch as patcher
    inputs(tmp_path)
    report(tmp_path, manifest=tmp_path / 'patched.manifest.json')
    output = tmp_path / 'pinned.parquet'
    patcher.pin_parquet(tmp_path / 'patched.parquet', output)
    document = json.loads(output.with_suffix('.manifest.json').read_text())
    assert document['source']['dataset'] == 'org/source'
    assert [entry['action'] for entry in document['tasks']] == ['changed', 'unchanged', 'dropped']
    assert pq.read_table(output.with_suffix('.archive.parquet'))['path'].to_pylist() == ['set-3']
    (tmp_path / 'original.parquet').write_bytes(b'changed source')
    with pytest.raises(ValueError, match='original Parquet changed'):
        patcher.pin_parquet(tmp_path / 'patched.parquet', tmp_path / 'other.parquet')




def test_change_table_counts_unique_tasks_and_shows_explanations():
    record = {'patch_provenance': {'demo': {
        'source': {'dataset': 'org/source', 'url': 'https://example.org/source', 'revision': 'abc', 'task_count': 5},
        'counts': {'changed': 3, 'unchanged': 1, 'dropped': 1},
        'tasks': [
            {'task_id': 'a', 'action': 'changed', 'labels': ['fix', 'fix', 'pin'], 'reason': 'Issue: text only. Fix: run tests.'},
            {'task_id': 'b', 'action': 'changed', 'labels': ['fix'], 'reason': 'Another explanation.'},
            {'task_id': 'c', 'action': 'changed', 'labels': []},
            {'task_id': 'd', 'action': 'unchanged', 'labels': []},
            {'task_id': 'e', 'action': 'dropped', 'labels': ['missing']}],
    }}}
    text = patch_provenance.render(record)
    assert '**3 of the original 5 tasks**' in text
    assert '| fix | 2 | Another explanation.<br>Issue: text only. Fix: run tests. |' in text
    assert '| pin | 1 |' in text and '| other-changes | 1 |  |' in text
    assert 'https://example.org/source' in text
    assert 'missing' not in text


def test_patch_report_freezes_script_for_later_publication(tmp_path):
    inputs(tmp_path)
    script = tmp_path / 'patch.py'
    script.write_text('print("patch version 1")\n')
    document = report(tmp_path, patcher=script)
    snapshot = tmp_path / document['patcher_script']['path']
    script.write_text('print("patch version 2")\n')
    assert snapshot.read_text() == 'print("patch version 1")\n'
    record = {'run': 'saved'}
    files = patch_provenance.stage_patchers(record, [f'set={tmp_path}/manifest.json'], tmp_path / 'upload')
    assert (tmp_path / 'upload' / files[0]).read_bytes() == snapshot.read_bytes()


def test_change_table_uses_label_specific_reasons():
    record = {'patch_provenance': {'set': {
        'source': {'dataset': 'native', 'revision': 'pinned', 'task_count': 1},
        'counts': {'changed': 1},
        'tasks': [{'task_id': 'set-1', 'action': 'changed',
                   'labels': ['upstream-to-harbor', 'anti-cheat-instruction'],
                   'reason': 'Rebuilt from native source.',
                   'label_reasons': {'anti-cheat-instruction': 'Added the anti-cheat sentence.'}}],
    }}}
    text = patch_provenance.render(record)
    assert text.count('Rebuilt from native source.') == 1
    assert text.count('Added the anti-cheat sentence.') == 1
    assert '| anti-cheat-instruction | 1 | Added the anti-cheat sentence. |' in text
