#!/usr/bin/env python3
"""Static review flag: the verifier runs a test harness the agent could edit.

Harbor uploads tests/ into the agent's container after the agent finishes and
runs test.sh there (shared verifier mode, the default). /tests is therefore
fresh, but every other file is in whatever state the agent left it. A verifier
that executes a provided test or check script from the agent workspace, without
restoring or checksumming it first, can be passed by editing that script.

Scope: tests/setup.sh (run first by harbor_patches/verifier_setup.py), then
tests/test.sh, following nested /tests scripts and `bash -c` strings, plus
subprocess calls in tests/*.py. Tasks whose task.toml selects a separate
verifier environment are not applicable. No task code is executed; findings are
review evidence, not proof that a task can be exploited.
"""
from __future__ import annotations
import argparse
import ast
import json
from pathlib import Path, PurePosixPath
import re
import shlex

try:
    import tomllib
except ImportError:  # pragma: no cover
    import tomli as tomllib

RUN_KINDS = {'run', 'suite', 'net'}
COMPARE = re.compile(r'\|\s*(grep|egrep|fgrep|diff|cmp|jq|awk)\b|\b(diff|cmp)\s|\[\[?\s|\btest\s|==|!=|\s-(eq|ne|lt|gt|le|ge)\s')
INTERPRETERS = re.compile(r'^(bash|sh|zsh|dash|python[\d.]*|node|perl|ruby|Rscript|php|pwsh)$')
WRAPPERS = {'timeout', 'env', 'nice', 'sudo', 'exec', 'time', 'xvfb-run', 'stdbuf', 'nohup', 'su'}
SUITE_COMMANDS = [('make', {'test', 'check', 'tests'}), ('npm', {'test'}), ('yarn', {'test'}), ('pnpm', {'test'}),
                  ('go', {'test'}), ('cargo', {'test'}), ('mvn', {'test', 'verify'}), ('gradle', {'test'}),
                  ('./gradlew', {'test'}), ('ctest', None), ('tox', None), ('nox', None), ('phpunit', None)]
TRUSTED_ROOTS = ('/tests', '/setup_files', '/logs', '/usr', '/bin', '/sbin', '/lib', '/etc', '/proc', '/dev', '/opt', '/var/lib/dpkg')
TEST_LIKE = re.compile(r'(^|[_./-])(tests?|spec|check|verify|grade|eval|run[_-]?tests?|harness)([_./-]|$)', re.I)
CHANGE_WORDS = re.compile(r'\b(create|write|implement|add|build|develop|make|produce|generate|modify|fix|update|edit|extend)\b', re.I)


def verifier_mode(task):
    cfg = task / 'task.toml'
    if not cfg.is_file():
        return 'shared'
    try:
        data = tomllib.loads(cfg.read_text())
    except (tomllib.TOMLDecodeError, UnicodeError):
        return 'unknown'
    steps = [data.get('verifier', {})] + [s.get('verifier', {}) for s in data.get('steps', []) if isinstance(s, dict)]
    if any(v.get('environment_mode') == 'separate' or 'environment' in v for v in steps if isinstance(v, dict)):
        return 'separate'
    return 'shared'


def norm(path, cwd):
    if not path or path.startswith('$') or '$(' in path or '*' in path:
        return None
    if not path.startswith('/'):
        if not cwd:
            return None
        path = str(PurePosixPath(cwd) / path)
    parts = []
    for part in PurePosixPath(path).parts:
        if part == '..':
            parts = parts[:-1]
        elif part != '.':
            parts.append(part)
    return str(PurePosixPath(*parts)) if parts else '/'


def covers(restored, target):
    """A restored path covers the target if it is the target or an ancestor."""
    return target == restored or target.startswith(restored.rstrip('/') + '/')


