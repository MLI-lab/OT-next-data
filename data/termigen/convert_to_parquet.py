#!/usr/bin/env python3
"""Lossless pinned TermiGen packaging; verification runs only in a batch job."""
import argparse
import gzip
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import random
import stat
import subprocess
import sys
import tarfile
import tempfile

PIN = '03bddac74ada2fa344df0e93b4a3ace2034294d1'
SEED = 42
COUNT = 3566


def source_check(source):
    env = dict(os.environ, GIT_OPTIONAL_LOCKS='0')
    def git(*args):
        return subprocess.check_output(['git', '-C', str(source.parent), *args], env=env, text=True).strip()
    if git('rev-parse', 'HEAD') != PIN or git('status', '--porcelain', '--', source.name):
        raise ValueError('source revision or working tree differs from pinned input')


def entries(task):
    for p in [task, *sorted(task.rglob('*'), key=lambda p: p.relative_to(task).as_posix())]:
        st = p.lstat()
        kind = 'file' if stat.S_ISREG(st.st_mode) else 'directory' if stat.S_ISDIR(st.st_mode) else None
        if kind is None:
            raise ValueError(f'unsupported entry: {p}')
        name = '.' if p == task else p.relative_to(task).as_posix()
        name.encode('utf-8')
        yield name, kind, stat.S_IMODE(st.st_mode), p


def content_hash(stream):
    h = hashlib.sha256()
    while chunk := stream.read(1024 * 1024):
        h.update(chunk)
    return h.digest()


def tree_records(task):
    records = []
    for name, kind, mode, p in entries(task):
        if kind == 'file':
            with p.open('rb') as stream:
                digest = content_hash(stream)
        else:
            digest = b''
        records.append((name, kind, mode, digest))
    return records


def records_hash(records):
    h = hashlib.sha256()
    for name, kind, mode, digest in sorted(records):
        header = json.dumps([name, kind, mode], ensure_ascii=False, separators=(',', ':')).encode()
        h.update(len(header).to_bytes(8, 'big'))
        h.update(header)
        h.update(len(digest).to_bytes(8, 'big'))
        h.update(digest)
    return h.hexdigest()


def archive_records(blob):
    records = []
    seen = set()
    with tarfile.open(fileobj=io.BytesIO(blob), mode='r|*') as archive:
        for member in archive:
            rel = PurePosixPath(member.name)
            if rel.is_absolute() or '..' in rel.parts or member.name in seen:
                raise ValueError(f'unsafe or duplicate archive entry: {member.name}')
            seen.add(member.name)
            if member.isfile():
                kind = 'file'
                with archive.extractfile(member) as stream:
                    digest = content_hash(stream)
            elif member.isdir():
                kind, digest = 'directory', b''
            else:
                raise ValueError(f'unsupported archive entry: {member.name}')
            records.append((member.name, kind, member.mode, digest))
    return records


def pack(task):
    buffer = io.BytesIO()
    with gzip.GzipFile(fileobj=buffer, mode='wb', filename='', mtime=0, compresslevel=9) as gz:
        with tarfile.open(fileobj=gz, mode='w|', format=tarfile.PAX_FORMAT) as archive:
            for name, kind, mode, p in entries(task):
                info = tarfile.TarInfo(name)
                info.mode = mode
                info.mtime = info.uid = info.gid = 0
                info.uname = info.gname = ''
                if kind == 'directory':
                    info.type = tarfile.DIRTYPE
                    archive.addfile(info)
                else:
                    info.size = p.stat().st_size
                    with p.open('rb') as stream:
                        archive.addfile(info, stream)
    return buffer.getvalue()


def write_parquet(tasks, destination):
    import pyarrow as pa
    import pyarrow.parquet as pq
    schema = pa.schema([pa.field('path', pa.string(), nullable=False),
                        pa.field('task_binary', pa.binary(), nullable=False)])
    with pq.ParquetWriter(destination, schema, version='2.6', compression='NONE',
                          use_dictionary=False, write_statistics=False,
                          data_page_version='1.0') as writer:
        for start in range(0, len(tasks), 32):
            rows = [{'path': task.name, 'task_binary': pack(task)} for task in tasks[start:start + 32]]
            writer.write_table(pa.Table.from_pylist(rows, schema=schema), row_group_size=32)


