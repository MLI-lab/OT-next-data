#!/usr/bin/env python3
"""Per-script execution coverage, independent of no_behavioural_tests.

Static evidence of invocation/calls is not a runtime coverage guarantee or a
test of output correctness. Unresolved execution passes conservatively.
"""
from pathlib import Path, PurePosixPath
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
import ast
import json
import re
import shlex

from validation.rules.static.no_behavioural_tests import editable_harness as harness
from validation.rules.dynamic.verifier_paths import resolve, name
from validation.rules.static.script_execution_graph import Sources

SCRIPT = re.compile(r'(?<![\w.-])(?:[\w./+-]+)\.(?:py|sh|bash|js|ts|rb|pl|php|ps1|lua|jl|r)\b', re.I)
CREATE = re.compile(r'\b(?:write|create|implement|develop|build|add|modify|fix|update|change|repair|run|execute)\b', re.I)
NEGATIVE = re.compile(r"\b(?:do not|don't|must not|never|optional(?:ly)?|for example)\b", re.I)
OTHER_ROLE = re.compile(r'\b(?:read(?:s|ing)?|use|uses|using|from|load(?:s|ing)?|import(?:s|ing)?|call(?:s|ing)?|inspect|provided|existing|input)\b', re.I)
EXEC = {'subprocess.run', 'subprocess.call', 'subprocess.check_call', 'subprocess.check_output', 'subprocess.Popen', 'os.system', 'os.popen', 'os.execv', 'os.execve', 'os.execl'}


def requested(instruction):
    found = {}
    for sentence in re.split(r'(?<=[.!?])\s+|\n', instruction):
        if not CREATE.search(sentence) or NEGATIVE.search(sentence):
            continue
        for match in SCRIPT.finditer(sentence):
            actions = list(CREATE.finditer(sentence[:match.start()]))
            if not actions:
                continue
            between = sentence[actions[-1].end():match.start()]
            action = actions[-1][0].lower()
            # Bind the creation verb to its object, not to paths in a later
            # input/usage clause: "write report.py using input.py".
            role_text = re.sub(r'\bexisting\b', '', between, flags=re.I) if action in ('modify', 'fix', 'update', 'change', 'repair', 'run', 'execute') else between
            if OTHER_ROLE.search(role_text):
                continue
            path = match[0]
            # A build command can be tested through its compiled deliverable;
            # this rule must not demand a redundant rebuild automatically.
            build_helper = (PurePosixPath(path).name == 'setup.py' and
                            re.search(r'\bbuild_ext\b', sentence[match.end():]))
            found.setdefault(path, dict(script=path, instruction=sentence.strip(),
                                       action=action, requirement='build_helper' if build_helper else 'functionality'))
    return list(found.values())


def sources(task):
    """Read Python files and Python heredocs; never execute task code."""
    python, problems = [], []
    for path in sorted((task / 'tests').rglob('*')):
        if not path.is_file():
            continue
        label = path.relative_to(task).as_posix()
        if path.suffix == '.py':
            python.append((label, path.read_text(), 0))
        elif path.suffix == '.sh':
            lines = path.read_text().splitlines()
            i = 0
            while i < len(lines):
                match = re.search(r'<<-?\s*[\x27\"]?(\w+)', lines[i])
                if match:
                    end = i + 1
                    while end < len(lines) and lines[end].strip() != match[1]:
                        end += 1
                    if end == len(lines):
                        problems.append(label + ': unterminated heredoc')
                    elif re.search(r'\bpython[\d.]*\b|>\s*[\x27\"]?[^\s]+\.py\b', lines[i]):
                        python.append((label, '\n'.join(lines[i+1:end]), i+1))
                    i = end
                i += 1
        elif path.suffix in ('.js', '.ts', '.rb', '.ps1', '.go', '.rs'):
            problems.append(label + ': unsupported verifier language')
    return python, problems


