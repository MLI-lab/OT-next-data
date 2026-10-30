"""Resolve the latest task payloads from a revision results index."""
from pathlib import Path, PurePosixPath


def index_path(source):
    source = Path(source)
    path = source if source.name == 'results.parquet' else source / 'results.parquet'
    return path if path.is_file() else None


def selection(source):
    import pyarrow.parquet as pq
    index = index_path(source)
    if index is None:
        return None
    selected = {}
    seen = set()
    for row in pq.read_table(index, columns=['dataset', 'task_id', 'content_sha256', 'latest', 'task_file']).to_pylist():
        if not row['latest']:
            continue
        if row['task_id'] in seen:
            raise ValueError('Results index contains multiple latest revisions for one task')
        seen.add(row['task_id'])
        name = row.get('task_file')
        if not name:
            raise ValueError('This results table has no task payload links; provide the original task dataset')
        if row['dataset'] and name.startswith(row['dataset'] + '/'):
            name = name[len(row['dataset']) + 1:]
        relative = PurePosixPath(name)
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('Unsafe task revision path')
        path = (index.parent / name).resolve()
        if not path.is_relative_to(index.parent.resolve()) or not path.is_file():
            raise ValueError(f'Missing or unsafe task revision file: {name}')
        selected.setdefault(path, {})[row['task_id']] = row['content_sha256']
    return selected


def accepts(selected, path, row):
    if selected is None:
        return True
    expected = selected.get(Path(path).resolve(), {}).get(row['path'])
    return expected is not None and row.get('content_sha256') == expected