def patch_paths(task, patch, cwd):
    """Files a /tests patch writes, resolved against the directory it is applied in."""
    local = task / 'tests' / PurePosixPath(patch).relative_to('/tests') if patch.startswith('/tests/') else None
    if not local or not local.is_file():
        return []
    out = []
    for line in local.read_text(errors='replace').splitlines():
        m = re.match(r'^\+\+\+ (?:b/)?(\S+)', line)
        if m and m.group(1) != '/dev/null':
            p = norm(m.group(1), cwd)
            if p:
                out.append(p)
    return out


class Shell:
    """Line-oriented walk over verifier shell scripts with cwd tracking."""

    def __init__(self, task):
        self.task = task
        self.cwd = None
        self.events = []  # (kind, path, source, line, text)
        self.seen = set()
        self.vars = {}  # simple VAR=literal assignments
        self.ran_vars = set()  # variables holding output of something the verifier ran

    def script(self, container_path, depth=0):
        if depth > 4 or container_path in self.seen or not container_path.startswith('/tests/'):
            return
        local = self.task / 'tests' / PurePosixPath(container_path).relative_to('/tests')
        if not local.is_file():
            return
        self.seen.add(container_path)
        text = local.read_text(errors='replace')
        # Join continuations; drop heredoc bodies but record the file they write.
        lines, heredoc = [], None
        for number, raw in enumerate(text.replace('\\\n', ' ').splitlines(), 1):
            if heredoc:
                if raw.strip() == heredoc:
                    heredoc = None
                continue
            m = re.search(r"<<-?\s*['\"]?(\w+)['\"]?", raw)
            if m:
                heredoc = m.group(1)
            lines.append((number, raw))
        for number, raw in lines:
            before = len(self.events)
            m = re.match(r'\s*(?:export\s+|local\s+)?(\w+)=\$\(', raw)
            for command in re.split(r'\s*(?:&&|\|\||;|\|(?!\|))\s*', raw):
                self.command(command, container_path, number, depth)
            ran_here = any(e[0] in RUN_KINDS for e in self.events[before:])
            if m and ran_here:
                self.ran_vars.add(m.group(1))
            uses = {v for v in re.findall(r'\$\{?(\w+)', raw)} & self.ran_vars
            if COMPARE.search(raw) and (ran_here or uses):
                self.events.append(('compare', None, container_path, number, raw.strip()))

    def command(self, text, source, number, depth):
        # Command substitutions run their inner command; the outer assignment does not.
        for inner in re.findall(r'\$\(([^()]*)\)', text):
            self.command(inner, source, number, depth)
        text = re.sub(r'\$\([^()]*\)', '$X', text).strip().lstrip('(').rstrip(')').strip()
        m = re.fullmatch(r'(?:export\s+|local\s+)?(\w+)=([\'"]?)([^\s\'"$`]*)\2', text)
        if m:
            self.vars[m.group(1)] = m.group(3)
            return
        text = re.sub(r'\$\{?(\w+)\}?', lambda v: self.vars.get(v.group(1), v.group(0)), text)
        try:
            tokens = shlex.split(text, comments=True)
        except ValueError:
            tokens = text.split()
        if not tokens:
            return
        # Redirection writes (`cat > f`, `echo x > f`) recreate the target.
        for m in re.finditer(r'(?<![0-9&])>>?\s*([^\s;&|]+)', text):
            p = norm(m.group(1), self.cwd)
            if p and not m.group(0).startswith('>>'):
                self.events.append(('restore', p, source, number, text.strip()))
        while tokens and (re.match(r'^\w+=', tokens[0]) or tokens[0] in WRAPPERS or tokens[0] in {'if', 'then', 'else', 'do', '!', '{', '('}):
            tokens = tokens[1:]
            while tokens and tokens[0].startswith('-'):
                tokens = tokens[1:]
            if tokens and tokens[0].isdigit():  # timeout 60
                tokens = tokens[1:]
        if not tokens:
            return
        head = tokens[0]
        name = PurePosixPath(head).name
        args = tokens[1:]
        if head == 'cd' and args:
            self.cwd = norm(args[0], self.cwd) or self.cwd
            return
        if name in {'cp', 'install', 'rsync', 'ln'} and len(args) >= 2:
            srcs = [a for a in args[:-1] if not a.startswith('-')]
            dest = norm(args[-1], self.cwd)
            if dest and any(s.startswith(('/tests', '/setup_files')) for s in srcs):
                for s in srcs:
                    self.events.append(('restore', dest if len(srcs) > 1 or dest.endswith('/') else dest, source, number, text.strip()))
                    if s.startswith(('/tests', '/setup_files')):
                        self.events.append(('restore', str(PurePosixPath(dest) / PurePosixPath(s).name), source, number, text.strip()))
            return
        if name == 'tar' and any('x' in a for a in args if a.startswith('-')) and any(a.startswith('/tests') for a in args):
            dest = args[args.index('-C') + 1] if '-C' in args and args.index('-C') + 1 < len(args) else self.cwd
            if norm(dest, self.cwd):
                self.events.append(('restore', norm(dest, self.cwd), source, number, text.strip()))
            return
        if name == 'git':
            sub = [a for a in args if not a.startswith('-')]
            if sub[:1] in (['checkout'], ['restore']) and '--' in args:
                for p in args[args.index('--') + 1:]:
                    q = norm(p, self.cwd)
                    if q:
                        self.events.append(('restore', q, source, number, text.strip()))
            elif sub[:1] in (['reset'], ['stash']) or (sub[:1] == ['checkout'] and ('-f' in args or '.' in args)):
                if self.cwd:
                    self.events.append(('restore', self.cwd, source, number, text.strip()))
            elif sub[:1] == ['apply']:
                for p in sub[1:]:
                    for q in patch_paths(self.task, p, self.cwd):
                        self.events.append(('restore', q, source, number, text.strip()))
            return
        if name == 'patch':
            for i, a in enumerate(args):
                if a in {'-i', '--input'} and i + 1 < len(args):
                    for q in patch_paths(self.task, args[i + 1], self.cwd):
                        self.events.append(('restore', q, source, number, text.strip()))
            return
        if name in {'curl', 'wget', 'nc', 'ncat', 'http', 'grpcurl', 'websocat', 'redis-cli', 'psql', 'mysql', 'sqlite3'}:
            self.events.append(('net', None, source, number, text.strip()))
            return
        if re.match(r'^(sha\d+|md5)sum$', name) and '-c' in args:
            for p in re.findall(r'(/[\w./-]+)', text):
                q = norm(p, self.cwd)
                if q:
                    self.events.append(('restore', q, source, number, text.strip()))
            return
        if head in {'.', 'source'} and args:
            self.run(args[0], source, number, text, depth)
            return
        if INTERPRETERS.match(name):
            rest = [a for a in args if not a.startswith('-')]
            if '-c' in args or '-lc' in args or '-ec' in args:
                flag = next(a for a in args if a in {'-c', '-lc', '-ec'})
                i = args.index(flag)
                if i + 1 < len(args):
                    for inner in re.split(r'\s*(?:&&|\|\||;)\s*', args[i + 1]):
                        self.command(inner, source, number, depth)
                return
            if name.startswith('python') and '-m' in args:
                i = args.index('-m')
                module = args[i + 1] if i + 1 < len(args) else ''
                if module == 'pytest':
                    self.pytest(args[i + 2:], source, number, text)
                elif module in {'unittest', 'nose', 'nose2', 'tox', 'green', 'trial', 'django'} and self.cwd:
                    # Dotted test ids resolve inside the working directory.
                    self.events.append(('suite', self.cwd, source, number, text.strip()))
                return
            if rest and rest[0].startswith('/tests/') and rest[0].endswith('.py') and self.pytest_runner(rest, source, number, text):
                return
            if rest:
                self.run(rest[0], source, number, text, depth)
            return
        if name in {'nosetests', 'nose2', 'tox', 'trial'} and self.cwd:
            self.events.append(('suite', self.cwd, source, number, text.strip()))
            return
        if name in {'pytest', 'py.test'}:
            self.pytest(args, source, number, text)
            return
        for command, subs in SUITE_COMMANDS:
            if head == command and (subs is None or any(a in subs for a in args)):
                if self.cwd:
                    self.events.append(('suite', self.cwd, source, number, text.strip()))
                return
        if '/' in head and re.fullmatch(r'[\w./-]+', head):
            self.run(head, source, number, text, depth)

    def pytest_runner(self, rest, source, number, text):
        """A /tests Python script that calls pytest runs a test suite: on the files
        named by a /tests JSON id manifest argument, else from the working directory."""
        local = self.task / 'tests' / PurePosixPath(rest[0]).relative_to('/tests')
        try:
            code = local.read_text(errors='replace')
        except OSError:
            return False
        if not re.search(r'pytest\.main\(|[\'"]pytest[\'"]|-m[\'"],\s*[\'"]pytest', code):
            return False
        files = set()
        for arg in rest[1:]:
            if arg.startswith('/tests/') and arg.endswith('.json'):
                try:
                    ids = json.loads((self.task / 'tests' / PurePosixPath(arg).relative_to('/tests')).read_text())
                except (OSError, ValueError):
                    ids = []
                files |= {i.split('::')[0] for i in ids if isinstance(i, str)}
        for t in sorted(files) or [None]:
            target = norm(t, self.cwd) if t else self.cwd
            if target:
                self.events.append(('suite', target, source, number, text.strip()))
        return True

    def pytest(self, args, source, number, text):
        # Options with values (-p no:x, -W ignore, -k expr) are not paths.
        valued = {'-p', '-W', '-k', '-m', '-o', '-c', '-n', '--rootdir', '--junitxml', '--ctrf', '--tb', '--timeout', '-r'}
        paths, skip = [], False
        for a in args:
            if skip:
                skip = False
            elif a in valued:
                skip = True
            elif not a.startswith('-') and not re.match(r'^\w+=', a) and ('/' in a or a.endswith('.py') or '::' in a):
                paths.append(a.split('::')[0])
        targets = [norm(p, self.cwd) for p in paths] or [self.cwd]
        for t in targets:
            if t:
                self.events.append(('suite', t, source, number, text.strip()))

    def run(self, path, source, number, text, depth):
        p = norm(path, self.cwd)
        if not p:
            return
        if p.startswith('/tests/'):
            if p.endswith('.sh') or '.' not in PurePosixPath(p).name:
                self.script(p, depth + 1)  # /tests scripts are fresh; follow shell ones
            else:
                self.events.append(('grade', p, source, number, text.strip()))
            return
        self.events.append(('run', p, source, number, text.strip()))


