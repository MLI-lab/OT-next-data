"""Select unchanged TaskTrove rows, spread across input files and row positions."""
import argparse
import hashlib
import json
from pathlib import Path


def main():
    import pyarrow as pa
    import pyarrow.parquet as pq
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('source', type=Path)
    ap.add_argument('destination', type=Path)
    ap.add_argument('--count', type=int, default=10)
    a = ap.parse_args()
    files = sorted(a.source.glob('*.parquet'))
    if not files or a.count < len(files):
        raise ValueError('count must cover at least one row per input Parquet')
    a.destination.mkdir(parents=True, exist_ok=False)
    rows, inputs = [], []
    for i, file in enumerate(files):
        table = pq.read_table(file)
        count = a.count // len(files) + (i < a.count % len(files))
        if count > len(table):
            raise ValueError(f'not enough rows in {file}')
        indices = [j * (len(table) - 1) // (count - 1) for j in range(count)] if count > 1 else [0]
        selected = table.take(pa.array(indices)).to_pylist()
        rows.extend(selected)
        inputs.append({'path': str(file.resolve()), 'sha256': hashlib.sha256(file.read_bytes()).hexdigest(),
                       'row_indices_zero_based': indices, 'task_ids': [r['path'] for r in selected]})
    pq.write_table(pa.Table.from_pylist(rows), a.destination / 'tasks.parquet')
    original = json.loads((a.source / 'source.json').read_text())
    meta = {'repo': original['repo'], 'revision': original['revision'],
        'selection': {'method': 'equal per source file (remainder to first files); evenly spaced row positions including endpoints',
            'count': a.count, 'script': str(Path(__file__).resolve()),
            'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), 'inputs': inputs}}
    (a.destination / 'source.json').write_text(json.dumps(meta, indent=2) + '\n')
    print(f'Selected {len(rows)} unchanged tasks into {a.destination}')


if __name__ == '__main__':
    main()
