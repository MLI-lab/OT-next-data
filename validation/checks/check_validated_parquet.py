#!/usr/bin/env python3
"""Run the existing static runner against parquet-hosted task bundles.

The parquet `task_binary` field is a tar archive. This adapter only restores
one row at a time into a temporary directory and calls `run_static_rubric.analyze`;
the source parquet files are never modified.
"""
from __future__ import annotations
import argparse, json, os, random, shutil, sys, tarfile, tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

WORKDIR=Path.cwd()
DEFAULT_VALIDATED=WORKDIR.parent/'data'/'ot-next-data'/'validated_task'
sys.path.insert(0, str(Path(__file__).parent))
from run_static_rubric import analyze

OPTIONAL_CHECKS={
    # Cross-dataset warning policy used in RUBRIC_STATIC_CHECK.md. Benchmark
    # profile compatibility checks are intentionally excluded from this set.
    'ctrf_reporting',
    'instruction_concision', 'deterministic_reproducible',
}

def relativeize(value):
    """Remove machine-specific workspace prefixes from persisted evidence."""
    if isinstance(value, str) and value.startswith('/'):
        try:
            return os.path.relpath(value, WORKDIR)
        except ValueError:
            return value
    if isinstance(value, list): return [relativeize(x) for x in value]
    if isinstance(value, dict): return {k: relativeize(v) for k,v in value.items()}
    return value

def summarize_criteria(checks):
    """Return per-criterion results plus an aggregate decisive pass rate."""
    criteria={}
    for name, value in checks.items():
        status=value.get('status', 'error')
        criteria[name]={
            'status': status,
            'pass': status == 'pass',
            'rationale': value.get('reason', ''),
            'evidence': value.get('evidence', []),
        }
    decisive=[x for x in criteria.values() if x['status'] in {'pass','fail'}]
    passed=sum(x['status']=='pass' for x in decisive)
    return criteria, {
        'passed': passed,
        'failed': len(decisive)-passed,
        'not_decisive': len(criteria)-len(decisive),
        'pass_rate': round(passed/len(decisive), 6) if decisive else None,
    }

def extract_safe(blob: bytes, dest: Path):
    with tarfile.open(fileobj=__import__('io').BytesIO(blob), mode='r:*') as tf:
        for member in tf.getmembers():
            target=(dest/member.name).resolve()
            if not str(target).startswith(str(dest.resolve()) + '/'):
                raise ValueError(f'unsafe tar path: {member.name}')
            if member.isdir(): target.mkdir(parents=True, exist_ok=True); continue
            if not member.isfile(): continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with tf.extractfile(member) as src, target.open('wb') as out:
                shutil.copyfileobj(src, out)

def rows(path: Path, limit: int|None, seed: int):
    import pyarrow.parquet as pq
    pf=pq.ParquetFile(path)
    indices=list(range(pf.metadata.num_rows))
    if limit is not None and limit < len(indices):
        random.Random(seed).shuffle(indices); indices=sorted(indices[:limit])
    wanted=set(indices)
    for batch in pf.iter_batches(batch_size=32, columns=['path','task_binary','stages_passed','validation_variant','reason'] if 'reason' in pf.schema_arrow.names else ['path','task_binary','stages_passed','validation_variant']):
        for i in range(batch.num_rows):
            # iter_batches does not expose global offset, so use a caller-side
            # counter instead of relying on row-group layout.
            yield batch, i

def inspect_row(path, idx, task_path, blob, metadata):
    task_name=Path(task_path).name
    with tempfile.TemporaryDirectory(prefix='validated-task-') as td:
        taskdir=Path(td)/task_name; taskdir.mkdir()
        extract_safe(blob, taskdir)
        checks=analyze(taskdir)
        criteria,overall=summarize_criteria(checks)
        failures={k:v for k,v in checks.items() if v.get('status')=='fail'}
        warnings={k:v for k,v in failures.items() if k in OPTIONAL_CHECKS}
        hard_failures={k:v for k,v in failures.items() if k not in OPTIONAL_CHECKS}
        return relativeize({
            'source':os.path.relpath(path, WORKDIR), 'row':idx,
            'task_path':task_path, 'known_reason':metadata.get('reason'),
            'stages_passed':metadata.get('stages_passed'),
            # `static_failures` is intentionally hard failures only. The raw
            # union is available through `criteria` and is not persisted as a
            # third ambiguous aggregate.
            'static_failures':hard_failures, 'static_warnings':warnings,
            'static_hard_failures':hard_failures,
            'static_status_counts':__import__('collections').Counter(v.get('status') for v in checks.values()),
            'criteria':criteria, 'overall':overall,
        })

