DATASET = 'CalibForge'
"""Repair for the verifier_tamper check.

What the check records: a wrapper around python/pytest/git/gcc/... in the agent's container notes every run of the
tool while the task is graded. The harness itself runs `python3`/`python` (and `$VIRTUAL_ENV/bin/python`) of the
agent's container with `-I -S` to discover the agent's package directories (harbor_patches/agent_package_fallback.py)
before it activates the prepared verifier runtime, and it may run `git config --system` for the proxy
(verifier_setup.git_proxy_command). Those runs are recorded too, although the verifier never started them.

Decisions
* false_positive: the verifier (tests/test.sh and the test files) starts no interpreter from the agent's container; the
  recorded python/git runs are the harness's own.
* fixed: the tests do start python/pytest by name or path from the agent's container; the test script puts the prepared
  runtime first (PATH shim for bare names) and the test file runs absolute interpreter paths with sys.executable.
* not_fixable: the verifier needs a non-Python toolchain (compiler, make, node, perl, ...) or the agent's own Python
  environment / virtualenv / package manager is what is tested, or the task has no prepared runtime to use.
"""
import json
import re

# ---------------------------------------------------------------------------------------------------------------------
# Evidence

PYTHON_NAME = re.compile(r'^(python[\d.]*(-config)?|pypy[\d.]*|py|pip[\d.]*)$')
PYTEST_NAME = re.compile(r'^(pytest|py\.test)$')
GIT = {'git'}
COMPILE = {'gcc', 'g++', 'cc', 'c++', 'make', 'cmake', 'ctest'}

INTERP = (r'(?:(?:/usr/(?:local/)?bin/|/bin/|/opt/[\w.-]+/bin/|/root/\.local/bin/|/app/venv/bin/|/app/\.venv/bin/|'
          r'/usr/local/venv/bin/|/tmp/[\w.-]+/bin/)?(?:python[\d.]*|pytest|pip[\d.]*))')
ABS_INTERP = re.compile(r'''(?P<q>["'])(?P<path>/(?:usr/(?:local/)?bin|bin|opt/[\w.-]+/bin|root/\.local/bin|usr/local/venv/bin)'''
                        r'''/python[\d.]*)(?P=q)''')
# an absolute venv interpreter ("/opt/venv/bin/python", "/app/venv/bin/python", "/opt/task-venv/bin/python3")
VENV_INTERP = re.compile(r'''["'](?:/[\w.-]+)*/(?:\.?venv|[\w.-]*-venv|[\w.-]*env)/bin/(?:python[\d.]*|pytest|pip[\d.]*)["']''')


def parse_tools(evidence):
    details = str((evidence or {}).get('details') or '')
    if 'replaced:' not in details:
        return None
    tools = [t.strip() for t in details.split('replaced:', 1)[1].split(',') if t.strip()]
    return tools or None


def base(path):
    return path.rsplit('/', 1)[-1]


def text_of(data):
    return data.decode('utf-8', errors='replace')


def is_text_source(name, data):
    if name.endswith(('.py', '.sh', '.bash')):
        return True
    return data[:2] == b'#!'


def verifier_sources(files):
    """Everything under tests/ that can run during grading, as {path: text}."""
    out = {}
    for name, data in files.items():
        if not name.startswith('tests/'):
            continue
        if name.endswith('.json'):
            continue
        if name == 'tests/setup.sh' and b'OT_RUNTIME_RECIPE' in data:
            continue            # the runtime preparation stub; its python3 only runs with --prepare
        if not is_text_source(name, data):
            continue
        out[name] = text_of(data)
    return out


def code_lines(text, shell=False):
    for number, line in enumerate(text.splitlines()):
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            continue
        yield number, line


def mentions(sources, word):
    rx = re.compile(r'(?<![\w.-])' + re.escape(word) + r'(?![\w-])')
    for name, text in sources.items():
        for _, line in code_lines(text):
            if rx.search(line):
                return True
    return False


# ---------------------------------------------------------------------------------------------------------------------
# What the tests start from the agent's container

