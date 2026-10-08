"""Shared patcher output: original dropped payloads and a complete source comparison.

Call write_patch_report after writing the patched tasks Parquet. Labels may be
passed by task ID or supplied in its optional list<string> patch_labels column.
The input must be the declared original source, not an intermediate patch output.
"""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import tarfile

from data.utils.environment_counts import fingerprints, summarize


def _files(blob):
    """Compare contents, modes and links, ignoring packaging timestamps/order."""
    result = {}
    with tarfile.open(fileobj=io.BytesIO(blob), mode='r:*') as archive:
        for member in archive:
            name = PurePosixPath(member.name)
            if name.is_absolute() or '..' in name.parts:
                raise ValueError('unsafe task archive path')
            if member.isdir():
                continue
            key = name.as_posix()
            if key in result:
                raise ValueError('duplicate task archive path')
            content = hashlib.sha256(archive.extractfile(member).read()).hexdigest() if member.isfile() else member.linkname
            result[key] = (member.type.decode('ascii'), member.mode, content)
    return result


def _rows(path):
    import pyarrow.parquet as pq
    for batch in pq.ParquetFile(path).iter_batches(batch_size=32):
        yield from batch.to_pylist()


def write_patch_report(original, patched, *, source, dropped, archive=None, manifest=None,
                       change_labels=None, patches=(), file_labels=None, change_reasons=None, unresolved=None, patcher=None):
    """Write archive + manifest, requiring an explicit reason for every missing ID.

    source: {dataset, url, revision}; dropped: {ID: {category, reason}}.
    change_labels: optional {ID: [label, ...]}; labels describe final net changes.
    Task IDs must be preserved. Splits, merges and renames need explicit mapping
    and are deliberately not inferred by this helper.
    """
    import pyarrow as pa
    import pyarrow.parquet as pq
    patched = Path(patched)
    archive = Path(archive) if archive is not None else patched.with_suffix('.archive.parquet')
    manifest = Path(manifest) if manifest is not None else patched.with_suffix('.manifest.json')
    paths = [Path(original), Path(patched), archive, manifest]
    patcher_bytes = Path(patcher).read_bytes() if patcher else None
    patcher_copy = manifest.with_suffix('.patch.py') if patcher else None
    if patcher_copy is not None and patcher_copy.exists():
        raise ValueError('use a new patcher snapshot destination')
    if len({p.resolve() for p in paths}) != len(paths) or archive.exists() or manifest.exists():
        raise ValueError('use distinct paths and new archive/manifest destinations')
    if not all(isinstance(source.get(k), str) and source[k] for k in ('dataset', 'url', 'revision')):
        raise ValueError('source requires dataset, URL and pinned revision')
    change_labels = change_labels or {}
    unresolved = unresolved or {}
    outputs = {}
    for row in _rows(patched):
        name = row['path']
        if name in outputs:
            raise ValueError('duplicate patched task ID: ' + name)
        labels = change_labels.get(name, row.get('patch_labels') or [])
        if not isinstance(labels, (list, tuple)) or any(not isinstance(label, str) or not label for label in labels):
            raise ValueError('patch_labels must be a list of nonempty strings')
        outputs[name] = (_files(row['task_binary']), hashlib.sha256(row['task_binary']).hexdigest(), sorted(set(labels)))
    if set(change_labels) - set(outputs):
        raise ValueError('change labels refer to a task absent from patched output')
    tasks, seen, removed = [], set(), set()
    source_environments = []
    for row in _rows(original):
        name, blob = row['path'], row['task_binary']
        if name in seen:
            raise ValueError('duplicate original task ID: ' + name)
        seen.add(name)
        before = _files(blob)
        source_environments.append(fingerprints(before))
        task = {'task_id': name, 'source_task_id': name,
                'source_binary_sha256': hashlib.sha256(blob).hexdigest()}
        if name in outputs:
            files, digest, labels = outputs[name]
            changes = sorted(k for k in before.keys() | files.keys() if before.get(k) != files.get(k))
            if labels and not changes:
                raise ValueError('change labels supplied for an unchanged task: ' + name)
            labels = sorted(set(labels) | {label for prefix, label in (file_labels or {}).items()
                                          if any(f == prefix or (prefix.endswith('/') and f.startswith(prefix)) for f in changes)})
            task.update(action='changed' if changes else 'unchanged', changed_files=changes,
                        labels=labels, output_binary_sha256=digest)
            if changes and (change_reasons or {}).get(name):
                task['reason'] = change_reasons[name]
        elif name in unresolved:
            task.update(action='not_run', labels=[], reason=unresolved[name])
        else:
            exclusion = dropped.get(name) or {}
            if not exclusion.get('category') or not exclusion.get('reason'):
                raise ValueError('missing explicit drop category/reason: ' + name)
            removed.add(name)
            task.update(action='dropped', labels=[exclusion['category']], reason=exclusion['reason'],
                        output_binary_sha256=task['source_binary_sha256'])
        tasks.append(task)
    if set(outputs) - seen:
        raise ValueError('patched output contains new/renamed task IDs; explicit mapping is required')
    if set(dropped) != removed:
        raise ValueError('drop decisions must exactly match tasks absent from patched output')
    if set(unresolved) - seen or set(unresolved) & (set(outputs) | set(dropped)):
        raise ValueError('unresolved tasks must be original tasks outside the kept/dropped sets')
    document = {'version': 1, 'source': {**source, 'task_count': len(seen)},
                'complete': not unresolved, 'patches': list(patches), 'tasks': tasks,
                'comparison_scope': 'patcher' if patcher else 'published'}
    document['source_environments'] = summarize(source_environments)
    with Path(original).open('rb') as stream:
        document['original_parquet'] = {'path': str(Path(original).resolve()),
                                        'sha256': hashlib.file_digest(stream, 'sha256').hexdigest()}
    archive.parent.mkdir(parents=True, exist_ok=True)
    manifest.parent.mkdir(parents=True, exist_ok=True)
    schema = pa.schema([('path', pa.string()), ('task_binary', pa.binary()),
                        ('archive_category', pa.string()), ('archive_reason', pa.string())])
    with pq.ParquetWriter(archive, schema) as writer:
        batch = []
        for row in _rows(original):
            if row['path'] not in removed:
                continue
            exclusion = dropped[row['path']]
            category = exclusion['category']
            batch.append({'path': row['path'], 'task_binary': row['task_binary'],
                          'archive_category': category, 'archive_reason': category + ': ' + exclusion['reason']})
            if len(batch) == 32:
                writer.write_table(pa.Table.from_pylist(batch, schema=schema))
                batch = []
        if batch:
            writer.write_table(pa.Table.from_pylist(batch, schema=schema))
    if patcher_copy is not None:
        patcher_copy.write_bytes(patcher_bytes)
        document['patcher_script'] = {'path': patcher_copy.name, 'sha256': hashlib.sha256(patcher_bytes).hexdigest()}
    manifest.write_text(json.dumps(document, indent=2) + '\n')
    return document


