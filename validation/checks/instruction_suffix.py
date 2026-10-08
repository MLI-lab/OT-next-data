"""Stage-1 suffix fix-up, applied before contract hashing; originals stay unchanged."""
import copy
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


def normalize_archive(blob, transform=None):
    output = io.BytesIO()
    with tarfile.open(fileobj=io.BytesIO(blob), mode='r:*') as source:
        members = source.getmembers()
        for member in members:
            path = PurePosixPath(member.name)
            if path.is_absolute() or '..' in path.parts or not (member.isfile() or member.isdir()):
                raise ValueError('unsafe task archive')
        files = {str(PurePosixPath(m.name)): source.extractfile(m).read() for m in members if m.isfile()}
        normalized = transform(files['instruction.md'], files['task.toml']) if transform else suffix(files['instruction.md'], files['task.toml'])
        with tarfile.open(fileobj=output, mode='w') as target:
            for member in members:
                member = copy.copy(member)
                data = normalized if str(PurePosixPath(member.name)) == 'instruction.md' else files.get(str(PurePosixPath(member.name)))
                if data is not None:
                    member.size = len(data)
                target.addfile(member, io.BytesIO(data) if data is not None else None)
    return output.getvalue()


def prepare(args, destination):
    """Persist selected derived tasks and original provenance before contract hashing."""
    from validation.contract import inventory, digest
    original = args.tasks.resolve()
    records, files = inventory(original, args.limit, args.task_id_range)
    ids = {r['task_id'] for r in records}
    from validation.checks.instruction_paths import Normalizer
    paths = Normalizer(args, records)
    changes = {}
    def transform(task_id, instruction, config):
        normalized = paths.apply(task_id, instruction)
        labels = changes.setdefault(task_id, set())
        if normalized != instruction:
            labels.add('relative-path-fix')
        final = suffix(normalized, config) if getattr(args, 'fix_instruction_suffix', True) else normalized
        if final != normalized:
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
                    for batch in parquet.iter_batches(batch_size=32):
                        rows = [r for r in batch.to_pylist() if r['path'] in ids]
                        for row in rows:
                            row['task_binary'] = normalize_archive(row['task_binary'], lambda instruction, config: transform(row['path'], instruction, config))
                        if rows:
                            table = pa.Table.from_pylist(rows, schema=parquet.schema_arrow)
                            if writer is None:
                                writer = pq.ParquetWriter(destination / f'{index:04d}.parquet', table.schema)
                            writer.write_table(table)
                finally:
                    if writer:
                        writer.close()
        else:
            selected_tasks = select_paths(discover_tasks(original), args)
            from validation.publishing.image_release import copy_references
            copy_references(selected_tasks, destination)
            from validation.checks.path_cache import copy_cache
            copy_cache(original, destination)
            for task in selected_tasks:
                target = destination / task.name
                shutil.copytree(task, target)
                (target / 'instruction.md').write_bytes(transform(task.name, (task / 'instruction.md').read_bytes(), (task / 'task.toml').read_bytes()))
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
        meta['normalization']['suffix_enabled'] = getattr(args, 'fix_instruction_suffix', True)
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
