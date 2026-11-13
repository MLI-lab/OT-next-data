"""Failure-level inventory of unmentioned absolute verifier paths.

This is a literal-path screen, not a syscall trace or a proof of task impossibility.
Keep ambiguous uses visible for review rather than claiming they are required.
"""
import ast

from validation.rules.dynamic.verifier_commands import command_paths
from validation.rules.dynamic.verifier_writes import helper_writes
from validation.rules.dynamic.verifier_artifacts import artifact_instruction_outputs, extraction_requests, archive_member
from contextvars import ContextVar
import json
import inspect
from pathlib import Path, PurePosixPath
import re
import shlex

from validation.rules.static.task_absolute_path.check import without_urls

CHECK = 'verifier_paths'
ARTIFACT = 'verifier-paths.json'
PHASES = ('before_setup', 'after_setup', 'after_verifier')
instruction_views = ContextVar('verifier_path_instruction_views', default=None)
LIMITATION = ('Only statically resolved absolute paths are covered. Computed paths, '
              'conditional execution and files created then deleted between snapshots '
              'need manual review. Unknown subprocess argument roles are excluded to minimize false positives. A finding does not establish task impossibility.')


def absolute(value):
    return (isinstance(value, str) and value.startswith('/') and
            not value.startswith('//') and not re.search(r'[\s\x00*?${}]', value))


def name(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return name(node.value) + '.' + node.attr
    return ''


def resolve(node, constants):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Attribute) and name(node) == 'sys.executable':
        return 'python3'  # Interpreter identity, not the host interpreter's path.
    if isinstance(node, ast.JoinedStr):
        pieces = []
        for part in node.values:
            if isinstance(part, ast.Constant) and isinstance(part.value, str):
                pieces.append(part.value)
            elif isinstance(part, ast.FormattedValue):
                value = resolve(part.value, constants)
                if isinstance(part.value, ast.Constant) and isinstance(part.value.value, (int, float)):
                    value = part.value.value
                spec = resolve(part.format_spec, constants) if part.format_spec else ''
                if value is None or spec is None:
                    return None
                if part.conversion in (97, 114):
                    value = ascii(value) if part.conversion == 97 else repr(value)
                elif part.conversion == 115:
                    value = str(value)
                try:
                    pieces.append(format(value, spec))
                except (ValueError, TypeError):
                    return None
            else:
                return None
        return ''.join(pieces)
    if isinstance(node, ast.Name):
        return constants.get(node.id)
    if isinstance(node, ast.BinOp):
        left, right = resolve(node.left, constants), resolve(node.right, constants)
        if left is not None and right is not None:
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Div):
                return str(PurePosixPath(left) / right)
    if isinstance(node, ast.Call):
        func = name(node.func)
        values = [resolve(arg, constants) for arg in node.args]
        if values and all(v is not None for v in values):
            if func in ('Path', 'pathlib.Path', 'PurePosixPath', 'os.path.join'):
                return str(PurePosixPath(*values))
            if func == 'str' and len(values) == 1:
                return values[0]
    return None


def existence_target(node, constants):
    """Resolve only standard positive filesystem predicates."""
    if not isinstance(node, ast.Call):
        return None
    func = name(node.func)
    if func in ('os.path.exists', 'os.path.isfile', 'os.path.isdir') and node.args:
        return resolve(node.args[0], constants)
    if isinstance(node.func, ast.Attribute) and node.func.attr in ('exists', 'is_file', 'is_dir'):
        return resolve(node.func.value, constants)
    return None


def literal_paths(node, assignments, constants):
    if isinstance(node, ast.Name):
        values = assignments.get(node.id, [])
        if len(values) != 1:
            return None
        node = values[0]
    if not isinstance(node, (ast.List, ast.Tuple)):
        return None
    values = [resolve(value, constants) for value in node.elts]
    return values if values and all(absolute(value) for value in values) else None


