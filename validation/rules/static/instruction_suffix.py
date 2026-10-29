"""Stage-4 suffix fix-up, applied before contract hashing; originals stay unchanged."""
import copy
from contextlib import nullcontext
import gzip
import io
import json
from pathlib import PurePosixPath
import re
import shutil
import tarfile
import tomllib

from validation.data.materialize import parquet_files
from validation.data.selection import select_paths
from validation.data.selection import discover_tasks


def suffix(instruction, config):
    timeout = tomllib.loads(config.decode()).get('agent', {}).get('timeout_sec')
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0 or int(timeout) != timeout:
        raise ValueError('suffix normalization requires an explicit positive integer [agent].timeout_sec')
    sentence = f'You have {int(timeout)} seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.'
    text = instruction.decode().rstrip()
    # Replace only a canonical trailing suffix, including a stale timeout.
    text = re.sub(r'(?:\n\s*)?You have [0-9]+ seconds to complete this task\. Do not cheat by using online solutions or hints specific to this task\.$', '', text).rstrip()
    return (text + '\n\n' + sentence + '\n').encode()


def normalize_archive(blob, transform=None, repair=None):
    output = io.BytesIO()
    with tarfile.open(fileobj=io.BytesIO(blob), mode='r:*') as source:
        members = source.getmembers()
        for member in members:
            path = PurePosixPath(member.name)
            if path.is_absolute() or '..' in path.parts or not (member.isfile() or member.isdir()):
                raise ValueError('unsafe task archive')
        files = {str(PurePosixPath(m.name)): source.extractfile(m).read() for m in members if m.isfile()}
        normalized = transform(files['instruction.md'], files['task.toml']) if transform else suffix(files['instruction.md'], files['task.toml'])
        files['instruction.md'] = normalized
        if repair:
            files = repair(files)
        # Preserve compression for packaged-input tasks: expanding a task into
        # one raw Parquet value can exceed Parquet's signed 32-bit value size.
        stream = (gzip.GzipFile(fileobj=output, mode='wb', filename='', mtime=0, compresslevel=1)
                  if blob.startswith(b'\x1f\x8b') else nullcontext(output))
        with stream as encoded, tarfile.open(fileobj=encoded, mode='w') as target:
            for member in members:
                member = copy.copy(member)
                data = files.get(str(PurePosixPath(member.name)))
                if data is not None:
                    member.size = len(data)
                target.addfile(member, io.BytesIO(data) if data is not None else None)
            # Files a repair adds (setup_files/ot-task-setup.sh) take the mode of the file they replace.
            existing = {str(PurePosixPath(m.name)): m for m in members}
            for name in sorted(set(files) - set(existing)):
                member = tarfile.TarInfo(name)
                origin = existing.get('setup_files/setup.sh') if name.startswith('setup_files/') else None
                member.mode = origin.mode if origin is not None else 0o755 if name.endswith('.sh') else 0o644
                member.size = len(files[name])
                target.addfile(member, io.BytesIO(files[name]))
    return output.getvalue()


def record_resolved_paths(path, args):
    """Carry completed path discoveries of --path-resolution-report into a derived Parquet.

    Shards materialize this cache and skip rediscovery for exactly these task contents,
    as the unsharded discovery does with its own derived tasks.
    """
    reports = getattr(args, 'path_resolution_report', None) or []
    if not reports:
        return
    import pyarrow.parquet as pq
    from validation.rules.static.task_absolute_path.repair import collect_reports
    from validation.rules.static.task_absolute_path.path_cache import KEY, blob_fingerprint, completed
    evidence = collect_reports(reports)
    table = pq.read_table(path)
    metadata = dict(table.schema.metadata or {})
    cache = json.loads(metadata.get(KEY, b'{}'))
    for name, blob in zip(table.column('path').to_pylist(), table.column('task_binary').to_pylist()):
        entry = evidence.get(name)
        if entry and completed(entry['selected']['diagnostic']):
            cache[name] = blob_fingerprint(blob)
    metadata[KEY] = json.dumps(cache, sort_keys=True).encode()
    pq.write_table(table.replace_schema_metadata(metadata), path)


