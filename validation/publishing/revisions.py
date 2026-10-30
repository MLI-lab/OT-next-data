"""Publish task payload deltas and one results index, without deleting history."""
import json
from pathlib import Path

from validation import run as results


class History:
    """Read one pinned remote snapshot; absence is distinct from access failure."""
    def __init__(self, repo=None, root=None):
        self.repo, self.root, self.revision = repo, Path(root) if root else None, None
        self.cache = {}
        if repo:
            from huggingface_hub import HfApi
            self.revision = HfApi().dataset_info(repo).sha

    def file(self, name):
        if self.root:
            path = self.root / name
            return path if path.is_file() else None
        if not self.repo:
            return None
        from huggingface_hub import hf_hub_download
        from huggingface_hub.utils import EntryNotFoundError
        try:
            return Path(hf_hub_download(self.repo, name, repo_type='dataset', revision=self.revision))
        except EntryNotFoundError:
            return None

    def load(self, folder):
        if folder in self.cache:
            return self.cache[folder]
        path = self.file(folder + '/results.parquet')
        if path:
            value = (results.read(path), [])
        else:
            # First publication in the new format preserves every legacy payload,
            # including tasks absent from this run.
            import pyarrow.parquet as pq
            rows, legacy = [], []
            for name in ('tasks.parquet', 'archive.parquet'):
                path = self.file(folder + '/' + name)
                if not path:
                    continue
                legacy.append(path)
                columns = pq.read_schema(path).names
                required = {'path', 'content_sha256'}
                if not required <= set(columns):
                    raise ValueError(f'{path}: legacy migration needs task IDs and content hashes')
                for batch in pq.ParquetFile(path).iter_batches(columns=[c for c in columns if c != 'task_binary']):
                    for task in batch.to_pylist():
                        row = {r: 'not_recorded' for r in results.known_rules()}
                        row.update(dataset=folder, task_id=task['path'], content_sha256=task['content_sha256'],
                                   revision=1, latest=True, change_reason='Imported legacy task',
                                   task_file=f'{folder}/tasks/r1.parquet',
                                   publication_details=results.encode(task))
                        rows.append(row)
            if len({r['task_id'] for r in rows}) != len(rows):
                raise ValueError('Legacy files contain duplicate task IDs; reconcile their revisions before migration')
            value = (rows, legacy)
        self.cache[folder] = value
        return value


def internet_setting(binary):
    """Read the declared task setting without extracting or executing the payload."""
    import io
    import tarfile
    import tomllib
    try:
        with tarfile.open(fileobj=io.BytesIO(binary), mode='r:*') as archive:
            members = [member for member in archive.getmembers()
                       if member.isfile() and member.name.removeprefix('./') == 'task.toml']
            if len(members) != 1:
                return 'unknown'
            stream = archive.extractfile(members[0])
            config = tomllib.loads(stream.read().decode())
            value = config.get('environment', {}).get('allow_internet')
            return 'true' if value is True else 'false' if value is False else 'unspecified'
    except (tarfile.TarError, UnicodeError, ValueError):
        return 'unknown'


