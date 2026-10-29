#!/usr/bin/env python3
"""Static review flag: grading only checks the submitted code's text.

Flagged only on positive evidence: the Python verifier analysis
(verifier_text_comparison) fully models the grading, finds at least one check on
the agent's source text (keyword, regex, AST) and no assert on what the code
does, and no behavioural test is found elsewhere. A verifier that is not fully
understood passes.

A behavioural test checks the output of something the verifier itself ran:
- Python tests: asserts on program stdout, HTTP responses, files read after
  running a program, calls into agent code, exit codes compared with a non-zero value.
- Shell (setup.sh then test.sh): a run of the agent's program or a service
  request whose output is compared on the same line or through a variable, or
  graded afterwards by a /tests script.
- A test suite (pytest on project tests, make/npm/go/cargo test, provided test
  scripts) that runs on files restored from /tests, git or a /tests patch.

Behaviour run only from a harness the agent could have edited (see
verifier_editable_harness) does not count. Tasks whose verifier runs in a
separate environment are analysed the same way; only the editable-harness part
depends on the shared mode. No task code is executed.
"""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[4]))  # run as a stage-4 script
import ast
import json
import shlex

import re

from validation.rules.static.no_behavioural_tests import editable_harness as harness
from validation.rules.static.no_behavioural_tests import text_comparison as text

CODE_REQUEST = re.compile(r'\b(write|create|implement|develop|build|add)\b[^.\n]*?(\b(script|program|function|module|class)\b|'
                          r'[\w./-]+\.(py|sh|bash|js|ts|c|cc|cpp|go|rs|java|rb|pl|php|cs|kt|scala|lua|r|jl)\b)', re.I)


# A shell write target: `> f`, `>> f`, `tee f`, or the destination of cp/mv/install.
WRITE_TARGET = re.compile(r'(?:>>?|\btee(?:\s+-a)?)\s*["\']?([\w./~$-]+)|\b(?:cp|mv|install)\b[^\n;&|]*\s["\']?([\w./~$-]+)["\']?\s*$', re.M)


def reference_writes_code(task):
    """Whether the reference solution produces code; None when the task has no reference solution.

    Code: a patch that changes a code file, or a code file the reference ships next to solve.sh
    or writes/copies in solve.sh, provided the instruction or the tests name it (by file or
    module name); an unnamed script is a helper for producing the result, not the deliverable."""
    solution = task / 'solution'
    files = [p for p in solution.rglob('*') if p.is_file()] if solution.is_dir() else []
    if not files:
        return None
    named = (task / 'instruction.md').read_text(errors='replace') if (task / 'instruction.md').is_file() else ''
    named += '\n'.join(p.read_text(errors='replace') for p in (task / 'tests').rglob('*')
                       if p.is_file() and p.stat().st_size < 5_000_000) if (task / 'tests').is_dir() else ''
    def deliverable(path):
        p = Path(path)
        return bool(re.search(r'(?<![\w.-])' + re.escape(p.name) + r'(?![\w-])', named)
                    or re.search(r'\b(?:from|import)\s+' + re.escape(p.stem) + r'\b', named))
    for f in files:
        rel = f.relative_to(solution).as_posix()
        if rel != 'solve.sh' and f.suffix.lower() in text.CODE_SUFFIXES and deliverable(rel):
            return True
        body = f.read_text(errors='replace') if f.stat().st_size < 5_000_000 else ''
        if any(Path(line[4:].split('\t')[0].strip()).suffix.lower() in text.CODE_SUFFIXES
               for line in body.splitlines() if line.startswith('+++ ') and not line.startswith('+++ /dev/null')):
            return True
        if rel == 'solve.sh':
            targets = [a or b for a, b in WRITE_TARGET.findall(body)]
            if any(Path(t).suffix.lower() in text.CODE_SUFFIXES and deliverable(t) for t in targets):
                return True
    return False