def run_file(path: Path, limit: int|None, seed: int, workers: int):
    import pyarrow.parquet as pq
    pf=pq.ParquetFile(path); total=pf.metadata.num_rows
    indices=list(range(total))
    if limit is not None and limit < total:
        random.Random(seed).shuffle(indices); indices=sorted(indices[:limit])
    wanted=set(indices); pending=[]; results=[]; offset=0
    cols=['path','task_binary']+[c for c in ('stages_passed','validation_variant','reason','category') if c in pf.schema_arrow.names]
    for batch in pf.iter_batches(batch_size=16, columns=cols):
        for j in range(batch.num_rows):
            idx=offset+j
            if idx not in wanted: continue
            task_path=batch['path'][j].as_py()
            metadata={c: batch[c][j].as_py() for c in cols if c not in {'path','task_binary'}}
            pending.append((idx,task_path,batch['task_binary'][j].as_py(),metadata))
        offset += batch.num_rows
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures=[pool.submit(inspect_row,path,*item) for item in pending]
        for n,f in enumerate(as_completed(futures),1):
            results.append(f.result())
            if n % max(1, workers*2) == 0 or n == len(futures):
                print(f'{path.name}: {n}/{len(futures)}', file=sys.stderr, flush=True)
    return sorted(results,key=lambda x:x['row'])

def main():
    ap=argparse.ArgumentParser()
    # This checker is intended to be run with OT-next-data/ as cwd.
    ap.add_argument('--root', default=os.path.relpath(DEFAULT_VALIDATED, WORKDIR),
                    help='validated-task directory; defaults to the sibling data store')
    ap.add_argument('--limit', type=int, default=100, help='per parquet; omit or use 0 for all')
    ap.add_argument('--archive-all', action='store_true')
    ap.add_argument('--seed', type=int, default=20261017)
    ap.add_argument('--workers', type=int, default=8)
    ap.add_argument('--out', default='validation/checks/validated_parquet_results.json')
    ap.add_argument('--dataset-out-dir', default=os.path.relpath(DEFAULT_VALIDATED, WORKDIR),
                    help='Write one JSONL file per dataset named <dataset>_static_check.json')
    args=ap.parse_args(); root=Path(args.root).resolve(); all_results=[]
    dataset_results={}
    for family in sorted(p for p in root.iterdir() if p.is_dir()):
        family_results=[]
        for kind in ('archive','tasks'):
            p=family/f'{kind}.parquet'
            if not p.exists(): continue
            limit=None if (kind=='archive' and args.archive_all) or args.limit==0 else args.limit
            print(f'Running {p} limit={limit}', file=sys.stderr)
            part=run_file(p,limit,args.seed,args.workers)
            all_results += part
            family_results += part
        if family_results:
            dataset_results[family.name]=family_results
    out=Path(args.out); out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps({'results':all_results},ensure_ascii=False,indent=2,default=list)+'\n')
    dataset_out=Path(args.dataset_out_dir); dataset_out.mkdir(parents=True, exist_ok=True)
    for dataset, records in dataset_results.items():
        target=dataset_out/f'{dataset}_static_check.json'
        with target.open('w', encoding='utf-8') as f:
            for record in records:
                f.write(json.dumps(record, ensure_ascii=False, default=list, separators=(',', ':'))+'\n')
    from collections import Counter
    print(json.dumps({'rows':len(all_results),'with_static_failure':sum(bool(x['static_failures']) for x in all_results),
                      'failure_criteria':Counter(k for x in all_results for k in x['static_failures'])},ensure_ascii=False))

if __name__=='__main__': main()