BARE = r'(?:python3?(?:\.\d+)?|pytest|pip3?)'
# an interpreter written as a whole string literal, followed by a comma: the program of an argv list
ELEMENT = re.compile(r"""(?P<q>["'])(?P<cmd>""" + INTERP + r""")(?P=q)\s*,""")
TOOL_ELEMENT = re.compile(r"""(?P<q>["'])(?P<cmd>uvx?|conda|poetry|pipenv|virtualenv|pip[\d.]*|/[\w./-]*/pip[\d.]*)(?P=q)\s*,""")
CANDIDATES = re.compile(r"""[\[(]\s*(?:(?:[frb]*(["'])""" + INTERP + r"""\1)\s*,\s*)+[frb]*(["'])""" + INTERP + r"""\2\s*,?\s*[\])]""")
SHELL_CALL = re.compile(r"""run_cmd|run_command|subprocess|os\.system|os\.popen|Popen|\bcommand\b|\bcmd\b|shell|bash|\.run\(|\.spawn\(|\.exec\w*\(""")
SCRIPT_ARGS = re.compile(r"""^\s+(?:-[cmuIB]\b|/|\.?/?[\w.-]+\.py\b)""")
CMD_STRING = re.compile(r"""(?P<pre>[frb]*)(?P<q>["'])(?P<cmd>""" + INTERP + r""")(?P<rest>\s[^"'\n]*)(?P=q)""")
ASSIGN = re.compile(r"""^(?P<head>[ \t]*(?:[\w.]+[ \t]*=|return)[ \t]*)(?P<q>["'])(?P<cmd>""" + INTERP + r""")(?P=q)[ \t]*$""", re.M)
WHICH = re.compile(r"""shutil\.which\(\s*["'](?P<cmd>""" + BARE + r""")["']""")
DISCOVER = re.compile(r"""^def\s+\w*python\w*\s*\([^)]*\)\s*:(?P<body>(?:\n(?:[ \t]+[^\n]*|[ \t]*))*)""", re.M)
SHELL_WORD = re.compile(r"""(?:^|[\s;&|(`"'{])(?:sudo\s+)?(?P<cmd>python3?(?:\.\d+)?|pytest|pip3?)\s+(?=-|[\w./~$"'{-])""")
ENV_MGMT = re.compile(r"""-m["',\s]+(?:pip|venv|ensurepip|build|virtualenv)\b|\bpip3?["',\s]+(?:install|uninstall|list|show|freeze|download)\b|"""
                      r"""\bvirtualenv\b|\bensurepip\b|\bsetup\.py\b|\bpoetry\b|\bpipenv\b|\bconda\b|/bin/activate\b|\bsource\s+\S*activate\b""")
BARE_WORDS = {'python', 'python3', 'perl', 'ruby', 'node', 'bash', 'sh', 'pip', 'pip3', 'pytest', 'which', 'timeout'}


def blank_comments(text):
    """Comment lines replaced by blanks (same offsets and line numbers)."""
    return '\n'.join(' ' * len(line) if line.strip().startswith('#') else line for line in text.split('\n'))


def second_is_argument(rest):
    """After `"python3",`: a path, flag, variable or expression, not another bare word (a list of names)."""
    rest = rest.lstrip()
    if not rest or rest.startswith((']', ')')):
        return False
    m = re.match(r"""[frb]*(["'])([^"'\n]*)\1""", rest)
    if m:
        value = m.group(2)
        if value in BARE_WORDS or (re.fullmatch(r'[A-Za-z][\w+-]*', value) and value not in ('script', 'main')):
            return False
        return True
    return True


def line_of(text, pos):
    return text.count('\n', 0, pos)