def graded_output_files(task):
    """Files a /tests grader reads in test.sh, as an argument or via `<`; not code, /tests or /logs files."""
    path = task / 'tests' / 'test.sh'
    if not path.is_file():
        return []
    sh = path.read_text(errors='replace')
    variables = dict(re.findall(r'^\s*(\w+)=["\']?([^"\'\s;]+)', sh, re.M))
    found = []
    for line in sh.splitlines():
        m = re.search(r'/tests/[\w./-]+(.*)', line.split('#')[0])
        if not m:
            continue
        for token in re.findall(r'["\']?(\$\{?\w+\}?|/[\w./-]+)["\']?', m[1]):
            token = variables.get(token.strip('${}'), token) if token.startswith('$') else token
            if (token.startswith('/') and not token.startswith(('/tests/', '/logs/', '/dev/'))
                    and Path(token).suffix.lower() not in text.CODE_SUFFIXES):
                found.append(token)
    return sorted(set(found))

# test.sh lines that only set up, report or exit; anything else makes the verifier not understood.
SHELL_BOILERPLATE = re.compile(
    r'^(?:fi|else|then|done|esac|;;|\{|\}|\)|set [-+][\w -]+|exit\b.*|\w+=\$(?:\?|\{PIPESTATUS\[\d\]\})|'
    r'(?:el)?if \[\[? [^;`]*\]\]?; then|case "?\$\w+"? in|[\w*]+\)\s*(?:echo|printf) [^;`]*(?:; exit "?\$\w+"?)? ?;;|'
    r'(?:echo|printf) [^;`|]*|mkdir -p [\w/.$"-]+|rm -f [\w/. "]*/logs/[\w/. "]*|'
    r'cd [\w/.$"-]+|export \w+=[^;`]*|(?:source|\.) "?\$HOME/\.local/bin/env"?|'
    r'cat /logs/[\w/.-]+|(?:uv )?pip3? install [^;`|]*|apt-get [^;`|]*|'
    r'\(?(?:set -o pipefail; )?curl [^;`]*astral\.sh/uv/[^;`]*\| *(?:ba)?sh\)?|uv (?:venv|init|add|sync)\b[^;`|]*)$')
TEST_FILE_MODULES = {'__future__', 'os', 'os.path', 're', 'stat', 'pathlib', 'pytest', 'ast', 'typing', 'textwrap',
                     'string', 'pwd', 'grp', 'hashlib', 'fnmatch', 'shlex', 'tokenize', 'sys', 'unittest'}
PATH_READS = {'read_text', 'read_bytes', 'open'}
PATH_META = {'exists', 'is_file', 'is_dir', 'stat', 'lstat', 'owner', 'group', 'is_symlink'}
OS_META = {'os.path.exists', 'os.path.isfile', 'os.path.isdir', 'os.path.getsize', 'os.stat', 'os.lstat', 'os.access'}


def shell_text_check(segment, variables):
    """Code files a test.sh condition reads (grep, [ -f FILE ]), or None when it does anything else."""
    segment = re.sub(r'\s*(?:[12]?>\s*/dev/null|2>&1)', '', segment).strip()
    segment = re.sub(r'^!\s*', '', segment)
    if '$(' in segment or '`' in segment or '|' in segment:
        return None  # a command substitution or pipe runs something else
    try:
        tokens = shlex.split(segment)
    except ValueError:
        return None
    tokens = [re.sub(r'\$\{?(\w+)\}?', lambda m: variables.get(m[1], '\0'), t) for t in tokens]
    if tokens and tokens[0] in ('[', '[[', 'test'):
        body = tokens[1:-1] if tokens[0] != 'test' else tokens[1:]
        if len(body) == 2 and body[0] in ('-f', '-e', '-x', '-s', '-r', '-d'):
            return [] if Path(body[1]).suffix.lower() in text.CODE_SUFFIXES or body[0] == '-d' else None
        # A comparison of variables or numbers ([ "$n" -ge 1 ]); command substitutions never get here.
        return [] if not any(t.startswith('-') and t[1:] in 'efxsrdLhpSbcugkOGNt' and len(t) == 2 for t in body) else None
    if not tokens or tokens[0] not in ('grep', 'egrep', 'fgrep'):
        return None
    files, pattern, options = [], False, True
    for i, t in enumerate(tokens[1:], 1):
        if t == '--' and options:
            options = False
            continue
        if options and t.startswith('-') and not (i > 1 and tokens[i - 1] == '-e'):
            if (re.search(r'[rRfd]', t[1:]) if not t.startswith('--') else
                    t.startswith(('--file', '--recursive', '--dereference-recursive', '--directories'))):
                return None
            pattern = pattern or t == '-e'
        elif options and i > 1 and tokens[i - 1] == '-e':
            continue
        elif not pattern:
            pattern = True
        else:
            files.append(t)
    if not files or any('\0' in f or Path(f).suffix.lower() not in text.CODE_SUFFIXES for f in files):
        return None
    return files