def scoped_names(tree):
    """Keep function-local assignments from poisoning unrelated test functions.

    Rename lexical locals only; retain source positions for evidence. Globals
    stay shared. Nonlocal rebinding is left unsupported rather than guessed.
    """
    class Scope(ast.NodeTransformer):
        def __init__(self):
            self.bindings = []

        def visit_Name(self, node):
            for bindings in reversed(self.bindings):
                if node.id in bindings:
                    node.id = bindings[node.id]
                    break
            return node

        def visit_FunctionDef(self, node):
            local, shared = set(), set()
            def collect(part):
                if isinstance(part, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                    return
                if isinstance(part, ast.Name) and isinstance(part.ctx, ast.Store):
                    local.add(part.id)
                if isinstance(part, (ast.Global, ast.Nonlocal)):
                    shared.update(part.names)
                for child in ast.iter_child_nodes(part):
                    collect(child)
            for part in node.body:
                collect(part)
            args = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
            args += [a for a in (node.args.vararg, node.args.kwarg) if a]
            local.update(a.arg for a in args)
            mapping = {key: f'__scope_{node.lineno}_{key}' for key in local - shared}
            self.bindings.append(mapping)
            for arg in args:
                arg.arg = mapping.get(arg.arg, arg.arg)
            node.body = [self.visit(part) for part in node.body]
            self.bindings.pop()
            return node

        visit_AsyncFunctionDef = visit_FunctionDef
    return Scope().visit(tree)


def implied_key_output(path, instruction):
    """A requested SSH keypair also produces its public key, not arbitrary .pub files."""
    if not path.endswith('.pub'):
        return False
    base = path[:-4]
    for line in instruction.splitlines():
        if (re.search(r'\b(?:generat(?:e|ing)|creat(?:e|ing))\b', line, re.I)
                and re.search(r'\b(?:SSH|ed25519|RSA)\b', line, re.I)
                and re.search(r'\bkey\s*pair\b', line, re.I)
                and not re.search(r"\b(?:do\s+not|never|don't)\s+(?:generate|create)\b", line, re.I)):
            if re.search(r'(?<![\w/.-])' + re.escape(base) + r'(?![\w/.-])', line):
                return True
    return False


HTTP_METHODS = {'GET', 'POST', 'PUT', 'DELETE', 'PATCH', 'HEAD', 'OPTIONS'}
HTTP_CALL = re.compile(r'(?:^|[._])(?:get|post|put|delete|patch|head|options|request|requests|fetch|urlopen|http\w*|url\w*|route|endpoint|api\w*|\w*curl|wget|\w*health\w*)$', re.I)
# Library calls whose first argument is an existing file or directory they read.
READER_CALLS = {'np.load', 'numpy.load', 'torch.load', 'joblib.load', 'pickle.load', 'h5py.File', 'Image.open',
                'PIL.Image.open', 'os.stat', 'os.listdir', 'os.scandir', 'os.path.getsize', 'filecmp.cmp',
                'tarfile.open', 'zipfile.ZipFile', 'wave.open', 'shutil.copy', 'shutil.copyfile', 'shutil.copytree'}
READER_METHODS = {'loadtxt', 'genfromtxt', 'fromfile', 'read_csv', 'read_json', 'read_parquet', 'read_excel',
                  'read_table', 'read_pickle', 'read_feather', 'read_hdf', 'imread', 'loadmat'}
SENTINEL = '/__ot_path_parameter__'


def literal_context(node, parent, constants, parents=None):
    """Role of an otherwise unclassified literal; only provable non-path uses are named.

    `compared`: an operand of ==, !=, in or not in (log text, code substrings,
    server routes such as `self.path == '/health'`). `http_route`: an argument
    of a request-style call or of a call that also passes an HTTP method.
    Everything else stays `unclassified` and remains a dependency.
    """
    compare = lambda n: isinstance(n, ast.Compare) and all(isinstance(op, (ast.Eq, ast.NotEq, ast.In, ast.NotIn)) for op in n.ops)
    if compare(parent):
        return 'compared'
    # Regular-expression patterns (re.findall(r'/home/user/[^\s]+', text)) describe text, not files.
    if isinstance(parent, ast.Call) and node in parent.args[:1] and re.fullmatch(r're\.\w+', name(parent.func) or ''):
        return 'compared'
    # Keys of an expectation mapping ({"libc.a": 4, "/long/path/lib.o": 1}) are compared values.
    if isinstance(parent, ast.Dict) and node in parent.keys:
        return 'compared'
    # [..., "/tmp/.hidden", ...] used only as `any(x in text for x in LIST)`.
    collection = parent
    if isinstance(collection, (ast.List, ast.Tuple, ast.Set)) and parents is not None:
        owner = parents.get(collection)
        targets = []
        if isinstance(owner, ast.comprehension) and owner.iter is collection:
            targets = [owner]
        elif isinstance(owner, ast.Assign) and len(owner.targets) == 1 and isinstance(owner.targets[0], ast.Name):
            ident = owner.targets[0].id
            targets = [n for n in parents if isinstance(n, ast.comprehension) and isinstance(n.iter, ast.Name) and n.iter.id == ident]
        for clause in targets:
            if isinstance(clause.target, ast.Name):
                generator = parents.get(clause)
                uses = [n for n in ast.walk(generator) if isinstance(n, ast.Name) and n.id == clause.target.id
                        and isinstance(n.ctx, ast.Load)]
                if uses and all(compare(parents.get(n)) for n in uses):
                    return 'compared'
    # for bad in ['/login/', ...]: assert bad not in endpoints
    loop = (parents or {}).get(parent)
    if (isinstance(parent, (ast.List, ast.Tuple)) and isinstance(loop, ast.For) and loop.iter is parent
            and isinstance(loop.target, ast.Name)):
        uses = [n for stmt in loop.body for n in ast.walk(stmt)
                if isinstance(n, ast.Name) and n.id == loop.target.id]
        if uses and all(compare(parents.get(n)) for n in uses):
            return 'compared'
    if isinstance(parent, ast.Call) and node in parent.args:
        method = [resolve(arg, constants) for arg in parent.args]
        url_argument = any(isinstance(arg, ast.Name) and 'url' in arg.id.lower() or
                           isinstance(m, str) and m.startswith(('http://', 'https://'))
                           for arg, m in zip(parent.args, method) if arg is not node)
        if (HTTP_CALL.search(name(parent.func) or '') or url_argument
                or any(isinstance(m, str) and m.upper() in HTTP_METHODS for m in method)):
            return 'http_route'
    return 'unclassified'


def sentinel_roles(variable, body, source, context, depth):
    """Roles `body` gives `variable` when it holds an absolute path (helper parameter, loop item).

    Classified with the same rules as literals; the inventory then combines the
    roles exactly as for a direct use (e.g. written by a command, then read).
    """
    if depth >= 2:
        return set()
    text = variable + ' = ' + repr(SENTINEL) + '\n' + '\n'.join(ast.unparse(stmt) for stmt in body)
    try:
        roles = {r['role'] for r in python_paths(text, source, context=context, depth=depth + 1)
                 if r['path'] == SENTINEL}
    except (SyntaxError, ValueError, RecursionError):
        return set()
    return roles - {'unclassified'}


def python_paths(text, source, offset=0, context=None, depth=0):
    tree = scoped_names(ast.parse(text))
    constants = {}
    assignments = {}
    # Do not resolve names with conflicting assignments or shadowing parameters.
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    assignments.setdefault(target.id, []).append(node.value)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            assignments.setdefault(node.target.id, []).append(node.value)
        elif isinstance(node, ast.arg):
            assignments.setdefault(node.arg, []).append(None)
    for _ in range(8):
        updated = dict(constants)
        for key, values in assignments.items():
            resolved = [resolve(v, constants) for v in values]
            if resolved and resolved[0] is not None and len(set(resolved)) == 1:
                updated[key] = resolved[0]
        if updated == constants:
            break
        constants = updated
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    records = []

    def emit(value, node, role):
        if absolute(value):
            ancestor = node
            while ancestor in parents and not isinstance(ancestor, (ast.FunctionDef, ast.AsyncFunctionDef)):
                ancestor = parents[ancestor]
            scope = ancestor.lineno if isinstance(ancestor, (ast.FunctionDef, ast.AsyncFunctionDef)) else 0
            records.append(dict(path=value, source=source, line=node.lineno + offset, role=role, scope=scope))

    def guarded_role(value, node, default):
        child = node
        while child in parents:
            parent = parents[child]
            if (isinstance(parent, ast.If) and child in parent.body
                    and existence_target(parent.test, constants) == value):
                return 'optional_check'
            child = parent
        return default

    # Recognize small, statically resolved searches. A list alone is never
    # optional: every use of the loop variable must be existence-guarded.
    for loop in ast.walk(tree):
        if not isinstance(loop, ast.For) or not isinstance(loop.target, ast.Name):
            continue
        values = literal_paths(loop.iter, assignments, constants)
        if not values:
            continue
        if isinstance(loop.iter, ast.Name):
            loads = [n for n in ast.walk(tree) if isinstance(n, ast.Name)
                     and isinstance(n.ctx, ast.Load) and n.id == loop.iter.id]
            if loads != [loop.iter]:
                continue
        valid = True
        guarded = False
        for value in values:
            bindings = dict(constants, **{loop.target.id: value})
            allowed = set()
            for stmt in ast.walk(loop):
                if isinstance(stmt, ast.If) and existence_target(stmt.test, bindings) == value:
                    allowed.update(ast.walk(stmt.test))
                    for child in stmt.body:
                        allowed.update(ast.walk(child))
                    guarded = True
            for node in ast.walk(loop):
                if isinstance(node, ast.Name) and node.id == loop.target.id:
                    if isinstance(node.ctx, ast.Store) and node is not loop.target:
                        valid = False
                    elif isinstance(node.ctx, ast.Load) and node not in allowed:
                        valid = False
        if valid and guarded:
            for value in values:
                emit(value, loop, 'optional_check')

    # Explicit "at least one exists" assertions are required groups, not
    # individually required files. Do not infer requirements from search lists.
    for assertion in (n for n in ast.walk(tree) if isinstance(n, ast.Assert)):
        test = assertion.test
        values = None
        if isinstance(test, ast.BoolOp) and isinstance(test.op, ast.Or):
            candidates = [existence_target(n, constants) for n in test.values]
            if candidates and all(absolute(v) for v in candidates):
                values = candidates
        elif isinstance(test, ast.Call) and name(test.func) == 'any' and len(test.args) == 1:
            gen = test.args[0]
            if isinstance(gen, ast.GeneratorExp) and len(gen.generators) == 1:
                clause = gen.generators[0]
                if isinstance(clause.target, ast.Name) and not clause.ifs and not clause.is_async:
                    candidates = literal_paths(clause.iter, assignments, constants)
                    if candidates and all(existence_target(gen.elt, dict(constants, **{clause.target.id: v})) == v
                                          for v in candidates):
                        values = candidates
        if values:
            for value in values:
                emit(value, assertion, 'required_alternative')
                records[-1]['alternatives'] = sorted(set(values))

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = name(node.func)
        method = func.rsplit('.', 1)[-1]
        value = resolve(node.args[0], constants) if node.args else None
        if method == 'open' and not node.args:
            value = next((resolve(k.value, constants) for k in node.keywords if k.arg == 'file'), None)
        receiver = resolve(node.func.value, constants) if isinstance(node.func, ast.Attribute) else None
        if method in ('exists', 'is_file', 'is_dir', 'isfile', 'isdir', 'access'):
            target = receiver if absolute(receiver) else value
            parent = parents.get(node)
            if isinstance(parent, ast.Assert):
                role = 'required'
            elif isinstance(parent, ast.UnaryOp) and isinstance(parent.op, ast.Not) and isinstance(parents.get(parent), ast.Assert):
                role = 'expected_absent'
            else:
                role = 'optional_check'
            emit(target, node, role)
        elif method == 'open':
            path_method = absolute(receiver)
            target = receiver if path_method else value
            mode_index = 0 if path_method else 1
            mode = resolve(node.args[mode_index], constants) if len(node.args) > mode_index else 'r'
            for keyword in node.keywords:
                if keyword.arg == 'mode':
                    mode = resolve(keyword.value, constants)
            emit(target, node, 'generated' if mode and any(c in mode for c in 'wax') else guarded_role(target, node, 'read'))
        elif method in ('read_text', 'read_bytes'):
            emit(receiver, node, guarded_role(receiver, node, 'read'))
        elif method in ('stat', 'iterdir') and absolute(receiver):
            emit(receiver, node, guarded_role(receiver, node, 'read'))
        elif (func in READER_CALLS or method in READER_METHODS) and absolute(value):
            emit(value, node, guarded_role(value, node, 'read'))
            if func == 'filecmp.cmp' and len(node.args) > 1:
                emit(resolve(node.args[1], constants), node, guarded_role(resolve(node.args[1], constants), node, 'read'))
        elif method in ('write_text', 'write_bytes', 'touch', 'mkdir') and absolute(receiver):
            emit(receiver, node, 'generated')
        elif func in ('os.mkdir', 'os.makedirs'):
            emit(value, node, 'generated')
        elif func in ('os.rename', 'os.replace', 'shutil.move', 'shutil.copy', 'shutil.copy2', 'shutil.copyfile',
                      'shutil.copytree', 'os.symlink', 'os.link') and len(node.args) > 1:
            emit(resolve(node.args[1], constants), node, 'generated')
        elif method in ('rename', 'replace') and absolute(receiver) and node.args:
            emit(resolve(node.args[0], constants), node, 'generated')
        elif func in ('os.system', 'os.popen') and value:
            for target, role in shell_paths(value, context):
                emit(target, node, role)
        elif func in ('subprocess.run', 'subprocess.call', 'subprocess.check_call', 'subprocess.check_output', 'subprocess.Popen'):
            argument = node.args[0] if node.args else next((k.value for k in node.keywords if k.arg == 'args'), None)
            if isinstance(argument, ast.Name) and len(assignments.get(argument.id, [])) == 1:
                argument = assignments[argument.id][0]
            cwd = next((resolve(k.value, constants) for k in node.keywords if k.arg == 'cwd'), None)
            if isinstance(argument, (ast.List, ast.Tuple)):
                argv = [resolve(arg, constants) for arg in argument.elts]
                for target, role, evidence in command_paths(argv, absolute, python_paths, context, cwd):
                    emit(target, node, guarded_role(target, node, role) if role == 'execute' else role)
                    records[-1]['evidence'] = evidence
            else:
                command = resolve(argument, constants)
                shell = any(k.arg == 'shell' and isinstance(k.value, ast.Constant) and k.value.value is True
                            for k in node.keywords)
                if command and shell:
                    for target, role in shell_paths(command, context, cwd):
                        emit(target, node, role)
                elif absolute(command):
                    emit(command, node, guarded_role(command, node, 'execute'))
    records.extend(helper_writes(text, source, offset))
    # Command lists passed to any helper (_run([...])), with variables resolved:
    # the outputs they create are the verifier's own files.
    for node in ast.walk(tree):
        if isinstance(node, (ast.List, ast.Tuple)) and isinstance(parents.get(node), ast.Call):
            argv = [resolve(e, constants) for e in node.elts]
            for i, word in enumerate(argv):
                target = argv[i + 1] if i + 1 < len(argv) else None
                if word == '-o' and target:
                    emit(target, node.elts[i + 1], 'generated')
                elif word in ('venv', 'clone') and argv[-1] and i < len(argv) - 1:
                    emit(argv[-1], node.elts[-1], 'generated')
                elif word == 'mkdir':
                    for value, element in zip(argv[i + 1:], node.elts[i + 1:]):
                        if value and not value.startswith('-'):
                            emit(value, element, 'generated')
    classified = {r['path'] for r in records}
    functions = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions.setdefault(node.name, []).append(node)
    parameter_roles = {}

    def parameter_role(function, index=None, keyword=None):
        key = (function.name, function.lineno, index, keyword)
        if key not in parameter_roles:
            arguments = [*function.args.posonlyargs, *function.args.args]
            if keyword is not None:
                match = [a for a in arguments + function.args.kwonlyargs if a.arg.endswith('_' + keyword) or a.arg == keyword]
            else:
                match = arguments[index:index + 1] if index < len(arguments) else []
            parameter_roles[key] = (sentinel_roles(match[0].arg, function.body, source, context, depth)
                                    if match else set())
        return parameter_roles[key]

    # Literal arguments of local helpers take the role the helper gives that parameter.
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        candidates = functions.get(node.func.id, [])
        if len(candidates) != 1:
            continue  # Redefined or not local: leave unclassified.
        # A local shell wrapper receives a command, not one pathname. Resolve
        # the caller's f-string before assigning input/output roles to its paths.
        function = candidates[0]
        parameters = [*function.args.posonlyargs, *function.args.args]
        for index, argument in enumerate(node.args[:len(parameters)]):
            parameter = parameters[index].arg
            command = resolve(argument, constants)
            if not command or any(isinstance(n, ast.Name) and n.id == parameter
                                  and isinstance(n.ctx, ast.Store) for n in ast.walk(function)):
                continue
            forwards_shell = any(
                isinstance(call, ast.Call)
                and name(call.func) in ('subprocess.run', 'subprocess.call', 'subprocess.check_call',
                                       'subprocess.check_output', 'subprocess.Popen')
                and call.args and isinstance(call.args[0], ast.Name) and call.args[0].id == parameter
                and any(k.arg == 'shell' and isinstance(k.value, ast.Constant) and k.value.value is True
                        for k in call.keywords)
                for call in ast.walk(function))
            if forwards_shell:
                for target, role in shell_paths(command, context):
                    emit(target, argument, role)
        for index, argument in enumerate(node.args):
            value = resolve(argument, constants)
            if absolute(value) and value not in classified:
                for role in sorted(parameter_role(candidates[0], index=index)):
                    emit(value, argument, guarded_role(value, node, role) if role in ('read', 'execute') else role)
        for keyword in node.keywords:
            value = resolve(keyword.value, constants)
            if keyword.arg and absolute(value) and value not in classified:
                for role in sorted(parameter_role(candidates[0], keyword=keyword.arg)):
                    emit(value, keyword.value, guarded_role(value, node, role) if role in ('read', 'execute') else role)

    # Loops over a literal list of paths: each item takes the role the body gives it.
    for loop in ast.walk(tree):
        if not isinstance(loop, ast.For) or not isinstance(loop.target, ast.Name):
            continue
        values = literal_paths(loop.iter, assignments, constants)
        if not values:
            continue
        if isinstance(loop.iter, ast.Name):
            loads = [n for n in ast.walk(tree) if isinstance(n, ast.Name)
                     and isinstance(n.ctx, ast.Load) and n.id == loop.iter.id]
            if loads != [loop.iter]:
                continue  # The list is used elsewhere too: keep it visible.
        for role in sorted(sentinel_roles(loop.target.id, loop.body, source, context, depth)):
            for value in values:
                if value not in classified:
                    emit(value, loop, role)

    # lib = Path(A); if not lib.exists(): lib = Path(B)  ->  A or B, whichever exists.
    for statement in ast.walk(tree):
        if not (isinstance(statement, ast.If) and isinstance(statement.test, ast.UnaryOp)
                and isinstance(statement.test.op, ast.Not) and len(statement.body) == 1
                and isinstance(statement.body[0], ast.Assign) and len(statement.body[0].targets) == 1
                and isinstance(statement.body[0].targets[0], ast.Name)):
            continue
        variable = statement.body[0].targets[0].id
        check = statement.test.operand
        tested = (check.func.value if isinstance(check, ast.Call) and isinstance(check.func, ast.Attribute)
                  and check.func.attr in ('exists', 'is_file', 'is_dir') else
                  check.args[0] if isinstance(check, ast.Call) and check.args
                  and name(check.func) in ('os.path.exists', 'os.path.isfile', 'os.path.isdir') else None)
        values = assignments.get(variable, [])
        if not (isinstance(tested, ast.Name) and tested.id == variable and len(values) == 2):
            continue
        first, second = (resolve(v, constants) for v in values)
        if absolute(first) and absolute(second) and first not in classified and second not in classified:
            for value in (first, second):
                emit(value, statement, 'required_alternative')
                records[-1]['alternatives'] = sorted({first, second})

    # any(<exists>(p) for p in [...]) outside an assert: a search, not a requirement.
    for call in ast.walk(tree):
        if not (isinstance(call, ast.Call) and name(call.func) == 'any' and len(call.args) == 1
                and isinstance(call.args[0], ast.GeneratorExp) and len(call.args[0].generators) == 1):
            continue
        clause = call.args[0].generators[0]
        if not isinstance(clause.target, ast.Name):
            continue
        values = literal_paths(clause.iter, assignments, constants)
        if values and all(v not in classified for v in values) and all(
                existence_target(call.args[0].elt, dict(constants, **{clause.target.id: v})) == v for v in values):
            for value in values:
                emit(value, call, 'optional_check')
    # Preserve unclassified literals, including arguments passed through helpers.
    classified = {r['path'] for r in records}
    for node in ast.walk(tree):
        value = resolve(node, constants)
        if absolute(value) and value not in classified:
            # Constructor components are not independent dependencies.
            parent = parents.get(node)
            # Literal pieces of computed strings are not standalone paths.
            # Keep separately classified calls (e.g. open inside an f-string).
            ancestor = parent
            fragment = False
            while ancestor is not None:
                if isinstance(ancestor, (ast.JoinedStr, ast.BinOp)):
                    fragment = True
                    break
                if isinstance(ancestor, (ast.stmt, ast.Call)):
                    break
                ancestor = parents.get(ancestor)
            if fragment or absolute(resolve(parent, constants)):
                continue
            # NAME = "/path" (or Path("/path")) never read anywhere: not a dependency.
            holder = parent
            while isinstance(holder, ast.Call) and name(holder.func) in ('Path', 'pathlib.Path', 'str'):
                holder = parents.get(holder)
            if (isinstance(holder, ast.Assign) and len(holder.targets) == 1 and isinstance(holder.targets[0], ast.Name)
                    and not any(isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load) and n.id == holder.targets[0].id
                                for n in ast.walk(tree))):
                continue
            emit(value, node, literal_context(node, parent, constants, parents))
    return records