def scan_python(text):
    """What a test file starts from the agent's container.  Each finding is (kind, cmd, start, end, line)."""
    clean = blank_comments(text)
    found = []
    for m in ELEMENT.finditer(clean):
        if second_is_argument(clean[m.end():m.end() + 160]):
            found.append(('element', m.group('cmd'), m.start(), m.end() - 1, line_of(clean, m.start())))
    for m in TOOL_ELEMENT.finditer(clean):
        found.append(('tool', m.group('cmd'), m.start(), m.end() - 1, line_of(clean, m.start())))
    clean_lines = clean.split('\n')
    for m in CMD_STRING.finditer(clean):
        number = line_of(clean, m.start())
        before = clean[:m.start()].rstrip()
        if before and before[-1] not in '([,={:' and not before.endswith('return'):
            continue                        # inside a message or another string, not an argument
        if not SHELL_CALL.search('\n'.join(clean_lines[max(0, number - 2):number + 1])):
            if base(m.group('cmd')).startswith(('pip', 'pytest')) or not SCRIPT_ARGS.match(m.group('rest')):
                continue                    # not handed to a process-starting call
        found.append(('string', m.group('cmd'), m.start(), m.end(), number))
    for m in ASSIGN.finditer(clean):
        found.append(('assign', m.group('cmd'), m.start('q'), m.end('q') + len(m.group('cmd')) + 1,
                      line_of(clean, m.start())))
    for m in CANDIDATES.finditer(clean):
        found.append(('discover', 'python', m.start(), m.end(), line_of(clean, m.start())))
    for i, line in enumerate(clean.split('\n')):
        if SHELL_CALL.search(line):
            for m in SHELL_WORD.finditer(line):
                if not CMD_STRING.search(line) and not line.lstrip().startswith(('assert', 'print', 'raise', 'f"', "f'")):
                    found.append(('shell', m.group('cmd'), 0, 0, i))
                    break
    for m in WHICH.finditer(clean):
        found.append(('which', m.group('cmd'), m.start(), m.end(), line_of(clean, m.start())))
    for m in DISCOVER.finditer(clean):
        body = m.group('body')
        if 'OT_VERIFIER' in body or 'sys.executable' in body:
            continue
        if re.search(r'which|isfile|exists|access|glob|candidates|environ\.get\(["\']VIRTUAL_ENV', body):
            found.append(('discover', 'python', m.start(), m.end(), line_of(clean, m.start())))
    return clean, sorted(found, key=lambda f: f[2])


def analyse_python_source(name, text):
    """(bare, absolute, env_management, discover) findings of one test file as lists of (line number, line)."""
    clean, found = scan_python(text)
    lines = text.split('\n')
    bare, absolute, env, discover = [], [], [], []
    for kind, cmd, start, end, number in found:
        shown = (number, lines[number].strip())
        context = clean[max(0, start - 40):end + 260] if kind != 'shell' else lines[number]
        if kind == 'discover':
            discover.append(shown)
        elif kind == 'tool' or base(cmd).startswith('pip') or ENV_MGMT.search(context):
            env.append(shown)
        elif kind in ('which', 'shell') or '/' not in cmd:
            bare.append(shown)
        else:
            absolute.append(shown)
    return bare, absolute, env, discover


def shell_python_findings(name, text):
    """Interpreter started by name in a shell script of the tests, outside the prepared runtime."""
    found = []
    for number, line in code_lines(text):
        stripped = line.strip()
        if stripped.startswith(('echo', 'printf')):
            continue
        if 'OT_VERIFIER' in line or '$PYTHON' in line or '"$PYTHON"' in line:
            continue
        if re.match(r'^\s*(?:if\s+!?\s*)?pgrep\b', stripped):
            continue
        if SHELL_WORD.search(line) or re.search(r'(?:^|[\s;&|(])(?:python3?|pytest|pip3?|uvx?)\s*$', stripped):
            found.append((number, stripped))
    return found



# ---------------------------------------------------------------------------------------------------------------------
# Can the prepared runtime stand in for the agent's interpreter?

def norm(name):
    return re.sub(r'[-_.]+', '-', name.strip().lower())


def runtime_recipe(files):
    text = text_of(files.get('tests/setup.sh', b''))
    m = re.search(r'OT_RUNTIME_RECIPE: (\{.*\})', text)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except ValueError:
        return None


def agent_minors(tools):
    """Minor versions of the agent's own interpreters in the evidence (not the TB2 base /opt/verifier, not uv's)."""
    found = set()
    for t in tools:
        m = re.search(r'/python(3\.\d+)$', t)
        if m and not t.startswith(('/opt/verifier/', '/root/.local/')):
            found.add(m.group(1))
    if not found and any(t.startswith('/opt/verifier/bin/python') for t in tools):
        found.add('3.13')           # the TB2 verifier base image's own interpreter
    return found


