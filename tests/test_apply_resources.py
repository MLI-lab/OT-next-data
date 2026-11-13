import io
import json
import tarfile
import tomllib

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from validation.automatic_repairs import archive_files
from validation.contract import files_digest
from validation.metrics.apply_resources import apply_task, apply_parquet

NEED = {'cpus': 1, 'memory_mb': 2048, 'storage_mb': 3072}


def fixture():
    files = {'task.toml': b'[environment]\ncpus = 2\nmemory_mb = 8192\nstorage_mb = 20480\nallow_internet = false\n[verifier]\ntimeout_sec = 123\n',
             'tests/test.sh': b'#!/bin/sh\nexit 0\n', 'instruction.md': b'Do the task.'}
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w:gz') as archive:
        for name, data in files.items():
            info = tarfile.TarInfo(name); info.size = len(data); info.mode = 0o755 if name.endswith('.sh') else 0o644
            archive.addfile(info, io.BytesIO(data))
    digest = files_digest(files.items())
    return {'path': 'a', 'task_binary': buf.getvalue(), 'content_sha256': digest}, {
        'task_id': 'a', 'content_sha256': digest,
        'nop': {'status': 'pass', 'details': 'original evidence'},
        'resources_recommended': {**NEED, 'status': 'resolved', 'details': json.dumps({
            'original_resources': {'cpus': 2, 'memory_mb': 8192, 'storage_mb': 20480},
            'recommended_source': 'teacher successful traces', 'models': {'preserved': True}})}}


def test_applies_only_resources_preserves_evidence_permissions_and_is_idempotent():
    task, result = fixture()
    updated, evidence = apply_task(task, result)
    files = archive_files(updated['task_binary'])
    config = tomllib.loads(files['task.toml'].decode())
    assert {k: config['environment'][k] for k in NEED} == NEED
    assert config['environment']['allow_internet'] is False
    assert config['verifier']['timeout_sec'] == 123
    assert files['tests/test.sh'] == archive_files(task['task_binary'])['tests/test.sh']
    with tarfile.open(fileobj=io.BytesIO(updated['task_binary'])) as archive:
        assert archive.getmember('tests/test.sh').mode == 0o755
    assert evidence['nop'] == result['nop']
    detail = json.loads(evidence['resources_recommended']['details'])
    assert detail['models'] == {'preserved': True}
    assert detail['applied_resources'] == NEED and detail['original_resources']['cpus'] == 2
    assert updated['content_sha256'] != task['content_sha256']
    assert json.loads(evidence['resource_application'])['validation_at_recommended_resources'] is False
    assert apply_task(updated, evidence) == (updated, evidence)


def test_unresolved_is_unchanged_and_mismatched_evidence_is_rejected():
    task, row = fixture()
    row['content_sha256'] = 'wrong'
    with pytest.raises(ValueError, match='hash'):
        apply_task(task, row)
    row['resources_recommended']['status'] = 'no_reliable_resource_estimate'
    assert apply_task(task, row) == (task, row)


def test_pending_recommendation_accepts_plain_text_details():
    task, row = fixture()
    row['resources_recommended'].update(status='not_requested', details='Task repaired; fresh evaluation required.')
    assert apply_task(task, row) == (task, row)


def test_embedded_image_index_follows_updated_task_hash(tmp_path):
    task, result = fixture()
    manifest = {'tasks': {'a': {'content_sha256': task['content_sha256'], 'environments': [{'bundle': 'same-image'}]}},
                'bundles': {'same-image': {'files': {}}}, 'missing': []}
    tasks, results = tmp_path/'tasks.parquet', tmp_path/'results.parquet'
    pq.write_table(pa.Table.from_pylist([task]).replace_schema_metadata({b'ot.images.v1': json.dumps(manifest).encode()}), tasks)
    pq.write_table(pa.Table.from_pylist([result]), results)
    apply_parquet(tasks, results)
    saved = pq.read_table(tasks)
    updated = json.loads(saved.schema.metadata[b'ot.images.v1'])
    assert updated['tasks']['a']['content_sha256'] == saved['content_sha256'][0].as_py()
    assert updated['tasks']['a']['content_sha256'] != task['content_sha256']
    assert updated['bundles'] == manifest['bundles']
    assert updated['tasks']['a']['environments'] == manifest['tasks']['a']['environments']


