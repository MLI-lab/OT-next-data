"""Reuse reviewed runtime changes from existing before/after task packages.

No task-name-only compatibility assumptions: the pre-runtime payload must agree
with the current task before historical setup changes can be replayed.
"""
import copy
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import sqlite3
import tarfile
import tempfile
import zlib


def unpack(binary):
    files, members = {}, {}
    with tarfile.open(fileobj=io.BytesIO(binary)) as archive:
        for member in archive:
            path = PurePosixPath(member.name)
            if path.is_absolute() or '..' in path.parts or member.name in members:
                raise ValueError('Unsafe or duplicate historical member: ' + member.name)
            members[member.name] = copy.copy(member)
            if member.isfile():
                files[member.name] = archive.extractfile(member).read()
    return files, members


def comparable(name, data):
    # Only normalize known, non-executable annotations. Shell comments in other
    # places (including continued Dockerfile commands) remain significant.
    if data is None:
        return None
    if name == 'instruction.md':
        return re.sub(rb'\n+You have [0-9]+ seconds to complete this task\. Do not cheat by using online solutions or hints specific to this task\.\s*$', b'', data).rstrip()
    if name == 'environment/Dockerfile':
        return data.replace(b'# Verifier preparation from tests/test.sh, installed at build time.\n', b'')
    return data


class PackageIndex:
    """Disk-backed task lookup, without extracting thousands of task trees."""
    def __init__(self, connection, table, source):
        import pyarrow.parquet as pq
        self.connection, self.table = connection, table
        source = Path(source)
        if source.is_dir():
            if (source / 'tasks.parquet').is_file():
                paths = [source / 'tasks.parquet']
            elif (source / 'contract.stage0-tasks').is_dir():
                paths = sorted((source / 'contract.stage0-tasks').glob('*.parquet'))
            else:
                paths = sorted(source.glob('*.parquet'))
        else:
            paths = [source]
        if not paths:
            raise ValueError('No task packages found: ' + str(source))
        connection.execute(f'CREATE TABLE {table} (task TEXT PRIMARY KEY, payload BLOB, digest TEXT)')
        self.sources = []
        for path in paths:
            with path.open('rb') as stream:
                digest = hashlib.file_digest(stream, 'sha256').hexdigest()
            self.sources.append(dict(path=str(path.resolve()), sha256=digest))
            reader = pq.ParquetFile(path)
            columns = ['path', 'task_binary']
            if 'content_sha256' in reader.schema_arrow.names:
                columns.append('content_sha256')
            for batch in reader.iter_batches(batch_size=16, columns=columns):
                for row in batch.to_pylist():
                    try:
                        connection.execute(f'INSERT INTO {table} VALUES (?, ?, ?)',
                                           (row['path'], zlib.compress(row['task_binary'], 1), row.get('content_sha256')))
                    except sqlite3.IntegrityError as error:
                        raise ValueError('Duplicate historical task: ' + row['path']) from error
        connection.commit()

    def get(self, task):
        from validation.contract import files_digest
        row = self.connection.execute(f'SELECT payload, digest FROM {self.table} WHERE task=?', (task,)).fetchone()
        if row is None:
            return None
        files, members = unpack(zlib.decompress(row[0]))
        digest = files_digest(files.items())
        if row[1] and row[1] != digest:
            raise ValueError('Historical task checksum differs: ' + task)
        return files, members, digest


