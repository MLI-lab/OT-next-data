"""Pin flagged pip requirements from a successful oracle's environment snapshot.

Called by validation/run.py before normal validation. Discovery never counts as
stage 3 or 4, and original task files are never changed.
"""
from __future__ import annotations

import copy
from contextvars import ContextVar
import json
from pathlib import Path
import re
import shlex
from uuid import uuid4

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name

CHECK = 'check-pip-pinning.sh'
FILES = ('environment/Dockerfile', 'tests/Dockerfile', 'tests/test.sh', 'solution/solve.sh')
_capture = ContextVar('pip_pin_capture', default=None)
_TOKEN = re.compile(r'''(?:[^\s'"\\]+|'[^']*'|"[^"]*")+''')
_COMMAND = re.compile(r'(?m)^[ \t]*(?:RUN[ \t]+)?(?P<pip>(?:/[\w./-]+/)?pip[0-9.]*|(?:/[\w./-]+/)?python[0-9.]*[ \t]+-m[ \t]+pip)[ \t]+install[ \t]+(?P<args>[^\n]*)')
_VALUE_OPTIONS = {'-i', '--index-url', '--extra-index-url', '-f', '--find-links', '--trusted-host', '--timeout', '--retries', '--cache-dir'}
_BOOL_OPTIONS = {'--no-cache-dir', '--no-deps', '--upgrade', '-U', '--pre', '--prefer-binary', '--disable-pip-version-check', '--quiet', '-q', '--verbose', '-v'}


def commands(task):
    """Extract literal commands only. Unsupported shell constructs stay flagged."""
    found = []
    for filename in FILES:
        path = task / filename
        if not path.is_file():
            continue
        text = path.read_text()
        # A final snapshot cannot identify intermediate build-stage environments
        # or a script-local activated Python. Leave those to a dataset patcher.
        if (filename.endswith('Dockerfile') and len(re.findall(r'(?mi)^FROM\s', text)) > 1 or
                re.search(r'(?m)^\s*(?:source\s|\.\s|export\s+(?:PATH|VIRTUAL_ENV|PYTHONPATH)=)', text)):
            continue
        flat = re.sub(r'\\\n', '  ', text)  # preserve character offsets for edits
        for match in _COMMAND.finditer(flat):
            argv = list(_TOKEN.finditer(match['args']))
            requirements, target, supported = [], None, True
            i = 0
            while i < len(argv):
                token = argv[i]
                try:
                    value, = shlex.split(token.group())
                except ValueError:
                    supported = False
                    break
                if value in ('&&', '||', ';', '|', '#') or re.match(r'^\d*[<>]', value):
                    break
                if value.startswith('#'):
                    break
                if any(c in value for c in '$`\\'):
                    supported = False
                    break
                option, equal, attached = value.partition('=')
                if option in _VALUE_OPTIONS | {'--target', '-t'}:
                    if equal:
                        argument = attached
                    else:
                        i += 1
                        if i >= len(argv):
                            supported = False
                            break
                        argument, = shlex.split(argv[i].group())
                    if any(c in argument for c in '$`\\'):
                        supported = False
                        break
                    if option in ('--target', '-t'):
                        if not argument.startswith('/'):
                            supported = False
                            break
                        target = argument
                elif value in _BOOL_OPTIONS:
                    pass
                elif value.startswith('-'):
                    supported = False
                    break
                else:
                    try:
                        req = Requirement(value)
                    except InvalidRequirement:
                        supported = False
                        break
                    if req.url or req.marker:
                        supported = False
                        break
                    if not any(s.operator in ('==', '===') and '*' not in s.version for s in req.specifier):
                        start = match.start('args') + token.start()
                        end = match.start('args') + token.end()
                        requirements.append({'requirement': value, 'start': start, 'end': end,
                                             'original': text[start:end]})
                i += 1
            if supported and requirements:
                freeze = shlex.split(match['pip']) + ['freeze', '--all']
                if target:
                    freeze += ['--path', target]
                found.append({'file': filename, 'command': text[match.start():match.end()],
                              'freeze': shlex.join(freeze), 'requirements': requirements})
    return found


def enabled(args, numbers):
    return 1 in numbers and getattr(args, 'fix_pip_pins', False) and not args.dry_run


