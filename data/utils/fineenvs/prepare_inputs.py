"""Download each retained FineEnvs input family once into a resumable snapshot.

This is a separate preparation operation, never invoked by task setup.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import sys
import time
import tomllib

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from data.fineenvs.patch import EXCLUDED, SOURCE
from data.utils.task_archive import read_members


def sha256(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def family_key(bucket, prefix):
    return hashlib.sha256((bucket + '/' + prefix).encode()).hexdigest()[:24]


def save(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')
    temporary.replace(path)


def retry(call):
    for attempt in range(10):
        try:
            return call()
        except Exception as exc:
            response = getattr(exc, 'response', None)
            status = getattr(response, 'status_code', None)
            if status not in (429, 500, 502, 503, 504) and '429' not in str(exc):
                raise
            if attempt == 9:
                raise
            header = getattr(response, 'headers', {}).get('retry-after', '')
            delay = max(float(header) if header.isdigit() else 0, min(300, 10 * 2 ** attempt))
            print(f'Retrying input request after {delay}s (HTTP {status})', flush=True)
            time.sleep(delay)


REVIEWED_CONFLICT = {
    # Task 0001_690_1690859_qa_5 names only all_mias_scans.h5. Preserve both
    # different auxiliary Info.txt files instead of the old concurrent overwrite.
    'kmader__mias-mammography/Info.txt': (6423, '0bf02276c2534a307963d86eb52e57f4a8f377e5ebf99b00d65aab5a95ec11e4'),
    'kmader__mias-mammography/all-mias/Info.txt': (9860, '9d399d6a26750d8342db2718b95f91b072c178cb2b74c0ac0bbcc03129b38fbd'),
}


def unique_inputs(remote):
    """The upstream downloader flattens paths; identical aliases are harmless."""
    inventory = sorted([{'path': f.path, 'size': f.size, 'xet_hash': f.xet_hash} for f in remote], key=lambda f: f['path'])
    digest = hashlib.sha256(json.dumps(inventory, sort_keys=True).encode()).hexdigest()
    reviewed = json.loads(Path(__file__).with_name('reviewed-input-conflicts.json').read_text())
    approved = digest in {r['inventory_sha256'] for r in reviewed.values()}
    grouped = {}
    for item in remote:
        grouped.setdefault(PurePosixPath(item.path).name, []).append(item)
    result = []
    for name, items in sorted(grouped.items()):
        identities = {(f.size, f.xet_hash) for f in items}
        if len(identities) != 1 or (not items[0].xet_hash and items[0].size != 0):
            if not approved and {f.path: (f.size, f.xet_hash) for f in items} != REVIEWED_CONFLICT:
                raise ValueError('different upstream files have the same destination basename: ' + name)
            variants = {}
            for f in sorted(items, key=lambda f: (len(PurePosixPath(f.path).parts), f.path)):
                variants.setdefault((f.size, f.xet_hash), []).append(f)
            result.extend((group[0], [f.path for f in group]) for group in variants.values())
        else:
            result.append((items[0], [f.path for f in items]))
    return result


def prepare(source, cache, *, include_archived=False):
    os.environ.setdefault('HF_XET_CACHE', str(cache / '.xet'))
    import pyarrow.parquet as pq
    from huggingface_hub import list_bucket_tree, download_bucket_files
    cache.mkdir(parents=True, exist_ok=True)
    families = {}
    for batch in pq.ParquetFile(source).iter_batches(batch_size=32):
        for row in batch.to_pylist():
            if row['path'] in EXCLUDED and not include_archived:
                continue
            config = tomllib.loads(read_members(row['task_binary'])['task.toml'].decode())
            env = config['environment']['env']
            key = (env['HF_BUCKET'], env['BUCKET_PREFIX'].rstrip('/'))
            families.setdefault(key, []).append(row['path'])
    records, issues = [], []
    for (bucket, prefix), tasks in sorted(families.items()):
        folder = cache / family_key(bucket, prefix)
        folder.mkdir(exist_ok=True)
        manifest_path = folder / 'manifest.json'
        if manifest_path.exists():
            record = json.loads(manifest_path.read_text())
            if record['bucket'] != bucket or record['prefix'] != prefix:
                raise ValueError('input-cache identity mismatch')
            for item in record['files']:
                path = folder / 'files' / item['name']
                if path.stat().st_size != item['size'] or sha256(path) != item['sha256']:
                    raise ValueError(f'input-cache corruption: {path}')
        else:
            time.sleep(1)  # one family at a time; never fan out API listings
            remote = retry(lambda: list(list_bucket_tree(bucket, prefix=prefix + '/', recursive=True)))
            remote = sorted((f for f in remote if getattr(f, 'type', None) == 'file'), key=lambda f: f.path)
            if any(not f.path.startswith(prefix + '/') or '..' in PurePosixPath(f.path).parts for f in remote):
                raise ValueError('unexpected upstream input path')
            try:
                if not remote:
                    raise ValueError(f'empty input family: {prefix}')
                unique = unique_inputs(remote)
            except ValueError as exc:
                issues.append({'bucket': bucket, 'prefix': prefix, 'tasks': tasks, 'error': str(exc),
                               'files': [{'path': f.path, 'size': f.size, 'xet_hash': f.xet_hash} for f in remote]})
                save(cache / 'issues.json', issues)
                print(f'Unresolved input family {prefix}: {exc}; continuing other families', flush=True)
                continue
            remote = [f for f, paths in unique]
            aliases = {f.path: paths for f, paths in unique}
            from collections import Counter
            basenames = Counter(PurePosixPath(f.path).name for f in remote)
            names = [str(PurePosixPath(f.path).relative_to(prefix)) if basenames[PurePosixPath(f.path).name] > 1
                     else PurePosixPath(f.path).name for f in remote]
            files = folder / 'files'
            files.mkdir(exist_ok=True)
            for name in names:
                (files / name).parent.mkdir(parents=True, exist_ok=True)
            save(folder / 'remote-inventory.json', {'bucket': bucket, 'prefix': prefix, 'files': [
                {'path': f.path, 'name': n, 'size': f.size, 'xet_hash': f.xet_hash}
                for f, n in zip(remote, names)]})
            print(f'Downloading {prefix}: {len(remote)} files, {sum(f.size for f in remote)} bytes', flush=True)
            retry(lambda: download_bucket_files(bucket, files=[(f, files / n) for f, n in zip(remote, names)],
                                                 raise_on_missing_files=True))
            record = {'bucket': bucket, 'prefix': prefix, 'files': []}
            for f, name in zip(remote, names):
                path = files / name
                if path.stat().st_size != f.size:
                    raise ValueError('downloaded input size mismatch')
                record['files'].append({'path': f.path, 'source_paths': aliases[f.path], 'name': name, 'size': f.size,
                                        'xet_hash': f.xet_hash, 'sha256': sha256(path)})
            save(manifest_path, record)
        records.append({'bucket': bucket, 'prefix': prefix, 'tasks': tasks,
                        'manifest': str(manifest_path.relative_to(cache)), 'manifest_sha256': sha256(manifest_path),
                        'bytes': sum(f['size'] for f in record['files'])})
        save(cache / 'progress.json', {'complete': False, 'families_done': len(records),
                                     'families_total': len(families), 'families': records})
        print(f'Prepared {len(records)}/{len(families)} families', flush=True)
    index = {'complete': not issues, 'source': SOURCE, 'source_parquet_sha256': sha256(source), 'families': records,
             'unique_input_bytes': sum(r['bytes'] for r in records),
             'packaged_input_bytes': sum(r['bytes'] * len(r['tasks']) for r in records)}
    save(cache / 'index.json', index)
    if issues:
        raise ValueError(f'{len(issues)} input families need review; see {cache / "issues.json"}; packaging blocked')
    if (cache / 'issues.json').exists():
        (cache / 'issues.json').replace(cache / 'resolved-issues.json')
    return index


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--cache', type=Path, required=True)
    parser.add_argument('--include-archived', action='store_true',
                        help='prepare inputs for historical exclusions for revalidation')
    args = parser.parse_args()
    result = prepare(args.source, args.cache, include_archived=args.include_archived)
    print(json.dumps({'complete': result['complete'], 'families': len(result['families']),
                      'unique_input_bytes': result['unique_input_bytes'],
                      'packaged_input_bytes': result['packaged_input_bytes']}))
