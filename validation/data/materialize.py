"""Materialize TaskTrove Parquet task_binary archives into node-local storage."""
import argparse
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import tarfile


def parquet_files(source):
    source = Path(source)
    return [source] if source.is_file() and source.suffix == '.parquet' else sorted(source.glob('*.parquet'))


def materialize(source, destination, limit=None, per_file=False, selected_ids=None):
    import pyarrow.parquet as pq
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    records = []
    seen = set()
    image_sources = {}
    path_cache = {}
    for path in parquet_files(source):
        metadata = pq.read_schema(path).metadata or {}
        image_manifest = json.loads(metadata[b'ot.images.v1']) if b'ot.images.v1' in metadata else None
        from validation.checks.path_cache import KEY
        cached_paths = json.loads(metadata.get(KEY, b'{}'))
        selected = 0
        for batch in pq.ParquetFile(path).iter_batches(batch_size=1):
            for row in batch.to_pylist():
                if limit is not None and (selected if per_file else len(records)) >= limit:
                    break
                name = row['path']
                if selected_ids is not None and name not in selected_ids:
                    continue
                if not isinstance(name, str) or PurePosixPath(name).name != name or name in ('', '.', '..'):
                    raise ValueError(f'unsafe task name: {name!r}')
                if name in seen or (destination / name).exists():
                    raise ValueError(f'duplicate task: {name}')
                blob = row['task_binary']
                with tarfile.open(fileobj=io.BytesIO(blob), mode='r:*') as tar:
                    members = tar.getmembers()
                    for member in members:
                        rel = PurePosixPath(member.name)
                        if rel.is_absolute() or '..' in rel.parts or not (member.isfile() or member.isdir()):
                            raise ValueError(f'unsafe archive member: {member.name}')
                    # data filter additionally rejects link/permission tricks.
                    tar.extractall(destination / name, members=members, filter='data')
                if image_manifest and name in image_manifest['tasks']:
                    entry = image_manifest['tasks'][name]
                    image_sources[name] = {k: image_manifest[k] for k in ('version', 'repo_id', 'revision')}
                    image_sources[name].update(tasks={name: entry}, bundles={
                        env['bundle']: image_manifest['bundles'][env['bundle']] for env in entry['environments']})
                if name in cached_paths:
                    path_cache[name] = cached_paths[name]
                seen.add(name)
                selected += 1
                records.append({'task': name, 'parquet': str(path.resolve()),
                                'sha256': hashlib.sha256(blob).hexdigest()})
            if limit is not None and (selected if per_file else len(records)) >= limit:
                break
    if selected_ids is not None and seen != set(selected_ids):
        raise ValueError('materialization did not find all selected task IDs')
    if not records:
        raise ValueError('no tasks materialized from Parquet inputs')
    from validation.checks.path_cache import NAME
    (destination / NAME).write_text(json.dumps(path_cache))
    from validation.publishing.image_release import reference_path
    reference_path(destination).write_text(json.dumps(image_sources))
    (destination.parent / 'materialization.json').write_text(json.dumps(records, indent=2) + '\n')
    return [destination / record['task'] for record in records]


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('source', type=Path)
    ap.add_argument('destination', type=Path)
    ap.add_argument('--limit', type=int)
    ap.add_argument('--per-file', action='store_true')
    a = ap.parse_args()
    print(f'Materialized {len(materialize(a.source, a.destination, a.limit, a.per_file))} tasks')