def check(tasks, args, out):
    """Run the existing checker; hand its failed tasks and commands to resolve()."""
    from validation.checks.check_terminal_bench import load_checks, run_checks
    _, selected, _ = load_checks(args.static_profile, exclude=args.exclude)
    if CHECK not in selected:
        return {}
    run_checks(tasks, out, profile=args.static_profile,
               exclude=[*args.exclude, *(name for name in selected if name != CHECK)],
               concurrency=max(1, args.concurrency))
    results = json.loads((out / 'summary.json').read_text())
    by_name = {task.name: task for task in tasks}
    return {item['task']: commands(by_name[item['task']]) for item in results['tasks']
            if any(c['check'] == CHECK and c['status'] == 'failed' for c in item['checks'])}


async def snapshot(environment, findings):
    reports = []
    for finding in findings:
        try:
            result = await environment.exec(command=finding['freeze'], timeout_sec=60)
            reports.append({**finding, 'exit_code': result.return_code,
                            'stdout': result.stdout, 'stderr': result.stderr})
        except Exception as exc:
            reports.append({**finding, 'exit_code': None, 'error': str(exc)})
    return reports


def install_capture():
    """Snapshot immediately after grading, before Harbor tears down the container."""
    from harbor.verifier.verifier import Verifier
    if getattr(Verifier, '_pip_pin_capture_installed', False):
        return
    original = Verifier.verify

    async def verify(self):
        result = await original(self)
        state = _capture.get()
        if state and (result.rewards or {}).get(state['reward_key']) == 1:
            findings = state['tasks'].get(self.task.paths.task_dir.name, [])
            if findings:
                report = await snapshot(self.environment, findings)
                (self.trial_paths.trial_dir / 'pip-freeze.json').write_text(json.dumps(report, indent=2) + '\n')
        return result

    Verifier.verify = verify
    Verifier._pip_pin_capture_installed = True


def apply(task, observations):
    """Pin supported named requirements; ambiguous/missing versions stay unchanged."""
    edits, pending = [], {}
    for observation in observations:
        if observation.get('exit_code') != 0:
            continue
        versions = {}
        for line in observation.get('stdout', '').splitlines():
            try:
                req = Requirement(line)
            except InvalidRequirement:
                continue
            if req.url or req.marker:
                continue
            specs = list(req.specifier)
            if len(specs) == 1 and specs[0].operator == '==' and '*' not in specs[0].version:
                versions.setdefault(canonicalize_name(req.name), set()).add(specs[0].version)
        for entry in observation['requirements']:
            req = Requirement(entry['requirement'])
            observed = versions.get(canonicalize_name(req.name), set())
            if len(observed) != 1:
                continue
            version = next(iter(observed))
            if not req.specifier.contains(version, prereleases=True):
                continue
            extras = '[' + ','.join(sorted(req.extras)) + ']' if req.extras else ''
            replacement = shlex.quote(f'{req.name}{extras}=={version}')
            key = (observation['file'], entry['start'], entry['end'])
            pending.setdefault(key, []).append({**entry, 'file': observation['file'], 'replacement': replacement})
    by_file = {}
    for (filename, start, end), candidates in pending.items():
        if len({c['replacement'] for c in candidates}) == 1:
            by_file.setdefault(filename, []).append(candidates[0])
    for filename, changes in by_file.items():
        path = task / filename
        text = path.read_text()
        if any(text[c['start']:c['end']] != c['original'] for c in changes):
            raise ValueError(f'stale pip command evidence: {filename}')
        for change in sorted(changes, key=lambda c: c['start'], reverse=True):
            text = text[:change['start']] + change['replacement'] + text[change['end']:]
        path.write_text(text)
        edits.extend(changes)
    return edits


