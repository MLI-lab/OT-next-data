import pyarrow as pa
import pyarrow.parquet as pq

from validation.publishing.parquet import write_rows


def test_bounded_groups_preserve_payloads_and_metadata(tmp_path):
    rows = [{'path': str(i), 'task_binary': bytes([i]) * n}
            for i, n in enumerate([3, 4, 20, 2])]
    schema = pa.schema([('path', pa.string()), ('task_binary', pa.binary())],
                       metadata={b'ot.images.v1': b'example'})
    path = tmp_path / 'tasks.parquet'
    write_rows(iter(rows), path, schema, batch_bytes=8)
    parquet = pq.ParquetFile(path)
    assert [parquet.metadata.row_group(i).num_rows for i in range(parquet.num_row_groups)] == [2, 1, 1]
    assert parquet.schema_arrow.field('task_binary').type == pa.large_binary()
    assert parquet.schema_arrow.metadata[b'ot.images.v1'] == b'example'
    assert parquet.read().to_pylist() == rows


def test_empty_archive_has_schema(tmp_path):
    path = tmp_path / 'archive.parquet'
    write_rows([], path, pa.schema([('path', pa.string()), ('task_binary', pa.binary())]))
    assert pq.read_table(path).num_rows == 0
    assert pq.read_table(path).column_names == ['path', 'task_binary']


def test_parallel_pack_preserves_task_contents(tmp_path, monkeypatch):
    import io
    import tarfile
    from validation.publishing.publish import task_rows
    monkeypatch.setenv('OT_PUBLISH_PACK_WORKERS', '2')
    for name in ['a', 'b']:
        task = tmp_path / name
        task.mkdir()
        (task / 'task.toml').write_text('version = "1.0"\n')
        (task / 'instruction.md').write_text(name)
    rows = list(task_rows(tmp_path))
    assert [name for name, _ in rows] == ['a', 'b']
    for name, binary in rows:
        with tarfile.open(fileobj=io.BytesIO(binary)) as archive:
            assert archive.extractfile('instruction.md').read().decode() == name
            assert archive.extractfile('task.toml').read() == b'version = "1.0"\n'


def test_parallel_pack_does_not_queue_the_whole_dataset(tmp_path, monkeypatch):
    from validation.publishing import publish
    monkeypatch.setenv('OT_PUBLISH_PACK_WORKERS', '2')
    tasks = [tmp_path / str(i) for i in range(20)]
    monkeypatch.setattr(publish, 'discover_tasks', lambda source: tasks)
    called = []
    def pack(task):
        called.append(task.name)
        return task.name.encode()
    monkeypatch.setattr(publish, 'pack_task', pack)
    stream = publish.task_rows(tmp_path)
    assert next(stream) == ('0', b'0')
    stream.close()
    assert len(called) == 2
