import io
import json
import sys
import tarfile
import zlib

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from data.scaleswe.prepare import database, main, unpack


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
    connection = database(output / 'receipts.sqlite')
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
