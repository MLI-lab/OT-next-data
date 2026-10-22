import pytest

from data.tasktrove_bugsinpy.patch import (
    environment_exclusion_group, prepare_environment, remove_publication_suffix, sha256, task_tar,
)


SUFFIX = b'\nYou have 900 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.\n'


def test_suffix_removal_preserves_reviewed_instruction_and_runtime_bytes():
    files = {'instruction.md': b'Reviewed problem description with clarified requirements.\n',
             'tests/test.sh': b'#!/bin/bash\nrun_real_tests\n',
             'setup_files/project.tar.gz': b'opaque source archive bytes'}
    expected = task_tar(files)
    published = task_tar({**files, 'instruction.md': files['instruction.md'] + SUFFIX})
    actual, contents = remove_publication_suffix(published, sha256(expected))
    assert actual == expected
    assert contents == files


def test_restore_rejects_additional_runtime_change():
    files = {'instruction.md': b'Reviewed description.\n', 'tests/test.sh': b'original tests'}
    expected = sha256(task_tar(files))
    changed = task_tar({**files, 'instruction.md': files['instruction.md'] + SUFFIX,
                        'tests/test.sh': b'changed tests'})
    with pytest.raises(ValueError, match='accepted instruction-review payload'):
        remove_publication_suffix(changed, expected)


def test_no_suffix_is_not_silently_accepted():
    blob = task_tar({'instruction.md': b'Original\n'})
    with pytest.raises(ValueError, match='exactly one'):
        remove_publication_suffix(blob, sha256(blob))


def test_unrewritten_task_preserves_text_when_old_archive_metadata_is_not_reproducible():
    files = {'instruction.md': b'Unrewritten issue report.\n', 'tests/test.sh': b'original tests'}
    published = task_tar({**files, 'instruction.md': files['instruction.md'] + SUFFIX})
    _, restored = remove_publication_suffix(published, None)
    assert restored == files


@pytest.mark.parametrize('task,group', [
    ('httpie-4', 'reference-network-dependency'),
    ('youtube-dl-40', 'environment-compatibility'),
    ('scrapy-7', 'no-op-passes'),
    ('ansible-12', 'task-quality-review'),
])
def test_quality_exclusions_are_not_reported_as_runtime_failures(task, group):
    assert environment_exclusion_group('bugsinpy-original-' + task) == group


def test_unknown_exclusion_requires_review():
    with pytest.raises(ValueError, match='Unclassified'):
        environment_exclusion_group('bugsinpy-original-unknown-1')


def test_sharded_preparation_rejects_payload_not_bound_to_publication(tmp_path):
    import json
    import pyarrow as pa
    import pyarrow.parquet as pq

    task = 'bugsinpy-original-test-1'
    blob = task_tar({'instruction.md': b'Reviewed issue\n' + SUFFIX})
    source = pa.table({'path': [task], 'task_binary': [blob], 'patch_labels': [['anti-cheat-instruction']]})
    pq.write_table(source, tmp_path/'tasks.parquet')
    pq.write_table(source.slice(0, 0), tmp_path/'archive.parquet')
    manifest = {'task_id': task, 'output_binary_sha256': sha256(blob)}
    (tmp_path/'run.json').write_text(json.dumps({'run': 'fixture', 'patch_provenance': {
        'bugsinpy-original': {'source': {'task_count': 1}, 'tasks': [manifest]}}}))
    (tmp_path/'assembly.json').write_text(json.dumps({'instruction_changes': 1, 'tasks': [{
        'task_id': task, 'changed_files': ['instruction.md'], 'output_sha256': '0'*64}]}))
    shard = tmp_path/'shards/000'
    shard.mkdir(parents=True)
    pq.write_table(pa.table({'path': [task], 'task_binary': [b'tampered']}), shard/'tasks.parquet')
    with pytest.raises(ValueError, match='Published payload differs'):
        prepare_environment(tmp_path/'tasks.parquet', tmp_path/'archive.parquet',
                            tmp_path/'run.json', tmp_path/'assembly.json', 'a'*40,
                            tmp_path/'prepared', tmp_path/'shards')


def test_static_release_preserves_exact_published_payload_and_review(tmp_path):
    import json
    import pyarrow as pa
    import pyarrow.parquet as pq
    from validation.contract import files_digest

    task = 'bugsinpy-original-test-1'
    files = {'instruction.md': b'Reviewed issue\n' + SUFFIX,
             'environment/Dockerfile': b'FROM python:3.8\n',
             'tests/test.sh': b'#!/bin/bash\ntrue\n',
             'solution/solve.sh': b'#!/bin/bash\ntrue\n',
             'setup_files/requirements.lock': b'pytest==6.2.5\n',
             'tests/requirements.lock': b'pytest==6.2.5\n'}
    blob = task_tar(files)
    source = pa.table({'path': [task], 'task_binary': [blob],
                      'patch_labels': [['anti-cheat-instruction']],
                      'content_sha256': [files_digest(files.items())],
                      'stages_passed': ['1'], 'run': ['old']})
    pq.write_table(source, tmp_path/'tasks.parquet')
    pq.write_table(source.slice(0, 0), tmp_path/'archive.parquet')
    (tmp_path/'run.json').write_text(json.dumps({'run': 'fixture', 'patch_provenance': {
        'bugsinpy-original': {'source': {'task_count': 1}, 'tasks': [
            {'task_id': task, 'output_binary_sha256': sha256(blob)}]}}}))
    (tmp_path/'assembly.json').write_text(json.dumps({'instruction_changes': 1, 'tasks': [{
        'task_id': task, 'changed_files': ['instruction.md'], 'output_sha256': '0'*64}]}))
    summary = prepare_environment(tmp_path/'tasks.parquet', tmp_path/'archive.parquet',
                                  tmp_path/'run.json', tmp_path/'assembly.json', 'a'*40,
                                  tmp_path/'prepared', include_static_repairs=True)
    result = pq.read_table(tmp_path/'prepared/tasks.parquet').to_pylist()[0]
    assert result['task_binary'] == blob
    assert result['content_sha256'] == files_digest(files.items())
    assert result['patch_labels'] == ['anti-cheat-instruction']
    assert result['stages_passed'] == '1,3,4,5'
    assert summary['automatic_suffix_preserved'] == 1
    assert summary['automatic_suffix_removed'] == 0
    assert summary['reviewed_instruction_changes_preserved'] == 1
