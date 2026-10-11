"""Reuse unchanged successful static checks from a verified checkpoint."""
import ast
import copy
import hashlib
import json
from pathlib import Path


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def checker_source(contract):
    """Locate the checker in current or historical frozen code snapshots."""
    for name in ('validation/checks/check_terminal_bench.py',
                 'validation/verify/check_terminal_bench.py'):
        if name in contract['implementation']:
            return name
    raise ValueError('static checkpoint contract has no checker implementation')


def checkpoint_record(directory):
    directory = Path(directory).resolve()
    meta = json.loads((directory / 'checkpoint.json').read_text())
    for name, expected in meta['files'].items():
        if Path(name).name != name or sha(directory / name) != expected:
            raise ValueError(f'static checkpoint artifact mismatch: {name}')
    return {'path': str(directory), 'sha256': sha(directory / 'checkpoint.json'),
            'source_contract_sha256': meta['source_contract_sha256'],
            'checkpoint_tasks': meta['checkpoint_tasks']}


def load(directory, tasks, manifest, checks, profile, expected=None, accept_previous_path_check=False):
    from validation.contract import read, task_records, task_digest
    from validation.checks import check_terminal_bench as checker
    directory = Path(directory)
    record = checkpoint_record(directory)
    if expected is not None and record != expected:
        raise ValueError('static checkpoint differs from frozen contract')
    old_contract = read(directory / 'contract.json')
    if old_contract['sha256'] != record['source_contract_sha256']:
        raise ValueError('static checkpoint source contract mismatch')
    if sha(directory / 'checker.py') != old_contract['implementation'][checker_source(old_contract)]:
        raise ValueError('static checkpoint checker differs from source contract')
    old = json.loads((directory / 'summary.json').read_text())
    if len(old['tasks']) != record['checkpoint_tasks']:
        raise ValueError('static checkpoint task count mismatch')
    if old['commit'] != manifest['commit'] or old['profile'] != profile:
        raise ValueError('static checkpoint upstream/profile mismatch')
    if old['checks'] != old_contract['success_criteria']['static_checks']:
        raise ValueError('static checkpoint checks differ from source contract')
    # Audit shared input transformations before reusing any old outcome. Compare
    # syntax trees so harmless formatting changes do not invalidate evidence.
    def functions(path):
        return {n.name: ast.dump(n, include_attributes=False) for n in ast.parse(Path(path).read_text()).body
                if isinstance(n, ast.FunctionDef)}
    previous = functions(directory / 'checker.py')
    current = functions(checker.__file__)
    for name in ('validate_input', 'safe_copy'):
        if previous.get(name) != current.get(name):
            raise ValueError(f'static checkpoint input transformation changed: {name}')
    reusable = set(checks) & set(old['checks'])
    # Local checks and their Harbor schema may change independently of the
    # upstream checksum manifest. Rerun instead of trusting stale evidence.
    reusable.difference_update(checker.LOCAL_CHECKS)
    for name in list(reusable):
        if old.get('adaptations', {}).get(name) != checker.ADAPTATIONS.get(name):
            reusable.remove(name)
    # Adaptation helpers can change independently of their descriptions.
    dependencies = {
        checker.PATH_CHECK: ('without_source_code', 'without_urls', 'path_check_copy', 'path_check_text'),
        'check-test-file-references.sh': ('without_urls', 'reference_check_text', 'adapted_check_copy'),
        'check-pip-pinning.sh': ('pip_check_text', 'adapted_check_copy'),
        'check-nproc.sh': ('nproc_check_text', 'adapted_check_copy'),
    }
    for check, helpers in dependencies.items():
        if any(previous.get(name) != current.get(name) for name in helpers):
            reusable.discard(check)
    if accept_previous_path_check and checker.PATH_CHECK in checks and checker.PATH_CHECK in old['checks']:
        reusable.add(checker.PATH_CHECK)
    if old.get('adaptations', {}).get('file names') != checker.ADAPTATIONS.get('file names'):
        raise ValueError('static checkpoint filename adaptation changed')
    hashes = {r['task_id']: r['sha256'] for r in task_records(old_contract)}
    selected = {t.name: t for t in tasks}
    imported, seen = {}, set()
    for item in old['tasks']:
        name = item['task']
        if name in seen or name not in hashes:
            raise ValueError(f'duplicate or unknown checkpoint task: {name}')
        seen.add(name)
        if name not in selected:
            continue
        if task_digest(selected[name]) != hashes[name]:
            raise ValueError(f'checkpoint task content changed: {name}')
        entries = {}
        for check in item.get('checks', []):
            key = check['check']
            if key in entries:
                raise ValueError(f'duplicate checkpoint check: {name}/{key}')
            if key in reusable and check['status'] == 'passed' and check.get('exit_code') == 0:
                saved = copy.deepcopy(check)
                saved['checkpoint_log'] = saved.pop('log', None)
                saved['log'] = str(directory / 'logs.tar.gz') + f'!/{name}/{key}.log'
                saved['resumed_from'] = record['sha256']
                entries[key] = saved
        imported[name] = entries
    return imported, {**record, 'reusable_checks': sorted(reusable),
                      'accepted_previous_path_check': accept_previous_path_check,
                      'previous_adaptations': old.get('adaptations', {}),
                      'imported_checks': sum(map(len, imported.values())),
                      'rerun_changed_checks': sorted(set(checks) - reusable)}