def resolve(tasks, findings, out, args):
    """Build/run flagged tasks with the normal oracle; only reward 1 permits edits."""
    from validation.stages.runner import run_trial_batch
    from validation.stages.harbor import trial_results
    from validation.upstream import checkout
    selected, skipped = [], []
    for task in tasks:
        if task.name not in findings:
            continue
        try:
            from harbor.models.task.task import Task
            from harbor.models.task.verifier_mode import resolve_effective_verifier_env_config
            config = Task(task).config
            separate = resolve_effective_verifier_env_config(config, None) is not None
        except Exception as exc:
            skipped.append({'task': str(task), 'status': 'unresolved', 'reason': str(exc)})
            continue
        if not findings[task.name] or config.steps or separate:
            skipped.append({'task': str(task), 'status': 'unresolved',
                            'reason': 'unsupported command or separate/multistep verifier'})
        else:
            selected.append(task)
    if not selected:
        return skipped
    discovery = copy.copy(args)
    discovery.attempts = 1
    discovery._pip_pin_findings = findings
    items = run_trial_batch(4, selected, out, discovery, checkout('terminal-bench'))
    for item in items:
        item['pip_snapshots'] = []
        if item['status'] != 'passed':
            continue
        for trial, result in trial_results(Path(item['job_dir'])):
            task_name = str(result.get('task_name') or '').split('/')[-1]
            if not task_name:
                task_name = Path(result.get('config', {}).get('task', {}).get('path', '')).name
            evidence = trial / 'pip-freeze.json'
            if task_name == Path(item['task']).name and evidence.is_file():
                item['pip_snapshots'].extend(json.loads(evidence.read_text()))
    return skipped + items


def prepare(args, numbers):
    if not enabled(args, numbers):
        return
    from validation.contract import verify_materialized, create, task_digest
    from validation.data.selection import discover_tasks, select_paths
    from validation.stages.runner import save
    parent = verify_materialized(args)
    tasks = select_paths(discover_tasks(args.tasks), args)
    out = args.out.resolve() / 'pip-normalization' / uuid4().hex[:12]
    if any(out.is_relative_to(task.resolve()) for task in tasks):
        raise ValueError('--out must be outside source task directories')
    out.mkdir(parents=True)
    findings = getattr(args, '_pip_findings', None)
    if findings is None:
        findings = check(tasks, args, out / 'check')
    if not findings:
        return
    save(out / 'findings.json', findings)
    print(f'Pip pin discovery: {len(findings)} flagged tasks; running supported oracles.', flush=True)
    hashes = {task.name: task_digest(task) for task in tasks}
    try:
        items = resolve(tasks, findings, out / 'oracle', args)
    except Exception as exc:
        items = [{'task': str(task), 'status': 'unresolved', 'reason': str(exc)}
                 for task in tasks if task.name in findings]
    save(out / 'resolver-evidence.json', {'purpose': 'pip-pin-discovery-only', 'task_hashes': hashes, 'items': items})
    # Copy the selected dataset only when a passing oracle produced a snapshot.
    if not any(item.get('pip_snapshots') for item in items):
        return
    destination = out / 'tasks'
    from validation.checks.instruction_suffix import prepare as prepare_copy
    derived = copy.copy(args)
    derived.path_resolution_report = []
    # A derived task tree needs fresh stage-3 validation.
    derived.reuse_stage3 = None
    prepare_copy(derived, destination)
    by_name = {task.name: task for task in tasks}
    edits = []
    for item in items:
        name = Path(item['task']).name
        if item['status'] == 'passed' and item.get('pip_snapshots'):
            if task_digest(by_name[name]) != hashes[name]:
                raise ValueError(f'task changed during pip discovery: {name}')
            edits.append({'task': name, 'edits': apply(destination / name, item['pip_snapshots'])})
    save(out / 'edits.json', edits)
    if not any(item['edits'] for item in edits):
        return
    derived.static_resume = None
    for key in list(vars(derived)):
        if key.startswith('_'):
            delattr(derived, key)
    meta_path = destination / 'source.json'
    meta = json.loads(meta_path.read_text())
    changes = meta.setdefault('automation_changes', {})
    for item in edits:
        if item['edits']:
            changes[item['task']] = sorted(set(changes.get(item['task'], [])) | {'dependency-pinning'})
    meta['pip_normalization'] = {'parent_contract_sha256': parent['sha256'] if parent else None,
                                 'evidence': str(out / 'resolver-evidence.json'), 'edits': edits}
    save(meta_path, meta)
    contract_path = out / 'contract.json'
    create(derived, numbers, contract_path)
    args.tasks, args.contract = destination, contract_path
    args.limit = args.task_id_range = args.static_resume = None
    print(f'Pip pins saved: {destination}; normal validation follows.', flush=True)
