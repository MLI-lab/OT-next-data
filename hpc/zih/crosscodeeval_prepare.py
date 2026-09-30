"""Download the published repair at its immutable revision, then apply pip pins."""
import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from huggingface_hub import hf_hub_download
import pyarrow as pa
import pyarrow.parquet as pq
from data.crosscodeeval.patch import pin_parquet

REVISION = '12df4483fe99c79ccbb4c923d76ab5a2b042e64a'
SOURCES = {'csharp-v5': 1353, 'java-v4': 1895, 'python-v3': 432, 'typescript-v3': 3030}


def main(root):
    root = root.resolve()
    if not any(root.is_relative_to(path) for path in ('/data/horse', '/data/ws', '/data/cat')):
        raise ValueError('Dataset root must be in an available data workspace')
    os.environ.setdefault('HF_HOME', str(root / 'cache/huggingface'))
    records, sample = [], []
    for family, count in SOURCES.items():
        source = f'laion__exp_rpt_crosscodeeval-{family}/tasks.parquet'
        original = hf_hub_download('open-thoughts/TaskTrove', source,
                                   repo_type='dataset', revision=REVISION,
                                   local_dir=root / 'published', cache_dir=root / 'cache/huggingface/hub')
        output = root / 'parquets-pinned' / f'{family}.parquet'
        record = pin_parquet(Path(original), output)
        if record['tasks'] != count:
            raise ValueError(f'Unexpected coverage: {record}')
        records.append(record)
        table = pq.read_table(output)
        sample.append(table.slice(0, 3 if family.startswith(('csharp', 'java')) else 2))
        print(json.dumps(record), flush=True)
    provenance = {'dataset': 'open-thoughts/TaskTrove', 'revision': REVISION, 'files': records}
    (root / 'parquets-pinned/source.json').write_text(json.dumps(provenance, indent=2) + '\n')
    (root / 'sample').mkdir(exist_ok=True)
    pq.write_table(pa.concat_tables(sample), root / 'sample/tasks.parquet')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    main(parser.parse_args().root)
