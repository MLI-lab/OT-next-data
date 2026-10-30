import json
from pathlib import Path
from types import SimpleNamespace

import pyarrow as pa
import pyarrow.parquet as pq

from validation import run as results
from validation.publishing import revisions


def write(root, tables, history, monkeypatch, reports=None):
    from validation.publishing import publish
    monkeypatch.setattr(publish, 'description', lambda record: 'revision publication')
    record = {}
    files = revisions.write(tables, record, 'runs/run.json', root, reports or {}, {}, history)
    return files, results.read(root / 'd/results.parquet')


def task(name, digest):
    return {'path': name, 'content_sha256': digest, 'task_binary': digest.encode()}


def test_initial_unchanged_and_changed_payloads(tmp_path, monkeypatch):
    tables = {'d': ([task('a', 'a1'), task('b', 'b1')], [])}
    first = tmp_path / 'first'
    files, rows = write(first, tables, revisions.History(), monkeypatch)
    assert 'd/tasks/r1.parquet' in files
    assert len(pq.read_table(first / 'd/tasks/r1.parquet')) == 2
    second = tmp_path / 'second'
    files, rows = write(second, tables, revisions.History(root=first), monkeypatch)
    assert not any('/tasks/' in f for f in files)
    tables = {'d': ([task('a', 'a2')], [])}
    files, rows = write(tmp_path / 'third', tables, revisions.History(root=first), monkeypatch)
    assert 'd/tasks/r2.parquet' in files
    payload = pq.read_table(tmp_path / 'third/d/tasks/r2.parquet').to_pylist()
    assert [r['path'] for r in payload] == ['a']
    assert len(rows) == 3
    assert {r['task_id'] for r in rows if r['latest']} == {'a', 'b'}
    assert next(r for r in rows if r['content_sha256'] == 'a1')['latest'] is False


def test_migration_preserves_unselected_legacy_tasks_and_unknown_results(tmp_path, monkeypatch):
    legacy = tmp_path / 'legacy/d'
    legacy.mkdir(parents=True)
    pq.write_table(pa.Table.from_pylist([task('a', 'a1'), task('b', 'b1')]), legacy / 'tasks.parquet')
    files, rows = write(tmp_path / 'new', {'d': ([task('a', 'a2')], [])},
                        revisions.History(root=legacy.parent), monkeypatch)
    assert {'d/tasks/r1.parquet', 'd/tasks/r2.parquet'} <= set(files)
    old = next(r for r in rows if r['task_id'] == 'b')
    assert old['latest'] and old['nproc'] == 'not_recorded'
    new = next(r for r in rows if r['content_sha256'] == 'a2')
    assert new['nproc'] == 'not_run'


def test_latest_revision_roundtrips_through_contract_and_materialization(tmp_path, monkeypatch):
    import shutil
    from validation.contract import task_digest, inventory
    from validation.publishing.publish import pack_task
    from validation.task_inputs.materialize import materialize
    source = tmp_path / 'source/a'
    source.mkdir(parents=True)
    (source / 'instruction.md').write_text('first')
    first = {'path': 'a', 'content_sha256': task_digest(source), 'task_binary': pack_task(source)}
    old = tmp_path / 'old'
    write(old, {'d': ([first], [])}, revisions.History(), monkeypatch)
    (source / 'instruction.md').write_text('second')
    second = {'path': 'a', 'content_sha256': task_digest(source), 'task_binary': pack_task(source)}
    current = tmp_path / 'current'
    write(current, {'d': ([second], [])}, revisions.History(root=old), monkeypatch)
    shutil.copy(old / 'd/tasks/r1.parquet', current / 'd/tasks/r1.parquet')
    inventory_rows, _ = inventory(current / 'd')
    assert inventory_rows == [{'task_id': 'a', 'sha256': second['content_sha256']}]
    materialize(current / 'd', tmp_path / 'unpacked')
    assert (tmp_path / 'unpacked/a/instruction.md').read_text() == 'second'


def test_missing_revision_payload_is_rejected(tmp_path):
    import pytest
    from validation.task_inputs.revisions import selection
    results.write(tmp_path / 'results.parquet', [{
        'dataset': 'd', 'task_id': 'a', 'content_sha256': 'a', 'latest': True,
        'revision': 1, 'task_file': 'd/tasks/r1.parquet'}])
    with pytest.raises(ValueError, match='Missing'):
        selection(tmp_path)


def test_release_card_uses_revision_index_and_saved_classification(tmp_path, monkeypatch):
    from validation.publishing import publish
    monkeypatch.setattr(publish, 'description', lambda record: 'release')
    metadata = tmp_path / 'metadata.json'
    metadata.write_text(json.dumps({'required_rules': ['nproc'], 'upstream_tasks': 1}))
    annotation = tmp_path / 'annotation.json'
    annotation.write_text(json.dumps({'dataset_name': 'd', 'original_datasources': [], 'domains': [],
                                     'capabilities': [], 'expected_benchmark_transfer': [],
                                     'no_transfer_reason': 'Not established.'}))
    reports = {4: {'items': [{'task': 'a', 'checks': [{'check': 'nproc', 'status': 'passed'}]}]}}
    files = revisions.write({'d': ([task('a', 'a1')], [])}, {}, 'runs/r.json', tmp_path / 'out',
                            reports, {}, revisions.History(), release_specs={
                                'd': {'view': 'v1', 'run': str(metadata), 'annotation': str(annotation)}})
    assert 'd/v1/selection.parquet' in files
    assert 'd/v1/annotation.json' in files
    assert '| Kept tasks | 1 |' in (tmp_path / 'out/d/README.md').read_text()
    selected = pq.read_table(tmp_path / 'out/d/v1/selection.parquet').to_pylist()
    assert selected[0]['task_file'] == 'd/tasks/r1.parquet'


def test_internet_setting_reads_task_config():
    import io
    import tarfile
    for setting, expected in [('true', 'true'), ('false', 'false'), (None, 'unspecified')]:
        content = ('[environment]\n' + (f'allow_internet = {setting}\n' if setting else '')).encode()
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode='w:gz') as archive:
            info = tarfile.TarInfo('./task.toml')
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
        assert revisions.internet_setting(buffer.getvalue()) == expected
    assert revisions.internet_setting(b'not an archive') == 'unknown'
