"""Resolve the task payloads a results index refers to, by task ID and content hash."""
from pathlib import Path


def index_path(source):
    source = Path(source)
    path = source if source.name == 'results.parquet' else source / 'results.parquet'
    return path if path.is_file() else None


def selection(source):
    """Map each payload file to the {task_id: content_sha256} it supplies for the index."""
    import pyarrow.parquet as pq
    index = index_path(source)
    if index is None:
        return None
    wanted = {}
    for row in pq.read_table(index, columns=['task_id', 'content_sha256']).to_pylist():
        if row['task_id'] in wanted:
            raise ValueError('Results index contains one task twice')
        wanted[row['task_id']] = row['content_sha256']
    selected, found = {}, set()
    for path in sorted((index.parent / 'tasks').glob('*.parquet')):
        names = pq.read_schema(path).names
        if not {'path', 'content_sha256'} <= set(names):
            continue
        for row in pq.read_table(path, columns=['path', 'content_sha256']).to_pylist():
            name = row['path']
            if name not in found and wanted.get(name) == row['content_sha256']:
                selected.setdefault(path.resolve(), {})[name] = row['content_sha256']
                found.add(name)
    missing = sorted(set(wanted) - found)
    if missing:
        raise ValueError(f'No payload with the indexed content hash for {len(missing)} task(s), e.g. {missing[0]}')
    return selected


def accepts(selected, path, row):
    if selected is None:
        return True
    expected = selected.get(Path(path).resolve(), {}).get(row['path'])
    return expected is not None and row.get('content_sha256') == expected