def python_runs(task):
    """Scripts run through subprocess in tests/*.py (literal argument lists and strings)."""
    events = []
    calls = {'subprocess.run', 'subprocess.check_output', 'subprocess.check_call', 'subprocess.call', 'subprocess.Popen', 'os.system'}
    for path in sorted((task / 'tests').rglob('*.py')):
        try:
            tree = ast.parse(path.read_text())
        except (SyntaxError, UnicodeError):
            continue
        consts = {}
        for n in ast.walk(tree):
            if isinstance(n, ast.Assign):
                v = n.value
                if isinstance(v, ast.Call) and ast.unparse(v.func) in {'Path', 'pathlib.Path', 'str'} and v.args:
                    v = v.args[0]
                if isinstance(v, ast.Constant) and isinstance(v.value, str):
                    for t in n.targets:
                        if isinstance(t, ast.Name):
                            consts[t.id] = v.value
        for n in ast.walk(tree):
            if not (isinstance(n, ast.Call) and ast.unparse(n.func) in calls and n.args):
                continue
            first = n.args[0]
            cwd = next((k.value.value for k in n.keywords if k.arg == 'cwd' and isinstance(k.value, ast.Constant)), None)
            if isinstance(first, (ast.List, ast.Tuple)):
                def item(e):
                    if isinstance(e, ast.Constant): return e.value
                    if isinstance(e, ast.Name): return consts.get(e.id)
                    if isinstance(e, ast.Call) and ast.unparse(e.func) == 'str' and e.args: return item(e.args[0])
                    if ast.unparse(e) == 'sys.executable': return 'python3'
                    return None
                items = [item(e) for e in first.elts]
                text = ' '.join(shlex.quote(i) if isinstance(i, str) else '$X' for i in items)
            elif isinstance(first, ast.Constant) and isinstance(first.value, str):
                text = first.value
            else:
                continue
            shell = Shell(task)
            shell.cwd = cwd
            shell.command(text, str(path.relative_to(task)), n.lineno, 0)
            events += shell.events
    return events


