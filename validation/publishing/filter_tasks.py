"""Filter a task/results Parquet pair; usable standalone with only pyarrow installed.

Example: python filter_tasks.py --judge qwen --experiment-1 --out experiment1/qwen
Optional judge and pass@4 filters. Warnings stay; missing/incomplete checks do not pass.
"""
import argparse
from collections import Counter
import hashlib
import io
import json
from pathlib import Path
import tarfile


# Recorded default-training checks through deterministic validation and leakage.
# Upstream-only conventions and AI detection (user policy) are intentionally absent.
REQUIRED_CHECKS = (
    'agent_editable_test', 'artifact_efficiency', 'build', 'compose-host-binds',
    'declared_internet_access', 'declared_workdir', 'do_not_modify',
    'dockerfile-platform', 'dockerfile-references', 'dockerfile-sanity',
    'instruction-suffix', 'no-cheat-dir', 'no_behavioural_tests', 'nop',
    'nop_determinism', 'nproc', 'pip-pinning', 'python_missing_imports',
    'reference', 'reference_determinism', 'reference_leaks', 'repository_history',
    'repository_timestamps', 'resource-sizes', 'script_execution_coverage',
    'task-absolute-path', 'task-timeout', 'task-toml-schema',
    'test-file-references', 'verifier_paths', 'verifier_tamper',
)
JUDGE_CRITERIA = (
    'ANSWER-LEAK', 'INSTR-AMBIGUOUS', 'INSTR-ENV-MISMATCH',
    'INSTR-VERIFIER-CONTRADICTION', 'INSTR-VERIFIER-UNSTATED-REQUIREMENT',
    'TASK-TRIVIAL', 'VERIFIER-ACCEPTS-INCOMPLETE', 'VERIFIER-ACCEPTS-SHORTCUTS',
)
JUDGES = {'qwen': 'Qwen/Qwen3.8-27B', 'glm': 'zai-org/GLM-5.3-Flash'}
PASSK_MODELS = {'qwen': 'passk_target', 'glm': 'passk_teacher'}
ACCEPTED = {'pass', 'warning', 'not_applicable'}
EXPERIMENT = {
    'clean': set(),
    'clean-plus-incomplete': {'VERIFIER-ACCEPTS-INCOMPLETE'},
    'clean-plus-shortcuts': {'VERIFIER-ACCEPTS-SHORTCUTS'},
}


def status(value):
    return value.get('status') if isinstance(value, dict) else value


def baseline_issues(row, checks=REQUIRED_CHECKS):
    return {key: status(row.get(key)) or 'missing' for key in checks
            if status(row.get(key)) not in ACCEPTED}


def judge_statuses(cell):
    """Read per-judge JSON cells and legacy maps without changing review selection."""
    if isinstance(cell, str):
        cell = json.loads(cell)
    if not isinstance(cell, dict):
        return {}
    old = cell.get('verdicts', cell.get('status'))
    if isinstance(old, (dict, list)):
        return dict(old)
    return {model: [r['status'] for r in value['reviews']] if 'reviews' in value
            else [value['status']]
            for model, value in cell.items()
            if isinstance(value, dict) and 'status' in value}


def judge_failures(row, judge, criteria=JUDGE_CRITERIA):
    """Return failed criteria, or None when any required review is unresolved.

    If several reviews exist, any fail counts as a fail. Unknown/missing verdicts
    exclude the task even when another review passed. Other models never vote.
    """
    failed = set()
    for criterion in criteria:
        cell = row.get(criterion) or {}
        reviews = judge_statuses(cell).get(JUDGES.get(judge, judge), [])
        if not reviews or any(v not in ACCEPTED | {'fail'} for v in reviews):
            return None
        if 'fail' in reviews:
            failed.add(criterion)
    return failed


def passk_successes(row, model):
    """Successful attempts in the selected complete four-attempt batch only."""
    cell = row.get(PASSK_MODELS[model])
    if not isinstance(cell, dict) or cell.get('status') not in ('pass', 'fail'):
        return None
    details = cell.get('details')
    if isinstance(details, str):
        try:
            details = json.loads(details)
        except ValueError:
            return None
    if not isinstance(details, dict) or details.get('k') != 4:
        return None
    batch = details.get('selected_batch')
    if not isinstance(batch, dict):
        return None
    successes = batch.get('successes')
    if type(batch.get('valid_attempts')) is not int or batch['valid_attempts'] != 4 or type(successes) is not int or not 0 <= successes <= 4:
        return None
    return successes