def verifier_steps(task, path=None, depth=0):
    """(Python test files, code files read by shell checks) when test.sh only runs pytest, unittest,
    `python /tests/x.py` or `bash /tests/x.sh` (checked the same way) on /tests files and greps/tests
    code files; None when it does anything else."""
    path = path or task / 'tests' / 'test.sh'
    if depth > 3 or not path.is_file() or any(next((task / 'tests').rglob(n), None) for n in ('conftest.py', 'pytest.ini')):
        return None
    lines = [re.sub(r'\s*\|\| exit \$\?$', '', l.split(' #')[0].strip()) for l in path.read_text(errors='replace').replace('\\\n', ' ').splitlines()]
    files, reads, variables = [], [], {}
    statements = []
    for line in [l for l in lines if l and not l.startswith('#')]:
        if '$(' not in line and SHELL_BOILERPLATE.match(line):  # no hidden command substitution
            continue
        # One statement at a time: `if [ $? -eq 0 ]; then echo 1 > ...; fi` -> condition, echo, fi.
        for part in re.split(r';(?!;)\s*', line):
            part = re.sub(r'^(?:(?:el)?if|then|else|do)\s+', '', part.strip())
            if part and part not in ('then', 'else', 'fi', 'done', 'esac'):
                statements.append(part)
    for line in statements:
        if '$(' not in line and SHELL_BOILERPLATE.match(line):
            continue
        assigned = re.fullmatch(r'(\w+)=(["\']?)([\w./-]+)\2', line)
        if assigned:
            variables[assigned[1]] = assigned[3]
            continue
        # A condition built from grep / [ -f ] on code files: `if grep -q x f; then`, `grep -q x f && echo 1 > ...`.
        condition = line
        captured = re.fullmatch(r'\w+=\$\((.*)\)', condition)
        segments = re.split(r'\s*(?:&&|\|\|)\s*', captured[1] if captured else condition)
        nested = re.fullmatch(r'(?:(?:ba)?sh\s+)?(/tests/[\w./-]+\.sh)', re.sub(r'\s*(?:2>&1\s*)?(?:(?:\|\s*tee (?:-a )?|>>?\s*)/logs/[\w/.-]+)?$', '', line))
        if nested and '..' not in nested[1]:
            inner = verifier_steps(task, task / nested[1][1:], depth + 1)
            if inner is None:
                return None
            files += inner[0]
            reads += inner[1]
            continue
        if not any(re.match(r'\s*!?\s*(?:e|f)?grep\b', s) for s in segments) and 'pytest' not in line \
                and not re.search(r'\bpython3?\b', line):
            if not captured and all(SHELL_BOILERPLATE.match(s) or shell_text_check(s, variables) == [] for s in segments):
                continue
            return None
        if any(re.match(r'\s*!?\s*(?:e|f)?grep\b', s) for s in segments):
            got = [shell_text_check(s, variables) if not SHELL_BOILERPLATE.match(s) else [] for s in segments]
            if None in got:
                return None
            reads += [f for g in got for f in g]
            continue
        line = re.sub(r'\s*(?:2>&1\s*)?(?:(?:\|\s*tee (?:-a )?|>>?\s*)/logs/[\w/.-]+)?$', '', line)
        try:
            tokens = shlex.split(line)
        except ValueError:
            return None
        names = [Path(t).name for t in tokens]
        runner = ('pytest' if 'pytest' in names else 'unittest' if 'unittest' in names else
                  next((n for n in names if re.fullmatch(r'python3?(?:\.\d+)?', n)), None))
        if runner is None:
            return None
        at = names.index(runner) if runner in ('pytest', 'unittest') else max(i for i, n in enumerate(names) if n == runner)
        if runner == 'unittest':
            args = [a for a in tokens[at + 1:] if a not in ('-v', '-q', '-b', '-f', '--verbose', '--quiet', '--buffer', '--failfast')]
            if args[:1] == ['discover']:
                start = dict(zip(args[1::2], args[2::2])).get('-s', '.' if 'cd /tests' in lines else None)
                if start not in ('/tests', '.') or len(args) not in (1, 3):
                    return None
                files += sorted((task / 'tests').glob('test*.py'))
                continue
            args = [a if a.endswith('.py') or '/' in a else 'tests/' + a.replace('.', '/') + '.py' for a in args]
            tokens = tokens[:at + 1] + ['/' + a if a.startswith('tests/') else a for a in args]
            names = [Path(t).name for t in tokens]
            runner = 'pytest'  # same selector rules as pytest: /tests/x.py files
        if any(t not in ('uvx', 'uv', 'run', 'python', 'python3', '-m', '--with', '-w', '-p') and
               not re.fullmatch(r'[\w.-]+(?:[=<>]=?[\w.]+)?', t) for t in names[:at]):
            return None
        args = tokens[at + 1:]
        if runner != 'pytest' and (not args or args[0].startswith('-') or len(args) > 1):
            return None  # only `python /tests/x.py`, no -m/-c or arguments
        for i, a in enumerate(args):
            if a.startswith('-'):
                if a in ('-p', '-c', '--rootdir', '--confcutdir', '-o', '--override-ini'):
                    return None
            elif re.fullmatch(r'/tests/[\w./-]+\.py(?:::\w+)*', a) and (task / a.split('::')[0][1:]).is_file():
                files.append(task / a.split('::')[0][1:])
            elif (re.fullmatch(r'\w[\w.-]*\.py(?:::\w+)*', a) and 'cd /tests' in lines
                  and (task / 'tests' / a.split('::')[0]).is_file()):
                files.append(task / 'tests' / a.split('::')[0])
            elif not (i and args[i - 1] in ('--ctrf', '--junitxml', '--tb')) and not a.startswith('/logs/'):
                return None
    return (files, reads) if files or reads else None