def shell_fallbacks(lines):
    """Recognize literal if/elif selectors; leave other shell syntax untouched.

    Each branch must test one path and assign that same path to one shared
    variable. Only those exact source occurrences are classified, so a later
    unconditional reference to a candidate still counts as a dependency.
    """
    def tokens(line):
        try:
            return shlex.split(line, comments=True)
        except ValueError:
            return []

    def condition(line, keyword):
        words = tokens(line.replace(';', ' ; '))
        if not words or words[0] != keyword or words[-2:] != [';', 'then']:
            return None
        test = words[1:-2]
        if len(test) == 4 and (test[0], test[-1]) in (('[', ']'), ('[[', ']]')):
            test = test[1:-1]
        elif len(test) == 3 and test[0] == 'test':
            test = test[1:]
        else:
            return None
        return test[1] if len(test) == 2 and test[0] in ('-f', '-e', '-r', '-x') and absolute(test[1]) else None

    result = {}
    for start in range(len(lines)):
        first = condition(lines[start], 'if')
        if first is None:
            continue
        cursor, keyword, variable, branches = start, 'if', None, []
        while cursor < len(lines):
            path = condition(lines[cursor], keyword)
            if path is None:
                break
            test_line = cursor
            cursor += 1
            while cursor < len(lines) and not tokens(lines[cursor]):
                cursor += 1
            words = tokens(lines[cursor]) if cursor < len(lines) else []
            match = re.fullmatch(r'([A-Za-z_][A-Za-z_0-9]*)=(.*)', words[0]) if len(words) == 1 else None
            if not match or match[2] != path or (variable is not None and match[1] != variable):
                break
            variable = match[1]
            branches.append((path, test_line, cursor))
            cursor += 1
            while cursor < len(lines) and not tokens(lines[cursor]):
                cursor += 1
            if cursor < len(lines) and condition(lines[cursor], 'elif'):
                keyword = 'elif'
                continue
            required = False
            if cursor < len(lines) and tokens(lines[cursor]) == ['else']:
                cursor += 1
                # Permit only diagnostic echo lines followed by a nonzero exit.
                while cursor < len(lines) and (not tokens(lines[cursor]) or tokens(lines[cursor])[0] == 'echo'):
                    cursor += 1
                end = tokens(lines[cursor]) if cursor < len(lines) else []
                if len(end) != 2 or end[0] != 'exit' or not end[1].isdigit() or int(end[1]) == 0:
                    break
                required = True
                cursor += 1
                while cursor < len(lines) and not tokens(lines[cursor]):
                    cursor += 1
            if cursor >= len(lines) or tokens(lines[cursor]) != ['fi'] or len(branches) < 2:
                break
            paths = sorted({b[0] for b in branches})
            for path, check_line, assignment_line in branches:
                for line in (check_line, assignment_line):
                    result[(line, path)] = dict(role='required_alternative' if required else 'optional_check',
                                               **({'alternatives': paths} if required else {}))
            break
    return result


