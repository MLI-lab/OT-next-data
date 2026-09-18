#!/usr/bin/env python3
"""Does the patcher still produce exactly the dataset that was published?

That property is what makes a published dataset auditable: anyone can run the
patcher on the pinned upstream and get the same tasks back.

Download what was published (`hf download <repo> ...`), then either:

  # run the patcher yourself and compare (the whole check in one command)
  python verify/check_reproducible.py --patcher data/crosscodeeval/patch.py \\
         --archive $CCEVAL_ARCHIVE --reference published/*/tasks.parquet

  # or compare parquets you already patched
  python verify/check_reproducible.py $PILOT_ROOT/patched/*/tasks.parquet \\
         --reference published/*/tasks.parquet

The comparison is by task *contents* - for each task, a hash over its files -
not by parquet bytes, so a different pyarrow version or compression setting does
not make an identical dataset look different. Nothing is stored in the repo: the
published dataset itself is the reference.

--partial compares only the tasks present on the new side, for checking one
source of a multi-source dataset.

Exit code is 0 only if the task sets and every task's contents match.
"""
from __future__ import annotations
import argparse
import hashlib
import io
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

import pyarrow.parquet as pq


def task_hashes(parquets):
    """{task id: hash of its files}, independent of parquet encoding."""
    out = {}
    for parquet in parquets:
        for row in pq.read_table(parquet).to_pylist():
            h = hashlib.sha256()
            with tarfile.open(fileobj=io.BytesIO(row['task_binary']), mode='r:*') as t:
                for m in sorted((m for m in t if m.isfile()), key=lambda m: m.name):
                    h.update(m.name.encode())
                    h.update(hashlib.sha256(t.extractfile(m).read()).digest())
            out[row['path']] = h.hexdigest()[:16]
    return out


def run_patcher(patcher, archive, workdir):
    """Patch every pinned upstream source into workdir, and return the parquets."""
    cmd = [sys.executable, str(patcher), '--all', '--archive', str(archive),
           '--upstream', str(workdir / 'upstream'), '--outdir', str(workdir / 'patched')]
    print(f'$ {" ".join(cmd)}', flush=True)
    if subprocess.run(cmd).returncode != 0:
        sys.exit('the patcher failed')
    return sorted((workdir / 'patched').glob('*/tasks.parquet'))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('parquet', nargs='*', type=Path, help='the patched parquet(s) to check')
    ap.add_argument('--patcher', type=Path, help='run this patcher (--all) and check what it produces')
    ap.add_argument('--archive', type=Path, help='dataset-specific patcher input, passed through')
    ap.add_argument('--workdir', type=Path, help='where --patcher works (default: a temporary directory)')
    ap.add_argument('--reference', nargs='+', type=Path, required=True,
                    help='the published parquet(s) to compare against')
    ap.add_argument('--partial', action='store_true', help='compare only the tasks present on the new side')
    a = ap.parse_args()

    with tempfile.TemporaryDirectory() as tmp:
        if a.patcher:
            if not a.archive:
                sys.exit('--patcher needs --archive (the patcher input)')
            parquets = run_patcher(a.patcher, a.archive, a.workdir or Path(tmp))
        elif a.parquet:
            parquets = a.parquet
        else:
            sys.exit('pass patched parquets, or --patcher to produce them')
        hashes = task_hashes(parquets)

    expected = task_hashes(a.reference)
    if a.partial:
        expected = {k: v for k, v in expected.items() if k in hashes}

    missing = sorted(set(expected) - set(hashes))
    extra = sorted(set(hashes) - set(expected))
    changed = sorted(t for t in set(hashes) & set(expected) if hashes[t] != expected[t])
    print(f'{len(hashes)} tasks produced, {len(expected)} published')
    for label, ids in (('missing now', missing), ('not published', extra), ('contents changed', changed)):
        if ids:
            print(f'  {label}: {len(ids)}  e.g. {", ".join(ids[:5])}')
    if missing or extra or changed:
        sys.exit('FAILED: the patcher no longer reproduces the published dataset')
    print('PASSED: the patcher reproduces the published dataset exactly')


if __name__ == '__main__':
    main()