def mentioned(instruction, path):
    return bool(instruction) and (path in instruction or re.search(rf'(?<![\w.-]){re.escape(PurePosixPath(path).name)}(?![\w-])', instruction) is not None)


def asked_to_write(instruction, path):
    name = re.escape(PurePosixPath(path).name)
    return any(CHANGE_WORDS.search(s) and re.search(rf'(?<![\w.-]){name}(?![\w-])', s) for s in re.split(r'(?<=[.!?])\s+|\n', instruction))


def verifier_events(task):
    """Ordered events of setup.sh (run first by the pipeline), test.sh and python subprocess calls."""
    shell = Shell(task)
    shell.script('/tests/setup.sh')
    shell.cwd = None
    shell.script('/tests/test.sh')
    return shell.events + python_runs(task)


def classify(task, instruction):
    """Annotate run/suite events with a role: trusted, deliverable, editable_harness, or tests_dir.

    Restorations only count when they happen before the run (event order).
    """
    out, restored = [], []
    for kind, path, source, line, text in verifier_events(task):
        e = dict(kind=kind, path=path, file=source, line=line, text=text, role=None, test_like=False)
        if kind == 'restore':
            restored.append(path)
        elif kind in ('run', 'suite') and path:
            e['test_like'] = bool(TEST_LIKE.search(PurePosixPath(path).name)) or any(TEST_LIKE.search(p) for p in PurePosixPath(path).parts[-3:-1])
            if path.startswith('/tests'):
                e['role'] = 'tests_dir'
            elif path.startswith(TRUSTED_ROOTS) or path in {'/', '/root', '/home'}:
                e['role'] = 'system'
            elif any(covers(r, path) for r in restored) or (kind == 'suite' and any(covers(path, r) for r in restored)):
                # For a suite run from a project dir, restoring its test files inside counts.
                e['role'] = 'trusted'
            elif mentioned(instruction, path) and not (e['test_like'] and not asked_to_write(instruction, path)):
                e['role'] = 'deliverable'  # running the agent's program is the behaviour under test
            else:
                e['role'] = 'editable_harness'
        out.append(e)
    return out, restored


def analyze_task(task):
    task = Path(task)
    mode = verifier_mode(task)
    if mode != 'shared':
        return dict(task=task.name, status='not_applicable' if mode == 'separate' else 'incomplete', verifier_mode=mode, findings=[])
    instruction = (task / 'instruction.md').read_text(errors='replace') if (task / 'instruction.md').is_file() else ''
    events, restored = classify(task, instruction)
    findings = [dict(path=e['path'], file=e['file'], line=e['line'], command=e['text'][:300], test_like=e['test_like'],
                     named_in_instruction=mentioned(instruction, e['path']))
                for e in events if e['role'] == 'editable_harness']
    return dict(task=task.name, status='review' if findings else 'not_evidenced', verifier_mode=mode,
                findings=findings, restored_paths=sorted(set(restored))[:50],
                interpretation='Static: the verifier executes a non-/tests script or test suite in the shared agent '
                               'environment without restoring or checksumming it first. Not proof of exploitability.')


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--task', type=Path, required=True)
    p.add_argument('--out', type=Path)
    a = p.parse_args()
    r = analyze_task(a.task)
    if a.out:
        a.out.write_text(json.dumps(r, indent=2) + '\n')
    print(json.dumps(r, indent=2))


if __name__ == '__main__':
    main()
