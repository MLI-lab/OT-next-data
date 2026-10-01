#!/usr/bin/env python3
"""Bounded storage/startup pilot, not a validation result or a load generator.

Keep permanent bundles/results on Horse; use only private job-local staging.
Sources: https://compendium.hpc.tu-dresden.de/data_lifecycle/working/
and jobs_and_resources/{barnard,romeo,julia}/ on the same site.
"""
import argparse
import concurrent.futures
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import tarfile
import tempfile
import time

CHECKS = ['check-instruction-suffix.sh', 'check-nproc.sh',
          'check-resource-sizes.sh', 'check-task-timeout.sh']
SOURCE = Path('/data/horse/ws/frwe188h-trp-shared/crosscodeeval')
REPO = Path(__file__).resolve().parents[2]


def command(argv, env=None, cwd=None, timeout=20):
    start = time.monotonic()
    proc = subprocess.Popen(list(map(str, argv)), env=env, cwd=cwd,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            start_new_session=True)
    expired = False
    try:
        out, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        expired = True
        os.killpg(proc.pid, signal.SIGKILL)
        out, _ = proc.communicate()
    return {'seconds': round(time.monotonic() - start, 4),
            'returncode': proc.returncode, 'timeout': expired,
            'output': out.decode(errors='replace')[-1500:]}