def shell_paths(line, context=None, cwd=None):
    """Whole shell tokens, with comments/URLs removed and direct writes marked.

    Do not interpret arbitrary command arguments as output filenames. Quoted
    redirection characters are ordinary arguments, not shell operators.
    """
    lexer = shlex.shlex(without_urls(line), posix=False, punctuation_chars='<>|&;()')
    lexer.whitespace_split = True
    lexer.commenters = '#'
    try:
        tokens = list(lexer)
    except ValueError:
        return []  # Multiline shell syntax cannot be resolved from this line.
    result = []
    # Classify simple commands only. Keep shell control syntax on the existing
    # fallback path; split ordinary pipelines/command lists at shell operators.
    command_roles = {}
    segment = []
    def classify_segment(indices):
        words, positions = [], []
        skip = False
        for j in indices:
            token = tokens[j]
            if skip:
                skip = False
                continue
            if token in ('<', '>', '>>', '>|', '&>', '&>>', '<<', '<<<', '>&', '<&'):
                skip = True
                continue
            if token.isdigit() and j + 1 < len(tokens) and tokens[j + 1].startswith(('>', '<')):
                continue
            if not words and re.match(r'^[A-Za-z_][A-Za-z0-9_]*=', token):
                continue
            words.append(token.strip("\"'"))
            positions.append(j)
        if not words or words[0] in ('if', 'elif', 'then', 'else', 'fi', 'for', 'while', 'do', 'done', '[', '[[', 'test'):
            return
        classified = command_paths(words, absolute, python_paths, context, cwd)
        for path, role, evidence in classified:
            for j, word in zip(positions, words):
                if word == path or word.endswith('=' + path):
                    command_roles[j] = role
    for i, token in enumerate(tokens):
        if token in (';', '&&', '||', '|'):
            classify_segment(segment)
            segment = []
        else:
            segment.append(i)
    classify_segment(segment)
    for i, token in enumerate(tokens):
        value = token[1:-1] if len(token) >= 2 and token[0] == token[-1] and token[0] in "\"'" else token
        # Assignment RHS can name a path, but PATH lists are not one filename.
        if re.match(r'^[A-Za-z_][A-Za-z0-9_]*=', value):
            value = value.split('=', 1)[1].strip("\"'")
        if not absolute(value) or ':' in value or '%' in value or '`' in value:
            continue
        role = ('generated' if i and tokens[i - 1] in ('>', '>>', '>|', '&>', '&>>') else
                'read' if i and tokens[i - 1] == '<' else command_roles.get(i, 'shell_literal'))
        result.append((value, role))
    return result