def dockerfile_packages(files):
    """Third-party Python packages the task image installs, or None when they cannot be listed."""
    text = text_of(files.get('environment/Dockerfile', b''))
    text = text.replace('\\\n', ' ')
    packages = set()
    for line in text.split('\n'):
        for m in re.finditer(r'pip3?\s+install\s+([^&;|\n]*)|-m\s+pip\s+install\s+([^&;|\n]*)', line):
            args = (m.group(1) or m.group(2) or '').split()
            skip = False
            for i, arg in enumerate(args):
                if skip:
                    skip = False
                    continue
                if arg in ('-r', '--requirement', '-c', '--constraint', '-e', '--editable', '-f', '--find-links',
                           '--index-url', '-i', '--extra-index-url', '--target', '-t', '--prefix'):
                    if arg in ('-r', '--requirement', '-e', '--editable'):
                        return None
                    skip = True
                    continue
                if arg.startswith('-'):
                    continue
                if arg.startswith(('.', '/', 'git+', 'http')):
                    return None
                packages.add(norm(re.split(r'[<>=!~\[;@ ]', arg.strip('"\''))[0]))
        for m in re.finditer(r'apt(?:-get)?\s+install\s+([^&;|\n]*)', line):
            for arg in m.group(1).split():
                if arg.startswith('python3-') and arg not in ('python3-pip', 'python3-venv', 'python3-dev',
                                                               'python3-setuptools', 'python3-wheel', 'python3-full'):
                    packages.add(norm(arg[len('python3-'):]))
    packages.discard('')
    return packages


def runtime_covers(files, tools):
    """(ok, reason). The tests run the agent's scripts with the prepared runtime instead of the agent's interpreter."""
    recipe = runtime_recipe(files)
    if recipe is None:
        return False, 'the runtime recipe in tests/setup.sh cannot be read'
    runtime_minor = '.'.join(str(recipe.get('python', '')).split('.')[:2])
    have = {norm(re.split(r'[<>=!~\[;@ ]', r)[0]) for r in recipe.get('requirements', [])}
    minors = agent_minors(tools)
    if minors and minors <= {runtime_minor}:
        return True, 'same Python minor (%s): the harness serves the agent\'s packages as a fallback' % runtime_minor
    needed = dockerfile_packages(files)
    if needed is None:
        return False, 'the packages installed in the agent\'s Python cannot be listed and its minor version (%s) is not the ' \
                      'runtime\'s (%s)' % (','.join(sorted(minors)) or 'unknown', runtime_minor)
    missing = sorted(needed - have)
    if missing:
        return False, 'the agent\'s Python (%s) has packages the runtime (python %s) lacks and cannot fall back to: %s' % (
            ','.join(sorted(minors)) or 'unknown', runtime_minor, ', '.join(missing[:6]))
    return True, 'the image installs no Python package that the runtime lacks'

# ---------------------------------------------------------------------------------------------------------------------
# Repairs

SHIM_MARK = '# ot-verifier-tamper: interpreters the tests start by name come from the prepared verifier runtime'


def shim_block(names):
    lines = [SHIM_MARK,
             ': "${OT_VERIFIER_PYTHON:?prepared verifier runtime required}"',
             '_ot_shim="$(mktemp -d)"']
    for name in names:
        if name in ('pip', 'pip3'):
            body = 'exec "$OT_VERIFIER_PYTHON" -m pip "$@"'
        elif name == 'pytest':
            body = 'exec "$OT_VERIFIER_PYTHON" -m pytest "$@"'
        else:
            body = 'exec "$OT_VERIFIER_PYTHON" "$@"'
        lines += ["cat > \"$_ot_shim/%s\" <<'OT_SHIM_EOF'" % name, '#!/bin/sh', body, 'OT_SHIM_EOF',
                  'chmod +x "$_ot_shim/%s"' % name]
    lines.append('export PATH="$_ot_shim:$PATH"')
    return '\n'.join(lines) + '\n'


def add_shim(script, names):
    if SHIM_MARK in script:
        return script
    block = shim_block(names)
    lines = script.split('\n')
    at = 1 if lines and lines[0].startswith('#!') else 0
    # after `set` lines at the top so that `set -u` is in force for the shim as well
    while at < len(lines) and (lines[at].startswith('set ') or not lines[at].strip()):
        at += 1
    lines[at:at] = block.rstrip('\n').split('\n')
    return '\n'.join(lines)


