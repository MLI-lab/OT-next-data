#!/usr/bin/env python3
"""Prepare and run ET identity/ownership checks in fresh environment-check containers.

Run ``prepare`` on a login node. Review the resulting source-derived expectations,
then run ``run`` inside an allocation with the normal Harbor bridge running.
This script never submits jobs, builds images, runs solutions, or runs graders.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
from collections import Counter
import copy
import hashlib
import json
import os
import re
from pathlib import Path
import shlex
import shutil
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
SCHEMA = 'ot.et.identity-audit.v1'
MARKER = 'ET_IDENTITY_JSON='
FLAG = 'identity-and-ownership-needs-review'


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def save(path, data):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(data, indent=2, sort_keys=True) + '\n')
    temporary.replace(path)


def template(record):
    """Only use declared identity and already recorded source expectations."""
    user = record['expected']['user']
    uid = 0 if user == 'root' else int(user) if user.isdecimal() else None
    paths = copy.deepcopy(record.get('reviewed_state', {}))
    for path, checksum in record.get('literal_files', {}).items():
        paths.setdefault(path, {})['sha256'] = checksum
    return {
        'environment_sha256': record['environment_sha256'],
        'declared_user': user,
        'review_flags': record.get('runtime_review', []),
        'identity': {'uid': uid, 'gid': 0 if user == 'root' else None,
                     'username': None if user.isdecimal() else user, 'groups': None},
        'paths': paths,
        'access': [],
        'accounts': {},
        'switches': [],
        'group_records': {},
        'coverage_reviewed': False,
        'review_evidence': '',
    }


def prepare(args):
    import pyarrow.parquet as pq
    records = {}
    for line in args.manifest.read_text().splitlines():
        record = json.loads(line)
        if record['task_id'] in records:
            raise ValueError('Duplicate manifest task ID')
        records[record['task_id']] = record
    selected = set(args.task or [name for name, record in records.items()
                                if FLAG in record.get('runtime_review', [])])
    if not selected or selected - records.keys():
        raise ValueError('Empty selection or task IDs absent from build manifest')
    tasks = {name: template(records[name]) for name in sorted(selected)}
    found = set()
    for batch in pq.ParquetFile(args.source).iter_batches(columns=['path', 'task_binary']):
        for row in batch.to_pylist():
            if row['path'] in selected:
                if row['path'] in found:
                    raise ValueError('Duplicate source task ID')
                found.add(row['path'])
                tasks[row['path']]['task_binary_sha256'] = hashlib.sha256(row['task_binary']).hexdigest()
    if found != selected:
        raise ValueError('Selected tasks absent from source Parquet')
    plan = {'schema': SCHEMA, 'source': str(args.source.resolve()),
            'source_sha256': digest(args.source),
            'build_manifest': str(args.manifest.resolve()),
            'build_manifest_sha256': digest(args.manifest), 'tasks': tasks,
            'instructions': 'Review numeric IDs, supplementary groups, paths and allowed/denied '
                            'access against original source or trusted Docker reference evidence. '
                            'Do not use the converted image as its own reference. Set '
                            'coverage_reviewed=true and document review_evidence only after '
                            'reviewing the complete task identity/ownership requirements.'}
    validate_plan(plan)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x') as stream:
        json.dump(plan, stream, indent=2, sort_keys=True)
        stream.write('\n')
    print(f'Prepared {len(tasks)} tasks: {args.output}. Expectations still require review.')


def validate_plan(plan):
    if plan.get('schema') != SCHEMA or not plan.get('tasks'):
        raise ValueError('Unsupported or empty audit plan')
    for name, expected in plan['tasks'].items():
        if Path(name).name != name or name in ('.', '..'):
            raise ValueError('Unsafe task ID')
        for key in ('environment_sha256', 'task_binary_sha256'):
            value = expected.get(key, '')
            if len(value) != 64 or any(c not in '0123456789abcdef' for c in value):
                raise ValueError(f'{name}: invalid {key}')
        identity = expected['identity']
        if not isinstance(identity, dict) or set(identity) != {'uid', 'gid', 'groups', 'username'}:
            raise ValueError('Identity must specify uid, gid, groups and username (unknown values may be null)')
        if identity['username'] is not None: validate_user(identity['username'])
        values = [identity.get('uid'), identity.get('gid')]
        if identity.get('groups') is not None:
            if not isinstance(identity['groups'], list):
                raise ValueError('groups must be a list or null')
            values += identity['groups']
        if any(v is not None and (type(v) is not int or v < 0) for v in values):
            raise ValueError('UIDs and GIDs must be nonnegative integers or null')
        if type(expected.get('coverage_reviewed')) is not bool:
            raise ValueError('coverage_reviewed must be a boolean')
        if expected['coverage_reviewed'] and not expected.get('review_evidence', '').strip():
            raise ValueError('Reviewed coverage requires source/reference evidence')
        for path, checks in expected['paths'].items():
            validate_path(path)
            if not checks or set(checks) - {'uid', 'gid', 'mode', 'sha256', 'link', 'absent', 'owner', 'group'}:
                raise ValueError('Unsupported or empty path expectations')
            if 'absent' in checks and (checks['absent'] is not True or len(checks) != 1):
                raise ValueError('Absent paths cannot also have metadata expectations')
            for key in ('uid', 'gid'):
                if key in checks and (type(checks[key]) is not int or checks[key] < 0):
                    raise ValueError('Path UID/GID expectations must be nonnegative integers')
            if 'mode' in checks:
                mode = checks['mode']
                if not isinstance(mode, str) or not mode.startswith('0o') or oct(int(mode, 8)) != mode:
                    raise ValueError('Modes must use canonical octal strings, such as 0o755')
            if 'sha256' in checks:
                checksum = checks['sha256']
                if not isinstance(checksum, str) or len(checksum) != 64 or any(c not in '0123456789abcdef' for c in checksum):
                    raise ValueError('Invalid path SHA-256 expectation')
            if 'owner' in checks:
                validate_user(checks['owner'])
                if checks['owner'] not in expected.get('accounts', {}):
                    raise ValueError('Symbolic ownership requires an account expectation')
            if 'group' in checks:
                validate_user(checks['group'])
                if checks['group'] not in expected.get('group_records', {}):
                    raise ValueError('Symbolic group ownership requires a group expectation')
        for check in expected['access']:
            validate_path(check['path'])
            if check['operation'] not in ('read', 'write', 'list', 'traverse', 'create'):
                raise ValueError('Unsupported access operation')
            if type(check['allowed']) is not bool:
                raise ValueError('Access allowed must be a boolean')
        for name, checks in expected.get('accounts', {}).items():
            validate_user(name)
            if not checks or set(checks) - {'uid', 'gid', 'home', 'shell', 'groups', 'absent', 'exists'}:
                raise ValueError('Unsupported account expectations')
            if 'absent' in checks and checks != {'absent': True}:
                raise ValueError('Absent account cannot also have field expectations')
            for key in ('uid', 'gid'):
                if key in checks and (type(checks[key]) is not int or checks[key] < 0):
                    raise ValueError('Account UID/GID must be nonnegative integers')
            if 'groups' in checks and (not isinstance(checks['groups'], list) or
                    any(type(v) is not int or v < 0 for v in checks['groups'])):
                raise ValueError('Account groups must be nonnegative integer IDs')
            for key in ('home', 'shell'):
                if key in checks: validate_path(checks[key])
            if 'exists' in checks and checks['exists'] is not True:
                raise ValueError('Use exists=true or absent=true for accounts')
        for name, checks in expected.get('group_records', {}).items():
            validate_user(name)
            if not checks or set(checks) - {'gid', 'members', 'exists', 'absent'}:
                raise ValueError('Unsupported group expectations')
            if 'gid' in checks and (type(checks['gid']) is not int or checks['gid'] < 0):
                raise ValueError('Group GID must be a nonnegative integer')
            if 'members' in checks:
                if not isinstance(checks['members'], list): raise ValueError('Group members must be a list')
                for member in checks['members']: validate_user(member)
            if 'exists' in checks and checks['exists'] is not True: raise ValueError('Use exists=true')
            if 'absent' in checks and checks != {'absent': True}: raise ValueError('Invalid absent group')
        if expected.get('identity_account') is not None:
            validate_user(expected['identity_account'])
            if expected['identity_account'] not in expected.get('accounts', {}):
                raise ValueError('Symbolic process identity requires an account expectation')
        omissions = expected.get('identity_omissions', {})
        if not isinstance(omissions, dict) or set(omissions) - {'uid', 'gid', 'groups'}:
            raise ValueError('Invalid identity omission fields')
        for key, reason in omissions.items():
            if not isinstance(reason, str) or not reason.strip() or identity[key] is not None:
                raise ValueError('Only unspecified source requirements may be omitted with an explanation')
        for check in expected.get('switches', []):
            if set(check) - {'user', 'allowed', 'uid', 'gid'} or type(check.get('allowed')) is not bool:
                raise ValueError('Invalid identity-switch expectation')
            validate_user(check['user'])
            if check['user'] not in expected.get('accounts', {}):
                raise ValueError('Switch probes require a source-grounded account expectation')
            for key in ('uid', 'gid'):
                if key in check and (type(check[key]) is not int or check[key] < 0):
                    raise ValueError('Explicit switch UID/GID must be nonnegative integers')


def validate_user(name):
    if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_.-]*[$]?', name):
        raise ValueError('Invalid account name')


def validate_path(path):
    if not isinstance(path, str) or not path.startswith('/') or '..' in Path(path).parts or '\0' in path:
        raise ValueError('Probe paths must be absolute without parent traversal')


# The top-level probe always runs as the actual agent. Explicit switch checks
# use short-lived child processes and never replace the agent's identity.
# Opening a file for writing does not truncate or write it. A create probe makes
# an empty temporary file in a disposable container and removes it immediately.
PROBE = r'''
import errno, hashlib, json, os, pwd, grp, stat, tempfile, subprocess, shlex
config = json.loads(CONFIG_JSON)
result = {"identity": {"uid": os.geteuid(), "gid": os.getegid(),
          "groups": sorted(set(os.getgroups()))}, "paths": {}, "access": [], "accounts": {}, "switches": [], "group_records": {}}
try: result["identity"]["username"] = pwd.getpwuid(os.geteuid()).pw_name
except KeyError: result["identity"]["username"] = None
for name in config.get("accounts", {}):
    try:
        account = pwd.getpwnam(name)
        result["accounts"][name] = {"uid": account.pw_uid, "gid": account.pw_gid,
            "home": account.pw_dir, "shell": account.pw_shell, "absent": False, "exists": True,
            "groups": sorted(set(os.getgrouplist(name, account.pw_gid)))}
    except KeyError: result["accounts"][name] = {"absent": True}
for name in config.get("group_records", {}):
    try:
        group = grp.getgrnam(name)
        result["group_records"][name] = {"gid": group.gr_gid, "members": sorted(group.gr_mem), "exists": True, "absent": False}
    except KeyError: result["group_records"][name] = {"absent": True}
for check in config.get("switches", []):
    item = {"user": check["user"]}
    child = "import os,json;print(json.dumps({'uid':os.geteuid(),'gid':os.getegid()}))"
    command = ["/usr/bin/su", "--shell", "/bin/sh", "--command",
               "/usr/bin/python3 -c " + shlex.quote(child), "--", check["user"]]
    try:
        completed = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=15)
        item.update(allowed=completed.returncode == 0, return_code=completed.returncode,
                    stdout=completed.stdout, stderr=completed.stderr)
        if completed.returncode == 0: item.update(json.loads(completed.stdout))
    except (OSError, ValueError, subprocess.TimeoutExpired) as e: item["error"] = str(e)
    result["switches"].append(item)
for path, expected in config["paths"].items():
    try:
        s = os.lstat(path)
        item = {"uid": s.st_uid, "gid": s.st_gid, "mode": oct(stat.S_IMODE(s.st_mode)),
                "absent": False, "link": os.readlink(path) if stat.S_ISLNK(s.st_mode) else None}
        if "sha256" in expected:
            if not stat.S_ISREG(s.st_mode):
                item["error"] = "Hash expectation requires a regular file"
            else:
                h = hashlib.sha256()
                with open(path, "rb") as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""): h.update(chunk)
                item["sha256"] = h.hexdigest()
        result["paths"][path] = item
    except OSError as e:
        result["paths"][path] = {"absent": True} if e.errno == errno.ENOENT else {"error": str(e)}
for check in config["access"]:
    path, op = check["path"], check["operation"]
    item = {"path": path, "operation": op}
    try:
        if op in ("read", "write"):
            if not stat.S_ISREG(os.stat(path).st_mode):
                raise ValueError("File open probes require an existing regular file")
            fd = os.open(path, (os.O_RDONLY if op == "read" else os.O_WRONLY) | os.O_NONBLOCK)
            os.close(fd)
        elif op == "list": os.listdir(path)
        elif op == "traverse":
            previous = os.open(".", os.O_RDONLY)
            try: os.chdir(path)
            finally:
                os.fchdir(previous)
                os.close(previous)
        elif op == "create":
            fd, temporary = tempfile.mkstemp(prefix=".ot-et-access-", dir=path)
            try: os.close(fd)
            finally:
                try: os.unlink(temporary)
                except OSError as e: raise ValueError("Temporary access-file cleanup failed: " + str(e))
        item["outcome"] = "allowed"
    except OSError as e:
        item["outcome"] = "denied" if e.errno in (errno.EACCES, errno.EPERM) else "error"
        item["error"] = str(e)
    except ValueError as e:
        item.update(outcome="error", error=str(e))
    result["access"].append(item)
print("ET_IDENTITY_JSON=" + json.dumps(result, sort_keys=True))
'''


def probe_command(expected):
    config = json.dumps({k: expected.get(k, {} if k in ('paths', 'accounts', 'group_records') else [])
                         for k in ('paths', 'access', 'accounts', 'switches', 'group_records')})
    script = PROBE.replace('CONFIG_JSON', repr(config))
    encoded = base64.b64encode(script.encode()).decode()
    return '/usr/bin/python3 -c ' + shlex.quote(
        'import base64;exec(base64.b64decode(' + repr(encoded) + '))')


def evaluate(expected, actual):
    checks, errors, review = [], [], []

    def compare(name, wanted, observed):
        checks.append({'check': name, 'expected': wanted, 'actual': observed,
                       'status': 'passed' if wanted == observed else 'failed'})

    for key, wanted in expected['identity'].items():
        if wanted is None:
            account = actual.get('accounts', {}).get(expected.get('identity_account'), {})
            if key in ('uid', 'gid') and expected.get('identity_account'):
                if key not in account or key not in actual.get('identity', {}):
                    errors.append('Identity account observation unavailable: ' + key)
                else:
                    compare('identity:' + key, account[key], actual['identity'][key])
            elif key in expected.get('identity_omissions', {}) and expected['coverage_reviewed']:
                checks.append({'check': 'identity:' + key + ':unspecified', 'status': 'not_required',
                               'reason': expected['identity_omissions'][key]})
            elif key != 'username': review.append('identity expectation missing: ' + key)
        elif key not in actual.get('identity', {}):
            errors.append('identity observation missing: ' + key)
        else:
            observed = actual['identity'][key]
            compare('identity:' + key, sorted(set(wanted)) if key == 'groups' else wanted,
                    sorted(set(observed)) if key == 'groups' else observed)
    for path, wanted in expected['paths'].items():
        observed = actual.get('paths', {}).get(path)
        if observed is None or observed.get('error'):
            errors.append('path observation unavailable: ' + path)
            continue
        for key, value in wanted.items():
            if key in ('owner', 'group'):
                field = 'uid' if key == 'owner' else 'gid'
                table = actual.get('accounts' if key == 'owner' else 'group_records', {})
                target = table.get(value, {})
                if field not in target:
                    errors.append('Path ownership reference unavailable: ' + path)
                else:
                    compare('path:' + key + ':' + path, target[field], observed.get(field))
            else:
                compare('path:' + key + ':' + path, value, observed.get(key))
    for name, wanted in expected.get('accounts', {}).items():
        observed = actual.get('accounts', {}).get(name)
        if observed is None:
            errors.append('Account observation missing: ' + name)
        else:
            for key, value in wanted.items():
                compare('account:' + key + ':' + name,
                        sorted(set(value)) if key == 'groups' else value, observed.get(key))
    for name, wanted in expected.get('group_records', {}).items():
        observed = actual.get('group_records', {}).get(name)
        if observed is None:
            errors.append('Group observation missing: ' + name)
        else:
            for key, value in wanted.items():
                compare('group:' + key + ':' + name,
                        sorted(set(value)) if key == 'members' else value, observed.get(key))
    switches = actual.get('switches', [])
    if len(switches) != len(expected.get('switches', [])):
        errors.append('Identity-switch observation count mismatch')
    else:
        for wanted, observed in zip(expected.get('switches', []), switches):
            if wanted['user'] != observed.get('user') or observed.get('error'):
                errors.append('Identity-switch probe unavailable: ' + wanted['user'])
                continue
            compare('switch:allowed:' + wanted['user'], wanted['allowed'], observed.get('allowed'))
            if wanted['allowed'] and observed.get('allowed'):
                for key in ('uid', 'gid'):
                    target = wanted.get(key, actual.get('accounts', {}).get(wanted['user'], {}).get(key))
                    if target is None:
                        errors.append('Switch account identity unavailable: ' + wanted['user'])
                    else:
                        compare('switch:' + key + ':' + wanted['user'], target, observed.get(key))
    observed_access = actual.get('access', [])
    if len(observed_access) != len(expected['access']):
        errors.append('access observation count mismatch')
    else:
        for wanted, observed in zip(expected['access'], observed_access):
            if (observed.get('path'), observed.get('operation')) != (wanted['path'], wanted['operation']):
                errors.append('access observation identity mismatch')
            elif observed.get('outcome') not in ('allowed', 'denied'):
                errors.append('access check could not run: ' + wanted['path'])
            else:
                compare('access:' + wanted['operation'] + ':' + wanted['path'],
                        wanted['allowed'], observed['outcome'] == 'allowed')
    if not expected['coverage_reviewed']:
        review.append('Complete task identity/ownership coverage has not been reviewed')
    failures = [c for c in checks if c['status'] == 'failed']
    status = 'error' if errors else 'failed' if failures else 'review' if review else 'passed'
    return {'status': status, 'checks': checks, 'errors': errors, 'review_reasons': review}


def observation(expected, stdout, return_code=0, stderr=''):
    lines = [line[len(MARKER):] for line in (stdout or '').splitlines() if line.startswith(MARKER)]
    try:
        if return_code or len(lines) != 1:
            raise ValueError('Probe did not return exactly one complete observation')
        actual = json.loads(lines[0])
        assessed = evaluate(expected, actual)
        return dict(assessed, actual=actual, stdout=stdout, stderr=stderr)
    except (ValueError, TypeError, KeyError) as exc:
        return {'status': 'error', 'errors': [str(exc)], 'stdout': stdout, 'stderr': stderr}


def combined_status(items):
    statuses = {item['status'] for item in items}
    return next((s for s in ('error', 'failed', 'review') if s in statuses), 'passed') if items else 'error'


def require_images(task, images, environment_sha256):
    from harbor_patches.image_context import environment_dir_hash, environment_dir_hash_truncated
    dockerfiles = list(task.rglob('Dockerfile'))
    if not dockerfiles or environment_dir_hash(task / 'environment') != environment_sha256:
        raise ValueError('Task environment differs from the recorded build manifest')
    for dockerfile in dockerfiles:
        key = environment_dir_hash_truncated(dockerfile.parent)
        if not any(p.is_file() for p in images.glob('*-' + key + '.sif')):
            raise ValueError(f'Required prepared image missing: {task.name} ({key}); no build attempted')
    # The worker's fallback search uses its own cache directory. Per-task staging
    # can be elsewhere, so provide the exact path sent by ApptainerEnvironment.
    from types import SimpleNamespace
    from harbor.models.task.config import TaskConfig
    from harbor.models.task.paths import TaskPaths
    from harbor.models.task.verifier_mode import resolve_effective_verifier_env_config
    from harbor_patches.verifier_setup import build_context
    config = TaskConfig.model_validate_toml((task / 'task.toml').read_text())
    short_name = config.task.short_name if config.task is not None else task.name
    wrapped = SimpleNamespace(config=config, paths=TaskPaths(task))
    specs = [('agent', task / 'environment')]
    for step in config.steps or [None]:
        if resolve_effective_verifier_env_config(config, step) is not None:
            specs.append(('verifier-' + step.name if step else 'verifier', build_context(wrapped, step)))
    for label, context in specs:
        key = environment_dir_hash_truncated(context)
        matches = sorted(p for p in images.glob('*-' + key + '.sif') if p.is_file())
        if not matches:
            raise ValueError(f'Required {label} image missing: {task.name}; no build attempted')
        source = matches[0]
        name = f'build_{short_name}-{label}-{key}.sif'
        if Path(name).name != name:
            raise ValueError('Unsafe environment cache alias')
        alias = images / name
        for suffix in ('.deferred.json', '.overlay.img', '.sif'):
            target = source.with_suffix(suffix)
            link = alias.with_suffix(suffix)
            if target.is_file() and not link.exists():
                link.symlink_to(target.name)


async def run(args):
    from validation.rules.dynamic.environment import check as inspection
    from validation.data.materialize import materialize
    from validation.stages import harbor as runtime
    from validation.stages.runner import parser as runtime_parser
    from hpc.local_assets import stage_images
    from data.endless_terminals.patch import terminal_probe

    plan = json.loads(args.plan.read_text())
    validate_plan(plan)
    source = args.source or Path(plan['source'])
    if digest(source) != plan['source_sha256']:
        raise ValueError('Source Parquet changed since expectation preparation')
    selected = set(args.task or plan['tasks'])
    if not selected or selected - plan['tasks'].keys():
        raise ValueError('Empty selection or unknown task ID')
    if args.shard:
        from validation.shards import parse_shard, slice_of
        selected = set(slice_of(selected, parse_shard(args.shard), key=lambda name: name))
        if not selected:
            raise ValueError('Selected shard is empty')
    if not args.bridge_url or not args.images:
        raise ValueError('Run inside an allocation with a bridge URL and prepared image cache')
    args.output.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(__file__, args.output / 'audit-script.py')
    save(args.output / 'expectations.json', plan)
    results = []
    header = {'schema': SCHEMA, 'stage': 1, 'plan_sha256': digest(args.plan),
              'source_sha256': plan['source_sha256'], 'selected_tasks': sorted(selected),
              'shard': args.shard,
              'note': 'Supplemental stage-1 environment evidence, not an official contract-bound stage report. '
                      'Only confirmed mismatches are exclusion candidates; error/review need follow-up.'}
    original = inspection.inspect_environment
    active = {}

    async def inspect(env, task, context, label):
        ordinary = await original(env, task, context, label)
        if label != 'agent':
            return ordinary
        expected = plan['tasks'][task.paths.task_dir.name]
        command = probe_command(expected)
        value = await env.exec(command, timeout_sec=args.probe_timeout)
        direct = observation(expected, value.stdout, value.return_code, value.stderr)
        terminal = await terminal_probe(env, command)
        interactive = observation(expected, terminal.get('stdout'),
                                  0 if terminal['status'] == 'observed' else 1,
                                  terminal.get('stderr', ''))
        audited = {'direct': direct, 'terminal': interactive,
                   'status': combined_status([direct, interactive]), 'ordinary_checks': ordinary}
        active.update(audited)
        return dict(ordinary, identity_audit=audited,
                    status='failed' if audited['status'] in ('failed', 'error') else ordinary['status'])

    try:
        with tempfile.TemporaryDirectory(prefix='et-identity-', dir=args.scratch) as temporary:
            scratch = Path(temporary)
            tasks = materialize(source, scratch / 'tasks', selected_ids=selected)
            materialized = json.loads((scratch / 'materialization.json').read_text())
            for item in materialized:
                if item['sha256'] != plan['tasks'][item['task']]['task_binary_sha256']:
                    raise ValueError('Task payload changed since expectation preparation')
            inspection.inspect_environment = inspect
            evidence = scratch / 'evidence'
            evidence.mkdir()
            try:
                for task in sorted(tasks):
                    active.clear()
                    result = {'task_id': task.name}
                    try:
                        with tempfile.TemporaryDirectory(prefix='images-', dir=scratch) as image_dir:
                            images = Path(image_dir)
                            stage_images([task], args.images, images)
                            require_images(task, images, plan['tasks'][task.name]['environment_sha256'])
                            settings = runtime_parser().parse_args([
                                str(source), '--submit', 'never',
                                '--no-fix-instruction-paths', '--no-fix-pip-pins', '--no-fix-instruction-suffix'])
                            settings.environment_kwargs = {'bridge_url': args.bridge_url, 'sif_cache': str(images)}
                            result['runtime'] = await runtime.build_task(task, evidence / task.name, settings)
                            result['audit'] = copy.deepcopy(active)
                            result['status'] = active.get('status', 'error')
                            lifecycle_error = any(e.get('error') or e.get('cleanup_error')
                                                  for e in result['runtime'].get('environments', []))
                            if lifecycle_error or (result['runtime']['status'] != 'passed' and result['status'] != 'failed'):
                                result.update(status='error', error='Fresh environment lifecycle did not complete successfully')
                    except Exception as exc:
                        result.update(status='error', error=f'{type(exc).__name__}: {exc}')
                    results.append(result)
                    write_results(args.output, header, results)
                    print(task.name, result['status'], flush=True)
            finally:
                with tarfile.open(args.output / 'evidence.tar.gz', 'w:gz') as archive:
                    archive.add(evidence, arcname='phase3-evidence')
    finally:
        inspection.inspect_environment = original
    counts = Counter(item['status'] for item in results)
    print(json.dumps(dict(counts), sort_keys=True))
    return 0 if counts.get('passed', 0) == len(selected) else 2 if counts.get('error') or counts.get('review') else 1


def write_results(output, header, items):
    complete = len(items) == len(header['selected_tasks'])
    save(output / 'identity-audit.json', dict(header, complete=complete, items=items,
                                            counts=dict(Counter(i['status'] for i in items))))
    save(output / 'filter-candidates.json', {
        'schema': SCHEMA, 'complete': complete,
        'exclude_candidates': [i['task_id'] for i in items if i['status'] == 'failed'],
        'review': [i['task_id'] for i in items if i['status'] == 'review'],
        'infrastructure_or_probe_errors': [i['task_id'] for i in items if i['status'] == 'error'],
        'passed': [i['task_id'] for i in items if i['status'] == 'passed'],
        'policy': 'No automatic exclusions. Confirm mismatches in a fresh retry before applying '
                  'them to the contract-bound stage-1 report. Never exclude on review flags or errors.'})


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    prepare_parser = commands.add_parser('prepare', help='Write source-derived expectations for review; no containers')
    prepare_parser.add_argument('--source', type=Path, required=True)
    prepare_parser.add_argument('--manifest', type=Path, required=True, help='Patcher build-manifest.jsonl')
    prepare_parser.add_argument('--output', type=Path, required=True, help='New expectation JSON file')
    prepare_parser.add_argument('--task', action='append', help='Select specific task IDs; default all identity review flags')
    run_parser = commands.add_parser('run', help='Check fresh containers through the existing bridge; never submit')
    run_parser.add_argument('--plan', type=Path, required=True)
    run_parser.add_argument('--source', type=Path, help='Relocated copy of the exact same source Parquet')
    run_parser.add_argument('--images', type=Path, default=os.environ.get('HARBOR_SIF_CACHE'))
    run_parser.add_argument('--bridge-url', default=os.environ.get('APPTAINER_BRIDGE_URL'))
    run_parser.add_argument('--output', type=Path, required=True, help='New evidence directory')
    run_parser.add_argument('--scratch', type=Path, default=os.environ.get('TMPDIR', tempfile.gettempdir()))
    run_parser.add_argument('--task', action='append', help='Run a subset of planned task IDs')
    run_parser.add_argument('--shard', help='Run INDEX/COUNT of the sorted selection, such as 0/20')
    run_parser.add_argument('--probe-timeout', type=int, default=60)
    args = parser.parse_args(argv)
    if args.command == 'prepare':
        prepare(args)
        return 0
    if args.probe_timeout <= 0:
        parser.error('--probe-timeout must be positive')
    return asyncio.run(run(args))


if __name__ == '__main__':
    raise SystemExit(main())
