"""Restore only evidence-backed instructions from an immutable historical release."""
from pathlib import Path, PurePosixPath
import hashlib
import io
import json
import shutil
import tarfile

import pyarrow as pa
import pyarrow.parquet as pq
from validation.contract import files_digest


def files(blob):
    result = {}
    with tarfile.open(fileobj=io.BytesIO(blob), mode='r:*') as archive:
        for member in archive:
            path = PurePosixPath(member.name)
            if path.is_absolute() or '..' in path.parts or not (member.isfile() or member.isdir()):
                raise ValueError('Unsafe historical archive member')
            if member.isfile():
                if str(path) in result:
                    raise ValueError('Duplicate historical archive member')
                result[str(path)] = (archive.extractfile(member).read(), member.mode)
    return result


def restore(current, historical, archive, record, dataset, revision, output):
    """Keep current runtime bytes/modes and image locations; restore recorded instructions.

    Historical archives must differ only in instruction.md. The caller pins and
    verifies historical input files against the stated immutable HF revision.
    """
    import re
    if not re.fullmatch('[0-9a-f]{40}', revision):
        raise ValueError('An immutable historical publication revision is required')
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    run = json.loads(Path(record).read_text())
    provenance = run['patch_provenance'][dataset]
    changes = {x['task_id']: x for x in provenance['tasks']}
    automatic = {x['task_id']: x for x in run['automation_changes'][dataset]['tasks']}
    old = {r['path']: r for b in pq.ParquetFile(historical).iter_batches(batch_size=32) for r in b.to_pylist()}
    source = pq.ParquetFile(current)
    metadata = dict(source.schema_arrow.metadata or {})
    images = json.loads(metadata[b'ot.images.v1']) if b'ot.images.v1' in metadata else None
    audit = []
    rows = []
    for batch in source.iter_batches(batch_size=32):
        for row in batch.to_pylist():
            name = row['path']
            prior = old.pop(name)
            before, after = files(row['task_binary']), files(prior['task_binary'])
            if {k:v for k,v in before.items() if k != 'instruction.md'} != {k:v for k,v in after.items() if k != 'instruction.md'}:
                raise ValueError('Runtime differs from historical evidence: ' + name)
            for payload, values in [(row, before), (prior, after)]:
                if payload['content_sha256'] != files_digest((k,v[0]) for k,v in values.items()):
                    raise ValueError('Invalid content digest: ' + name)
            if prior['stages_passed'] != '1,3,4,5' or set(automatic[name]['labels']) != {'anti-cheat-instruction'}:
                raise ValueError('Unreviewed or incomplete historical evidence: ' + name)
            if images:
                if images['tasks'][name]['content_sha256'] != row['content_sha256']:
                    raise ValueError('Image task binding mismatch: ' + name)
                images['tasks'][name]['content_sha256'] = prior['content_sha256']
            audit.append({'task_id': name, 'before_content_sha256': row['content_sha256'],
                          'content_sha256': prior['content_sha256'], 'labels': prior['patch_labels'],
                          'path_repair': 'instruction-path-clarified' in changes[name]['labels']})
            for key in ['task_binary', 'content_sha256', 'stages_passed', 'patch_changed', 'patch_labels']:
                row[key] = prior[key]
            row['run'] = f'https://huggingface.co/datasets/FWeindel/validated-tasks/blob/{revision}/runs/{run["run"]}.json'
            rows.append(row)
    if old:
        raise ValueError('Historical/current retained task sets differ')
    summary = {'historical_revision': revision, 'historical_run': run['run'],
               'retained': len(rows), 'archived': pq.ParquetFile(archive).metadata.num_rows,
               'changed_retained': len(rows), 'instruction_suffix_restored': len(rows),
               'instruction_path_repairs_restored': sum(x['path_repair'] for x in audit),
               'static_archived': 0, 'fresh_runtime_validation': False,
               'runtime_files_and_modes_identical_to_historical': True,
               'historical_stages': [1,3,4,5]}
    metadata.pop(b'ot.environment_reliable.v1', None)
    metadata[b'ot.full_release.v1'] = json.dumps(summary).encode()
    if images:
        metadata[b'ot.images.v1'] = json.dumps(images).encode()
        (output/'cached_images').mkdir()
        (output/'cached_images'/'manifest.json').write_text(json.dumps(images, indent=2)+'\n')
    pq.write_table(pa.Table.from_pylist(rows, schema=source.schema_arrow.with_metadata(metadata)), output/'tasks.parquet', compression='zstd')
    archived = pq.read_table(archive)
    if 'run' in archived.column_names:
        index = archived.column_names.index('run')
        archived = archived.set_column(index, archived.schema.field(index), pa.array(
            [f'https://huggingface.co/datasets/FWeindel/validated-tasks/blob/{revision}/runs/{run["run"]}.json'] * archived.num_rows,
            type=archived.schema.field(index).type))
    archive_metadata = dict(archived.schema.metadata or {})
    archive_metadata.pop(b'ot.environment_reliable.v1', None)
    archive_metadata[b'ot.full_release.v1'] = json.dumps(summary).encode()
    pq.write_table(archived.replace_schema_metadata(archive_metadata), output/'archive.parquet', compression='zstd')
    (output.parent/'restoration-audit.json').write_text(json.dumps({'summary':summary, 'tasks':audit}, indent=2)+'\n')
    return summary