def requested_script_outputs(task, instruction):
    """Infer direct literal outputs of a supplied shell script requested to run."""
    from validation.rules.static.test_file_references.supplied_references import literal_writes, supplied_files
    files = supplied_files(task)
    conflicted = set()
    for source in sorted((task / 'setup_files').rglob('*.sh')):
        for path, body, append in literal_writes(source.read_text(errors='replace'), None):
            if not path.endswith('.sh') or append:
                continue
            if path in files and files[path][0] != body:
                conflicted.add(path)
            files[path] = (body, str(source.relative_to(task)))
    outputs = {}
    for paragraph in re.split(r'\n\s*\n', instruction):
        scripts = set(re.findall(r'(?<![\w/.-])(/[\w/.-]+\.sh)(?![\w/.-])', paragraph))
        if len(scripts) != 1 or not re.search(r'\b(?:run|execute|invoke)\b', paragraph, re.I):
            continue
        if re.search(r"\b(?:do\s+not|never|don't)\s+(?:run|execute|invoke)\b", paragraph, re.I):
            continue
        script = next(iter(scripts))
        if script in conflicted or script not in files:
            continue
        body, source = files[script]
        for number, line in enumerate(body.splitlines(), 1):
            for path, role in shell_paths(line):
                if role == 'generated':
                    outputs[path.rstrip('/') or '/'] = dict(script=script, source=source, script_line=number)
    return outputs


def shell_blocks(lines):
    """Line ranges of top-level and nested `if ... fi` blocks (innermost first)."""
    stack, blocks = [], []
    for number, line in enumerate(lines):
        try:
            words = shlex.split(line, comments=True)
        except ValueError:
            continue
        if words[:1] == ['if']:
            stack.append(number)
        if stack and (words[-1:] == ['fi'] or words[:1] == ['fi']):
            blocks.append((stack.pop(), number))
    return sorted(blocks, key=lambda b: b[1] - b[0])


SHELL_TEST = re.compile(r'^\s*(?:if|elif)\s+(?:!\s+)?\[\[?\s+(?:!\s+)?-[efdrxs]\s+["\']?([^"\'\s\]]+)["\']?\s*\]\]?')


def shell_guarded(lines, index, path):
    """A shell use is optional when it is the tested path of `if/elif [ -f X ]` or inside that branch."""
    test = SHELL_TEST.match(lines[index])
    if test and test[1] == path:
        return True
    for start, end in shell_blocks(lines):
        if start <= index <= end:
            for k in range(start, index + 1):
                t = SHELL_TEST.match(lines[k])
                if t and t[1] == path and not re.search(r'\[\[?\s+!|\bif\s+!', lines[k]):
                    return True
    return False


X509_NAME = re.compile(r'(?:/[A-Za-z]{1,12}=[^/]*)+')  # openssl -subj /CN=test/O=Org, not a file
INTENTIONALLY_ABSENT = re.compile(r'(?i)(?:^|[/_.-])(?:non[-_]?existent|nonexist\w*|does[-_]?not[-_]?exist|not[-_]?exist\w*|bogus|fake)(?:$|[/_.-])')


def verifier_created(path, texts):
    """The verifier's own sources create the path: compiler -o, venv, git clone, mkdir, redirects."""
    q = re.escape(path)
    end = r'(?=["\'\s,)\]}]|$)'
    patterns = [r'-o\s*["\']?\s*' + q + end,
                r'["\']-o["\']\s*,\s*f?["\']' + q + end,
                r'\bvenv["\']?\s*,?\s*f?["\']?' + q + end,
                r'\bclone\b[^\n]*?["\'\s]' + q + end,
                r'\bmkdir\b[^\n]*?["\'\s]' + q + end,
                r'>>?\s*["\']?' + q + end]
    return any(re.search(pattern, text) for pattern in patterns for text in texts)


def provided_environment(task):
    """Text of the code and config the task provides (environment/ except the Dockerfile) and its WORKDIRs.

    The Dockerfile is left out on purpose: a COPY line naming an input that is never shipped
    is a real missing input, not code that produces the file.
    """
    texts, workdirs = [], set()
    root = Path(task) / 'environment'
    if root.is_dir():
        for item in sorted(root.rglob('*')):
            if not item.is_file() or item.stat().st_size > 2_000_000:
                continue
            try:
                text = item.read_text()
            except (UnicodeError, OSError):
                continue
            if item.name == 'Dockerfile':
                workdirs.update(m.rstrip('/') for m in re.findall(r'(?im)^\s*WORKDIR\s+(\S+)', text))
            else:
                texts.append(text)
    return '\n'.join(texts), workdirs | {'/app'}


def produced_by_provided_code(path, text, workdirs):
    """The provided program/config names the never-existing path: an output of code the agent builds or runs."""
    if not text:
        return False
    bound = r'(?![\w-])'
    if re.search(r'(?<![\w.-])' + re.escape(path) + bound, text):
        return True
    for workdir in workdirs:
        if path.startswith(workdir + '/'):
            relative = path[len(workdir) + 1:]
            if ('/' in relative or '.' in relative) and len(relative) >= 3 and \
                    re.search(r'(?<![\w/.-])(?:\./)?' + re.escape(relative) + bound, text):
                return True
    return False


def build_product(path, stems):
    name = PurePosixPath(path).name
    stem = name[:-2] if name.endswith(('.o', '.a')) else name[:-3] if name.endswith('.so') else name
    return bool(stems) and '.' not in stem.strip('.') and stem in stems or (name.endswith(('.o', '.so', '.a')) and stem in stems)


def under_named_directory(path, named, workdirs):
    """A specific directory (at least two levels, not a workdir or home) named in the instruction contains the path."""
    parent = PurePosixPath(path).parent
    while len(parent.parts) >= 3:
        text = str(parent)
        if text not in workdirs and not re.fullmatch(r'/home/[^/]+', text) and (text in named or text + '/' in named):
            return True
        parent = parent.parent
    return False


