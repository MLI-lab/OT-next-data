"""Revision-bound evidence of deterministic pipeline repairs, never manual task repairs."""
import json
from pathlib import Path


LABELS = {'anti-cheat-instruction': 'instruction-suffix',
          'relative-path-fix': 'task-absolute-path',
          'dependency-pinning': 'pip-pinning'}
RULES = {*LABELS.values(), 'repository_timestamps'}
# The suffix repair is the same for every task, so its cell only says repaired.
DETAILS = 'automatic_repair_details'


def record_details(meta, rule, changes):
    """Keep the task-specific text of a repair, e.g. {'task': 'data.csv -> /app/data.csv'}."""
    for task, text in changes.items():
        if text:
            meta.setdefault(DETAILS, {}).setdefault(task, {})[rule] = text


def path_details(report):
    return {record['task']: ', '.join(dict.fromkeys(f"{e['old']} -> {e['new']}" for e in record['edits']))
            for record in report.get('tasks', []) if record.get('edits')}


def pip_details(edits):
    return {item['task']: ', '.join(dict.fromkeys(
                f"{e['original']} -> {e['replacement'].strip(chr(39))}" for e in item['edits']))
            for item in edits if item.get('edits')}


def timestamp_details(repositories):
    done = [r for r in repositories if r.get('status') == 'done' and r.get('mtime_files', 0) > 0]
    return '; '.join(f"{r.get('path') or r.get('repository') or 'repository'}: "
                     f"{r['mtime_files']} file timestamps reset" for r in done) or None


def inherit_changes(args, tasks, metadata):
    """Materialized task copies can lack source.json; retain verified parent provenance."""
    path = getattr(args, 'contract', None)
    if not path:
        return
    from validation.contract import read, task_records
    parent = read(path)
    hashes = {task['task_id']: task['sha256'] for task in task_records(parent)}
    changes = metadata.setdefault('automation_changes', {})
    for task in tasks:
        name = task['task_id']
        if hashes.get(name) == task['sha256']:
            inherited = parent['dataset'].get('automation_changes', {}).get(name, [])
            if inherited:
                changes[name] = sorted(set(changes.get(name, [])) | set(inherited))
            rules = parent['dataset'].get('automatic_repairs', {}).get(name, {}).get('rules', {})
            for rule, record in rules.items():
                if record.get('details') and rule not in metadata.get(DETAILS, {}).get(name, {}):
                    record_details(metadata, rule, {name: record['details']})


def contract_records(tasks, metadata):
    changes = metadata.get('automation_changes', {})
    texts = metadata.get(DETAILS, {})
    def record(task, label, rule):
        applied = label in changes.get(task, [])
        value = {'applied': applied,
                 'evidence': [{'source': 'pipeline-normalization', 'label': label}] if applied else []}
        if applied and texts.get(task, {}).get(rule):
            value['details'] = texts[task][rule]
        return value
    return {task['task_id']: {
        'content_sha256': task['sha256'],
        'rules': {rule: record(task['task_id'], label, rule) for label, rule in LABELS.items()}}
        for task in tasks}


def capture(report, settings):
    """Freeze evidence before later normalization replaces the active contract."""
    if 'automatic_repairs' in report:
        return
    path = (settings or {}).get('contract')
    if not path:
        return
    contract = json.loads(Path(path).read_text())
    # Old reports must not acquire evidence from a different run/contract.
    if report.get('contract_sha256') not in (None, contract['sha256']):
        return
    records = contract['dataset'].get('automatic_repairs')
    if records is not None:
        report['automatic_repairs'] = records


def for_item(report, item, task, digest):
    record = report.get('automatic_repairs', {}).get(task, {})
    rules = dict(record.get('rules', {})) if record.get('content_sha256') == digest else {}
    runtime = [dict(env['leaks']['repair'], environment=env.get('environment'))
               for env in item.get('environments', [])
               if isinstance(env.get('leaks', {}).get('repair'), dict)]
    if runtime:
        repositories = [repo for entry in runtime for repo in entry.get('repositories', [])]
        applied = any(repo.get('status') == 'done' and repo.get('head') and repo.get('mtime_files', 0) > 0
                      for repo in repositories)
        incomplete = any(entry.get('discovery_status') == 'incomplete' for entry in runtime) or any(
            repo.get('status') == 'incomplete' for repo in repositories)
        rules['repository_timestamps'] = {'applied': True if applied else None if incomplete else False,
                                          'evidence': runtime}
        if applied:
            rules['repository_timestamps']['details'] = timestamp_details(repositories)
    return rules


def combine(previous, incoming):
    """Partial reruns preserve repair evidence; repeated exports do not double count."""
    merged = dict(json.loads(previous or '{}'))
    for rule, record in json.loads(incoming).items():
        old = merged.get(rule, {})
        applied = True if True in (old.get('applied'), record.get('applied')) else record.get('applied')
        evidence = {json.dumps(e, sort_keys=True): e
                    for e in [*old.get('evidence', []), *record.get('evidence', [])]}
        merged[rule] = {'applied': applied, 'evidence': list(evidence.values())}
        text = record.get('details') or old.get('details')
        if text:
            merged[rule]['details'] = text
    return json.dumps(merged, sort_keys=True)


