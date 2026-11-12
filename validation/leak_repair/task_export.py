"""Export the task bytes corresponding exactly to finalized stage-4 results."""
import json
from pathlib import Path
import tempfile


def stage_reports(results):
    """Reports beside a normal or merged results file; refuse ambiguous stages."""
    reports = {}
    for path in sorted(Path(results).parent.glob('stage-*.json')):
        report = json.loads(path.read_text())
        stage = report.get('stage')
        if stage not in (0, 1, 2, 3):
            continue
        if stage in reports:
            raise ValueError(f'ambiguous stage {stage} reports for task export: {results}')
        if report.get('dry_run') or not report.get('complete'):
            raise ValueError(f'incomplete stage report for task export: {path}')
        reports[stage] = report
    return reports


def export_tasks(request, report):
    """Write a new, atomic Parquet for the final cohort; never mutate task inputs.

    Unchanged payloads are copied byte-for-byte. Changed payloads are replayed
    from the same recorded leak/path/pip repairs as publishing. Every payload's
    digest must match its final results row; missing or drifting repairs abort
    the export rather than attaching old task bytes to new results.
    """
    import pyarrow as pa
    import pyarrow.parquet as pq
    from validation.run import read
    from validation.contract import read as read_contract, files_digest
    from validation.automatic_repairs import archive_files, plan, replace_in_archive
    from validation.leak_repair.tool import repaired_loader, leak_modules
    from validation.publishing.publish import task_rows

    out = Path(request['out'])
    results_path = out / 'results.parquet'
    rows = read(results_path)
    expected = {r['task_id']: r['content_sha256'] for r in rows}
    if len(expected) != len(rows) or any(not h for h in expected.values()):
        raise ValueError('task export requires unique task IDs and content hashes')
    changed = set(report.get('changed') or [])
    if changed - expected.keys():
        raise ValueError('changed tasks missing from finalized results')
    sources = [(request['tasks'], report.get('input_results') or request.get('results'), set(expected) - changed)]
    if changed:
        rerun = out / 'rerun'
        contract = read_contract(rerun / 'contract.json')
        sources.append((contract['arguments']['tasks'], rerun / 'merged/results.parquet', changed))
    target = out / 'repaired-tasks.parquet'
    if any(Path(source).resolve() == target.resolve() for source, _, _ in sources):
        raise ValueError('task export must not overwrite its source')
    schema = pa.schema([('path', pa.string()), ('task_binary', pa.large_binary()),
                        ('content_sha256', pa.string())])
    modules = None
    seen, modified = set(), []
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=out, prefix='.repaired-tasks-', suffix='.parquet', delete=False) as file:
        pending = Path(file.name)
    try:
        with pq.ParquetWriter(pending, schema) as writer:
            for source, result_source, wanted in sources:
                reports = None
                batch = []
                for task, binary in task_rows(source):
                    if task not in wanted:
                        continue
                    if task in seen:
                        raise ValueError(f'duplicate task payload: {task}')
                    files = archive_files(binary)
                    digest = files_digest(files.items())
                    if digest != expected[task]:
                        if reports is None:
                            if not result_source:
                                raise ValueError('no stage reports available to reconstruct changed tasks')
                            reports = stage_reports(result_source)
                        if modules is None:
                            modules = leak_modules(Path(request['dataset_dir']).parent)
                        load = repaired_loader(lambda names: {task: files} if task in names else {},
                                               reports, request['dataset'], modules)
                        repaired = load([task])[task]
                        plans = plan(reports, load)
                        repaired = plans.get(task, {}).get('files', repaired)
                        digest = files_digest(repaired.items())
                        if digest != expected[task]:
                            raise ValueError(f'exported task hash differs from final results: {task}')
                        binary = replace_in_archive(binary, repaired)
                        # Verify the archive itself, not just the dictionary used to write it.
                        if files_digest(archive_files(binary).items()) != digest:
                            raise ValueError(f'exported archive hash mismatch: {task}')
                        modified.append(task)
                    seen.add(task)
                    batch.append({'path': task, 'task_binary': binary, 'content_sha256': digest})
                    if len(batch) >= 32:
                        writer.write_table(pa.Table.from_pylist(batch, schema=schema)); batch.clear()
                if batch:
                    writer.write_table(pa.Table.from_pylist(batch, schema=schema))
            if seen != set(expected):
                raise ValueError(f'task export missing payloads: {sorted(set(expected) - seen)[:10]}')
        pending.replace(target)
    finally:
        pending.unlink(missing_ok=True)
    return {'path': str(target), 'tasks': len(seen), 'rewritten_payloads': sorted(modified),
            'results': str(results_path), 'hashes_verified': True}
