"""Revision-bound evidence of deterministic pipeline repairs, never manual task repairs."""
import json
from pathlib import Path


LABELS = {'anti-cheat-instruction': 'instruction-suffix',
          'relative-path-fix': 'task-absolute-path',
          'dependency-pinning': 'pip-pinning'}
RULES = {*LABELS.values(), 'repository_timestamps'}


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


def contract_records(tasks, metadata):
    changes = metadata.get('automation_changes', {})
    return {task['task_id']: {
        'content_sha256': task['sha256'],
        'rules': {rule: {'applied': label in changes.get(task['task_id'], []),
                         'evidence': [{'source': 'pipeline-normalization', 'label': label}]
                         if label in changes.get(task['task_id'], []) else []}
                  for label, rule in LABELS.items()}}
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
    if records is None and contract['dataset'].get('automation_changes'):
        # Legacy contracts recorded positive labels but did not guarantee a complete
        # repair history. Recover positives only; absence must remain unknown.
        from validation.contract import read, task_records
        verified = read(path)
        records = contract_records(task_records(verified), verified['dataset'])
        for record in records.values():
            record['rules'] = {rule: evidence for rule, evidence in record['rules'].items()
                               if evidence['applied']}
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
    return json.dumps(merged, sort_keys=True)