def write_conversion_report(output, rows, archived, *, source, records, errors=(), patcher=None,
                            change_labels=None, change_reasons=None):
    """Native source conversion: generated Harbor packages are not original packages."""
    import pyarrow as pa
    import pyarrow.parquet as pq
    output = Path(output)
    archive, manifest = output / 'tasks.archive.parquet', output / 'tasks.manifest.json'
    if archive.exists() or manifest.exists():
        raise ValueError('use new conversion reporting destinations')
    tasks = []
    for row in [*rows, *archived]:
        name = row['path']
        details = records[name]
        excluded = bool(row.get('archive_category'))
        labels = [row['archive_category']] if excluded else ['converted-to-harbor']
        if not excluded:
            if details.get('test_command_corrections') or details.get('verifier_test_corrections'):
                labels.append('verifier-adapted')
            if details.get('requirements_lock_sha256'):
                labels.append('dependencies-pinned')
            if name in (change_labels or {}):
                labels = change_labels[name]
                if not isinstance(labels, (list, tuple)) or any(
                        not isinstance(label, str) or not label for label in labels):
                    raise ValueError('change_labels must contain lists of nonempty strings')
                labels = sorted(set(labels))
        task = {'task_id': name, 'source_task_id': f"{details['project']}/{details['bug_id']}",
                'action': 'dropped' if excluded else 'changed', 'change_kind': 'conversion',
                'labels': labels, 'changed_files': sorted(_files(row['task_binary'])),
                'output_binary_sha256': hashlib.sha256(row['task_binary']).hexdigest()}
        if excluded:
            task['reason'] = row['archive_reason'].removeprefix(row['archive_category'] + ': ')
        elif (change_reasons or {}).get(name):
            task['reason'] = change_reasons[name]
        tasks.append(task)
    schema = pa.schema([('path', pa.string()), ('task_binary', pa.binary()),
                        ('archive_category', pa.string()), ('archive_reason', pa.string())])
    pq.write_table(pa.Table.from_pylist(list(archived), schema=schema), archive)
    document = {'version': 1, 'complete': not errors, 'comparison_kind': 'conversion',
                'source': source, 'tasks': tasks, 'conversion_errors': list(errors)}
    if patcher:
        snapshot = manifest.with_suffix('.patch.py')
        content = Path(patcher).read_bytes()
        if snapshot.exists():
            raise ValueError('use a new patcher snapshot destination')
        snapshot.write_bytes(content)
        document['patcher_script'] = {'path': snapshot.name, 'sha256': hashlib.sha256(content).hexdigest()}
    manifest.write_text(json.dumps(document, indent=2) + '\n')
    return document
