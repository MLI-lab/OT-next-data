"""Reproduce the pinned pymethods2test TaskTrove source with reviewed repairs.

R1 (stage 1): pin pytest in environment/Dockerfile and tests/test.sh, and drop
the `2>/dev/null` that makes check-pip-pinning.sh report the package as unpinned.
The version is the one the unpinned install resolved in the pilot stage-5 logs.

R2 (stage 4): add solution/solve.sh, which copies the existing solution/solution.py
to /app/solution.py.

Tests and reward logic are never edited.

R3: replace the ignored verifier.restart_environment=false field with the
supported environment_mode="shared" setting.

R4: repair the missing cmp_to_key import in reference 1059 and install the
missing numpy==2.2.6 dependency during verifier preparation for task 3412.
"""

# Support both direct execution and python -m data.<source>.patch.
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


import argparse
import copy
import gzip
import hashlib
import io
import json
import re
import tarfile
import tomllib
from pathlib import Path

REVISION = '946884702046be1a7dcea2638186ad6b0d2ea103'
PATCH_VERSION = 'pymethods2test-reference-import-and-numpy-v3'
EXPECTED_TASK_COUNT = 4990

DOCKER_LINE = 'RUN pip install --no-cache-dir pytest'
DOCKER_LINE_FIXED = 'RUN pip install --no-cache-dir pytest==9.1.1'
TEST_LINE = 'pip3 install --quiet pytest 2>/dev/null || true'
TEST_LINE_FIXED = 'pip3 install --quiet pytest==9.1.1 || true'
SOLVE_SH = b'#!/bin/bash\nset -euo pipefail\ncp /solution/solution.py /app/solution.py\n'

from data.utils.task_archive import read_members
from data.utils.patch_reporting import write_patch_report

REFERENCE_HASHES = {
    'pymethods2test-1059': '487e92ef39067b0649af5f61a1435fdcb5e6ddde4693c076977a5c169d14bde8',
    'pymethods2test-3412': 'e76b0b9ba081cda01d4eef0ccaee180c951c9ea66c433ed96401307b1e593f34',
}
REPAIR_REASONS = {
    'pymethods2test-1059': 'Added the missing functools.cmp_to_key import to the supplied reference solution; its seven unchanged tests pass locally.',
    'pymethods2test-3412': 'Added numpy==2.2.6 to tests/setup.sh because the supplied reference imports NumPy; its six unchanged tests pass locally. The shared Dockerfile is unchanged by this repair.',
}


def repair_reference(task_id, files):
    """Return targeted edits and additions, rejecting unexpected source payloads."""
    if task_id not in REFERENCE_HASHES:
        return {}, {}
    if hashlib.sha256(files['solution/solution.py']).hexdigest() != REFERENCE_HASHES[task_id]:
        raise ValueError(f'{task_id}: unexpected reference solution')
    if task_id == 'pymethods2test-1059':
        return {'solution/solution.py': b'from functools import cmp_to_key\n\n' + files['solution/solution.py']}, {}
    if 'tests/setup.sh' in files:
        raise ValueError(f'{task_id}: unexpected existing verifier setup')
    return {}, {'tests/setup.sh': b'#!/bin/bash\nset -euo pipefail\npython -m pip install --quiet numpy==2.2.6\n'}


def replace_single_line(text, old, new, name):
    lines = text.split('\n')
    if lines.count(old) != 1:
        raise ValueError(f'{name}: expected exactly one {old!r} line, got {lines.count(old)}')
    return '\n'.join(new if line == old else line for line in lines)


def pin_pytest(files):
    """R1. Raises unless each file has exactly one matching line and no other pip line."""
    for name, old, new in (('environment/Dockerfile', DOCKER_LINE, DOCKER_LINE_FIXED),
                           ('tests/test.sh', TEST_LINE, TEST_LINE_FIXED)):
        text = files[name].decode()
        others = [line for line in text.split('\n') if 'pip' in line and line != old]
        if others:
            raise ValueError(f'{name}: unexpected pip lines {others!r}')
        files[name] = replace_single_line(text, old, new, name).encode()


def add_oracle_wrapper(files):
    """R2. Raises if solve.sh already exists or solution.py is missing."""
    if 'solution/solve.sh' in files:
        raise ValueError('solution/solve.sh already exists')
    if 'solution/solution.py' not in files:
        raise ValueError('solution/solution.py is missing')
    return SOLVE_SH


