"""Import fresh, source-bound image preparation failures without building in validation.

The manifest is an index of measured preparation records, never expected verdicts.
Every use verifies the original records and logs. Only matching environment bytes
can block a container trial; other tasks must still pass the prebuilt-image guard.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def load(path):
    document = json.loads(Path(path).read_text())
    if document.get('format') != 'image-preparation-evidence-v1':
        raise ValueError('Unsupported image preparation evidence')
    entries = {}
    for source in document['records']:
        record_path = Path(source['path'])
        if sha(record_path) != source['sha256']:
            raise ValueError('Image preparation record changed: ' + str(record_path))
        for record in json.loads(record_path.read_text()):
            if record.get('status') != 'error':
                continue
            task = record.get('task_id')
            if not task or Path(task).name != task:
                raise ValueError('Missing or unsafe preparation task ID')
            for field in ('source_task_sha256', 'environment_sha256', 'builder_sha256',
                          'bridge_patch_sha256', 'directory_builder_sha256', 'log_sha256'):
                if not re.fullmatch('[a-f0-9]{64}', record.get(field, '')):
                    raise ValueError('Missing preparation binding: ' + field)
            if not record.get('command') or not record.get('started_at') or not record.get('node'):
                raise ValueError('Missing actual preparation invocation')
            if sha(record['log']) != record['log_sha256']:
                raise ValueError('Image preparation log changed: ' + record['log'])
            if record.get('reconstructed_provenance'):
                for binding in record['reconstructed_provenance']['bindings']:
                    if sha(binding['path']) != binding['sha256']:
                        raise ValueError('Reconstructed preparation input changed: ' + binding['path'])
            if record.get('key') != record['environment_sha256'][:12]:
                raise ValueError('Preparation environment key mismatch')
            entries.setdefault(task, []).append(dict(record, evidence_record=str(record_path)))
    return entries


def failures(tasks, path):
    if not path:
        return {}
    from harbor_patches.image_context import task_dockerfiles, environment_dir_hash
    from validation.outcomes import INFRA, OOM, TIMEOUT
    entries = load(path)
    results = {}
    for task in tasks:
        records = entries.get(task.name, [])
        if not records:
            continue
        hashes = {environment_dir_hash(p.parent) for p in task_dockerfiles(task)}
        if any(r['environment_sha256'] not in hashes for r in records):
            raise ValueError('Preparation environment differs from validation task: ' + task.name)
        logs = '\n'.join(Path(r['log']).read_text(errors='replace') for r in records)
        # Resource/scheduler failures and invocations without a process exit are
        # incomplete. A nonzero image-builder exit is an observed BUILD failure.
        category = ('oom' if OOM.search(logs) else 'timeout' if TIMEOUT.search(logs)
                    else 'infrastructure_error' if INFRA.search(logs)
                    else 'execution_error' if any(type(r.get('returncode')) is not int or
                                                  r['returncode'] <= 0 for r in records) else None)
        incomplete = category is not None
        status = 'error' if incomplete else 'failed'
        results[task.name] = dict(task=str(task), status=status, build_status=status,
            cause=('infrastructure-error' if category == 'infrastructure_error' else
                   'validation-not-run' if incomplete else 'build-execution-error'),
            reason=('Image preparation did not complete; see bound preparation logs' if incomplete else
                    'Image build command exited unsuccessfully; see bound preparation logs'),
            image_preparation=records, image_preparation_manifest=str(Path(path).resolve()),
            image_preparation_manifest_sha256=sha(path),
            infrastructure_error=category == 'infrastructure_error',
            seconds=sum(r.get('seconds', 0) for r in records),
            node=','.join(sorted({r['node'] for r in records})))
        if category:
            results[task.name].update(classification_version=1, category=category,
                                     reason_code='image_preparation_' + category, phase='image_build')
    return results


def freeze(paths, destination):
    destination = Path(destination)
    document = dict(format='image-preparation-evidence-v1', records=[
        dict(path=str(Path(p).resolve()), sha256=sha(p)) for p in sorted(set(paths))])
    with destination.open('x') as stream:
        stream.write(json.dumps(document, indent=2) + '\n')
    load(destination)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--record', type=Path, action='append', required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    freeze(args.record, args.out)
