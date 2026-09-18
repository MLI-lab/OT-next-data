#!/usr/bin/env python3
"""Running the patcher on the pinned upstream must reproduce the published dataset.

This is the property that makes a published dataset auditable: anyone can take
the pinned upstream parquet and the pinned benchmark archive, run
`data/<dataset>/patch.py`, and get exactly the tasks that were published.

Compares by content, not by parquet bytes: for every task it hashes the sorted
(filename, content) pairs of the task tarball, so a different pyarrow version or
compression setting does not make an identical dataset look different.

  # record what was published (once, from the parquets you uploaded)
  python verify/check_reproducible.py --digests upload/*/tasks.parquet -o data/<ds>/published_digests.json

  # check that today's patcher still produces it, from the pinned upstream
  python data/<ds>/patch.py --input upstream-<lang>.parquet --output out-<lang>.parquet --archive $CCEVAL_ARCHIVE
  python verify/check_reproducible.py out-*.parquet --expect data/<ds>/published_digests.json

Exit code is 0 only if the task sets and every task's contents match.
"""
from __future__ import annotations
import argparse
import hashlib
import io
import json
import sys
import tarfile
from pathlib import Path

import pyarrow.parquet as pq


def task_digests(parquet):
    """{task id: digest of its files}, independent of parquet encoding."""
    out = {}
    for row in pq.read_table(parquet).to_pylist():
        h = hashlib.sha256()
        with tarfile.open(fileobj=io.BytesIO(row['task_binary']), mode='r:*') as t:
            for m in sorted((m for m in t if m.isfile()), key=lambda m: m.name):
                h.update(m.name.encode())
                h.update(hashlib.sha256(t.extractfile(m).read()).digest())
        out[row['path']] = h.hexdigest()[:16]
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('parquet', type=Path, nargs='+', help='the parquet(s) to check, or to record with --digests')
    ap.add_argument('--digests', action='store_true', help='write the digests instead of checking them')
    ap.add_argument('-o', '--out', type=Path, help='where --digests writes')
    ap.add_argument('--expect', type=Path, help='digest file to compare against')
    a = ap.parse_args()

    digests = {}
    for parquet in a.parquet:
        digests.update(task_digests(parquet))
    if a.digests:
        out = a.out or a.parquet[0].with_suffix('.digests.json')
        out.write_text(json.dumps(digests, indent=0, sort_keys=True) + '\n')
        print(f'wrote {len(digests)} task digests to {out}')
        return
    if not a.expect:
        sys.exit('pass --expect <digest file>, or --digests to record one')

    expected = json.loads(a.expect.read_text())
    missing = sorted(set(expected) - set(digests))
    extra = sorted(set(digests) - set(expected))
    changed = sorted(t for t in set(digests) & set(expected) if digests[t] != expected[t])
    print(f'{len(digests)} tasks now, {len(expected)} published')
    for label, ids in (('missing now', missing), ('not published', extra), ('contents changed', changed)):
        if ids:
            print(f'  {label}: {len(ids)}  e.g. {", ".join(ids[:5])}')
    if missing or extra or changed:
        sys.exit('FAILED: the patcher no longer reproduces the published dataset')
    print('PASSED: the patcher reproduces the published dataset exactly')


if __name__ == '__main__':
    main()