def inventory(task, context=None, instruction_view=None):
    task = Path(task)
    context = Path(context) if context is not None else task / 'tests'
    instruction = (instruction_view or {}).get('instruction', (task / 'instruction.md').read_text())
    script_outputs = requested_script_outputs(task, instruction)
    artifact_outputs = artifact_instruction_outputs(task, instruction)
    records, errors, coverage = [], [], []
    source_lines = {}
    for source in sorted(context.rglob('*')):
        if not source.is_file() or source.name == 'setup.sh':
            continue
        if source.suffix not in ('.py', '.sh'):
            if source.suffix in ('.js', '.ts', '.go', '.rs', '.rb', '.bat', '.ps1'):
                coverage.append(f'unsupported verifier source: {source.relative_to(context)}')
            continue
        label = source.relative_to(context).as_posix()
        try:
            text = source.read_text()
            source_lines[label] = text.splitlines()
            if source.suffix == '.py':
                records.extend(python_paths(text, label, context=context))
            else:
                # Extract Python heredocs without executing task-controlled code.
                from validation.rules.dynamic.verifier_shell_sources import python_heredoc_command, unused_function_lines
                # Bash ignores NUL bytes while reading shell source, including heredocs.
                # Do not apply this normalization to standalone Python files.
                if re.match(r'#![^\n]*\bbash\b', text):
                    text = text.replace('\x00', '')
                lines = text.splitlines()
                unused = unused_function_lines(lines)
                if unused:
                    coverage.append(f'unreferenced shell function body excluded: {label}')
                fallbacks = shell_fallbacks(lines)
                cursor = 0
                while cursor < len(lines):
                    if cursor in unused:
                        cursor += 1
                        continue
                    line = lines[cursor]
                    match = re.search(r'<<-?\s*[\x27\"]?(\w+)', line)
                    if match and python_heredoc_command(line):
                        end = cursor + 1
                        while end < len(lines) and lines[end].strip() != match[1]:
                            end += 1
                        body = lines[cursor + 1:end]
                        if '<<-' in line:
                            body = [part.lstrip('\t') for part in body]
                        records.extend(python_paths('\n'.join(body), label, cursor + 1, context=context))
                        cursor = end + 1
                        continue
                    for path, role in shell_paths(line, context):
                        details = fallbacks.get((cursor, path), {'role': role}) if role != 'generated' else {'role': role}
                        if details['role'] in ('shell_literal', 'read', 'subprocess_input', 'execute') and shell_guarded(lines, cursor, path):
                            details = {'role': 'optional_check'}
                        records.append(dict(path=path, source=label, line=cursor + 1, **details))
                    if match:
                        # Body of a non-Python heredoc (a config file the verifier writes):
                        # its content is data, not paths the verifier uses.
                        end = cursor + 1
                        while end < len(lines) and lines[end].strip() != match[1]:
                            end += 1
                        cursor = end + 1
                        continue
                    cursor += 1
        except (SyntaxError, UnicodeError, OSError) as exc:
            errors.append(f'{label}: {type(exc).__name__}: {exc}')
    # Normalize directory spelling without resolving symlinks or '..'.
    for item in records:
        item['path'] = item['path'].rstrip('/') or '/'
        if 'alternatives' in item:
            item['alternatives'] = sorted({p.rstrip('/') or '/' for p in item['alternatives']})
    def verifier_location_fallback(path, uses):
        """Another location of a file shipped in tests/, selected only by a condition.

        Each use must sit in a shell if/elif/else block, or on a Python
        conditional-expression line, that also names the /tests location the
        harness always provides. Unconditional uses remain dependencies.
        """
        match = re.fullmatch(r'(/.+)/tests(/.*)?', path)
        if not match:
            return False
        relative = (match[2] or '').lstrip('/')
        if not ((context / relative).exists() if relative else context.is_dir()):
            return False
        canonical = re.compile(r'(?<![\w/.-])/tests(?:' + re.escape(match[2] or '') + r')?(?![\w.-])')
        for use in uses:
            lines = source_lines.get(use['source'], [])
            index = use['line'] - 1
            if not 0 <= index < len(lines):
                return False
            if use['source'].endswith('.py') or not use['source'].endswith('.sh'):
                line = lines[index]
                if not (canonical.search(line) and re.search(r'\bif\b.*\belse\b', line)):
                    return False
                continue
            block = next((b for b in shell_blocks(lines) if b[0] <= index <= b[1]), None)
            assignment = re.match(r'\s*([A-Za-z_][A-Za-z_0-9]*)=', lines[index])
            if block is None and assignment:
                # `TEST=/workspace/tests/x` directly before `if [ ! -f "$TEST" ]; then TEST=/tests/x; fi`.
                block = next((b for b in shell_blocks(lines) if index < b[0] <= index + 3
                              and '$' + assignment[1] in lines[b[0]].replace('{', '')), None)
            # Also `TEST=/tests/x` directly before `if [ ! -f "$TEST" ]; then TEST=...; fi`.
            if block is None or not any(canonical.search(line) for line in lines[max(0, block[0] - 3):block[1] + 1]):
                return False
        return True

    verifier_texts = ['\n'.join(lines) for lines in source_lines.values()]
    named_paths = {m.rstrip('/.,:;)') for m in re.findall(r'(?<![\w.-])(/[\w.+-]+(?:/[\w.+*{}-]+)*/?)', instruction)}
    environment_text, workdirs = provided_environment(task)
    # A binary or object named after a supplied or named source file (csv2json.c -> /app/csv2json).
    source_stems = {PurePosixPath(m).stem for m in re.findall(r'[\w.+-]+\.(?:c|cc|cpp|cxx|go|rs|f90|f|zig)\b', instruction)}
    environment_root = task / 'environment'
    if environment_root.is_dir():
        source_stems |= {item.stem for item in environment_root.rglob('*')
                         if item.suffix in ('.c', '.cc', '.cpp', '.cxx', '.go', '.rs', '.f90', '.zig')}
    paths = []
    for path in sorted({r['path'] for r in records}):
        uses = [r for r in records if r['path'] == path]
        roles = {r['role'] for r in uses}
        # Exact token match, not substring or parent-directory exclusion.
        boundary = r'(?![\w/-]|\.[\w/.-])'
        mentioned = bool(re.search(r'(?<![\w/.-])' + re.escape(path) + r'/?' + boundary, instruction))
        parts = PurePosixPath(path).parts[1:]
        # The user defines a relative filename/suffix as a mention for this
        # dependency screen, without requiring successful filesystem resolution.
        # Do not match inside a different absolute path or a longer filename.
        relative_mention = next((relative for i in range(len(parts))
                                 if (relative := '/'.join(parts[i:])) and re.search(
                                     r'(?<![\w/.-])(?:\./)?' + re.escape(relative) + r'/?' + boundary, instruction)), None)
        unknown_uses = [u for u in uses if u['role'] == 'subprocess_unknown']
        # An unknown command may produce a file read/asserted later. Explicit
        # earlier reads and independently identified subprocess inputs still fail.
        definite_input = 'subprocess_input' in roles or 'execute' in roles or any(
            u['role'] in ('read', 'required') and all(u['source'] == v['source']
                                      and u.get('scope', 0) == v.get('scope', 0) and u['line'] < v['line']
                                      for v in unknown_uses) for u in uses)
        reason = ('instruction' if mentioned else 'instruction_relative' if relative_mention else
                  'instruction_keypair_output' if implied_key_output(path, instruction) else
                  'instruction_script_output' if path in script_outputs else
                  'instruction_artifact_output' if path in artifact_outputs else
                  'verifier_generated' if 'generated' in roles and 'subprocess_input' not in roles else
                  'subprocess_role_unresolved' if unknown_uses and not definite_input else
                  'optional_or_negative_check' if roles <= {'optional_check', 'expected_absent'} else
                  'harness_output' if path == '/logs/verifier' or path.startswith('/logs/verifier/') else
                  'verifier_location_fallback' if verifier_location_fallback(path, uses) else
                  'not_a_path' if roles <= {'compared', 'http_route'} else
                  'not_a_path' if X509_NAME.fullmatch(path) else
                  'verifier_created' if any(verifier_created(str(p), verifier_texts)
                                            for p in [PurePosixPath(path), *PurePosixPath(path).parents][:-1]
                                            if len(p.parts) >= 3) else
                  'intentionally_absent' if INTENTIONALLY_ABSENT.search(path) else
                  'parent_of_named_path' if (not roles & {'read', 'execute', 'subprocess_input'} and
                                             any(p.startswith(path.rstrip('/') + '/') for p in named_paths)) else
                  'under_named_directory' if under_named_directory(path, named_paths, workdirs) else
                  'produced_by_provided_code' if produced_by_provided_code(path, environment_text, workdirs) else
                  'build_product_of_provided_source' if build_product(path, source_stems) else None)
        paths.append(dict(path=path, uses=uses, excluded=reason,
                          instruction_match='absolute' if mentioned else 'relative' if relative_mention else None,
                          relative_instruction_mention=relative_mention,
                          requested_script_output=script_outputs.get(path),
                          requested_artifact_output=artifact_outputs.get(path)))
    return dict(paths=paths, errors=errors, coverage=coverage, limitation=LIMITATION,
                extraction_requests=extraction_requests(instruction),
                instruction_resolution={k: v for k, v in (instruction_view or {}).items() if k != 'instruction'})


