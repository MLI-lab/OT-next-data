"""Publish task payload deltas and one results index per dataset folder."""
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

    def payload_files(self, folder):
        """Task payload files already published for a folder."""
        prefix = folder + '/tasks/'
        if self.root:
            return sorted(p.relative_to(self.root).as_posix() for p in (self.root / prefix).glob('*.parquet'))
        if not self.repo:
            return []
        from huggingface_hub import HfApi
        return sorted(f for f in HfApi().list_repo_files(self.repo, repo_type='dataset', revision=self.revision)
                      if f.startswith(prefix) and f.endswith('.parquet'))

    def load(self, folder):
        if folder in self.cache:
            return self.cache[folder]
        path = self.file(folder + '/results.parquet')
        value = results.read(path) if path else []
        self.cache[folder] = value
        return value


def task_environment(binary):
    """Read task.toml [environment] without extracting or executing the payload."""
    import io
    import tarfile
    import tomllib
    try:
        with tarfile.open(fileobj=io.BytesIO(binary), mode='r:*') as archive:
            members = [member for member in archive.getmembers()
                       if member.isfile() and member.name.removeprefix('./') == 'task.toml']
            if len(members) != 1:
                return None
            stream = archive.extractfile(members[0])
            return tomllib.loads(stream.read().decode()).get('environment', {})
    except (tarfile.TarError, UnicodeError, ValueError):
        return None


def internet_setting(binary):
    environment = task_environment(binary)
    if environment is None:
        return 'unknown'
    value = environment.get('allow_internet')
    return 'true' if value is True else 'false' if value is False else 'unspecified'


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
                        ('content_sha256', pa.string())])
    if image_manifest:
        schema = schema.with_metadata({b'ot.images.v1': results.encode(image_manifest).encode()})
    for folder, (kept, archived) in sorted(tables.items()):
        payloads = [*kept, *archived]
        previous = history.load(folder)
        validated = {r['path']: r['content_sha256'] for r in payloads}
        # Rows stage by stage: after a stage whose leak repairs changed a task, its row moves to the repaired version.
        arguments = contract.get('arguments', {})
        observed = []
        for stage in sorted(reports, key=int):
            observed = results.merge(observed, results.observations({stage: reports[stage]}, validated, folder, arguments))
            observed = results.repair_rows(observed, reports[stage].get('leak_repairs') or {})
        # Tasks as the run left them: the recorded leak repairs replayed on the validated originals.
        from validation.automatic_repairs import archive_files, plan, replace_in_archive
        from validation.contract import files_digest
        from validation.leak_repair.tool import repaired_loader
        binaries = {r['path']: r['task_binary'] for r in payloads}
        originals = lambda tasks: {t: archive_files(binaries[t]) for t in tasks if t in binaries}
        load = repaired_loader(originals, reports, (contract.get('dataset') or {}).get('source'))
        repaired = sorted({t for report in reports.values() for t, r in (report.get('leak_repairs') or {}).items()
                           if r['to'] != r['from'] and t in binaries})
        repaired_files = load(repaired) if repaired else {}
        current = {**validated, **{t: files_digest(f.items()) for t, f in repaired_files.items()}}
        # Evidence from another input revision is not evidence for these bytes.
        observed = [r for r in observed if current.get(r['task_id']) == r['content_sha256']]
        # The published task carries this run's leak repairs and its automatic path and pip repairs.
        plans = plan(reports, load)
        results.repair_rows(observed, plans)
        payloads = [dict(r, task_binary=replace_in_archive(r['task_binary'], plans[r['path']]['files']),
                         content_sha256=plans[r['path']]['to'])
                    if r['path'] in plans and current.get(r['path']) == plans[r['path']]['from'] else
                    dict(r, task_binary=replace_in_archive(r['task_binary'], repaired_files[r['path']]),
                         content_sha256=current[r['path']])
                    if r['path'] in repaired_files else r
                    for r in payloads]
        # Apply settled calibration settings to the actual published task packages.
        from validation.metrics.apply_resources import apply_task
        matched = {(r['task_id'], r['content_sha256']): i for i, r in enumerate(observed)}
        for i, task in enumerate(payloads):
            index = matched.get((task['path'], task['content_sha256']))
            if index is not None:
                payloads[i], observed[index] = apply_task(task, observed[index])
        incoming = {(r['task_id'], r['content_sha256']): r for r in observed}
        for task in payloads:
            key = task['path'], task['content_sha256']
            row = incoming.setdefault(key, {'dataset': folder, 'task_id': key[0], 'content_sha256': key[1]})
            row['allow_internet'] = internet_setting(task['task_binary'])
            environment = task_environment(task['task_binary']) or {}
            row['cpus'], row['memory_mb'] = environment.get('cpus'), environment.get('memory_mb')
            row['run'] = run_file
            row['publication_details'] = results.encode({k: v for k, v in task.items() if k != 'task_binary'})
        merged = results.merge(previous, list(incoming.values()))
        existing = {(r['task_id'], r['content_sha256']) for r in previous}
        new = [r for r in merged if (r['task_id'], r['content_sha256']) not in existing]
        # Payload files are dataset-wide batches; a new file holds only new or changed tasks.
        published = history.payload_files(folder)
        numbers = [int(Path(f).stem[1:]) for f in published if Path(f).stem[1:].isdigit()]
        delta_file = f'{folder}/tasks/r{max(numbers, default=0) + 1}.parquet'
        new_keys = {(r['task_id'], r['content_sha256']): r for r in new}
        if new:
            path = out / delta_file
            path.parent.mkdir(parents=True, exist_ok=True)
            def changed():
                for task in payloads:
                    row = new_keys.get((task['path'], task['content_sha256']))
                    if row:
                        yield {k: task[k] for k in ('path', 'task_binary', 'content_sha256')}
            write_rows(changed(), path, schema)
            files.append(delta_file)
        index = f'{folder}/results.parquet'
        results.write(out / index, merged)
        files.append(index)
        record['result_files'][folder] = index
        record['task_revision_files'][folder] = sorted({*published, *([delta_file] if new else [])})
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