def has_import_sys(text):
    return re.search(r'^\s*import\s+(?:[\w.]+\s*,\s*)*sys\b', text, re.M) is not None


def exe_expression(text):
    return 'sys.executable' if has_import_sys(text) else "__import__('sys').executable"


def replace_absolute(text):
    """Absolute interpreter paths used as commands become sys.executable (the prepared runtime's interpreter).
    Returns (new text, number of replacements)."""
    expr = exe_expression(text)
    clean, found = scan_python(text)
    edits = []
    for kind, cmd, start, end, number in found:
        if '/' not in cmd or kind in ('which', 'discover', 'tool', 'shell'):
            continue
        if base(cmd).startswith('pip'):
            continue
        if kind in ('element', 'assign'):
            q_end = start + len(cmd) + 2           # start is the opening quote of the whole-literal interpreter
            replacement = expr
            if base(cmd).startswith('pytest'):
                replacement = expr + ', "-m", "pytest"'
            edits.append((start, q_end, replacement))
        elif kind == 'string':
            literal = text[start:end]
            pre = re.match(r'[frb]*', literal).group(0)
            quote = literal[len(pre)]
            rest = literal[len(pre) + 1 + len(cmd):-1]
            if base(cmd).startswith('pytest'):
                continue
            if 'f' in pre.lower():
                edits.append((start, end, pre + quote + '{' + expr + '}' + rest + quote))
            else:
                edits.append((start, end, '(' + expr + ' + ' + repr(rest) + ')'))
    count = 0
    out = text
    for start, end, replacement in sorted(set(edits), reverse=True):
        out = out[:start] + replacement + out[end:]
        count += 1
    if count and not has_import_sys(text) and not expr.startswith('__import__'):
        out = 'import sys\n' + out
    return out, count


# True while the harness probes the container's python3/python in shared mode (harbor_patches/agent_package_fallback.py).
HARNESS_RUNS_AGENT_PYTHON = True


def decision(kind, reason, files=None):
    return {'decision': kind, 'reason': reason}, files