class RuntimeHistory:
    def __init__(self, *, prepared, baseline, refs):
        self.temporary = tempfile.TemporaryDirectory(prefix='calibforge-history-')
        self.connection = sqlite3.connect(str(Path(self.temporary.name) / 'tasks.sqlite'))
        self.baseline = PackageIndex(self.connection, 'baseline', baseline)
        self.prepared = PackageIndex(self.connection, 'prepared', prepared)
        path = Path(refs)
        mapping = json.loads(path.read_text())
        if not isinstance(mapping, dict):
            raise ValueError('Expected runtime ID to task-list mapping in runtime-refs.json')
        self.by_task = {}
        for key, record in mapping.items():
            if not re.fullmatch('[a-f0-9]{64}', key) or not isinstance(record.get('tasks'), list):
                raise ValueError('Invalid historical runtime mapping')
            for task in record['tasks']:
                self.by_task.setdefault(task, {})[key] = record
        self.provenance = dict(baseline=self.baseline.sources, prepared=self.prepared.sources,
                               refs=dict(path=str(path.resolve()), sha256=hashlib.sha256(path.read_bytes()).hexdigest()))

    def close(self):
        self.connection.close()
        self.temporary.cleanup()

    def apply(self, task, binary, workflow, record):
        from data.calibforge.runtime_workflow import rewrite
        from harbor_patches.verifier_bundle import FORMAT, OFFLINE_FORMAT, TOOLCHAIN_FORMAT
        expected = self.by_task.get(task)
        before = self.baseline.get(task)
        after = self.prepared.get(task)
        if not expected or before is None or after is None:
            record['reason'] = 'no complete historical before/after runtime evidence for this task'
            return binary, False
        old, old_members, old_digest = before
        prepared, prepared_members, prepared_digest = after
        current, current_members = unpack(binary)
        # Refuse changed task inputs, tests or environments. Already-applied
        # historical changes are accepted, but unrelated repairs are never undone.
        conflicts = []
        for name in old.keys() | current.keys():
            if comparable(name, current.get(name)) not in (comparable(name, old.get(name)), comparable(name, prepared.get(name))):
                conflicts.append(name)
        for name in old_members.keys() | current_members.keys():
            a, b = old_members.get(name), current_members.get(name)
            if (a and not a.isfile()) or (b and not b.isfile()):
                if not (a and b and (a.type, a.linkname) == (b.type, b.linkname)):
                    conflicts.append(name)
        if conflicts:
            record.update(reason='current task differs from historical runtime inputs', conflicting_files=sorted(set(conflicts)))
            return binary, False
        changed = {name for name in old.keys() | prepared.keys() if old.get(name) != prepared.get(name)}
        updates, removed = {}, set()
        for name in changed:
            if comparable(name, old.get(name)) == comparable(name, prepared.get(name)):
                continue
            if not name.startswith(('tests/', 'setup_files/')):
                record.update(reason='historical changes extend beyond runtime setup and verifier files', conflicting_files=[name])
                return binary, False
            if name in prepared:
                updates[name] = prepared[name]
            else:
                removed.add(name)
        for name, member in prepared_members.items():
            if not member.isfile() and name not in old_members and not member.isdir():
                record.update(reason='historical runtime adds an unsupported nonregular member', conflicting_files=[name])
                return binary, False
        references = {}
        for name, data in prepared.items():
            if name.endswith('.json') and name.startswith(('tests/ot-', 'setup_files/ot-')):
                ref = json.loads(data)
                if isinstance(ref, dict) and ref.get('format') in (FORMAT, OFFLINE_FORMAT, TOOLCHAIN_FORMAT):
                    references[name] = ref
        if {ref['id'] for ref in references.values()} != set(expected):
            record['reason'] = 'historical task references disagree with runtime-refs.json'
            return binary, False
        missing, ready = [], {}
        for ref in references.values():
            key = ref['id']
            binding = expected[key]
            if any(ref.get(field) != binding.get(field) for field in ('format', 'archive_sha256')):
                raise ValueError('Historical runtime reference identity differs: ' + key)
            manifest = workflow.cache / (key + '.squashfs.json')
            if not manifest.is_file() or not (workflow.cache / (key + '.squashfs')).is_file():
                missing.append(key)
                continue
            cached = json.loads(manifest.read_text())
            if cached.get('archive_sha256') != ref['archive_sha256']:
                raise ValueError('Historical runtime cache archive checksum differs: ' + key)
            cached = workflow.cached_runtime(cached['spec'], ref['format'])
            if cached['id'] != key or (ref.get('image_sha256') and ref['image_sha256'] != cached['image_sha256']):
                raise ValueError('Historical runtime cache identity differs: ' + key)
            ready[key] = cached
        if missing:
            record.update(reason='historical runtime cache is incomplete', missing_runtime_ids=sorted(set(missing)))
            return binary, False
        for name, ref in references.items():
            ref = {k: v for k, v in ref.items() if k != 'spec'}
            ref.update(storage='squashfs', image_sha256=ready[ref['id']]['image_sha256'])
            updates[name] = (json.dumps(ref, indent=2) + '\n').encode()
        record.update(status='prepared', cache_prepared=True, evidence='historical before/after task packages',
                      baseline_content_sha256=old_digest, prepared_content_sha256=prepared_digest,
                      runtime_ids=sorted(ready), replayed_files=sorted(updates), removed_files=sorted(removed))
        return rewrite(binary, updates, removed=removed, templates=prepared_members), True
