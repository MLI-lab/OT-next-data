import io
import json
import sqlite3
import sys
import tarfile
import zlib

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from data.scaleswe.prepare import load_receipts, main, unpack


@pytest.mark.parametrize('name,kind', [('../outside', 'file'), ('/absolute', 'file'), ('link', 'symlink')])
def test_preparation_rejects_unsafe_payloads(name, kind):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w') as archive:
        member = tarfile.TarInfo(name)
        if kind == 'symlink':
            member.type = tarfile.SYMTYPE
            member.linkname = '/outside'
        archive.addfile(member)
    with pytest.raises(ValueError, match='Unsafe'):
        unpack(stream.getvalue())


def test_release_selection_export_accounts_for_every_unprepared_task(tmp_path, monkeypatch):
    source = tmp_path / 'tasks.parquet'
    pq.write_table(pa.Table.from_pylist([
        dict(path='one', content_sha256='a' * 64),
        dict(path='two', content_sha256='b' * 64),
    ]), source)
    output = tmp_path / 'preparation'
    monkeypatch.setattr(sys, 'argv', ['prepare', 'export', '--source', str(source),
        '--source-revision', 'pinned', '--out', str(output), '--cache', str(tmp_path / 'runtimes')])
    with pytest.raises(SystemExit, match='Incomplete runtime coverage'):
        main()
    assert [row['task'] for row in json.loads((output / 'incomplete.json').read_text())] == ['one', 'two']
    assert not (output / 'selections.json').exists()


def test_export_reports_invalid_cached_runtime_and_continues_accounting(tmp_path, monkeypatch):
    source = tmp_path / 'tasks.parquet'
    pq.write_table(pa.Table.from_pylist([
        dict(path='one', content_sha256='a' * 64),
        dict(path='two', content_sha256='b' * 64),
    ]), source)
    output = tmp_path / 'preparation'
    output.mkdir()
    connection = sqlite3.connect(output / 'receipts.sqlite')
    connection.execute('CREATE TABLE receipts (task TEXT, content_sha TEXT, status TEXT, receipt BLOB, '
        'PRIMARY KEY(task,content_sha))')
    receipt = dict(status='prepared', runtime={})
    connection.execute('INSERT INTO receipts VALUES (?,?,?,?)',
        ('one', 'a' * 64, 'prepared', zlib.compress(json.dumps(receipt).encode())))
    connection.commit()
    connection.close()
    def invalid(*args):
        raise ValueError('Archive checksum mismatch')
    monkeypatch.setattr('data.scaleswe.prepare.verify', invalid)
    monkeypatch.setattr(sys, 'argv', ['prepare', 'export', '--source', str(source),
        '--source-revision', 'pinned', '--out', str(output), '--cache', str(tmp_path / 'runtimes')])
    with pytest.raises(SystemExit, match='Incomplete runtime coverage'):
        main()
    failures = json.loads((output / 'incomplete.json').read_text())
    assert [row['task'] for row in failures] == ['one', 'two']
    assert 'checksum mismatch' in failures[0]['reason']


def _legacy_database(output, entries):
    output.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(output / 'receipts.sqlite')
    connection.execute('CREATE TABLE receipts (task TEXT, content_sha TEXT, status TEXT, receipt BLOB, '
        'PRIMARY KEY(task,content_sha))')
    for task, sha, receipt in entries:
        connection.execute('INSERT INTO receipts VALUES (?,?,?,?)',
            (task, sha, receipt['status'], zlib.compress(json.dumps(receipt).encode())))
    connection.commit()
    connection.close()


def test_prepare_resumes_from_legacy_database_and_writes_shard_jsonl(tmp_path, monkeypatch):
    source = tmp_path / 'tasks.parquet'
    pq.write_table(pa.Table.from_pylist([
        dict(path='one', content_sha256='a' * 64),
        dict(path='two', content_sha256='b' * 64),
        dict(path='three', content_sha256='c' * 64),
    ]), source)
    output = tmp_path / 'preparation'
    _legacy_database(output, [('one', 'a' * 64, dict(status='prepared', runtime={})),
                              ('two', 'b' * 64, dict(status='incomplete', error='old failure'))])
    legacy_bytes = (output / 'receipts.sqlite').read_bytes()
    prepared = []
    def fake_prepare(row, images, args, log):
        prepared.append(row['path'])
        return dict(status='prepared', runtime={}, runtime_id='r-' + row['path'])
    monkeypatch.setattr('data.scaleswe.prepare.prepare_one', fake_prepare)
    monkeypatch.setattr('data.scaleswe.prepare.verify', lambda *args: None)
    monkeypatch.setattr(sys, 'argv', ['prepare', 'prepare', '--source', str(source),
        '--source-revision', 'pinned', '--out', str(output), '--cache', str(tmp_path / 'runtimes')])
    main()
    assert prepared == ['two', 'three']
    assert (output / 'receipts.sqlite').read_bytes() == legacy_bytes
    assert sorted(p.name for p in output.iterdir() if p.name.startswith('receipts')) == [
        'receipts-0-of-1.jsonl', 'receipts.sqlite']
    receipts = load_receipts(output)
    assert {task for (task, _), r in receipts.items() if r['status'] == 'prepared'} == {'one', 'two', 'three'}
    # A second pass prepares nothing.
    prepared.clear()
    main()
    assert prepared == []


def test_load_receipts_skips_truncated_line_and_prefers_newest(tmp_path):
    output = tmp_path / 'preparation'
    _legacy_database(output, [('one', 'a' * 64, dict(status='incomplete', error='old'))])
    with (output / 'receipts-0-of-2.jsonl').open('w') as stream:
        stream.write(json.dumps(dict(task='one', content_sha='a' * 64, written=2.0,
            receipt=dict(status='prepared'))) + '\n')
        stream.write('{"task": "two", "content_sha"')
    with (output / 'receipts-1-of-2.jsonl').open('w') as stream:
        stream.write(json.dumps(dict(task='one', content_sha='a' * 64, written=1.0,
            receipt=dict(status='incomplete', error='stale'))) + '\n')
    receipts = load_receipts(output)
    assert receipts == {('one', 'a' * 64): dict(status='prepared')}