def verify(tasks, output, scratch):
    import pyarrow.parquet as pq
    from validation.contract import inventory
    from validation.data.materialize import materialize
    originals = {task.name: records_hash(tree_records(task)) for task in tasks}
    seen = set()
    mismatches = []
    files = directories = 0
    for batch in pq.ParquetFile(output).iter_batches(batch_size=1):
        for row in batch.to_pylist():
            name = row['path']
            if name in seen or name not in originals:
                raise ValueError(f'duplicate or unexpected task: {name}')
            seen.add(name)
            records = archive_records(row['task_binary'])
            files += sum(kind == 'file' for _, kind, _, _ in records)
            directories += sum(kind == 'directory' and entry != '.' for entry, kind, _, _ in records)
            if records_hash(records) != originals[name]:
                mismatches.append(name)
    print(f'Archive inventory: files={files}, directories={directories} (excluding task roots)', flush=True)
    missing = sorted(set(originals) - seen)
    print(f'In-memory round trip: tasks={len(seen)}, mismatches={len(mismatches)}, missing={len(missing)}', flush=True)
    if mismatches or missing:
        raise ValueError(f'round-trip mismatch: changed={mismatches}, missing={missing}')
    sample = sorted(random.Random(SEED).sample(sorted(originals), 50))
    print(f'Extraction sample: seed={SEED}, task_ids={json.dumps(sample)}', flush=True)
    print(f'TMPDIR resolves to {scratch}', flush=True)
    with tempfile.TemporaryDirectory(prefix='termigen-sample-', dir=scratch) as temp:
        destination = Path(temp) / 'tasks'
        paths = materialize(output, destination, selected_ids=sample)
        mismatches = [p.name for p in paths if records_hash(tree_records(p)) != originals[p.name]]
        print(f'Pipeline extraction round trip: tasks={len(paths)}, mismatches={len(mismatches)}', flush=True)
        if mismatches:
            raise ValueError(f'extraction mismatch: {mismatches}')
    print('Extraction sample and materialization manifest deleted', flush=True)
    records, _ = inventory(output)
    if {r['task_id'] for r in records} != set(originals):
        raise ValueError('pipeline inventory task set differs')
    print(f'Pipeline read/listing only: tasks={len(records)}', flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--source', type=Path, required=True)
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--verify', action='store_true')
    a = ap.parse_args()
    if not os.environ.get('SLURM_JOB_ID'):
        ap.error('conversion must run in the approved batch job')
    source, output = a.source.resolve(), a.output.resolve()
    scratch = Path(os.environ['TMPDIR']).resolve(strict=True)
    if not scratch.is_dir() or scratch.is_relative_to('/hnvme'):
        ap.error('TMPDIR must be node-local, not /hnvme')
    if output.exists():
        ap.error('output exists; refusing to overwrite')
    if not output.parent.is_dir():
        ap.error('output workspace does not exist')
    if not a.verify:
        ap.error('--verify is required before publishing the final Parquet')
    temporary = output.with_name(output.stem + '.tmp.parquet')
    if temporary.exists():
        temporary.unlink()
        print(f'Removed stale temporary output: {temporary}', flush=True)
    source_check(source)
    tasks = sorted(p for p in source.iterdir() if p.is_dir() and (p / 'task.toml').is_file())
    if len(tasks) != COUNT or {p.name for p in source.iterdir()} - {p.name for p in tasks} != {'data'}:
        ap.error('task count or root entries differ from audited source')
    import pyarrow
    print(f'Python={sys.version.split()[0]}, PyArrow={pyarrow.__version__}, source={source}, pin={PIN}', flush=True)
    write_parquet(tasks, temporary)
    with temporary.open('rb') as stream:
        first = content_hash(stream)
    # Second conversion stays in memory: no second shared-filesystem Parquet.
    with io.BytesIO() as repeat:
        write_parquet(tasks, repeat)
        second = hashlib.sha256(repeat.getbuffer()).digest()
    if first != second:
        raise ValueError('deterministic repeat differs; stopping')
    print(f'Parquet: tasks={len(tasks)}, bytes={temporary.stat().st_size}, sha256={first.hex()}, byte-identical repeat=PASS', flush=True)
    verify(tasks, temporary, scratch)
    source_check(source)
    # Report only; never delete any source file, including Git metadata.
    inode_ids = {(p.lstat().st_dev, p.lstat().st_ino) for p in [source.parent, *source.parent.rglob('*')]}
    print(f'Raw checkout unique inodes (including .git): {len(inode_ids)}; retained', flush=True)
    with temporary.open('rb') as stream:
        os.fsync(stream.fileno())
    if output.exists():
        raise ValueError('final output appeared during conversion; stopping')
    os.rename(temporary, output)
    print(f'All requested checks PASS; atomically published {output}', flush=True)


if __name__ == '__main__':
    main()