def path_evidence(build_item):
    """The stage-1 path lookup of a task: where its relative instruction paths really are."""
    runs = (build_item or {}).get('preparation_runs') or [build_item or {}]
    found = [env['instruction_paths'] for run in runs for env in run.get('environments', [])
             if env.get('environment') == 'agent' and env.get('instruction_paths')]
    completed = [f for f in found if f.get('status') == 'completed']
    return (completed or found or [None])[0]


def task_repairs(files, build_item=None, reference_item=None):
    """Apply the automatic path and pip repairs to one task's files (path -> bytes).

    The evidence comes from stages the task runs anyway: the stage-1 path lookup and
    the stage-2 reference run's pip snapshots. The result depends only on the files
    and these two items, so the run, the shard merge and publishing all get the same
    repaired files. Returns (files, rules); rules maps each applied repair's check to
    {'status', 'details'}, where status is the check's result after the repair."""
    files, rules = dict(files), {}
    baseline = path_evidence(build_item)
    if baseline and baseline.get('status') == 'completed' and 'instruction.md' in files:
        from validation.rules.static.task_absolute_path.instruction_paths import automatic
        try:
            text, edits = automatic(files['instruction.md'].decode(), {'agent_baseline': baseline, 'observations': []},
                                    (build_item or {}).get('status') == 'passed')
        except ValueError:
            edits = []
        if edits:
            files['instruction.md'] = text.encode()
            changed = {e['old'] for e in edits}
            unresolved = {str(c) for c in baseline.get('candidates') or []} - changed
            rules['task-absolute-path'] = {
                'status': 'warning' if unresolved else 'pass',
                'details': ', '.join(dict.fromkeys(f"{e['old']} -> {e['new']}" for e in edits))}
    snapshots = (reference_item or {}).get('pip_snapshots')
    if snapshots:
        from validation.rules.static.pip_pinning.repair import pin
        try:
            files, edits = pin(files, snapshots)
        except ValueError:
            edits = []
        if edits:
            wanted = {(o['file'], r['start']) for o in snapshots for r in o.get('requirements', [])}
            rules['pip-pinning'] = {
                'status': 'warning' if len(wanted) > len({(e['file'], e['start']) for e in edits}) else 'pass',
                'details': pip_details([{'task': '', 'edits': edits}])['']}
    return files, rules


def plan(reports, load):
    """Repairs of every task with stage-1 or stage-2 evidence in `reports` ({stage: report}).

    load(tasks) returns {task: original files} for the given task IDs. Returns
    {task: {'from', 'to', 'rules', 'files'}} for each task a repair changed."""
    from validation.contract import files_digest
    items = {stage: {Path(i['task']).name: i for i in reports.get(stage, {}).get('items', []) if i.get('task')}
             for stage in (1, 2)}
    candidates = sorted(task for task in set(items[1]) | set(items[2])
                        if path_evidence(items[1].get(task)) or (items[2].get(task) or {}).get('pip_snapshots'))
    originals = load(candidates) if candidates else {}
    plans = {}
    for task in candidates:
        if task not in originals:
            continue
        repaired, rules = task_repairs(originals[task], items[1].get(task), items[2].get(task))
        if rules:
            plans[task] = {'from': files_digest(originals[task].items()), 'to': files_digest(repaired.items()),
                           'rules': rules, 'files': repaired}
    return plans


def archive_files(binary):
    """Regular files of a stored task archive (path -> bytes)."""
    import io
    import tarfile
    with tarfile.open(fileobj=io.BytesIO(binary), mode='r:*') as archive:
        return {member.name.removeprefix('./'): archive.extractfile(member).read()
                for member in archive.getmembers() if member.isfile()}


def replace_in_archive(binary, files):
    """The stored archive with changed file contents; modes and order stay as they were."""
    import io
    import tarfile
    out = io.BytesIO()
    with tarfile.open(fileobj=io.BytesIO(binary), mode='r:*') as source, \
            tarfile.open(fileobj=out, mode='w:gz', compresslevel=1) as target:
        for member in source.getmembers():
            name = member.name.removeprefix('./')
            if member.isfile() and name in files:
                data = files[name]
                member.size = len(data)
                target.addfile(member, io.BytesIO(data))
            else:
                target.addfile(member, source.extractfile(member) if member.isfile() else None)
    return out.getvalue()


def files_from(source, wanted):
    """Original files of the `wanted` tasks in a task directory tree or TaskTrove Parquet."""
    from validation.publishing.publish import task_rows
    from validation.task_inputs.materialize import parquet_files
    wanted = set(wanted)
    if not parquet_files(source):
        return {name: {p.relative_to(Path(source) / name).as_posix(): p.read_bytes()
                       for p in (Path(source) / name).rglob('*') if p.is_file()}
                for name in wanted if (Path(source) / name).is_dir()}
    found = {}
    for task_id, binary in task_rows(source):
        if task_id in wanted:
            found[task_id] = archive_files(binary)
    return found