def prepare(args, destination, instructions=True):
    """Persist selected derived tasks and original provenance before contract hashing.

    instructions=False keeps instruction.md as it is (stage 4 not selected); the
    repository-leak repair follows --fix-repository-leaks either way.
    """
    from validation.contract import inventory, digest
    from validation.rules.dynamic.environment import repair as repository_repair
    original = args.tasks.resolve()
    records, files = inventory(original, args.limit, args.task_id_range)
    ids = {r['task_id'] for r in records}
    from validation.rules.static.task_absolute_path.instruction_paths import Normalizer
    paths = Normalizer(args, records)
    changes = {}
    repository_fix = getattr(args, 'fix_repository_leaks', True)
    repairs = {}
    def repair(task_id, files):
        if not repository_fix:
            return files
        repaired, repairs[task_id] = repository_repair.repair_files(files)
        if any(repaired.get(name) != files.get(name) for name in ('environment/Dockerfile', repository_repair.SETUP)):
            changes.setdefault(task_id, set()).add('repository-leak-repair')
        return repaired
    def prepared(task_id, files):
        """Hash of the task with every automatic fix except path edits: the copy a
        separate discovery run inspected."""
        from validation.contract import files_digest
        files = dict(files)
        if instructions and getattr(args, 'fix_instruction_suffix', True):
            files['instruction.md'] = suffix(files['instruction.md'], files['task.toml'])
        if repository_fix:
            files = repository_repair.repair_files(files)[0]
        paths.prepared[task_id] = files_digest(files.items())
    def transform(task_id, instruction, config):
        if not instructions:
            return instruction
        add_suffix = getattr(args, 'fix_instruction_suffix', True)
        with_suffix = suffix(instruction, config) if add_suffix else instruction
        normalized = paths.apply(task_id, instruction, with_suffix)
        labels = changes.setdefault(task_id, set())
        if normalized not in (instruction, with_suffix):
            labels.add('relative-path-fix')
        final = suffix(normalized, config) if add_suffix else normalized
        if with_suffix != instruction:
            labels.add('anti-cheat-instruction')
        return final
    destination.mkdir(parents=True, exist_ok=False)
    try:
        inputs = parquet_files(original)
        if inputs:
            import pyarrow as pa
            import pyarrow.parquet as pq
            for index, path in enumerate(inputs):
                writer = None
                try:
                    parquet = pq.ParquetFile(path)
                    # Several large task archives can exceed binary's 2 GiB
                    # cumulative offset limit within one Arrow batch.
                    schema = parquet.schema_arrow
                    binary_index = schema.get_field_index('task_binary')
                    schema = schema.set(binary_index, schema.field(binary_index).with_type(pa.large_binary()))
                    for batch in parquet.iter_batches(batch_size=32):
                        rows = [r for r in batch.to_pylist() if r['path'] in ids]
                        for row in rows:
                            if row['path'] in paths.evidence:
                                with tarfile.open(fileobj=io.BytesIO(row['task_binary']), mode='r:*') as archive:
                                    prepared(row['path'], {str(PurePosixPath(m.name)): archive.extractfile(m).read()
                                                           for m in archive.getmembers() if m.isfile()})
                            row['task_binary'] = normalize_archive(
                                row['task_binary'], lambda instruction, config: transform(row['path'], instruction, config),
                                lambda files: repair(row['path'], files))
                        if rows:
                            table = pa.Table.from_pylist(rows, schema=schema)
                            if writer is None:
                                writer = pq.ParquetWriter(destination / f'{index:04d}.parquet', table.schema)
                            writer.write_table(table, row_group_size=1 if table.nbytes >= 1 << 30 else None)
                finally:
                    if writer:
                        writer.close()
                if writer:
                    record_resolved_paths(destination / f'{index:04d}.parquet', args)
        else:
            selected_tasks = select_paths(discover_tasks(original), args)
            from validation.publishing.image_release import copy_references
            copy_references(selected_tasks, destination)
            from validation.rules.static.task_absolute_path.path_cache import copy_cache
            copy_cache(original, destination)
            for task in selected_tasks:
                if task.name in paths.evidence:
                    prepared(task.name, {p.relative_to(task).as_posix(): p.read_bytes() for p in task.rglob('*') if p.is_file()})
                target = destination / task.name
                shutil.copytree(task, target)
                (target / 'instruction.md').write_bytes(transform(task.name, (task / 'instruction.md').read_bytes(), (task / 'task.toml').read_bytes()))
                names = ('environment/Dockerfile', repository_repair.SETUP, repository_repair.TASK_SETUP)
                current = {n: (target / n).read_bytes() for n in names if (target / n).is_file()}
                repaired = repair(task.name, current)
                for name, data in repaired.items():
                    if current.get(name) != data:
                        (target / name).parent.mkdir(parents=True, exist_ok=True)
                        (target / name).write_bytes(data)
                        if name == repository_repair.TASK_SETUP:
                            shutil.copymode(target / repository_repair.SETUP, target / name)
                        elif name == repository_repair.SETUP and name not in current:
                            (target / name).chmod(0o755)
        meta_path = (original if original.is_dir() else original.parent) / 'source.json'
        meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
        prior = meta.get('automation_changes', {})
        meta['automation_changes'] = {task: sorted(set(prior.get(task, [])) | changes.get(task, set()))
                                      for task in sorted(ids)
                                      if prior.get(task) or changes.get(task)}
        meta['normalization'] = {'operation': 'canonical-timeout-anticheat-suffix-v1', 'original_path': str(original),
            'description': 'Our extension to the upstream Terminal-Bench suffix check appends the sentence when missing and overwrites the saved instruction.md in the validation copy; an existing canonical suffix is updated without duplication. Original inputs are unchanged.',
            'sentence_template': 'You have N seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.',
            'timeout_source': '[agent].timeout_sec', 'saved_dataset': str(destination.resolve()),
            'original_manifest_sha256': digest(records), 'original_source_files': files,
            'original_selection': {'limit': args.limit, 'task_id_range': args.task_id_range}}
        meta['normalization']['instruction_paths'] = paths.report
        if paths.report['evidence_reports']:
            meta['normalization']['operation'] = 'automatic-instruction-normalization-v1'
        if not getattr(args, 'fix_instruction_suffix', True):
            meta['normalization']['description'] = 'Automatic evidence-backed path replacements in a validation copy; original inputs unchanged.'
        meta['normalization']['suffix_enabled'] = instructions and getattr(args, 'fix_instruction_suffix', True)
        if not instructions:
            meta['normalization']['operation'] = 'no-instruction-normalization'
            meta['normalization']['description'] = 'Stage 4 not selected: instruction.md unchanged in the validation copy.'
        applied = sorted(t for t, r in repairs.items() if r['status'] == 'applied')
        meta['normalization']['repository_leak_repair'] = (
            {'enabled': True, 'operation': repository_repair.OPERATION, 'description': repository_repair.DESCRIPTION,
             'cleanup_setup': repository_repair.cleanup_setup(),
             'setup_wrapper': repository_repair.setup_wrapper(),
             'applied_after_setup': sum(r.get('placement') == 'setup' for r in repairs.values()),
             'applied': len(applied),
             'not_applied': {t: r['reason'] for t, r in sorted(repairs.items()) if r['status'] != 'applied'}}
            if repository_fix else {'enabled': False, 'reason': 'disabled with --no-fix-repository-leaks'})
        (destination / 'instruction-path-edits.json').write_text(json.dumps(paths.report, indent=2) + '\n')
        (destination / 'source.json').write_text(json.dumps(meta, indent=2) + '\n')
    except BaseException:
        shutil.rmtree(destination)
        raise
    args.tasks = destination
    # Selection is already resolved in the derived dataset.
    args.limit = None
    args.task_id_range = None
    return meta['normalization']