def analyze_task(task):
    task = Path(task)
    required = requested((task / 'instruction.md').read_text())
    if not required:
        return dict(status='passed', scripts=[], limitations=['No explicitly named script creation, modification or execution requirement recognized.'])
    facts, problems = [], []
    required_names = {PurePosixPath(r['script']).name for r in required}
    python, source_problems = sources(task)
    problems.extend(source_problems)
    graph = Sources(task, python, problems)

    def opaque_target(path):
        # Project runners frequently have no extension (e.g. bin/ansible-test).
        # Shell heredoc pseudo-targets are source text, not executable paths.
        return (not any(c in path for c in '<>') and
                not path.startswith('/tests/') and
                PurePosixPath(path).name not in required_names and
                (path.endswith(('.sh', '.py')) or
                 path.startswith(('/app/', '/workspace/', '/testbed/', '/home/', '/opt/'))))

    def command(value, source, line, cwd=None):
        shell = harness.Shell(task)
        shell.cwd = cwd
        shell.command(value, source, line, 0)
        for kind, path, file, number, text in shell.events:
            if kind in ('run', 'grade') and path:
                facts.append(dict(path=path, kind='execution', file=file, line=number))
                followed = graph.follow_path(path, source, line, cwd)
                if not followed and opaque_target(path):
                    problems.append(f'{source}:{line}: indirect execution through {path}')
        # Keep explicit relative invocations visible when no cwd is known.
        try:
            tokens = shlex.split(value)
        except ValueError:
            problems.append(f'{source}:{line}: unresolved command')
            return
        if tokens and harness.INTERPRETERS.fullmatch(PurePosixPath(tokens[0]).name) and len(tokens) > 1:
            if tokens[1] == '-m' and len(tokens) > 2:
                facts.append(dict(module=tokens[2], kind='module_execution', file=source, line=line))
                graph.follow_module(tokens[2], source, line)
            elif SCRIPT.fullmatch(tokens[1]) and not tokens[1].startswith('/') and not cwd:
                facts.append(dict(path=tokens[1], kind='relative_execution', file=source, line=line))

    # Existing shell parser follows nested /tests shell scripts and assignments.
    for kind, path, source, line, _ in harness.verifier_events(task):
        if kind == 'suite':
            supplied_suite = path and graph.follow_path(path, source, line)
            # Python test files emitted by a heredoc are already in `python`.
            generated_suite = path and any(offset and label == source.lstrip('/') and
                re.search(r'>\s*[\x27\"]?' + re.escape(path) + r'(?:\s|$)',
                          (task / label).read_text()) for label, _, offset in python)
            if not supplied_suite and not generated_suite:
                problems.append(f'{source}:{line}: project test-suite call chain is not fully resolved')
        if kind in ('run', 'grade') and path:
            facts.append(dict(path=path, kind='execution', file=source, line=line))
            followed = graph.follow_path(path, source, line)
            if not followed and opaque_target(path):
                problems.append(f'{source}:{line}: indirect execution through {path}')
    for path in (task / 'tests').rglob('*.sh'):
        body = path.read_text()
        variables = set(re.findall(r'^\s*(?:export\s+)?(\w+)=', body, re.M))
        for line, statement in enumerate(body.splitlines(), 1):
            if statement.lstrip().startswith('#'):
                continue
            if re.search(r'\beval\s+|\b(?:bash|sh)\s+-c\s+', statement):
                problems.append(f'{path.name}:{line}: inline or dynamically evaluated shell code is not fully resolved')
            if re.search(r'\bpython[\d.]*\s+-c\s+', statement):
                try:
                    words = shlex.split(statement, comments=True)
                    index = next(i for i, word in enumerate(words[:-2])
                                 if re.fullmatch(r'python[\d.]*', word) and words[i+1] == '-c')
                    body = words[index+2]
                    if '$' in body or '`' in body:
                        raise ValueError('shell interpolation in inline Python')
                    graph.queue.append((path.relative_to(task).as_posix(), body, line-1))
                except (ValueError, StopIteration):
                    problems.append(f'{path.name}:{line}: inline Python is not fully resolved')
            match = re.search(r'\b(?:bash|sh|python[\d.]*|node)\s+[\x27\"]?\$\{?(\w+)', statement)
            if match and match[1] not in variables:
                problems.append(f'{path.name}:{line}: unresolved shell target')
            module = re.match(r'^\s*(?:exec\s+)?python[\d.]*\s+-m\s+([\w.]+)', statement)
            if module:
                facts.append(dict(module=module[1], kind='module_execution', file=path.relative_to(task).as_posix(), line=line))
                graph.follow_module(module[1], path.relative_to(task).as_posix(), line)
    for source, body, offset in graph.queue:
        try:
            tree = ast.parse(body)
        except SyntaxError as exc:
            problems.append(f'{source}: {exc}')
            continue
        aliases, constants, assignments, imported_paths = {}, {}, {}, {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for item in node.names:
                    aliases[item.asname or item.name] = item.name
                    facts.append(dict(module=item.name, kind='import', file=source, line=node.lineno+offset))
                    imported_paths[item.name] = graph.follow_module(item.name, source, node.lineno+offset)
            elif isinstance(node, ast.ImportFrom):
                facts.append(dict(module=node.module or '', kind='import', file=source, line=node.lineno+offset))
                module_path = graph.follow_module(node.module or '', source, node.lineno+offset, node.level)
                for item in node.names:
                    qualified = '.'.join(filter(None, (node.module, item.name)))
                    aliases[item.asname or item.name] = qualified
                    imported_paths[qualified] = module_path
                    # `from package import child` may import a submodule,
                    # whereas `from module import function` does not.
                    before = len(problems)
                    child_path = graph.follow_module(qualified, source, node.lineno+offset, node.level)
                    if child_path:
                        imported_paths[qualified] = child_path
                    else:
                        del problems[before:]  # symbol lookup, not a missing module
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        assignments.setdefault(target.id, []).append(node.value)
        for _ in range(8):
            old = dict(constants)
            for key, values in assignments.items():
                resolved = [resolve(v, constants) for v in values]
                if resolved and resolved[0] is not None and len(set(resolved)) == 1:
                    constants[key] = resolved[0]
            if constants == old:
                break
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = name(node.func)
            first, _, tail = func.partition('.')
            func = aliases.get(first, first) + ('.' + tail if tail else '')
            line = node.lineno + offset
            for imported, imported_path in imported_paths.items():
                if func == imported or func.startswith(imported + '.'):
                    facts.append(dict(module=imported, path=imported_path or '',
                                      kind='imported_call', callable=func, file=source, line=line))
            if func in EXEC:
                arg = node.args[0] if node.args else next((k.value for k in node.keywords if k.arg == 'args'), None)
                cwd = next((resolve(k.value, constants) for k in node.keywords if k.arg == 'cwd'), None)
                if isinstance(arg, (ast.List, ast.Tuple)):
                    values = ['python3' if name(v) == 'sys.executable' else resolve(v, constants) for v in arg.elts]
                    # Unknown data arguments do not hide a resolved executable.
                    if not values or values[0] is None or (harness.INTERPRETERS.fullmatch(PurePosixPath(values[0]).name) and (len(values) < 2 or values[1] is None)):
                        problems.append(f'{source}:{line}: unresolved subprocess target')
                        continue
                    command(' '.join(shlex.quote(v if v is not None else '__unknown_argument__') for v in values), source, line, cwd)
                else:
                    value = resolve(arg, constants)
                    if value is None:
                        problems.append(f'{source}:{line}: unresolved command')
                    else:
                        command(value, source, line, cwd)
            elif func in ('runpy.run_path', 'runpy.run_module', 'importlib.import_module'):
                value = resolve(node.args[0], constants) if node.args else None
                if value is None:
                    problems.append(f'{source}:{line}: unresolved dynamic import')
                else:
                    facts.append(dict({'path' if func.endswith('run_path') else 'module': value},
                                      kind='import' if func == 'importlib.import_module' else 'import_or_run', file=source, line=line))
                    if func.endswith('run_path'):
                        graph.follow_path(value, source, line)
                    else:
                        graph.follow_module(value, source, line)
            elif func in ('eval', 'exec'):
                problems.append(f'{source}:{line}: dynamic code execution')
            elif func.startswith('importlib.') or func.endswith('.exec_module'):
                problems.append(f'{source}:{line}: unresolved import loader')
            elif func.startswith(('requests.', 'httpx.', 'urllib.request.')) and func.rsplit('.', 1)[-1] in ('get', 'post', 'put', 'request', 'urlopen'):
                problems.append(f'{source}:{line}: service request may exercise a running script; cannot attribute it statically')
    facts.extend(graph.executions)
    records = []
    for item in required:
        path = item['script']
        basename, stem = PurePosixPath(path).name, PurePosixPath(path).stem
        matches = []
        imports = []
        for fact in facts:
            target = fact.get('path', '')
            module_matches = (fact.get('module', '').split('.')[-1] == stem or
                              fact.get('module', '').startswith(stem + '.'))
            if fact['kind'] == 'import' and module_matches:
                imports.append(fact)
                continue
            if target == path or (not path.startswith('/') and target and PurePosixPath(target).name == basename):
                matches.append(fact)
            elif module_matches and path.endswith('.py') and not (task / 'tests' / basename).exists():
                matches.append(fact)
        ambiguous = any(f.get('path') and not f['path'].startswith('/') and PurePosixPath(f['path']).name == basename for f in facts)
        uncertain = problems or ambiguous or imports or item['requirement'] == 'build_helper'
        status = 'covered' if matches else 'unknown' if uncertain else 'missing'
        matches = list({json.dumps(f, sort_keys=True): f for f in matches}.values())
        records.append(dict(item, status=status, evidence=matches, imports=imports,
                            conservative_pass=status == 'unknown'))
    missing = [r['script'] for r in records if r['status'] == 'missing']
    return dict(status='failed' if missing else 'passed',
                scripts=records, missing_scripts=missing,
                reason=('Required script functionality has no verifier execution or call evidence: ' +
                        '; '.join(f"{r['script']} (instruction: {r['instruction'][:200]!r})" for r in records if r['status'] == 'missing'))
                       if missing else '',
                unresolved=sorted(set(problems)),
                import_call_graph=graph.edges,
                conservative_pass=any(r['status'] == 'unknown' for r in records),
                limitations=['Static potential-call analysis, including supplied imports and wrappers; not proof of runtime reachability or meaningful assertions on output.',
                             'Unresolved chains pass conservatively; unknown does not mean verified coverage.',
                             'Build helpers may be verified through compiled functionality; rebuild coverage is not required by this rule.'])


def check(task):
    result = analyze_task(task)
    return result['status'], result


if __name__ == '__main__':
    from validation.rules.static.script import run
    sys.exit(run(check))
