"""Apply revision-matched resource recommendations to packaged task.toml files."""
import base64
import copy
import json
import os
from pathlib import Path
import tempfile
import tomllib

from validation.automatic_repairs import archive_files, replace_in_archive
from validation.contract import files_digest
from validation.stages.passk_stage import set_environment

KEYS = ('cpus', 'memory_mb', 'storage_mb')


def decoded(value):
    return json.loads(value) if isinstance(value, str) else (value or {})


def recommendation(row):
    cell = decoded(row.get('resources_recommended'))
    if cell.get('status'):
        if cell['status'] != 'resolved':
            return None, None
        return cell, decoded(cell.get('details')).get('recommended_source')
    legacy = decoded(row.get('resources'))
    return (legacy.get('requirements') if legacy.get('status') == 'resolved' else None), legacy.get('source')


def apply_task(task, result):
    """Return copies; never mutate input evidence or unresolvable task packages."""
    need, source = recommendation(result)
    if need is None:
        return task, result
    need = {k: need.get(k) for k in KEYS}
    if any(type(v) is not int or v < 1 for v in need.values()):
        raise ValueError(f'{task["path"]}: incomplete or invalid resource recommendation')
    files = archive_files(task['task_binary'])
    before = files_digest(files.items())
    if result.get('content_sha256') != before or (task.get('content_sha256') or before) != before:
        raise ValueError(f'{task["path"]}: resource recommendation does not match task content hash')
    if result.get('task_id') != task['path']:
        raise ValueError('Resource recommendation task ID mismatch')
    text = files['task.toml'].decode()
    original = tomllib.loads(text).get('environment', {})
    old = {k: original.get(k, original.get({'memory_mb': 'memory', 'storage_mb': 'storage'}.get(k, k))) for k in KEYS}
    if old != need:
        files['task.toml'] = set_environment(text, need).encode()
        new_env = tomllib.loads(files['task.toml'].decode())['environment']
        if any(new_env.get(k) != v for k, v in need.items()):
            raise ValueError('Resource rewrite verification failed')
    after = files_digest(files.items())
    task, result = copy.deepcopy(task), copy.deepcopy(result)
    if after != before:
        task['task_binary'] = replace_in_archive(task['task_binary'], files)
        if archive_files(task['task_binary']) != files:
            raise ValueError('Task archive verification failed')
    task['content_sha256'] = after
    result['content_sha256'] = after
    previous = decoded(result.get('resource_application'))
    application = previous if previous.get('to_content_sha256') == after and before == after else {
        'from_content_sha256': before, 'to_content_sha256': after,
        'original_resources': old, 'applied_resources': need, 'source': source,
        'validation_at_recommended_resources': False,
        'note': 'Recorded validation and rollout evidence predates this resource-only configuration change.'}
    result['resource_application'] = json.dumps(application)
    cell = result.get('resources_recommended')
    if isinstance(cell, dict) and cell.get('status'):
        detail = decoded(cell.get('details'))
        detail.setdefault('original_resources', old)
        detail.update(applied_resources=need, applied_source=source,
                      applied_reason='Written and verified in the task package.', resource_application=application)
        cell['details'] = json.dumps(detail)
    else:
        record = decoded(result.get('resources'))
        record.update(applied_resources=need, applied_source=source, resource_application=application)
        result['resources'] = json.dumps(record)
    for row in (task, result):
        for key in KEYS:
            if key in row:
                row[key] = need[key]
    return task, result