def source_reads(path):
    """Code files a pytest file reads, when every file it touches resolves to a constant path.

    Returns (code files read, reason); reason is set when the file does anything the
    text-only check cannot vouch for (unknown imports, dynamic paths, non-code files)."""
    tree = ast.parse(path.read_text(errors='replace'))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            mods = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or '']
            if any(m not in TEST_FILE_MODULES for m in mods) or getattr(node, 'level', 0):
                return [], 'import ' + ','.join(mods)
        if isinstance(node, ast.Name) and node.id in ('exec', 'eval', '__import__', 'globals'):
            return [], 'dynamic code: ' + node.id
    fixtures = {f.name: f for f in tree.body if isinstance(f, ast.FunctionDef)
                and any(text.name(d.func if isinstance(d, ast.Call) else d).endswith('fixture') for d in f.decorator_list)}

    def value(expr, env):
        if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
            return expr.value
        if isinstance(expr, ast.Name):
            return env.get(expr.id)
        if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.Div):
            a, b = value(expr.left, env), value(expr.right, env)
            return None if a is None or b is None else a.rstrip('/') + '/' + b.lstrip('/')
        if isinstance(expr, ast.JoinedStr):
            parts = [value(v.value if isinstance(v, ast.FormattedValue) else v, env) for v in expr.values]
            return None if None in parts else ''.join(parts)
        if (isinstance(expr, ast.Call) and not expr.keywords and expr.args and
                text.name(expr.func) in ('Path', 'pathlib.Path', 'PurePath', 'str', 'os.fspath', 'os.path.join')):
            parts = [value(a, env) for a in expr.args]
            return None if None in parts else '/'.join([parts[0].rstrip('/')] + [x.strip('/') for x in parts[1:]])
        return None

    def assign(stmts, env):
        for stmt in stmts:
            if isinstance(stmt, (ast.Assign, ast.AnnAssign)) and stmt.value is not None:
                for t in stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]:
                    if isinstance(t, ast.Name):
                        env[t.id] = value(stmt.value, env)
        return env

    module = assign(tree.body, {})
    for name, f in fixtures.items():
        ret = [s for s in f.body if isinstance(s, ast.Return)]
        module[name] = value(ret[0].value, assign(f.body, dict(module))) if len(ret) == 1 and ret[0].value else None
    helpers = {f.name: f for f in tree.body if isinstance(f, ast.FunctionDef)
               and f.name not in fixtures and not f.name.startswith('test')}
    called = {id(c.func) for c in ast.walk(tree) if isinstance(c, ast.Call)}
    if any(isinstance(n, ast.Name) and n.id in helpers and id(n) not in called for n in ast.walk(tree)):
        return [], 'helper used other than by a direct call'
    reads = set()

    def check(f, env, depth):
        """Reason the function touches something other than constant code files, or None."""
        env = assign([n for n in ast.walk(f) if isinstance(n, (ast.Assign, ast.AnnAssign))], env)
        for call in [n for n in ast.walk(f) if isinstance(n, ast.Call)]:
            fn = text.name(call.func)
            if fn in helpers:
                params = [a.arg for a in helpers[fn].args.args]
                if depth > 4 or len(call.args) > len(params):
                    return 'helper call too deep or variadic: ' + fn
                bound = dict(module)
                bound.update({p: value(a, env) for p, a in zip(params, call.args)})
                bound.update({k.arg: value(k.value, env) for k in call.keywords if k.arg})
                reason = check(helpers[fn], bound, depth + 1)
                if reason:
                    return reason
                continue
            if (fn in ('os.system', 'os.popen', 'os.listdir', 'os.walk', 'os.scandir') or fn.startswith(('os.exec', 'os.spawn'))
                    or fn.split('.')[-1] in ('glob', 'rglob', 'iterdir')):
                return 'unmodelled call ' + fn
            if fn == 'open' or fn in OS_META:
                if not call.args:
                    return 'unmodelled call ' + fn
                target, read = value(call.args[0], env), fn == 'open'
            elif isinstance(call.func, ast.Attribute) and call.func.attr in PATH_READS | PATH_META:
                target, read = value(call.func.value, env), call.func.attr in PATH_READS
            else:
                continue
            if target is None:
                return f'unresolved file in {fn} at line {call.lineno}'
            code = Path(target).suffix.lower() in text.CODE_SUFFIXES
            if not code and (read or Path(target).suffix):
                return 'non-code file ' + target
            if read and code:
                reads.add(target)
        return None

    entries = [f for f in ast.walk(tree) if isinstance(f, ast.FunctionDef) and f.name not in helpers]
    # Module-level statements run too (plain grader scripts, constants read at import).
    entries.append(ast.Module(body=[n for n in tree.body if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))],
                              type_ignores=[]))
    for f in entries:
        env = dict(module)
        env.update({a.arg: module.get(a.arg) if a.arg in fixtures else None for a in getattr(f, 'args', ast.arguments(args=[])).args})
        reason = check(f, env, 0)
        if reason:
            return [], reason
    return sorted(reads), None


