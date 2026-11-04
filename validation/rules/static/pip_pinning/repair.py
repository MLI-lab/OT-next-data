"""Pin unpinned pip requirements to the versions the reference run installed.

Stage 2 snapshots `pip freeze` after a reference run scores 1 (install_capture);
the automatic repair at the end of the run applies pin() to the task's files
(validation/automatic_repairs.py). No extra runs; original inputs never change.
"""
from __future__ import annotations

from contextvars import ContextVar
import json
from pathlib import Path
import re
import shlex

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name

CHECK = 'check-pip-pinning.sh'
FILES = ('environment/Dockerfile', 'tests/Dockerfile', 'tests/test.sh', 'solution/solve.sh')
_capture = ContextVar('pip_pin_capture', default=None)
_TOKEN = re.compile(r'''(?:[^\s'"\\]+|'[^']*'|"[^"]*")+''')
_COMMAND = re.compile(r'(?m)(?:^[ \t]*(?:RUN[ \t]+)?|(?<=[;&| \t])(?=/)|(?<=\|\|)[ \t]*(?=python[0-9.]+\s+-m\s+pip\b))(?P<pip>(?:/[\w./-]+/)?pip[0-9.]*|(?:/[\w./-]+/)?python[0-9.]*[ \t]+-m[ \t]+pip)[ \t]+install[ \t]+(?P<args>[^\n]*)')
_VALUE_OPTIONS = {'-i', '--index-url', '--extra-index-url', '-f', '--find-links', '--trusted-host', '--timeout', '--retries', '--cache-dir'}
_BOOL_OPTIONS = {'--no-cache-dir', '--no-deps', '--upgrade', '-U', '--pre', '--prefer-binary', '--disable-pip-version-check', '--quiet', '-q', '--verbose', '-v', '--user', '--no-input'}


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


def pin(files, observations):
    """Pin supported named requirements in a task's files (path -> bytes).

    Returns (files, edits). Ambiguous or missing versions stay unchanged."""
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
    files = dict(files)
    for filename, changes in by_file.items():
        text = files[filename].decode()
        if any(text[c['start']:c['end']] != c['original'] for c in changes):
            raise ValueError(f'stale pip command evidence: {filename}')
        for change in sorted(changes, key=lambda c: c['start'], reverse=True):
            text = text[:change['start']] + change['replacement'] + text[change['end']:]
        files[filename] = text.encode()
        edits.extend(changes)
    return files, edits