def prepare(root):
    root.mkdir(parents=True, exist_ok=False)
    bundle = root / 'bundle'
    bundle.mkdir()
    rows = json.loads((REPO / 'data/crosscodeeval/diagnostics/stage1-137324-errors.json').read_text())['retry_tasks']
    ids = [rows[i * (len(rows) - 1) // 7]['task'] for i in range(8)]
    times = {}
    start = time.monotonic()
    for task in ids:
        shutil.copytree(SOURCE / 'recovery/137304-stage1/tasks' / task, bundle / 'tasks' / task)
    for name in CHECKS:
        (bundle / 'checks').mkdir(exist_ok=True)
        shutil.copy2(REPO / 'external/terminal-bench/scripts/checks' / name, bundle / 'checks' / name)
    python = (SOURCE / 'venv/bin/python').resolve()
    shutil.copytree(python.parent.parent, bundle / 'python', symlinks=True)
    times['copy_bundle_seconds'] = time.monotonic() - start
    start = time.monotonic()
    with tarfile.open(root / 'bundle.tar', 'w') as tar:
        tar.add(bundle, arcname='bundle')
    times['tar_seconds'] = time.monotonic() - start
    start = time.monotonic()
    with (root / 'bundle.tar').open('rb') as src, gzip.open(root / 'bundle.tar.gz', 'wb', compresslevel=1) as dst:
        shutil.copyfileobj(src, dst)
    times['gzip_seconds'] = time.monotonic() - start
    manifest = {'task_ids': ids, 'original_python': str(python),
                'original_checks': str(REPO / 'external/terminal-bench/scripts/checks'),
                'python_name': python.name, 'prepare': times,
                'archive_bytes': {n: (root / n).stat().st_size for n in ('bundle.tar', 'bundle.tar.gz')},
                'sha256': {n: hashlib.sha256((root / n).read_bytes()).hexdigest() for n in ('bundle.tar', 'bundle.tar.gz')},
                'check_sha256': {n: hashlib.sha256((bundle / 'checks' / n).read_bytes()).hexdigest() for n in CHECKS}}
    (root / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    shutil.copy2(__file__, root / 'storage_probe.py')
    print(json.dumps(manifest, indent=2), flush=True)


def run(root, storage):
    manifest = json.loads((root / 'manifest.json').read_text())
    job = os.environ['SLURM_JOB_ID']
    cluster = os.environ['SLURM_CLUSTER_NAME']
    result = root / ('result-' + cluster + '-' + job)
    result.mkdir(exist_ok=False)
    records = []
    with (result / 'measurements.jsonl').open('x') as journal:
        def record(kind, **data):
            row = {'kind': kind, **data}
            records.append(row)
            journal.write(json.dumps(row) + '\n')
            journal.flush()
            print(json.dumps(row), flush=True)

        record('host', hostname=socket.gethostname(), cluster=cluster, job=job,
               affinity=sorted(os.sched_getaffinity(0)), storage=str(storage),
               mount=command(['findmnt', '-T', storage, '-no', 'SOURCE,FSTYPE,OPTIONS']),
               space=command(['df', '-h', storage]),
               note='Small warm-cache pilot; order/caching affect results. No global cache flush.')
        # Unique private directory: cleanup never touches another job or the source.
        with tempfile.TemporaryDirectory(prefix='ot-storage-' + job + '-', dir=storage) as scratch:
            scratch = Path(scratch)
            # Copy and unpack both archive formats; retain tar extraction for checks.
            for name in ('bundle.tar', 'bundle.tar.gz'):
                start = time.monotonic()
                shutil.copyfile(root / name, scratch / name)
                copy_seconds = time.monotonic() - start
                unpack = scratch / name.replace('.', '-')
                unpack.mkdir()
                extraction = command(['tar', '-xf', scratch / name, '-C', unpack], timeout=180)
                record('staging', archive=name, bytes=(root / name).stat().st_size,
                       copy_seconds=copy_seconds, extraction=extraction)
                if extraction['returncode']:
                    raise RuntimeError('Archive extraction failed')
            local = scratch / 'bundle-tar/bundle'
            shared = root / 'bundle'
            # A direct small-file copy provides the archive comparison.
            start = time.monotonic()
            shutil.copytree(shared / 'tasks', scratch / 'direct-tasks')
            record('direct_task_copy', seconds=time.monotonic() - start,
                   note='Tasks only, unlike archives which include Python/checks.')
            cases = [
                ('original', shared / 'tasks', Path(manifest['original_checks']), Path(manifest['original_python'])),
                ('home_python_only', shared / 'tasks', shared / 'checks', Path(manifest['original_python'])),
                ('home_checks_only', shared / 'tasks', Path(manifest['original_checks']), shared / 'python/bin' / manifest['python_name']),
                ('all_horse', shared / 'tasks', shared / 'checks', shared / 'python/bin' / manifest['python_name']),
                ('local_tasks', local / 'tasks', shared / 'checks', shared / 'python/bin' / manifest['python_name']),
                ('local_runtime', shared / 'tasks', local / 'checks', local / 'python/bin' / manifest['python_name']),
                ('all_local', local / 'tasks', local / 'checks', local / 'python/bin' / manifest['python_name']),
            ]
            # Repeat in reverse order to expose simple order/warm-cache effects.
            ordered = [(1, case) for case in cases] + [(2, case) for case in reversed(cases)]
            for round_id, (name, tasks, checks, python) in ordered:
                bindir = scratch / ('bin-' + name + '-' + str(round_id))
                bindir.mkdir()
                (bindir / 'python3').symlink_to(python)
                env = dict(os.environ, PATH=str(bindir) + ':' + os.environ['PATH'],
                           PYTHONDONTWRITEBYTECODE='1', PYTHONNOUSERSITE='1')
                for key in ('PYTHONPATH', 'PYTHONHOME', 'BASH_ENV', 'ENV', 'FIX_DIRS', 'BASE_DIR'):
                    env.pop(key, None)
                startup = command([python, '-c', 'import sys,tomllib,re,json; print(sys.executable); print(sys.prefix)'], env, scratch)
                record('python_startup', case=name, round=round_id, **startup)
                if startup['returncode']:
                    continue
                for concurrency in (1, 8):
                    if concurrency > int(os.environ.get('SLURM_CPUS_PER_TASK', '1')):
                        continue
                    def task_probe(task):
                        rows = []
                        for check in CHECKS:
                            row = command(['bash', checks / check, tasks / task], env, scratch)
                            rows.append({'task': task, 'check': check, **row})
                            if row['timeout']:
                                break
                        return rows
                    # Serial calibration uses two tasks; parallel uses all eight.
                    ids = manifest['task_ids'] if concurrency > 1 else manifest['task_ids'][:2]
                    start = time.monotonic()
                    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
                        rows = [row for group in pool.map(task_probe, ids) for row in group]
                    record('checks', case=name, round=round_id, concurrency=concurrency,
                           wall_seconds=time.monotonic() - start, task_count=len(ids),
                           checks=len(rows), timeouts=sum(r['timeout'] for r in rows),
                           nonzero=sum(r['returncode'] != 0 for r in rows), measurements=rows)
            record('complete', complete=True)
    (result / 'summary.json').write_text(json.dumps(records, indent=2) + '\n')


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('mode', choices=['prepare', 'run'])
    ap.add_argument('root', type=Path)
    ap.add_argument('--storage', type=Path)
    a = ap.parse_args()
    if not str(a.root.resolve()).startswith('/data/horse/'):
        ap.error('Permanent artifacts must be on Horse')
    if a.mode == 'prepare':
        prepare(a.root)
    else:
        if not a.storage or not os.environ.get('SLURM_JOB_ID'):
            ap.error('Run requires a Slurm allocation and --storage')
        run(a.root, a.storage)