def write(tables, record, run_file, out, reports, contract, history, cards=None, image_manifest=None, release_specs=None):
    import pyarrow as pa
    import pyarrow.parquet as pq
    from validation.publishing.parquet import write_rows
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    files = []
    release_specs = release_specs or {}
    if set(release_specs) - set(tables):
        raise ValueError('Release spec refers to unknown output folders')
    record['publication_layout'] = 'task-revisions-v1'
    record['result_files'], record['task_revision_files'] = {}, {}
    schema = pa.schema([('path', pa.string()), ('task_binary', pa.large_binary()),
                        ('content_sha256', pa.string()), ('revision', pa.int64())])
    if image_manifest:
        schema = schema.with_metadata({b'ot.images.v1': results.encode(image_manifest).encode()})
    for folder, (kept, archived) in sorted(tables.items()):
        payloads = [*kept, *archived]
        previous, legacy = history.load(folder)
        hashes = {r['path']: r['content_sha256'] for r in payloads}
        observed = results.observations(reports, hashes, folder, contract.get('arguments', {}))
        # Evidence from an earlier input revision is not evidence for repaired bytes.
        observed = [r for r in observed if hashes.get(r['task_id']) == r['content_sha256']]
        incoming = {(r['task_id'], r['content_sha256']): r for r in observed}
        for task in payloads:
            key = task['path'], task['content_sha256']
            row = incoming.setdefault(key, {'dataset': folder, 'task_id': key[0], 'content_sha256': key[1]})
            row['allow_internet'] = internet_setting(task['task_binary'])
            row['run'] = run_file
            row['publication_details'] = results.encode({k: v for k, v in task.items() if k != 'task_binary'})
            if task.get('patch_labels'):
                row['change_reason'] = ', '.join(task['patch_labels'])
        merged = results.merge(previous, list(incoming.values()))
        existing = {(r['task_id'], r['content_sha256']) for r in previous}
        new = [r for r in merged if (r['task_id'], r['content_sha256']) not in existing]
        # Revision files are dataset-wide batches; each row also has its per-task revision.
        last_file = max([int(Path(r['task_file']).stem[1:]) for r in previous if r.get('task_file')], default=0)
        delta_file = f'{folder}/tasks/r{last_file + 1}.parquet'
        new_keys = {(r['task_id'], r['content_sha256']): r for r in new}
        for row in new:
            row['task_file'] = delta_file
        if legacy:
            path = out / folder / 'tasks/r1.parquet'
            path.parent.mkdir(parents=True, exist_ok=True)
            def originals():
                for source in legacy:
                    for batch in pq.ParquetFile(source).iter_batches(batch_size=1):
                        for row in batch.to_pylist():
                            yield {k: row.get(k) for k in ('path', 'task_binary', 'content_sha256')} | {'revision': 1}
            write_rows(originals(), path, schema)
            files.append(f'{folder}/tasks/r1.parquet')
        if new:
            path = out / delta_file
            path.parent.mkdir(parents=True, exist_ok=True)
            def changed():
                for task in payloads:
                    row = new_keys.get((task['path'], task['content_sha256']))
                    if row:
                        yield {k: task[k] for k in ('path', 'task_binary', 'content_sha256')} | {'revision': row['revision']}
            write_rows(changed(), path, schema)
            files.append(delta_file)
        index = f'{folder}/results.parquet'
        results.write(out / index, merged)
        files.append(index)
        record['result_files'][folder] = index
        record['task_revision_files'][folder] = sorted({r['task_file'] for r in merged if r.get('task_file')})
    for folder, card in (cards or {}).items():
        path = out / folder / 'README.md'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(card['readme'])
        files.append(f'{folder}/README.md')
        annotation_path = path.with_name('annotation.json')
        annotation_path.write_text(results.encode({k: v for k, v in card.items() if k != 'readme'}) + '\n')
        files.append(f'{folder}/annotation.json')
    from validation.publishing.readme import write as write_readme
    for folder, spec in release_specs.items():
        annotation = (cards or {}).get(folder)
        if spec.get('annotation'):
            annotation = json.loads(Path(spec['annotation']).read_text())
        if annotation is None:
            saved = history.file(folder + '/annotation.json')
            if saved:
                annotation = json.loads(saved.read_text())
        if annotation:
            (out / folder / 'annotation.json').write_text(results.encode(annotation) + '\n')
            if f'{folder}/annotation.json' not in files:
                files.append(f'{folder}/annotation.json')
        metadata = json.loads(Path(spec['run']).read_text())
        view = spec['view']
        destination = write_readme(out / folder / 'results.parquet', metadata, out / folder, view, folder,
                                   annotation, spec.get('evaluation_key'), spec.get('student_key'))
        files.extend(path.relative_to(out).as_posix() for path in destination.iterdir() if path.is_file())
        # The folder card displays the release being published; version cards remain available.
        (out / folder / 'README.md').write_text((destination / 'README.md').read_text())
        if f'{folder}/README.md' not in files:
            files.append(f'{folder}/README.md')
        record.setdefault('release_views', {})[folder] = view
    target = out / run_file
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(results.encode(record) + '\n')
    from validation.publishing.publish import description
    (out / 'pull-request.md').write_text(description(record))
    return [*files, run_file]
