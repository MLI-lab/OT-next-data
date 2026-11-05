"""Bounded local-helper write tracing. No task code is executed."""
import ast
import posixpath

NUMBER = '\x01number\x01'


def function_name(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return function_name(node.value) + '.' + node.attr
    return ''


def helper_writes(text, source, offset=0):
    tree = ast.parse(text)
    functions = {n.name:n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    writes = []
    remaining = [4000]

    def value(node, bindings):
        if isinstance(node, ast.Constant) and isinstance(node.value, (str, int)):
            return str(node.value)
        if isinstance(node, ast.Name):
            return bindings.get(node.id)
        if isinstance(node, ast.JoinedStr):
            parts = []
            for part in node.values:
                if isinstance(part, ast.Constant):
                    parts.append(part.value)
                elif isinstance(part, ast.FormattedValue):
                    v = value(part.value, bindings)
                    if v is None:
                        return None
                    if part.format_spec and NUMBER not in v:
                        spec = value(part.format_spec, bindings)
                        try:
                            v = format(int(v) if v.isdecimal() else v, spec)
                        except (ValueError, TypeError):
                            return None
                    parts.append(v)
                else:
                    return None
            return ''.join(parts)
        if isinstance(node, ast.BinOp):
            left, right = value(node.left, bindings), value(node.right, bindings)
            if left is not None and right is not None:
                if isinstance(node.op, ast.Div):
                    return posixpath.join(left, right)
                if isinstance(node.op, ast.Add):
                    return left + right
        if isinstance(node, ast.Call):
            name = function_name(node.func)
            args = [value(a, bindings) for a in node.args]
            if args and all(a is not None for a in args):
                if name in ('Path', 'pathlib.Path', 'os.path.join'):
                    return posixpath.join(*args)
                if name == 'str' and len(args) == 1:
                    return args[0]
        return None

    def output(path, node, chain, recursive=False):
        if not path or not path.startswith('/'):
            return
        # Only numeric dynamic components are modeled. Arbitrary string
        # interpolation could contain separators/traversal and stays unresolved.
        if NUMBER in path:
            if not recursive:
                return
            before = path.split(NUMBER, 1)[0]
            path = before.rsplit('/', 1)[0]
        path = posixpath.normpath(path)
        if path in ('', '/'):
            return
        if chain:  # Direct writes are already covered by the ordinary scanner.
            writes.append(dict(path=path, source=source, line=node.lineno + offset,
                               role='generated', evidence='local helper write: ' + ' -> '.join(chain),
                               helper_calls=list(chain)))
        if recursive:
            # Recursive directory creation also creates absent parent directories.
            while path not in ('', '/'):
                path = posixpath.dirname(path)
                if path != '/' and chain:
                    writes.append(dict(path=path, source=source, line=node.lineno + offset,
                                       role='generated', evidence='parent created by local helper: ' + ' -> '.join(chain),
                                       helper_calls=list(chain)))

    def call(node, bindings, chain):
        if remaining[0] <= 0:
            return
        remaining[0] -= 1
        name = function_name(node.func)
        method = name.rsplit('.', 1)[-1]
        first = value(node.args[0], bindings) if node.args else None
        receiver = value(node.func.value, bindings) if isinstance(node.func, ast.Attribute) else None
        if name in functions and len(chain) < 6 and name not in chain:
            fn = functions[name]
            parameters = [*fn.args.posonlyargs, *fn.args.args]
            local = dict(globals_)
            # Function-local assignments never inherit unrelated caller locals.
            local_names = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)}
            for key in local_names:
                local.pop(key, None)
            defaults = [None] * (len(parameters) - len(fn.args.defaults)) + list(fn.args.defaults)
            for arg, default in zip(parameters, defaults):
                local[arg.arg] = value(default, globals_) if default else None
            for arg, supplied in zip(parameters, node.args):
                local[arg.arg] = value(supplied, bindings)
            for arg, default in zip(fn.args.kwonlyargs, fn.args.kw_defaults):
                local[arg.arg] = value(default, globals_) if default else None
            allowed = {a.arg for a in parameters + fn.args.kwonlyargs}
            for kw in node.keywords:
                if kw.arg in allowed:
                    local[kw.arg] = value(kw.value, bindings)
            statements(fn.body, local, chain + [name])
        elif name in ('os.makedirs', 'os.mkdir'):
            output(first, node, chain, recursive=name == 'os.makedirs')
        elif method == 'mkdir' and receiver:
            parents = any(k.arg == 'parents' and isinstance(k.value, ast.Constant) and k.value.value is True for k in node.keywords)
            output(receiver, node, chain, recursive=parents)
        elif method in ('write_text', 'write_bytes', 'touch'):
            output(receiver, node, chain)
        elif method == 'open':
            is_path = receiver is not None and receiver.startswith('/')
            target = receiver if is_path else first
            mode_i = 0 if is_path else 1
            mode = value(node.args[mode_i], bindings) if len(node.args)>mode_i else 'r'
            for kw in node.keywords:
                if kw.arg == 'mode': mode = value(kw.value, bindings)
                if kw.arg == 'file': target = value(kw.value, bindings)
            if mode and any(c in mode for c in 'wax'):
                output(target, node, chain)

    def expression(node, bindings, chain):
        if node is None:
            return
        for child in ast.walk(node):
            if isinstance(child, ast.Call):
                call(child, bindings, chain)

    def statements(nodes, bindings, chain):
        for node in nodes:
            if remaining[0] <= 0:
                return
            remaining[0] -= 1
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                expression(node.value, bindings, chain)
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if isinstance(target, ast.Name):
                        bindings[target.id] = value(node.value, bindings)
            elif isinstance(node, (ast.For, ast.AsyncFor)):
                local = dict(bindings)
                if isinstance(node.target, ast.Name):
                    local[node.target.id] = NUMBER if isinstance(node.iter, ast.Call) and function_name(node.iter.func) == 'range' else None
                statements(node.body, local, chain)
                statements(node.orelse, dict(bindings), chain)
            elif isinstance(node, ast.If):
                expression(node.test, bindings, chain)
                left, right = dict(bindings), dict(bindings)
                statements(node.body, left, chain); statements(node.orelse, right, chain)
                for key in left.keys() | right.keys():
                    bindings[key] = left.get(key) if left.get(key) == right.get(key) else None
            elif isinstance(node, (ast.With, ast.AsyncWith)):
                for item in node.items:
                    expression(item.context_expr, bindings, chain)
                statements(node.body, bindings, chain)
            elif isinstance(node, ast.Try):
                statements(node.body, dict(bindings), chain)
                for handler in node.handlers: statements(handler.body, dict(bindings), chain)
                statements(node.orelse, dict(bindings), chain)
                statements(node.finalbody, dict(bindings), chain)
            else:
                expression(node, bindings, chain)

    globals_ = {}
    statements(tree.body, globals_, [])
    for name, fn in functions.items():
        if not name.startswith('test_'):
            continue
        local = dict(globals_)
        for arg in [*fn.args.posonlyargs, *fn.args.args, *fn.args.kwonlyargs]:
            local[arg.arg] = None
        for decorator in fn.decorator_list:
            if (isinstance(decorator, ast.Call) and function_name(decorator.func) == 'pytest.mark.parametrize'
                    and len(decorator.args) >= 2 and isinstance(decorator.args[0], ast.Constant)
                    and isinstance(decorator.args[0].value, str)
                    and isinstance(decorator.args[1], ast.Call) and function_name(decorator.args[1].func) == 'range'):
                local[decorator.args[0].value] = NUMBER
        statements(fn.body, local, [])
    return writes