def validate_passk_filter(model, successes):
    if model is None and successes is None:
        return
    if model not in PASSK_MODELS or not successes:
        raise ValueError('--passk-model qwen|glm and --successes must be supplied together')
    if any(type(n) is not int or not 0 <= n <= 4 for n in successes):
        raise ValueError('--successes must contain integers from 0 to 4')


def select(rows, judge=None, allow_fail=(), criteria=JUDGE_CRITERIA,
           passk_model=None, successes=None):
    validate_passk_filter(passk_model, successes)
    allowed = set(allow_fail)
    if allowed - set(criteria):
        raise ValueError('Allowed failure must be one of the selected judge criteria')
    selected, reasons = {}, Counter()
    seen = set()
    for row in rows:
        name = row['task_id']
        if name in seen:
            raise ValueError(f'Duplicate result task ID: {name}')
        seen.add(name)
        issues = baseline_issues(row)
        if issues:
            reasons['validation_failed' if 'fail' in issues.values() else 'validation_incomplete'] += 1
            continue
        if judge:
            failures = judge_failures(row, judge, criteria)
            if failures is None:
                reasons['judge_incomplete'] += 1
                continue
            if failures - allowed:
                reasons['judge_failed_other_criteria'] += 1
                continue
        if passk_model:
            wins = passk_successes(row, passk_model)
            if wins is None:
                reasons['passk_incomplete'] += 1
                continue
            if wins not in successes:
                reasons['passk_successes_not_selected'] += 1
                continue
        selected[name] = row
    return selected, dict(reasons)


def payload_hash(binary):
    files = {}
    with tarfile.open(fileobj=io.BytesIO(binary), mode='r:*') as archive:
        for member in archive:
            name = Path(member.name).as_posix()
            if Path(name).is_absolute() or '..' in Path(name).parts:
                raise ValueError('Unsafe task archive path')
            if member.isfile():
                if name in files:
                    raise ValueError('Duplicate task archive path')
                files[name] = archive.extractfile(member).read()
            elif not member.isdir():
                raise ValueError('Unsupported task archive member')
    digest = hashlib.sha256()
    for name, data in sorted(files.items()):
        name = name.encode()
        digest.update(len(name).to_bytes(8, 'big') + name)
        digest.update(len(data).to_bytes(8, 'big') + data)
    return digest.hexdigest()


def subset_metadata(metadata, ids):
    metadata = dict(metadata or {})
    if b'ot.images.v1' in metadata:
        manifest = json.loads(metadata[b'ot.images.v1'])
        manifest['tasks'] = {k: v for k, v in manifest['tasks'].items() if k in ids}
        bundles = {e['bundle'] for t in manifest['tasks'].values() for e in t['environments']}
        manifest['bundles'] = {k: v for k, v in manifest['bundles'].items() if k in bundles}
        manifest['missing'] = [r for r in manifest.get('missing', []) if r.get('task') in ids]
        metadata[b'ot.images.v1'] = json.dumps(manifest, separators=(',', ':')).encode()
    return metadata