def test_parquet_rewrite_keeps_schema_metadata_and_updates_saved_payload(tmp_path):
    task, result = fixture()
    tasks, results = tmp_path/'tasks.parquet', tmp_path/'results.parquet'
    pq.write_table(pa.Table.from_pylist([task]).replace_schema_metadata({b'original': b'metadata'}), tasks)
    pq.write_table(pa.Table.from_pylist([result]), results)
    stats = apply_parquet(tasks, results)
    assert stats['applied'] == stats['changed'] == 1
    saved = pq.read_table(tasks)
    assert saved.schema.metadata == {b'original': b'metadata'}
    evidence = pq.read_table(results).to_pylist()[0]
    assert saved.to_pylist()[0]['content_sha256'] == evidence['content_sha256']
    assert json.loads(evidence['resources_recommended']['details'])['applied_resources'] == NEED
    assert apply_parquet(tasks, results)['changed'] == 0


def test_mismatch_does_not_replace_either_file(tmp_path):
    task, result = fixture(); result['content_sha256'] = 'wrong'
    tasks, results = tmp_path/'tasks.parquet', tmp_path/'results.parquet'
    pq.write_table(pa.Table.from_pylist([task]), tasks)
    pq.write_table(pa.Table.from_pylist([result]), results)
    before = (tasks.read_bytes(), results.read_bytes())
    with pytest.raises(ValueError, match='revision'):
        apply_parquet(tasks, results)
    assert (tasks.read_bytes(), results.read_bytes()) == before


def test_publisher_automatically_applies_stage6_recommendations(tmp_path, monkeypatch):
    from validation.publishing import revisions, publish
    monkeypatch.setattr(publish, 'description', lambda record: 'resource application test')
    task, _ = fixture()
    digest = task['content_sha256']
    report = {'items': [{'task': 'a', 'status': 'completed', 'content_sha256': digest,
        'passk': {'outcome': 'solved', 'successes': 1, 'valid_attempts': 4},
        'calibration': {'upstream': {'cpus': 2, 'memory_mb': 8192, 'storage_mb': 20480},
                        'requirements': {**NEED, 'status': 'resolved', 'basis': 'successful traces'}}}]}
    out = tmp_path/'published'
    revisions.write({'d': ([task], [])}, {}, 'runs/test.json', out, {6: report}, {},
                    revisions.History(root=tmp_path/'history'))
    payload = pq.read_table(out/'d/tasks/r1.parquet').to_pylist()[0]
    env = tomllib.loads(archive_files(payload['task_binary'])['task.toml'].decode())['environment']
    assert {k: env[k] for k in NEED} == NEED
    row = pq.read_table(out/'d/results.parquet').to_pylist()[0]
    assert row['content_sha256'] == payload['content_sha256']
    assert json.loads(row['resource_application'])['applied_resources'] == NEED


def test_missing_recommended_task_does_not_silently_export_subset(tmp_path):
    task, result = fixture()
    other = dict(result, task_id='missing')
    tasks, results = tmp_path/'tasks.parquet', tmp_path/'results.parquet'
    output = tmp_path/'calibrated-tasks.parquet'
    pq.write_table(pa.Table.from_pylist([task]), tasks)
    pq.write_table(pa.Table.from_pylist([result, other]), results)
    before = results.read_bytes()
    with pytest.raises(ValueError, match='missing matching task payloads'):
        apply_parquet(tasks, results, output)
    assert not output.exists() and results.read_bytes() == before


def test_duplicate_task_revision_is_rejected(tmp_path):
    task, result = fixture()
    tasks, results = tmp_path/'tasks.parquet', tmp_path/'results.parquet'
    pq.write_table(pa.Table.from_pylist([task, task]), tasks)
    pq.write_table(pa.Table.from_pylist([result]), results)
    before = (tasks.read_bytes(), results.read_bytes())
    with pytest.raises(ValueError, match='Duplicate task revision'):
        apply_parquet(tasks, results)
    assert (tasks.read_bytes(), results.read_bytes()) == before