def repair_verifier_tamper(files, evidence):
    tools = parse_tools(evidence)
    if tools is None:
        return None
    names = sorted({base(t) for t in tools})
    sources = verifier_sources(files)
    test_sh = text_of(files.get('tests/test.sh', b''))
    if not test_sh:
        return None
    has_runtime = 'tests/ot-verifier-runtime.json' in files
    uses_runtime = 'OT_VERIFIER_PYTHON' in test_sh or 'OT_VERIFIER_BIN' in test_sh

    py_names = [n for n in names if PYTHON_NAME.match(n) or PYTEST_NAME.match(n)]
    other = [n for n in names if n not in py_names]

    # --- tools that are not Python -----------------------------------------------------------------------------
    toolchain = [n for n in other if n not in GIT]
    git_used = 'git' in other and mentions(sources, 'git')
    if toolchain or git_used:
        used = toolchain + (['git'] if git_used else [])
        direct = [n for n in used if mentions(sources, n)]
        how = ('named in the tests: ' + ', '.join(direct)) if direct else \
            'not named in the tests, so started indirectly by the tests (build or service tooling)'
        if set(used) <= {'uv', 'uvx', 'activate', 'pip', 'pytest'}:
            why = ('the tests use the agent\'s own Python environment manager (%s), which is what the task is about, and '
                   'the prepared runtime has none' % ', '.join(used))
        else:
            why = 'no prepared runtime provides this non-Python toolchain, so the verifier needs the agent\'s copy'
        return decision('not_fixable', 'grading runs %s from the agent\'s container (%s); %s' % (', '.join(used), how, why))

    # --- the agent's own pytest / pip was run: the tests exercise that installation ---------------------------------
    own = [n for n in py_names if PYTEST_NAME.match(n) or n.startswith('pip')]
    if own:
        return decision('not_fixable',
                        'grading runs the agent container\'s own %s (%s); the tests run the project\'s tests / packages '
                        'with that installation, which the task asks the agent to repair, so the prepared runtime cannot '
                        'stand in for it' % (', '.join(own), ', '.join(t for t in tools if base(t) in own)))

    # --- tasks without a prepared runtime ----------------------------------------------------------------------
    if not has_runtime or not uses_runtime:
        return decision('not_fixable',
                        'tests/test.sh runs python/pytest from the agent\'s container (%s) but the task has no prepared '
                        'verifier runtime (no tests/ot-verifier-runtime.json / OT_VERIFIER_* in test.sh) to run it from'
                        % ', '.join(py_names))

    # --- Python: what do the tests start from the agent's container? ---------------------------------------------
    bare, absolute, env, discover = [], [], [], []
    shell = []
    for name, text in sources.items():
        if name.endswith('.py'):
            b, a, e, d = analyse_python_source(name, text)
            bare += [(name, n, l) for n, l in b]
            absolute += [(name, n, l) for n, l in a]
            env += [(name, n, l) for n, l in e]
            discover += [(name, n, l) for n, l in d]
        else:
            shell += [(name, n, l) for n, l in shell_python_findings(name, text)]
            if name != 'tests/test.sh':
                continue
    if not (bare or absolute or shell or env or discover):
        return decision('false_positive',
                        'tests/test.sh runs pytest from the prepared verifier runtime (OT_VERIFIER_*) and the test files '
                        'start no interpreter from the agent\'s container; the recorded %s runs are the harness\'s own '
                        '(agent_package_fallback probes python3/python of the container with -I -S before the runtime '
                        'is activated)' % ', '.join(py_names))
    if discover:
        return decision('not_fixable',
                        'the tests look for the interpreter that holds the task\'s packages in the agent\'s container '
                        '(%s: %s), so the agent\'s environment is what is tested' % (discover[0][0], discover[0][2][:70]))
    if env:
        return decision('not_fixable',
                        'the tests exercise the agent\'s own Python environment (venv/pip/uv/activate: %s), which is '
                        'what the task is about' % env[0][2][:80])
    if shell:
        return decision('not_fixable', 'test scripts start python from the container: %s' % shell[0][2][:80])

    ok, why = runtime_covers(files, tools)
    if not ok:
        return decision('not_fixable', 'the tests run the agent\'s own interpreter on the agent\'s code and the prepared '
                        'runtime cannot replace it without changing what is tested: ' + why)
    first = (bare + absolute)[0]
    seen = '%s line %d: %s' % (first[0].split('/')[-1], first[1] + 1, first[2][:70])
    if HARNESS_RUNS_AGENT_PYTHON:
        # Repairing the tests (PATH shim in test.sh, sys.executable in the test file) removes the verifier's own use of the
        # agent's interpreter, but the container run of such repaired tasks (validation service, stages 1-3) still records
        # the same python3/python programs: in shared mode with a prepared runtime the harness itself runs the container's
        # python3/python with -I -S before grading (agent_package_fallback.prepare). The finding cannot be cleared from
        # inside the task, so the task is not fixable; the repair below is kept for a harness without that probe.
        return decision('not_fixable',
                        'the tests start the agent container\'s interpreter (%s), but the check cannot be cleared by any '
                        'change to the task: the harness itself runs the container\'s python3/python (%s) before the '
                        'runtime is activated, and a repaired copy (test.sh shim / sys.executable) was still flagged with '
                        'the same programs when validated in a container' % (seen, ', '.join(py_names)))
    new = dict(files)
    changed = []
    if absolute:
        for name in sorted({n for n, _, _ in absolute}):
            text = text_of(files[name])
            result, count = replace_absolute(text)
            if count:
                new[name] = result.encode('utf-8')
                changed.append(name)
    left = []
    if bare or absolute:
        wanted = set()
        for _, _, line in bare:
            for m in re.finditer(r'(?<![\w/.-])(python3?|pytest|pip3?)(?![\w.-])', line):
                wanted.add(m.group(1))
        wanted |= {'python', 'python3'}
        if absolute and not bare:
            wanted = set()
        if wanted:
            new['tests/test.sh'] = add_shim(test_sh, sorted(wanted)).encode('utf-8')
    if new == files:
        return decision('false_positive', 'nothing in the tests starts a container interpreter')
    return decision('fixed', 'the tests start python/pytest from the agent\'s container (%d bare, %d absolute); '
                    'bare names resolve to the prepared runtime through a PATH shim in test.sh, absolute interpreter paths '
                    'in %s run with sys.executable' % (len(bare), len(absolute), ', '.join(changed) or 'no test file'),
                    new)