def text_only_grading(task):
    """Positive evidence that grading only reads code text: test.sh only runs Python test files from
    /tests and greps code files, nothing is executed, and every file touched is a code file."""
    steps = verifier_steps(task)
    if steps is None:
        return dict(flag=False, reason='test.sh does more than run /tests Python files and grep code files')
    files, reads = steps[0], set(steps[1])
    for f in files:
        try:
            got, reason = source_reads(f)
        except (SyntaxError, UnicodeError, RecursionError) as exc:
            got, reason = [], str(exc)
        if reason:
            return dict(flag=False, reason=f'{f.relative_to(task)}: {reason}')
        reads |= set(got)
    return dict(flag=bool(reads), code_files_read=sorted(reads), reason=None if reads else 'no code file is read')


def programming_task(instruction, python, task=None):
    """Tests that grade a submitted code file decide first; then the reference solution when there
    is one; otherwise whether the instruction asks for code."""
    if python.get('inferred_submitted_artifacts'):
        return True  # the tests grade a file the agent submits as code
    reference = reference_writes_code(Path(task)) if task is not None else None
    if reference is not None:
        return reference
    return bool(CODE_REQUEST.search(instruction))


def analyze_task(task):
    task = Path(task)
    instruction = (task / 'instruction.md').read_text(errors='replace') if (task / 'instruction.md').is_file() else ''
    python = text.analyze_task(task)
    mode = harness.verifier_mode(task)
    events, _ = harness.classify(task, instruction)
    if mode == 'separate':
        # A fresh verifier environment: nothing the agent edited outside artifacts is run.
        for e in events:
            if e['role'] == 'editable_harness':
                e['role'] = 'trusted'
    evidence = []
    # /tests scripts run as graders (`python /tests/grade.py out.txt`) are only as
    # trustworthy as the run they grade; that is decided by the shell evidence.
    # Only scripts that grade something run before them; a /tests script that is the
    # test itself (python3 /tests/verifier.py) is analysed like any test file.
    graders, ran_before = set(), False
    for e in events:
        if e['kind'] in ('run', 'suite', 'net') and e['role'] != 'tests_dir':
            ran_before = True
        if e['kind'] == 'grade' and ran_before:
            graders.add('tests/' + e['path'][len('/tests/'):])
    files = [f for f in python['files'] if f['file'] not in graders]
    lines = sum(len(f.get('behavioural_assert_lines', [])) for f in files)
    if lines:
        evidence.append(dict(kind='python_assert', count=lines))
    program = programming_task(instruction, python, task)
    state = sum(len(f.get('final_state_assert_lines', [])) for f in files)
    expected = sum(len(f.get('expected_output_assert_lines', [])) for f in files)
    if program and expected:
        # The program is not run, but its output must equal exact data only the verifier has.
        evidence.append(dict(kind='expected_output_check', count=expected))
    if state and not program:
        # The requested files are the result: checking their contents/attributes is the test.
        evidence.append(dict(kind='final_state_check', count=state,
                             files=sorted({p for f in files for p in f.get('final_state_files', [])})[:10]))
    for i, e in enumerate(events):
        if e['role'] == 'trusted' and (e['kind'] == 'suite' or e['test_like']):
            evidence.append(dict(kind='restored_test_suite', path=e['path'], file=e['file'], line=e['line']))
        if e['kind'] in ('run', 'net') and e['role'] in ('deliverable', 'trusted', None):
            later = events[i + 1:]
            same_line = any(x['kind'] == 'compare' and x['file'] == e['file'] and x['line'] == e['line'] for x in later)
            graded = any(x['kind'] == 'grade' for x in later)
            compared = any(x['kind'] == 'compare' for x in later if x['file'] == e['file'])
            if same_line or graded or (compared and e['kind'] == 'run'):
                evidence.append(dict(kind='shell_output_check', path=e['path'], file=e['file'], line=e['line'],
                                     command=e['text'][:200]))
    editable = [dict(path=e['path'], file=e['file'], line=e['line'], command=e['text'][:200])
                for e in events if e['role'] == 'editable_harness']
    text_checks = python['status'] in ('review', 'text_check_with_behavioural_tests')
    if not program and not any(e['role'] == 'editable_harness' for e in events):
        graded = graded_output_files(task)
        if graded:
            # No code is submitted; a /tests grader judges the produced answer or output file.
            evidence.append(dict(kind='graded_output_file', files=graded[:10]))
    # Positive evidence only: a fully modelled verifier whose checks read the source text.
    text_only = text_only_grading(task)
    flagged = (python['status'] == 'review' or text_only['flag']) and not evidence
    return dict(task=task.name, status='review' if flagged else 'not_evidenced', verifier_mode=mode,
                programming_task=program, final_state_asserts=state,
                behavioural_evidence=evidence[:20], editable_harness=editable[:20],
                source_text_checks=text_checks, text_comparison_status=python['status'], text_only_grading=text_only,
                interpretation='Static: flagged only when the fully modelled verifier checks the source text and '
                               'nothing it runs; verifiers that are not fully understood pass. Behaviour run only '
                               'through an editable harness does not count. Not proof that the task is exploitable.')


def check(task):
    """Stage-4 rule: 'flagged' when grading only reads the submitted code's text."""
    result = analyze_task(task)
    return ('flagged' if result['status'] == 'review' else 'passed'), result


if __name__ == '__main__':
    from validation.rules.static.script import run
    sys.exit(run(check))