def migrate_verifier_mode(content):
    """Migrate only the upstream false flag, preserving shared verification."""
    verifier = tomllib.loads(content.decode())["verifier"]
    if verifier.get("restart_environment") is not False:
        raise ValueError("expected verifier.restart_environment = false")
    if "environment_mode" in verifier or "environment" in verifier:
        raise ValueError("unexpected existing verifier environment configuration")
    updated, count = re.subn(
        rb'(?m)^([ \t]*)restart_environment[ \t]*=[ \t]*false[ \t]*$',
        rb'\1environment_mode = "shared"', content)
    if count != 1:
        raise ValueError("expected exactly one legacy verifier restart flag")
    return updated


def repack(blob, edits, additions):
    """Rewrite edited members in place, append each addition after solution/solution.py.

    Added members copy ownership from the anchor member, with mode 0755 and mtime 0.
    """
    out = io.BytesIO()
    compress = blob[:2] == b'\x1f\x8b'
    stream = gzip.GzipFile(filename='', fileobj=out, mode='wb', compresslevel=9, mtime=0) if compress else out
    anchor = 'solution/solution.py'
    with tarfile.open(fileobj=io.BytesIO(blob), mode='r:*') as src, \
            tarfile.open(fileobj=stream, mode='w', format=src.format) as dst:
        for member in src:
            info = copy.copy(member)
            info.mtime = 0
            info.pax_headers = {k: v for k, v in member.pax_headers.items()
                                if k not in ('mtime', 'atime', 'ctime')}
            if info.name in edits:
                info.size = len(edits[info.name])
                dst.addfile(info, io.BytesIO(edits[info.name]))
            elif member.isfile():
                dst.addfile(info, src.extractfile(member))
            else:
                dst.addfile(info)
            if member.name == anchor:
                for name, data in additions.items():
                    new = tarfile.TarInfo(name)
                    new.size = len(data)
                    new.mode = 0o755
                    new.mtime = 0
                    new.uid, new.gid = member.uid, member.gid
                    new.uname, new.gname = member.uname, member.gname
                    dst.addfile(new, io.BytesIO(data))
    if compress:
        stream.close()
    return out.getvalue()


def patch_task(blob, task_id=None):
    files = read_members(blob)
    additions = {'solution/solve.sh': add_oracle_wrapper(files)}
    pin_pytest(files)
    edits = {name: files[name] for name in ('environment/Dockerfile', 'tests/test.sh')}
    edits['task.toml'] = migrate_verifier_mode(files['task.toml'])
    reference_edits, reference_additions = repair_reference(task_id, files)
    edits.update(reference_edits)
    additions.update(reference_additions)
    return repack(blob, edits, additions)


def main():
    import pyarrow as pa
    import pyarrow.parquet as pq
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    source = pq.ParquetFile(args.source)
    if source.metadata.num_rows != EXPECTED_TASK_COUNT:
        raise ValueError(f'Expected {EXPECTED_TASK_COUNT} tasks, got {source.metadata.num_rows}')
    patched = 0
    labels, reasons = {}, {}
    with pq.ParquetWriter(args.output / "tasks.parquet", source.schema_arrow) as writer:
        for batch in source.iter_batches(batch_size=32):
            rows = batch.to_pylist()
            for row in rows:
                task_id = row['path']
                row['task_binary'] = patch_task(row['task_binary'], task_id)
                labels[task_id] = ['pymethods-preparation-repairs']
                reasons[task_id] = ('Pinned pytest, added the reference entry point and migrated the shared verifier schema. '
                                    + REPAIR_REASONS.get(task_id, 'The supplied reference solution is unchanged.'))
                patched += 1
            writer.write_table(pa.Table.from_pylist(rows, schema=source.schema_arrow))
    if patched != EXPECTED_TASK_COUNT:
        raise ValueError(f'Patched {patched} tasks, expected {EXPECTED_TASK_COUNT}')
    manifest = {'patch_version': PATCH_VERSION, 'revision': REVISION,
                'r1_pin_pytest_tasks': patched, 'r2_oracle_wrapper_tasks': patched,
                'r3_shared_verifier_schema_tasks': patched,
                'reference_repairs': {task: reason for task, reason in REPAIR_REASONS.items() if task in labels}}
    (args.output / 'manifest.json').write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n')
    write_patch_report(args.source, args.output / 'tasks.parquet',
                       source={'dataset': 'open-thoughts/TaskTrove', 'revision': REVISION,
                               'url': 'https://huggingface.co/datasets/open-thoughts/TaskTrove/tree/' + REVISION + '/DCAgent__exp_rpt_pymethods2test-large-v2',
                               'subset': 'DCAgent__exp_rpt_pymethods2test-large-v2'},
                       dropped={}, change_labels=labels, change_reasons=reasons,
                       patcher=__file__)


if __name__ == "__main__":
    main()
