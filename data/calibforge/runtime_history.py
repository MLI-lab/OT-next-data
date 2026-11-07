"""Reuse reviewed runtime changes from existing before/after task packages.

No task-name-only compatibility assumptions: the pre-runtime payload must agree
with the current task before historical setup changes can be replayed.
"""
import copy
import ast
import hashlib
import inspect
import io
import json
from pathlib import Path, PurePosixPath
import re
import sqlite3
import subprocess
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


def preserve_reviewed_verifier(task, old, current, prepared, review):
    """Replay runtime edits while retaining a reviewed missing-input guard repair.

    Only formatting/docstring indentation differences are eligible. The explicit
    review binds all three inputs and the result. Existing patcher guard helpers,
    not a handwritten merge or a relaxed test, produce the combined verifier.
    """
    for key, body in [('baseline', old), ('current', current), ('prepared', prepared)]:
        if body is None or hashlib.sha256(body).hexdigest() != review.get(key + '_sha256'):
            raise ValueError('Reviewed verifier binding differs: ' + task)
    if not review.get('reason'):
        raise ValueError('Reviewed verifier requires a reason')
    def normalized(body):
        tree = ast.parse(body)
        for node in ast.walk(tree):
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                node.value.value = inspect.cleandoc(node.value.value)
        return ast.dump(tree, include_attributes=False)
    if normalized(old) != normalized(current):
        raise ValueError('Reviewed verifier changes executable behavior: ' + task)
    from data.calibforge.repairs import assert_required_solution_file, assert_required_verifier_module
    result = assert_required_verifier_module(task, assert_required_solution_file(task, prepared.decode())).encode()
    prefix = current.split(b'else:\n', 1)[0]
    if (not prefix.startswith(b'# CalibForge: assert missing ') or b'else:\n' not in current or
            result.split(b'else:\n', 1)[0] != prefix):
        raise ValueError('Reviewed verifier guard was not preserved: ' + task)
    ast.parse(result)
    if hashlib.sha256(result).hexdigest() != review.get('result_sha256'):
        raise ValueError('Reviewed verifier merge result differs: ' + task)
    return result


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
    def __init__(self, *, prepared, baseline, refs, reviewed_inputs=None):
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
        self.reviewed_inputs = {}
        self.reviewed_verifiers = {}
        self.reviewed_nonruntime = {}
        if reviewed_inputs:
            path = Path(reviewed_inputs)
            review = json.loads(path.read_text())
            if review.get('format') not in ('calibforge-reviewed-instructions-v1', 'calibforge-reviewed-runtime-inputs-v1'):
                raise ValueError('Expected source-bound reviewed instruction differences')
            self.reviewed_inputs = review['tasks']
            self.reviewed_verifiers = review.get('verifiers', {})
            self.reviewed_nonruntime = review.get('nonruntime', {})
            self.provenance['reviewed_inputs'] = dict(path=str(path.resolve()), sha256=hashlib.sha256(path.read_bytes()).hexdigest())

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
        nonruntime_review = self.reviewed_nonruntime.get(task, {})
        for name, decision in nonruntime_review.items():
            if (name not in ('instruction.md', 'task.toml') or not decision.get('reason') or
                    decision.get('disposition') not in ('preserve_current', 'use_prepared_instruction') or
                    (decision['disposition'] == 'use_prepared_instruction' and name != 'instruction.md')):
                raise ValueError('Unsupported historical nonruntime review: ' + task)
            for kind, source in (('baseline', old), ('current', current), ('prepared', prepared)):
                if (name not in source or hashlib.sha256(source[name]).hexdigest() !=
                        decision.get(kind + '_sha256')):
                    raise ValueError('Reviewed nonruntime binding differs: ' + task + '/' + name)
        # Refuse changed task inputs, tests or environments. Already-applied
        # historical changes are accepted, but unrelated repairs are never undone.
        conflicts = []
        verifier_review = self.reviewed_verifiers.get(task)
        merged_verifier = None
        if verifier_review:
            name = 'tests/test_outputs.py'
            merged_verifier = preserve_reviewed_verifier(task, old.get(name), current.get(name),
                                                         prepared.get(name), verifier_review)
        reviewed = self.reviewed_inputs.get(task)
        if reviewed:
            # Only instruction-only changes can use this path. All executable,
            # environment and verifier inputs retain the ordinary comparisons.
            name = 'instruction.md'
            if (not reviewed.get('reason') or old.get(name) is None or current.get(name) is None or
                    hashlib.sha256(old[name]).hexdigest() != reviewed.get('baseline_sha256') or
                    hashlib.sha256(current[name]).hexdigest() != reviewed.get('current_sha256') or
                    comparable(name, prepared.get(name)) not in
                    (comparable(name, old[name]), comparable(name, current[name]))):
                raise ValueError('Reviewed instruction binding differs: ' + task)
        for name in old.keys() | current.keys():
            if name in nonruntime_review:
                continue
            if merged_verifier is not None and name == 'tests/test_outputs.py':
                continue
            if reviewed and name == 'instruction.md':
                continue
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
            if name in nonruntime_review:
                if nonruntime_review[name]['disposition'] == 'use_prepared_instruction':
                    updates[name] = prepared[name]
                continue
            if reviewed and name == 'instruction.md':
                continue  # Both supported historical forms were hash-bound above.
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
        if merged_verifier is not None:
            updates['tests/test_outputs.py'] = merged_verifier
            removed.discard('tests/test_outputs.py')
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
        # Historical packages may predate patcher fixes (e.g. the empty `if` left by a removed uv
        # installer before 41c1eeb). A replayed shell script must at least parse; otherwise plan
        # the task afresh with the current patcher instead of copying a broken verifier.
        broken = [name for name, data in updates.items() if name.endswith('.sh') and
                  subprocess.run(['bash', '-n'], input=data, capture_output=True).returncode]
        if broken:
            record.update(reason='historical shell script does not parse (bash -n)', invalid_files=sorted(broken))
            return binary, False
        record.update(status='prepared', cache_prepared=True, evidence='historical before/after task packages',
                      baseline_content_sha256=old_digest, prepared_content_sha256=prepared_digest,
                      runtime_ids=sorted(ready), replayed_files=sorted(updates), removed_files=sorted(removed))
        if reviewed:
            record['preserved_reviewed_instruction'] = reviewed
        if verifier_review:
            record['preserved_reviewed_verifier'] = verifier_review
        if nonruntime_review:
            record['reviewed_nonruntime'] = nonruntime_review
        return rewrite(binary, updates, removed=removed, templates=prepared_members), True