def resolved_instructions(tasks, out_root, contract_hash=None):
    """Use current stage-1/2 unique-path evidence before checking stage 3.

    Render the existing normalizer's instruction view without mutating frozen
    task files. Relative mentions are also accepted by this dependency screen.
    """
    from validation.rules.static.task_absolute_path.repair import collect_reports
    from validation.rules.static.task_absolute_path.instruction_paths import automatic
    from validation.contract import task_digest
    from validation.stages.layout import NAMES
    reports = []
    for stage in (1, 2):
        for path in sorted(Path(out_root).glob(f'stage_{stage}_{NAMES[stage]}/*/summary.json')):
            report = json.loads(path.read_text())
            if contract_hash is None or report.get('contract_sha256') == contract_hash:
                reports.append(path)
    evidence = collect_reports(reports)
    views = {}
    for task in tasks:
        entry = evidence.get(task.name)
        if not entry:
            continue
        selected = entry['selected']
        diagnostic = selected['diagnostic']
        if diagnostic.get('agent_baseline', {}).get('task_sha256') != task_digest(task):
            views[task.name] = {'status': 'stale_evidence', 'reports': [selected['report']]}
            continue
        try:
            instruction, edits = automatic((task / 'instruction.md').read_text(), diagnostic, selected['status'] == 'passed')
            baseline = diagnostic.get('agent_baseline', {})
            completed = baseline.get('status') == 'completed' or any(
                observation.get('status') == 'completed' for observation in diagnostic.get('observations', []))
            views[task.name] = dict(instruction=instruction, edits=edits,
                                   status='evidence_checked' if completed else 'incomplete',
                                   resolver_status=baseline.get('status'),
                                   resolver_error=baseline.get('error'), reports=[selected['report']])
        except ValueError as exc:
            views[task.name] = dict(status='unresolved', reason=str(exc), reports=[selected['report']])
    return views


def assess(record):
    inventory_ = record.get('inventory') or {}
    # No filesystem observation is needed when every parsed path is already
    # excluded by the rule's scope. Never apply this to a missing/broken inventory.
    if ('paths' in inventory_ and not inventory_.get('errors')
            and not any(not path.get('excluded') for path in inventory_['paths'])):
        return dict(status='passed', findings=[], errors=[], git_recoverable={}, archive_recoverable={},
                    limitation=LIMITATION, coverage=inventory_.get('coverage', []),
                    observations_required=False,
                    reason='No unmentioned static verifier input paths require filesystem observations.')
    errors = list(record.get('inventory', {}).get('errors', [])) + list(record.get('errors', []))
    snapshots = record.get('snapshots', {})
    missing_phases = [phase for phase in PHASES if phase not in snapshots]
    if missing_phases:
        errors.append('missing observations: ' + ', '.join(missing_phases))
    findings = []
    inventory_paths = record.get('inventory', {}).get('paths', [])
    by_path = {p['path']: p for p in inventory_paths}
    reported_groups = set()
    recoverable = {path: state['git_history'] for path, state in snapshots.get('before_setup', {}).items()
                   if state.get('git_history', {}).get('status') == 'recoverable'}
    archive_recoverable = {path: state['archive_member'] for phase in ('before_setup', 'after_setup')
                           for path, state in snapshots.get(phase, {}).items()
                           if state.get('archive_member', {}).get('status') == 'recoverable'}
    for candidate in record.get('inventory', {}).get('paths', []):
        if candidate['excluded'] or candidate.get('unresolved_basename_mention'):
            # Older inventories recorded exact relative filename mentions under
            # this field. Reassessment applies the same current exclusion policy.
            continue
        path = candidate['path']
        states = {phase: snapshots.get(phase, {}).get(path, {}).get('state', 'unknown') for phase in PHASES}
        if path in recoverable or path in archive_recoverable:
            continue
        history = snapshots.get('before_setup', {}).get(path, {}).get('git_history', {})
        if history.get('status') == 'error':
            errors.append(f'{path}: Git history lookup failed: ' + history.get('error', 'unknown error'))
        if 'unknown' in states.values():
            errors.append(f'{path}: incomplete filesystem observations')
        groups = [u['alternatives'] for u in candidate['uses'] if u['role'] == 'required_alternative']
        independent = any(u['role'] not in ('required_alternative', 'optional_check') for u in candidate['uses'])
        if groups and not independent:
            for group in groups:
                key = tuple(group)
                if key in reported_groups:
                    continue
                reported_groups.add(key)
                if any(p in recoverable or p in archive_recoverable or by_path.get(p, {}).get('excluded') in ('instruction', 'instruction_relative') for p in group):
                    continue
                group_states = {p: {phase: snapshots.get(phase, {}).get(p, {}).get('state', 'unknown')
                                    for phase in PHASES} for p in group}
                if all(s == 'missing' for states_ in group_states.values() for s in states_.values()):
                    findings.append(dict(candidate, alternatives=group, group_states=group_states,
                                         reason='all required alternatives are unmentioned and absent at all three boundaries'))
            continue
        if all(state == 'missing' for state in states.values()):
            findings.append(dict(candidate, states=states, reason='unmentioned path absent at all three boundaries'))
        elif states['after_setup'] == 'missing' and states['after_verifier'] == 'present':
            # The grader produced it; no missing-input warning.
            continue
        elif states['after_setup'] == 'missing' and states['after_verifier'] == 'missing':
            findings.append(dict(candidate, states=states, reason='path disappeared during setup and remained missing'))
    return dict(status='incomplete' if errors else 'failed' if findings else 'passed',
                findings=findings, errors=errors, git_recoverable=recoverable, archive_recoverable=archive_recoverable, limitation=LIMITATION,
                coverage=record.get('inventory', {}).get('coverage', []))


