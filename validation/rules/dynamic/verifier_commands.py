"""Conservative subprocess argument roles. Never execute task-controlled helpers."""
import ast
from pathlib import Path, PurePosixPath
import re
import shlex
import json


def supplied_helper(program, context, cwd=None):
    """Map /tests and literal Docker COPY sources; never search by basename."""
    if context is None:
        return None
    runtime = PurePosixPath(program)
    if not runtime.is_absolute():
        if not cwd or not str(cwd).startswith('/tests'):
            return None
        runtime = PurePosixPath(cwd) / runtime
    root = Path(context).resolve()
    candidates = []
    if runtime.is_relative_to('/tests'):
        candidates.append((root, root / str(runtime.relative_to('/tests'))))
    # Literal Docker COPY mappings provide provenance for environment helpers.
    # Dynamic destinations, multistage COPY and image-only files stay unknown.
    environment = root.parent / 'environment'
    dockerfile = environment / 'Dockerfile'
    if dockerfile.is_file():
        for line in dockerfile.read_text().splitlines():
            if not line.lstrip().upper().startswith('COPY '):
                continue
            value = line.strip()[5:].strip()
            try:
                words = json.loads(value) if value.startswith('[') else shlex.split(value, comments=True)
            except (ValueError, TypeError):
                continue
            if len(words) != 2 or any('$' in w or '*' in w for w in words) or words[0].startswith('--'):
                continue
            source, destination = words
            local = environment / source
            target = PurePosixPath(destination)
            if not target.is_absolute():
                continue
            if local.is_dir() and runtime.is_relative_to(target):
                candidates.append((environment.resolve(), local / str(runtime.relative_to(target))))
            elif local.is_file():
                if destination.endswith('/'):
                    target /= local.name
                if target == runtime:
                    candidates.append((environment.resolve(), local))
    found = {p.resolve() for boundary, p in candidates
             if p.resolve().is_relative_to(boundary) and p.is_file()}
    return next(iter(found)) if len(found) == 1 else None


def helper_roles(path, argv, analyze):
    """Bind literal sys.argv and simple argparse options in supplied Python."""
    if path.suffix != '.py':
        return {}
    try:
        tree = ast.parse(path.read_text())
    except (OSError, UnicodeError, SyntaxError):
        return {}
    # argparse destinations are accepted only for explicitly declared options.
    bindings = {}
    namespaces = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            if isinstance(node.value.func, ast.Attribute) and node.value.func.attr == 'parse_args':
                namespaces.update(t.id for t in node.targets if isinstance(t, ast.Name))
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute) or node.func.attr != 'add_argument':
            continue
        flags = [a.value for a in node.args if isinstance(a, ast.Constant) and isinstance(a.value, str)]
        if not flags or not all(f.startswith('-') for f in flags):
            continue
        # Complex nargs/actions are deliberately not inferred.
        if any(k.arg in ('nargs', 'action', 'type') for k in node.keywords):
            continue
        dest = next((k.value.value for k in node.keywords if k.arg == 'dest' and isinstance(k.value, ast.Constant)), None)
        dest = dest or max(flags, key=len).lstrip('-').replace('-', '_')
        values = []
        for i, value in enumerate(argv[1:], 1):
            if value in flags and i + 1 < len(argv):
                values.append(argv[i + 1])
            elif isinstance(value, str) and any(value.startswith(f + '=') for f in flags):
                values.append(value.split('=', 1)[1])
        if len(values) == 1 and values[0] is not None:
            bindings[dest] = values[0]

    class Bind(ast.NodeTransformer):
        def visit_Subscript(self, node):
            node = self.generic_visit(node)
            if (isinstance(node.value, ast.Attribute) and isinstance(node.value.value, ast.Name)
                    and node.value.value.id == 'sys' and node.value.attr == 'argv'
                    and isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, int)
                    and 0 <= node.slice.value < len(argv) and argv[node.slice.value] is not None):
                return ast.copy_location(ast.Constant(argv[node.slice.value]), node)
            return node

        def visit_Attribute(self, node):
            if isinstance(node.value, ast.Name) and node.value.id in namespaces and node.attr in bindings:
                return ast.copy_location(ast.Constant(bindings[node.attr]), node)
            return self.generic_visit(node)

    transformed = ast.fix_missing_locations(Bind().visit(tree))
    records = analyze(ast.unparse(transformed), str(path))
    roles = {}
    for item in sorted(records, key=lambda r: r['line']):
        existing = roles.setdefault(item['path'], set())
        if item['role'] == 'read' and 'generated' in existing:
            continue  # A helper may inspect its own output after writing it.
        existing.add(item['role'])
    return roles


def command_paths(argv, absolute, analyze, context=None, cwd=None):
    """Return path, role and evidence for a literal argv, preserving unknowns."""
    if not argv:
        return []
    command = argv[0]
    base = PurePosixPath(command).name if command else ''
    script_index = None
    if re.fullmatch(r'python(?:\d+(?:\.\d+)*)?', base) or base in ('bash', 'sh'):
        # Interpreter switches/module execution need separate parsing.
        if len(argv) > 1 and argv[1] and not argv[1].startswith('-'):
            script_index = 1
    program = argv[script_index] if script_index is not None else command
    helper = supplied_helper(program, context, cwd) if program else None
    roles = helper_roles(helper, argv[script_index or 0:], analyze) if helper else {}
    records = []
    for i, raw in enumerate(argv):
        if raw is None:
            continue
        flag, sep, value = raw.partition('=') if raw.startswith('-') else ('', '', raw)
        path = value if sep else raw
        if not absolute(path):
            continue
        previous = flag if sep else (argv[i - 1] if i else None)
        role, evidence = 'subprocess_unknown', 'argument role unresolved; excluded by conservative policy'
        if i == 0 or i == script_index:
            role, evidence = 'execute', 'invoked program or interpreter script'
        elif path in roles:
            uses = roles[path]
            if 'read' in uses:
                role = 'read'
            elif 'generated' in uses:
                role = 'generated'
            evidence = f'supplied helper {helper}: {", ".join(sorted(uses))}'
        elif base == 'openssl' and previous in ('-in', '-out', '-inkey', '-CAfile', '-cert', '-key'):
            role = 'generated' if previous == '-out' else 'read'
            evidence = f'openssl {previous} file argument'
        elif base in ('gcc', 'g++', 'cc', 'clang', 'clang++') and previous == '-o':
            role, evidence = 'generated', 'compiler -o output'
        elif base in ('cat', 'cmp', 'diff') and all(a is not None and not a.startswith('-') for a in argv[1:]):
            role, evidence = 'read', f'{base} positional input'
        elif base in ('cp', 'mv') and len(argv) == 3 and all(a and not a.startswith('-') for a in argv[1:]):
            role = 'read' if i == 1 else 'generated'
            evidence = f'{base} source/destination'
        if role == 'read':
            role = 'subprocess_input'
        records.append((path, role, evidence))
    return records
