"""Apply reviewed, runtime-backed path clarifications to pinned Scale-SWE tasks."""
from __future__ import annotations

# Support both direct execution and python -m data.<source>.patch.
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


import argparse
import copy
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import tarfile

import pyarrow as pa
import pyarrow.parquet as pq

from data.utils.patch_reporting import write_patch_report
from data.utils.resolve_absolute_paths import apply_reviewed_replacements, collect_reports
from validation.contract import files_digest

REVISION = '8935f8e55244fd56080cdb8dcd0819a57e8a003c'

# Explicitly reviewed instruction corrections; keep the task's other hints intact.
INSTRUCTION_CORRECTIONS = {
    'appium_python-client_pr517': (
        'b1f4a2dc528408bb6651e198b76963c04069b6558453c3da42233ed54848bea2',
        [('Attempt to run iOS functional tests in parallel using the `-n` flag.',
          'Attempt to run iOS functional tests in parallel using the existing test file:'),
         ('pytest -n 2 test/functional/ios/find_by_ios_class_chain_tests.py',
          'pytest -n 2 /workspace/python-client/test/functional/ios/search_context/find_by_ios_class_chain_tests.py')]),
    'beetbox_mediafile_pr86': (
        '25ddb4ef83ecc81c538044f8bf4ebfeb367be92adb7a8e2eb5ea283927184e24',
        [('**`mediafile/utils.py`**: For general utility functions and helpers.',
          '**`/workspace/mediafile/mediafile/utils/`**: For general utility functions and helpers.')]),
}


def patch_blob(blob, diagnostic=None, decisions=(), oracle_passed=False, task_id=None, automatic_paths=False):
    with tarfile.open(fileobj=io.BytesIO(blob)) as archive:
        members = archive.getmembers()
        contents = {}
        for member in members:
            name = PurePosixPath(member.name)
            if name.is_absolute() or '..' in name.parts or not (member.isfile() or member.isdir()):
                raise ValueError('Unsafe task archive member')
            if member.isfile():
                if str(name) in contents:
                    raise ValueError('Duplicate archive member')
                contents[str(name)] = archive.extractfile(member).read()
        if (decisions or automatic_paths) and files_digest(contents.items()) != (diagnostic or {}).get('agent_baseline', {}).get('task_sha256'):
            raise ValueError('Task changed since path resolution')
        original = contents['instruction.md'].decode()
        changed, edits = (apply_reviewed_replacements(original, diagnostic, decisions, oracle_passed=oracle_passed)
                          if decisions else (original, []))
        if automatic_paths:
            from validation.checks.instruction_paths import automatic
            changed, edits = automatic(original, diagnostic, oracle_passed)
            # Task-specific corrections take precedence over generic spans.
            protected = []
            for old, _ in INSTRUCTION_CORRECTIONS.get(task_id, ('', []))[1]:
                start = original.find(old)
                if start >= 0:
                    protected.append((start, start + len(old)))
            edits = [e for e in edits if not any(e['start'] < end and start < e['end'] for start, end in protected)]
            changed = original
            for edit in reversed(edits):
                changed = changed[:edit['start']] + edit['new'] + changed[edit['end']:]
        if task_id in INSTRUCTION_CORRECTIONS:
            digest, replacements = INSTRUCTION_CORRECTIONS[task_id]
            if hashlib.sha256(original.encode()).hexdigest() != digest:
                raise ValueError('Instruction changed since task-specific correction review')
            for old, new in replacements:
                if changed.count(old) != 1:
                    raise ValueError('Reviewed instruction correction is missing or conflicts with another edit')
                changed = changed.replace(old, new, 1)
                edits.append({'old': old, 'new': new, 'phase': 'explicit-instruction-review',
                              'reason': 'User-approved minimal path correction; preserve remaining task wording'})
        if not edits:
            return blob, []
        contents['instruction.md'] = changed.encode()
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode='w') as target:
            for member in members:
                member = copy.copy(member)
                if member.isfile():
                    data = contents[str(PurePosixPath(member.name))]
                    member.size = len(data)
                    target.addfile(member, io.BytesIO(data))
                else:
                    target.addfile(member)
        return output.getvalue(), edits


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True, help='new output directory')
    parser.add_argument('--resolution-report', type=Path, action='append', default=[])
    parser.add_argument('--automatic-paths', action='store_true', help='use shared automatic normalization, without a review file')
    parser.add_argument('--review', type=Path, help='review JSON: tasks maps IDs to decisions and unresolved reasons')
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('Use a new output directory')
    review = json.loads(args.review.read_text())['tasks'] if args.review else {}
    evidence = collect_reports(args.resolution_report)
    source = pq.read_table(args.source)
    rows = source.to_pylist()
    names = {row['path'] for row in rows}
    if set(review) - names:
        raise ValueError('Review contains tasks outside the source')
    labels, reasons, unresolved, changes = {}, {}, {}, {}
    for row in rows:
        name = row['path']
        decision = review.get(name, {})
        automatic_paths = args.automatic_paths and name in evidence and bool(evidence[name]['selected'].get('diagnostic', {}).get('agent_baseline', {}).get('task_sha256'))
        if automatic_paths or decision.get('replacements') or name in INSTRUCTION_CORRECTIONS:
            selected = evidence[name]['selected'] if automatic_paths or decision.get('replacements') else {}
            row['task_binary'], edits = patch_blob(
                row['task_binary'], selected.get('diagnostic'), decision.get('replacements', []),
                oracle_passed=selected.get('status') == 'passed' and bool(selected.get('rewards')) and all(r == 1 for r in selected['rewards']),
                task_id=name, automatic_paths=automatic_paths)
            if not edits:
                continue
            changes[name] = edits
            labels[name] = ['instruction-path-clarified']
            reasons[name] = ('User-approved minimal instruction correction' if name in INSTRUCTION_CORRECTIONS
                             else 'Absolute paths supported by task-setup/reference-solution observations')
        if decision.get('unresolved'):
            unresolved[name] = decision['unresolved']
    args.output.mkdir(parents=True)
    output = args.output / 'tasks.parquet'
    from validation.checks.path_cache import KEY, blob_fingerprint, completed
    cache = {row['path']: blob_fingerprint(row['task_binary']) for row in rows
             if row['path'] in evidence and completed(evidence[row['path']]['selected']['diagnostic'])}
    metadata = dict(source.schema.metadata or {})
    metadata[KEY] = json.dumps(cache).encode()
    pq.write_table(pa.Table.from_pylist(rows, schema=source.schema).replace_schema_metadata(metadata), output)
    (args.output / 'path-edits.json').write_text(json.dumps(changes, indent=2)+'\n')
    (args.output / 'unresolved-paths.json').write_text(json.dumps(unresolved, indent=2)+'\n')
    write_patch_report(args.source, output, patcher=__file__, source={
        'dataset': 'PrimeIntellect/Scale-SWE-Verified', 'revision': REVISION,
        'url': f'https://huggingface.co/datasets/PrimeIntellect/Scale-SWE-Verified/tree/{REVISION}'},
        dropped={}, change_labels=labels, change_reasons=reasons,
        patches=[{'version': 'absolute-paths-v1', 'resolver_reports': [str(p.resolve()) for p in args.resolution_report],
                  'automatic_paths': args.automatic_paths, 'review': str(args.review.resolve()) if args.review else None}])
    print(json.dumps({'tasks': len(rows), 'changed': len(changes), 'output': str(output)}))


if __name__ == '__main__':
    main()