PROBE = '''import os,sys,json,errno,subprocess,shutil,time
# Read-only, exact repository-relative paths. Never fetch or change the checkout.
deadline=time.monotonic()+20
cache={}
def git(directory,*args):
    remaining=deadline-time.monotonic()
    if remaining <= 0:
        raise RuntimeError("Git history probe time budget exhausted")
    env=dict(os.environ,GIT_OPTIONAL_LOCKS="0",GIT_TERMINAL_PROMPT="0",GIT_NO_LAZY_FETCH="1")
    return subprocess.run(["git","--no-optional-locks","-C",directory,*args],
                          stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,
                          timeout=min(3,remaining),env=env)

def history(path):
    if not shutil.which("git"):
        return {"status":"unavailable","reason":"git not installed"}
    directory=os.path.dirname(path)
    while not os.path.isdir(directory) and directory != "/":
        directory=os.path.dirname(directory)
    try:
        if directory not in cache:
            cache[directory]=git(directory,"rev-parse","--show-toplevel")
        root_result=cache[directory]
        if root_result.returncode:
            if "not a git repository" in root_result.stderr:
                return {"status":"not_in_repository"}
            # Bare repositories have no work tree: their files are Git metadata,
            # not tracked paths that history could restore.
            bare=git(directory,"rev-parse","--is-bare-repository")
            if bare.returncode == 0 and bare.stdout.strip() == "true":
                return {"status":"not_in_repository","reason":"bare repository metadata"}
            raise RuntimeError(root_result.stderr.strip() or "cannot identify repository")
        root=root_result.stdout.strip()
        relative=os.path.relpath(path,root)
        if relative == ".." or relative.startswith("../"):
            return {"status":"not_in_repository"}
        log=git(root,"--literal-pathspecs","log","--all","-n","129","--format=%H","--",relative)
        if log.returncode:
            raise RuntimeError(log.stderr.strip() or "cannot inspect history")
        commits=log.stdout.splitlines()
        for commit in commits[:128]:
            obj=commit+":"+relative
            kind=git(root,"cat-file","-t",obj)
            if kind.returncode == 0 and kind.stdout.strip() == "blob":
                blob=git(root,"rev-parse","--verify",obj)
                if blob.returncode == 0:
                    return {"status":"recoverable","repository":root,"relative_path":relative,
                            "commit":commit,"blob":blob.stdout.strip()}
        if len(commits)>128:
            raise RuntimeError("Git history search exceeded 128 path commits")
        return {"status":"not_found","repository":root,"relative_path":relative}
    except (OSError,subprocess.TimeoutExpired,RuntimeError) as exc:
        return {"status":"error","error":str(exc)}

result={}
archive_requests=json.loads(sys.argv[2]) if len(sys.argv)>2 else []
archive_deadline=time.monotonic()+25
for path in json.loads(sys.argv[1]):
    try:
        info=os.stat(path)
        result[path]={"state":"present","mode":info.st_mode}
    except OSError as exc:
        result[path]={"state":"missing" if exc.errno in (errno.ENOENT,errno.ENOTDIR) else "unknown","errno":exc.errno}
        if result[path]["state"] == "missing":
            result[path]["git_history"]=history(path)
            if archive_requests:
                result[path]["archive_member"]=archive_member(path,archive_requests,archive_deadline)
print(json.dumps(result))
'''


PROBE = inspect.getsource(archive_member) + "\n" + PROBE


async def observe(wrapper, phase, kwargs):
    """Called only for no-op diagnostics; never modify the grading outcome."""
    record = getattr(wrapper, '_verifier_path_record', None)
    directory = wrapper.verifier.trial_paths.verifier_dir
    artifact = directory / ARTIFACT
    try:
        if record is None:
            _, context, _ = wrapper.verifier._resolve_tests()
            record = dict(step=getattr(wrapper.verifier, 'step_name', None),
                          inventory=inventory(wrapper.verifier.task.paths.task_dir, context,
                                              (instruction_views.get() or {}).get(wrapper.verifier.task.paths.task_dir.name)),
                          snapshots={}, errors=[])
            wrapper._verifier_path_record = record
        paths = [p['path'] for p in record['inventory']['paths'] if not p['excluded']]
        if not paths and not record['inventory'].get('errors'):
            record['snapshots'][phase] = {}
        else:
            from validation.stages.reference_leaks import PROBE_PYTHONS
            from harbor_patches.verifier_mount import command as mount_command
            command = ('ot_path_python=; for ot_candidate in ' + PROBE_PYTHONS + '; do '
                       'if "$ot_candidate" -I -c "import sys; assert sys.version_info >= (3, 7)" >/dev/null 2>&1; '
                       'then ot_path_python="$ot_candidate"; break; fi; done; '
                       'if [ -z "$ot_path_python" ]; then echo "verifier_paths probe needs an existing Python 3.7+ interpreter (including uv-managed locations)" >&2; exit 127; fi; '
                       '"$ot_path_python" -I -c ' + shlex.quote(PROBE) + ' ' + shlex.quote(json.dumps(paths)) + ' ' +
                       shlex.quote(json.dumps(record['inventory'].get('extraction_requests', []))))
            options = {key: value for key, value in kwargs.items() if key in ('user', 'env', 'cwd')}
            result = await wrapper.environment.exec(mount_command(wrapper._runtime_bundle, command), timeout_sec=30, **options)
            if result.return_code == 127 and 'probe needs an existing Python' in (result.stderr or ''):
                from validation.probe_python import staged
                async with staged(wrapper.environment) as (python, digest):
                    probe = (python + ' -c ' + shlex.quote(PROBE) + ' ' + shlex.quote(json.dumps(paths))
                             + ' ' + shlex.quote(json.dumps(record['inventory'].get('extraction_requests', []))))
                    result = await wrapper.environment.exec(mount_command(wrapper._runtime_bundle, probe),
                                                            timeout_sec=30, **options)
                    record['probe_runtime_sha256'] = digest
            if result.return_code:
                raise RuntimeError(f'probe exited {result.return_code}: {result.stderr}')
            snapshot = json.loads(result.stdout.strip().splitlines()[-1])
            if set(snapshot) != set(paths):
                raise ValueError('probe returned an incomplete path inventory')
            record['snapshots'][phase] = snapshot
    except Exception as exc:
        if record is None:
            record = dict(inventory={}, snapshots={}, errors=[])
            wrapper._verifier_path_record = record
        record['errors'].append(f'{phase}: {type(exc).__name__}: {exc}')
    record['result'] = assess(record)
    directory.mkdir(parents=True, exist_ok=True)
    # Each SetupEnvironment is one verifier invocation. Preserve multi-step calls.
    previous = json.loads(artifact.read_text()) if artifact.exists() else {'invocations': []}
    index = getattr(wrapper, '_verifier_path_index', None)
    if index is None:
        index = len(previous['invocations'])
        wrapper._verifier_path_index = index
        previous['invocations'].append(record)
    else:
        previous['invocations'][index] = record
    artifact.write_text(json.dumps(previous, indent=2) + '\n')


def aggregate(rows):
    reports, errors = [], []
    for trial, _ in rows:
        path = Path(trial) / 'verifier' / ARTIFACT
        try:
            data = json.loads(path.read_text())
            if not data['invocations']:
                raise ValueError('empty invocation list')
            reports.extend(dict(assess(r), artifact=str(path), step=r.get('step')) for r in data['invocations'])
        except (OSError, ValueError, KeyError, TypeError) as exc:
            errors.append(f'{path}: {exc}')
    incomplete = errors or not reports or any(r['status'] == 'incomplete' for r in reports)
    return dict(status='incomplete' if incomplete else 'failed' if any(r['findings'] for r in reports) else 'passed',
                trials=reports, errors=errors, limitation=LIMITATION)