def export(tasks, results, out, judge=None, allow_fail=(), experiment2=False,
           criteria=JUDGE_CRITERIA, passk_model=None, successes=None):
    import pyarrow as pa
    import pyarrow.parquet as pq
    tasks, results, out = Path(tasks), Path(results), Path(out)
    validate_passk_filter(passk_model, successes)
    if experiment2 and (not judge or allow_fail):
        raise ValueError('--experiment-1 requires --judge and cannot use --allow-fail')
    if allow_fail and not judge:
        raise ValueError('--allow-fail requires --judge')
    table = pq.read_table(results)
    if passk_model and PASSK_MODELS[passk_model] not in table.column_names:
        raise ValueError('Missing pass@4 column: ' + PASSK_MODELS[passk_model])
    absent = set(REQUIRED_CHECKS) - set(table.column_names)
    if absent:
        raise ValueError('Missing required validation columns: ' + ', '.join(sorted(absent)))
    if judge and set(criteria) - set(table.column_names):
        raise ValueError('Unknown judge criteria: ' + ', '.join(sorted(set(criteria) - set(table.column_names))))
    rows = table.to_pylist()
    if judge:
        available = {model for row in rows for criterion in criteria
                     for model in judge_statuses(row.get(criterion))}
        if JUDGES.get(judge, judge) not in available:
            raise ValueError('No verdicts for judge ' + judge + '; available: ' + ', '.join(sorted(available)))
    variants = EXPERIMENT if experiment2 else {'selected': set(allow_fail)}
    groups = {name: select(rows, judge, allowed, criteria, passk_model, successes)
              for name, allowed in variants.items()}
    source = pq.ParquetFile(tasks)
    schemas, writers, paths = {}, {}, {}
    for name, (selected, _) in groups.items():
        folder = out / name if experiment2 else out
        if (folder / 'tasks.parquet').exists() or (folder / 'results.parquet').exists():
            raise FileExistsError(f'Output already exists: {folder}')
        folder.mkdir(parents=True, exist_ok=True)
        paths[name] = folder
        schemas[name] = source.schema_arrow.with_metadata(subset_metadata(source.schema_arrow.metadata, set(selected)))
        writers[name] = pq.ParquetWriter(folder / 'tasks.parquet.partial', schemas[name])
    seen, written = set(), {name: set() for name in groups}
    try:
        for batch in source.iter_batches(batch_size=16):
            outputs = {name: [] for name in groups}
            for row in batch.to_pylist():
                task = row['path']
                if task in seen:
                    raise ValueError(f'Duplicate task payload: {task}')
                seen.add(task)
                matches = [name for name, (selected, _) in groups.items() if task in selected]
                if not matches:
                    continue
                digest = payload_hash(row['task_binary'])
                if row.get('content_sha256') and row['content_sha256'] != digest:
                    raise ValueError(f'Task content hash mismatch: {task}')
                for name in matches:
                    if groups[name][0][task].get('content_sha256') != digest:
                        raise ValueError(f'Results describe another task version: {task}')
                    outputs[name].append(row)
                    written[name].add(task)
            for name, output in outputs.items():
                if output:
                    writers[name].write_table(pa.Table.from_pylist(output, schema=schemas[name]))
        for name, (selected, _) in groups.items():
            if set(selected) - written[name]:
                raise ValueError(f'{name}: selected results have no task payload')
    finally:
        for writer in writers.values():
            writer.close()
    def file_identity(path):
        with path.open('rb') as stream:
            return {'path': str(path), 'sha256': hashlib.file_digest(stream, 'sha256').hexdigest()}
    summary = {'inputs': {'tasks': file_identity(tasks), 'results': file_identity(results)},
               'judge': JUDGES.get(judge, judge), 'validation_checks': list(REQUIRED_CHECKS),
               'judge_criteria': list(criteria) if judge else [],
               'passk_filter': {'model': passk_model, 'column': PASSK_MODELS[passk_model],
                                'k': 4, 'successes': sorted(set(successes))} if passk_model else None,
               'warnings_retained': True, 'incomplete_excluded': True, 'groups': {}}
    for name, (selected, excluded) in groups.items():
        folder = paths[name]
        pq.write_table(pa.Table.from_pylist([selected[n] for n in sorted(written[name])], schema=table.schema),
                       folder / 'results.parquet')
        (folder / 'tasks.parquet.partial').rename(folder / 'tasks.parquet')
        summary['groups'][name] = {'tasks': len(written[name]), 'allowed_failures': sorted(variants[name]),
                                  'excluded': excluded, 'selection': sorted(written[name])}
    out.mkdir(parents=True, exist_ok=True)
    (out / 'selection.json').write_text(json.dumps(summary, indent=2) + '\n')
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tasks', type=Path, default=Path('tasks.parquet'))
    parser.add_argument('--results', type=Path, default=Path('results.parquet'))
    parser.add_argument('--judge', help='qwen, glm, or exact model ID; omitted = validation only')
    parser.add_argument('--allow-fail', action='append', default=[], metavar='CRITERION',
                        help='Allow this judge criterion to fail; all others must pass (repeatable)')
    parser.add_argument('--criteria', nargs='+', default=JUDGE_CRITERIA,
                        help='Judge criteria to enforce; default = all eight adjusted-rubric criteria')
    parser.add_argument('--experiment-1', '--experiment-2', dest='experiment_2', action='store_true',
                        help='Export all three Experiment 1 groups')
    parser.add_argument('--passk-model', choices=PASSK_MODELS,
                        help='Filter completed pass@4 results for Qwen3.5-35B-A3B or GLM-5.3-Flash')
    parser.add_argument('--successes', type=int, nargs='+', choices=range(5),
                        help='Keep these numbers of successful attempts out of four, requires --passk-model')
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    try:
        validate_passk_filter(args.passk_model, args.successes)
    except ValueError as exc:
        parser.error(str(exc))
    summary = export(args.tasks, args.results, args.out, args.judge, args.allow_fail,
                     args.experiment_2, args.criteria, args.passk_model, args.successes)
    print(json.dumps({k: v['tasks'] for k, v in summary['groups'].items()}, indent=2))


if __name__ == '__main__':
    main()