def apply_parquet(tasks, results, output_tasks=None):
    """Stream and verify a task export, then atomically replace each output file.

    Input tasks stay untouched when output_tasks is supplied. Results are updated only
    after the task file is successfully saved. Existing check column schemas are retained.
    """
    import pyarrow as pa
    import pyarrow.parquet as pq
    tasks, results = Path(tasks), Path(results)
    destination = Path(output_tasks) if output_tasks else tasks
    if destination.resolve() == results.resolve():
        raise ValueError('Task and result files must be distinct')
    table = pq.read_table(results)
    rows = table.to_pylist()
    indexed = {}
    for i, row in enumerate(rows):
        key = row['task_id'], row.get('content_sha256')
        if key in indexed:
            raise ValueError(f'Duplicate result revision: {key}')
        indexed[key] = i
    destination.parent.mkdir(parents=True, exist_ok=True)
    source = pq.ParquetFile(tasks)
    schema = source.schema_arrow
    images = json.loads(schema.metadata[b'ot.images.v1']) if schema.metadata and b'ot.images.v1' in schema.metadata else None
    if 'content_sha256' not in schema.names:
        schema = schema.append(pa.field('content_sha256', pa.string()))
    expected, count, changed = {}, 0, 0
    required = {i for i, row in enumerate(rows) if recommendation(row)[0] is not None}
    applied_indices, task_revisions = set(), set()
    temporary = []
    def staging(path):
        fd, name = tempfile.mkstemp(prefix=path.name + '.', suffix='.tmp', dir=path.parent)
        os.close(fd)
        temporary.append(Path(name))
        return Path(name)
    task_tmp, results_tmp = staging(destination), staging(results)
    try:
        with pq.ParquetWriter(task_tmp, schema) as writer:
            for batch in source.iter_batches(batch_size=1):
                task = batch.to_pylist()[0]
                digest = task.get('content_sha256') or files_digest(archive_files(task['task_binary']).items())
                task['content_sha256'] = digest
                revision = (task['path'], digest)
                if revision in task_revisions:
                    raise ValueError(f'Duplicate task revision: {revision}')
                task_revisions.add(revision)
                index = indexed.get((task['path'], digest))
                if index is None:
                    candidates = [r for r in rows if r['task_id'] == task['path'] and recommendation(r)[0] is not None]
                    if candidates:
                        raise ValueError(f'{task["path"]}: no recommendation matches this task revision')
                elif recommendation(rows[index])[0] is not None:
                    before = task['task_binary']
                    task, rows[index] = apply_task(task, rows[index])
                    changed += task['task_binary'] != before
                    count += 1
                    applied_indices.add(index)
                    expected[(task['path'], task['content_sha256'])] = recommendation(rows[index])[0]
                if images is not None and task['path'] in images['tasks']:
                    images['tasks'][task['path']]['content_sha256'] = task['content_sha256']
                writer.write_table(pa.Table.from_pylist([task], schema=schema))
            if images is not None:
                metadata = {**schema.metadata, b'ot.images.v1': json.dumps(images).encode()}
                # Arrow restores schema metadata from its serialized schema, ahead
                # of the plain Parquet footer keys. Keep both copies consistent.
                encoded = base64.b64encode(schema.with_metadata(metadata).serialize().to_pybytes())
                writer.add_key_value_metadata({**metadata, b'ARROW:schema': encoded})
        if required - applied_indices:
            missing = sorted(rows[i]['task_id'] for i in required - applied_indices)
            raise ValueError(f'Resolved recommendations missing matching task payloads: {missing[:10]}')
        # Verify the serialized output, rather than claiming success from the in-memory edit.
        verified = 0
        for batch in pq.ParquetFile(task_tmp).iter_batches(batch_size=1):
            task = batch.to_pylist()[0]
            key = task['path'], task['content_sha256']
            if key in expected:
                files = archive_files(task['task_binary'])
                env = tomllib.loads(files['task.toml'].decode())['environment']
                if files_digest(files.items()) != key[1] or any(env.get(k) != expected[key][k] for k in KEYS):
                    raise ValueError(f'{key[0]}: serialized task resource verification failed')
                verified += 1
        if verified != count:
            raise ValueError('Applied task count differs after saving')
        result_schema = table.schema
        if 'resource_application' not in result_schema.names:
            result_schema = result_schema.append(pa.field('resource_application', pa.string()))
        pq.write_table(pa.Table.from_pylist(rows, schema=result_schema), results_tmp)
        os.replace(task_tmp, destination)
        os.replace(results_tmp, results)
    finally:
        for path in temporary:
            path.unlink(missing_ok=True)
    return {'applied': count, 'changed': changed, 'tasks_path': str(destination)}


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tasks', required=True, type=Path)
    parser.add_argument('--results', required=True, type=Path)
    parser.add_argument('--output-tasks', type=Path)
    args = parser.parse_args()
    print(json.dumps(apply_parquet(args.tasks, args.results, args.output_tasks), indent=2))


if __name__ == '__main__':
    main()
