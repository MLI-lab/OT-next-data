#!/usr/bin/env python3
"""Static review signals for text comparisons connected to assertions/reward.

No verifier code is executed. This is a bounded Python AST dependency analysis,
not proof of control flow or exploitability. Use --task TASK_DIR for automatic
path/role inference without hand-supplied answers or labels. For diagnosis,
explicitly identify submitted source paths via --submitted-source and reference
paths via --reference. CLI bindings resolve argparse attributes:
--bindings bindings.json, e.g. {"a.prediction":"/app/solution.txt",
"a.reference":"/tests/solution.txt","a.out":"/logs/verifier"}.

Supports local helper calls, normalization, comprehensions, tuples/dictionaries,
assertions and reward-file writes. Unknown calls and aliases are limitations;
no findings means not_evidenced, never pass. Branch merging overapproximates
possible dependencies. Flags are static filtering evidence, not exploit proofs.
"""
from __future__ import annotations
import argparse
import ast
from dataclasses import dataclass, field
import json
from pathlib import Path
import re
import shlex


@dataclass
class Value:
    tags: set = field(default_factory=set)
    fields: dict = field(default_factory=dict)


def merge(*values):
    out = Value()
    for v in values:
        out.tags |= v.tags
        for k, item in v.fields.items():
            out.fields[k] = merge(out.fields[k], item) if k in out.fields else item
    return out


def name(node):
    if isinstance(node, ast.Name): return node.id
    if isinstance(node, ast.Attribute): return name(node.value) + '.' + node.attr
    return ''


# Tokens any plausible source file contains; a containment check on them
# cannot distinguish a real implementation from an empty stub.
EXEC_CALLS = {'subprocess.run','subprocess.check_output','subprocess.check_call','subprocess.Popen','subprocess.getoutput',
              'os.system','os.popen','exec','eval','runpy.run_path','importlib.import_module','urllib.request.urlopen',
              'urlopen','socket.create_connection','pexpect.spawn','ctypes.CDLL','ctypes.cdll.LoadLibrary'}
DATA_READERS = {'read_csv','read_json','read_parquet','read_table','read_excel','read_pickle','load','loads','loadtxt',
                'genfromtxt','imread','open_dataset','read_sql','fromfile','safe_load','readlines','readline'}
STATE_CALLS = {'os.stat','os.lstat','os.access','os.readlink','os.path.realpath','os.path.getsize','pwd.getpwuid',
               'grp.getgrgid','os.listdir','os.scandir','glob.glob','os.walk','sqlite3.connect','zipfile.ZipFile',
               'tarfile.open','ET.parse','ElementTree.parse','xml.etree.ElementTree.parse','configparser.ConfigParser'}
CODE_SUFFIXES = {'.py','.sh','.bash','.js','.mjs','.ts','.tsx','.jsx','.c','.cc','.cpp','.h','.hpp','.java','.go','.rs',
                 '.rb','.pl','.php','.cs','.kt','.scala','.lua','.r','.jl','.swift','.m','.zsh','.ps1'}
EXIT_ATTRS = {'returncode','status_code','ok','pid','exitstatus'}
COMMON_MODULES = {'numpy','np','pandas','pd','torch','scipy','sklearn','requests','yaml','pytest','psutil','h5py','PIL',
                  'cv2','matplotlib','networkx','jsonschema','httpx','aiohttp','redis','docker','boto3','lxml','bs4',
                  'tensorflow','jax','sqlalchemy','flask','fastapi','django','hypothesis','toml','tomli','pyarrow','numba'}


INFRA_COMMANDS = {'git','pip','pip3','chmod','chown','cp','mv','rm','mkdir','ls','which','apt-get','apt','tar','find',
                  'cat','grep','sha256sum','md5sum','touch','ln','id','whoami','stat','du','df','ps','pkill','kill'}


def infrastructure_command(call):
    """subprocess/os.system call whose program is a setup/inspection tool."""
    if not call.args: return False
    first = call.args[0]
    if isinstance(first, (ast.List, ast.Tuple)) and first.elts and isinstance(first.elts[0], ast.Constant):
        head = str(first.elts[0].value)
    elif isinstance(first, ast.Constant) and isinstance(first.value, str):
        head = first.value.split()[0] if first.value.split() else ''
    else:
        return False
    if head in {'python','python3','-m'} and isinstance(first, (ast.List, ast.Tuple)) and len(first.elts) > 2:
        return False
    return Path(head).name in INFRA_COMMANDS or (Path(head).name.startswith('python') and isinstance(first,(ast.List,ast.Tuple))
        and len(first.elts) > 2 and isinstance(first.elts[1],ast.Constant) and first.elts[1].value == '-m'
        and isinstance(first.elts[2],ast.Constant) and first.elts[2].value == 'pip')


UNITTEST_COMPARE = {'assertEqual': ast.Eq, 'assertEquals': ast.Eq, 'assertAlmostEqual': ast.Eq,
                    'assertListEqual': ast.Eq, 'assertDictEqual': ast.Eq, 'assertTupleEqual': ast.Eq,
                    'assertSetEqual': ast.Eq, 'assertSequenceEqual': ast.Eq, 'assertCountEqual': ast.Eq,
                    'assertMultiLineEqual': ast.Eq, 'assertNotEqual': ast.NotEq, 'assertNotAlmostEqual': ast.NotEq,
                    'assertIn': ast.In, 'assertNotIn': ast.NotIn, 'assertIs': ast.Is, 'assertIsNot': ast.IsNot,
                    'assertGreater': ast.Gt, 'assertGreaterEqual': ast.GtE, 'assertLess': ast.Lt,
                    'assertLessEqual': ast.LtE}


def unittest_assert(call):
    """`self.assertX(...)` as the equivalent assert expression, or None for any other call."""
    if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
            and isinstance(call.func.value, ast.Name) and call.func.value.id == 'self'):
        return None
    method, args = call.func.attr, call.args
    if method in UNITTEST_COMPARE and len(args) >= 2:
        test = ast.Compare(left=args[0], ops=[UNITTEST_COMPARE[method]()], comparators=[args[1]])
    elif method in {'assertTrue', 'assert_'} and args:
        test = args[0]
    elif method == 'assertFalse' and args:
        test = ast.UnaryOp(op=ast.Not(), operand=args[0])
    elif method in {'assertIsNone', 'assertIsNotNone'} and args:
        test = ast.Compare(left=args[0], ops=[ast.Is() if method == 'assertIsNone' else ast.IsNot()],
                           comparators=[ast.Constant(None)])
    elif method in {'assertRegex', 'assertNotRegex'} and len(args) >= 2:
        test = ast.Call(func=ast.Attribute(value=ast.Name('re', ast.Load()), attr='search', ctx=ast.Load()),
                        args=[args[1], args[0]], keywords=[])
    elif method == 'assertIsInstance' and len(args) >= 2:
        test = ast.Call(func=ast.Name('isinstance', ast.Load()), args=args[:2], keywords=[])
    else:
        return None
    return ast.fix_missing_locations(ast.copy_location(test, call))


def agent_module(module):
    import sys
    root = module.split('.')[0]
    return bool(root) and root not in sys.stdlib_module_names and root not in COMMON_MODULES


GENERIC_TOKENS = {'#!', '#!/', '#!/bin/bash', '#!/bin/sh', '#!/usr/bin/env', '#!/usr/bin/env python3',
                  '#!/usr/bin/env bash', 'import', 'from', 'def', 'class', 'return', 'function', '#include',
                  'include', 'int', 'void', 'main', 'func', 'package', 'fn', 'var', 'let', 'const', 'public'}


# Uppercase KEY=value / KEY = "value" lines (env/config convention); lowercase
# key=value is usually a function argument in code.
CONFIG_LINE = re.compile(r'''\s*[A-Z_][A-Z0-9_]*(=[\w./:@"'-]+|\s*[=:]\s*("[^"]*"|'[^']*'|[\d.:]+))\s*;?\s*''')
CONFIG_NAMES = {'setup.py'}


def config_file(path):
    return Path(path).name.lower() in CONFIG_NAMES


DATA_SUFFIXES = {'.txt', '.json', '.csv', '.tsv', '.yaml', '.yml', '.log', '.out', '.jsonl'}


def generic_literal(value):
    return value.strip() in GENERIC_TOKENS


class Analyzer:
    def __init__(self, text, submitted, references, bindings=None, selected_tests=None, originals=None,
                 instruction='', test_identifiers=frozenset(), artifact_paths=frozenset()):
        self.tree = ast.parse(text)
        self.artifact_paths = set(artifact_paths)
        # Names imported from non-standard, non-common modules: likely the
        # agent's own code, so calling them exercises submitted behaviour.
        self.agent_imports = set()
        for n in ast.walk(self.tree):
            if isinstance(n, ast.Import):
                self.agent_imports |= {a.asname or a.name.split('.')[0] for a in n.names if agent_module(a.name)}
            elif isinstance(n, ast.ImportFrom) and (n.level > 0 or agent_module(n.module or '')):
                self.agent_imports |= {a.asname or a.name for a in n.names}
        if '*' in self.agent_imports:
            # `from solution import *`: a called name that this file neither defines, assigns,
            # imports elsewhere nor gets from builtins can only come from the agent module.
            import builtins
            local = {n.name for n in ast.walk(self.tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
            local |= {n.id for n in ast.walk(self.tree) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)}
            local |= {n.arg for n in ast.walk(self.tree) if isinstance(n, ast.arg)}
            local |= {a.asname or a.name.split('.')[0] for n in ast.walk(self.tree)
                      if isinstance(n, (ast.Import, ast.ImportFrom)) for a in n.names if a.name != '*'}
            self.agent_imports |= {n.func.id for n in ast.walk(self.tree) if isinstance(n, ast.Call)
                                   and isinstance(n.func, ast.Name) and n.func.id not in local
                                   and not hasattr(builtins, n.func.id)}
            self.agent_imports.discard('*')
        self.functions = {n.name:n for n in self.tree.body if isinstance(n, ast.FunctionDef)}
        self.submitted, self.references = set(submitted), set(references)
        self.bindings = bindings or {}
        self.comparisons, self.sinks, self.execution, self.limitations = {}, [], [], set()
        self.stack = []
        # Set once the current test ran a program; files read afterwards are
        # program output rather than hand-written results.
        self.ran = False
        self.selected_tests = selected_tests
        self.created = set()
        self.suppressed = []
        # Task-provided copies of submitted files, keyed by container path.
        self.originals = originals or {}
        # Required interface names: given in the instruction and used by the tests.
        self.instruction = instruction
        self.test_identifiers = set(test_identifiers)
        self.instruction_api = set()
        for m in re.finditer(r'\b(?:def|class)\s+(\w+)\s*(?:\(([^)]*)\))?', instruction):
            self.instruction_api.add(m.group(1))
            for param in (m.group(2) or '').split(','):
                param = re.split(r'[:=]', param)[0].strip().lstrip('*')
                if re.fullmatch(r'[A-Za-z_]\w*', param) and param not in {'self','cls'}: self.instruction_api.add(param)

    def literal(self, value):
        return Value({('literal', str(value))})

    def bind(self, node, value, env):
        if isinstance(node, ast.Name): env[node.id] = value
        elif isinstance(node, (ast.Tuple, ast.List)):
            for i, child in enumerate(node.elts): self.bind(child, value.fields.get(i, Value(value.tags)), env)
        elif isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) and isinstance(node.slice, ast.Constant):
            old = env.get(node.value.id, Value())
            env[node.value.id] = Value(old.tags, dict(old.fields, **{}))
            env[node.value.id].fields[node.slice.value] = value

    def paths(self, value):
        return {v for k,v in value.tags if k in {'literal','path'} and v.startswith('/')}

    def compare(self, node, left, right, op=None):
        left_files = {v for k,v in left.tags if k == 'file'}
        right_files = {v for k,v in right.tags if k == 'file'}
        for a,b,other in [(left_files,right_files,right),(right_files,left_files,left)]:
            sources = a & self.submitted
            targets = b & self.references
            # Delimiter/whitespace checks often implement normalization, not
            # answer matching. Retain content-bearing literal comparisons.
            literals = [v for k,v in other.tags if k == 'literal' and re.search(r'\w',v)]
            # A needle read from another (non-reference) file, e.g. the agent's
            # answer looked up in a provided program, is not fixed text.
            if sources and not targets and any(k == 'file' for k,v in other.tags):
                self.suppressed.append(dict(line=node.lineno, reason='file_derived_needle',
                                            expression=ast.unparse(node)[:500]))
                continue
            # Narrow file-format exceptions, not arbitrary source keywords.
            source_value = left if a is left_files else right
            format_literals = {str(b'\r\n'), str(b'\xef\xbb\xbf'), '\r\n'}
            header_literals = {'bash', 'python', 'python3', '#!/bin/bash',
                               '#!/usr/bin/env bash', '#!/usr/bin/env python3',
                               '#!/usr/bin/python3', '#!/usr/bin/zsh'}
            is_format = bool(literals) and all(v in format_literals for v in literals)
            is_header = ('first_line', '') in source_value.tags and bool(literals) and all(v in header_literals for v in literals)
            if sources and not targets and (is_format or is_header):
                self.suppressed.append(dict(line=node.lineno, reason='file_format',
                                            expression=ast.unparse(node)[:500]))
                continue
            # `"x" in content` already true for the task-provided file only
            # guards against deleting existing code; it cannot replace work.
            if (sources and not targets and literals and isinstance(op, ast.In) and a is right_files
                    and ('lines','') not in source_value.tags and all(src in self.originals for src in sources)):
                folded = ('lower','') in source_value.tags
                texts = [self.originals[src].lower() if folded else self.originals[src] for src in sources]
                if all(v in t for v in literals for t in texts):
                    self.suppressed.append(dict(line=node.lineno, reason='present_in_original',
                                                expression=ast.unparse(node)[:500]))
                    continue
            if sources and not targets and literals and isinstance(op, ast.In) and a is right_files:
                whole_file = isinstance(node.comparators[-1], (ast.Name, ast.Call)) and ('lines','') not in source_value.tags
                if whole_file and all(generic_literal(v) for v in literals):
                    self.suppressed.append(dict(line=node.lineno, reason='generic_token',
                                                expression=ast.unparse(node)[:500]))
                    continue
                if all(self.required_name(v) for v in literals):
                    self.suppressed.append(dict(line=node.lineno, reason='required_interface_name',
                                                expression=ast.unparse(node)[:500]))
                    continue
            # Configuration values: `KEY=value` lines, or any check on a
            # build/config file, test the requested setting, not code behaviour.
            if sources and not targets and literals and (
                    all(CONFIG_LINE.fullmatch(v) for v in literals) or all(config_file(src) for src in sources)):
                self.suppressed.append(dict(line=node.lineno, reason='config_value',
                                            expression=ast.unparse(node)[:500]))
                continue
            if sources and (targets or literals):
                key = f'{node.lineno}:{node.col_offset}'
                self.comparisons[key] = dict(line=node.lineno, submitted_sources=sorted(sources),
                    reference_files=sorted(targets), literal_target=not bool(targets),
                    expression=ast.unparse(node)[:500])
                return Value({('comparison',key)})
        return Value()

    def expr(self, node, env):
        if node is None: return Value()
        if isinstance(node, ast.Constant): return self.literal(node.value)
        if name(node) in self.bindings: return self.literal(self.bindings[name(node)])
        if isinstance(node, ast.Name): return env.get(node.id, Value())
        if isinstance(node, ast.Attribute):
            base = self.expr(node.value,env)
            if ('exec','') in base.tags and node.attr in EXIT_ATTRS:
                return Value(base.tags - {('exec','')} | {('exit','')})
            if node.attr in base.fields:  # self.ATTR of a pytest test class
                return base.fields[node.attr]
            return base
        if isinstance(node, ast.Subscript):
            base = self.expr(node.value,env)
            if isinstance(node.slice, ast.Constant) and node.slice.value == 0 and ('lines', '') in base.tags:
                return Value(base.tags | {('first_line', '')})
            if isinstance(node.slice, ast.Constant) and isinstance(node.slice.value,str):
                base = Value(base.tags | {('field',node.slice.value)}, base.fields)
            if isinstance(node.slice, ast.Constant) and node.slice.value in base.fields:
                return base.fields[node.slice.value]
            return Value(base.tags)
        if isinstance(node, ast.Dict):
            fields = {k.value:self.expr(v,env) for k,v in zip(node.keys,node.values) if isinstance(k,ast.Constant)}
            return Value(merge(*fields.values()).tags, fields)
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            fields = {i:self.expr(v,env) for i,v in enumerate(node.elts)}
            return Value(merge(*fields.values()).tags, fields)
        if isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp)):
            local = dict(env)
            for gen in node.generators:
                self.bind(gen.target,self.iterate(self.expr(gen.iter,local)),local)
                for cond in gen.ifs: self.expr(cond,local)  # binds walrus targets
            return self.expr(node.elt,local)
        if isinstance(node, ast.Compare):
            values = [self.expr(n,env) for n in [node.left,*node.comparators]]
            matched = [self.compare(node,a,b,op) for a,b,op in zip(values,values[1:],node.ops)]
            result = merge(*values,*matched)
            # An exit code compared with a non-zero value means the program must
            # tell inputs apart (e.g. evil corpus exits 1): behavioural.
            if any(('exit','') in v.tags for v in values) and any(
                    isinstance(n,ast.Constant) and isinstance(n.value,int) and not isinstance(n.value,bool) and n.value!=0
                    for n in [node.left,*node.comparators]):
                result.tags.add(('exec',''))
            return result
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            left,right = self.expr(node.left,env),self.expr(node.right,env)
            joined = {('path',a.rstrip('/')+'/'+b) for a in self.paths(left) for k,b in right.tags if k=='literal'}
            return Value(joined or merge(left,right).tags)
        if isinstance(node, ast.Call):
            fn = name(node.func); method = fn.split('.')[-1]
            args = [self.expr(a,env) for a in node.args]
            kwargs = {k.arg:self.expr(k.value,env) for k in node.keywords if k.arg}
            receiver = self.expr(node.func.value,env) if isinstance(node.func,ast.Attribute) else Value()
            if fn in {'importlib.util.spec_from_file_location', 'spec_from_file_location'} and len(args)>1:
                return Value({('module_spec',p) for p in self.paths(args[1])})
            if fn in {'importlib.util.module_from_spec', 'module_from_spec'} and args:
                return Value({('loaded_module',p) for k,p in args[0].tags if k=='module_spec'})
            if method == 'exec_module':
                return Value()  # loading alone is not a behavioral assertion
            if any(k=='loaded_module' for k,p in receiver.tags):
                return Value({('exec','')} | merge(*args,*kwargs.values()).tags)
            if fn == 'torch.load':
                return Value({('weights',p) for a in args for p in self.paths(a)})
            if method == 'load_state_dict' and isinstance(node.func,ast.Attribute):
                obj=name(node.func.value)
                if obj in env and any(k=='weights' for a in args for k,p in a.tags):
                    env[obj]=merge(env[obj],*args)
                return Value()  # checking load success alone is not inference
            if fn in env and any(k=='weights' for k,p in env[fn].tags):
                return Value({('exec','')})
            if fn in {n.name for n in self.tree.body if isinstance(n,ast.ClassDef)}:
                return Value({('local_object',fn)})
            if method in {'read_csv','read_json','read_parquet','loadtxt','genfromtxt','read_table'}:
                return Value({('state',p) for a in args for p in self.paths(a)} |
                             ({('ran_output','')} if self.ran else set()))
            if fn == 'len':
                return Value({t for t in merge(*args).tags if t[0] in {'exec','ran_output','state','field'}})
            if fn in {'abs','float','sum','min','max','round','len','any','all','tuple','dict'} or method in {
                    'mean','sum','std','min','max','item','tolist','to_numpy','allclose','isclose',
                    'sqrt','exp','log','abs','square','view','reshape','flatten','cross_entropy'}:
                return merge(receiver,*args,*kwargs.values())
            if fn in self.functions and fn not in self.stack and len(self.stack)<8:
                f = self.functions[fn]; local = dict(env)
                for i,p in enumerate(f.args.args): local[p.arg] = args[i] if i<len(args) else kwargs.get(p.arg, Value())
                self.stack.append(fn)
                returned = self.block(f.body,local)
                self.stack.pop()
                return returned
            if fn in {'re.search','re.match','re.fullmatch','re.findall','re.finditer'}:
                v=merge(*args)
                return Value(v.tags | {('report_parse','')},v.fields)
            if method == 'get' and args and any(k=='state' for k,p in receiver.tags):
                return Value(merge(receiver,*args).tags | {('field',p) for k,p in args[0].tags if k=='literal'})
            if fn in {'json.load','json.loads','yaml.safe_load','tomllib.loads','csv.reader'}:
                # Parsed data keeps the provenance of the file it was read from.
                paths={p for a in args for p in self.paths(a)}
                return Value({('file',p) for p in paths - self.created} | {t for a in args for t in a.tags if t[0] in ('file','exec','ran_output','state')}
                             | {('state',p) for p in paths} | ({('ran_output','')} if self.ran else set()))
            if method == 'group' and ('report_parse','') in receiver.tags:
                return receiver
            if method in {'stat','lstat','owner','group','readlink','resolve'} or fn in STATE_CALLS:
                paths = self.paths(receiver) | {p for a in args for p in self.paths(a)}
                return Value({('state',p) for p in paths} | {t for t in merge(receiver,*args).tags if t[0]=='state'})
            if fn in {'Path','pathlib.Path','open'}:
                result = merge(*args)
                if fn == 'open': result.tags.add(('handle',''))
                if fn == 'open':
                    mode = args[1] if len(args)>1 else kwargs.get('mode',self.literal('r'))
                    if mode.tags in [{('literal','w')},{('literal','wb')}]:
                        result.tags.add(('overwrite_handle',''))
                return result
            if fn in {'zip','enumerate'}:
                # Keep positions so `for a, b in zip(x, y)` binds a to x and b to y.
                parts = [Value(), *args] if fn == 'enumerate' else args
                return Value(merge(*args).tags, {i: part for i, part in enumerate(parts)})
            if method in {'read','read_text','read_bytes','readlines','readline'}:
                paths = self.paths(receiver)
                for p in paths & self.created:
                    self.suppressed.append(dict(line=node.lineno, reason='verifier_created_file', path=p))
                return Value({('file',p) for p in paths - self.created} | ({('exec','')} & receiver.tags)
                             | {('state',p) for p in paths} | {t for t in receiver.tags if t[0]=='state'}
                             | ({('ran_output','')} if self.ran else set()))
            if method in {'write','write_text','write_bytes'}:
                paths = self.paths(receiver)
                value = merge(*args)
                # Only a definite singleton full overwrite of non-source-derived
                # content establishes a verifier-created file. Appends do not.
                if len(paths) == 1 and (method in {'write_text','write_bytes'} or ('overwrite_handle','') in receiver.tags) and value.tags and all(k in {'literal','path'} for k,v in value.tags):
                    self.created |= paths
                if any(Path(p).name in {'reward.txt','reward.json'} for p in paths):
                    # A reward computed from a file's content checks that file's final state.
                    state=sorted({p for k,p in value.tags if k=='state' and not p.startswith('/tests') and p not in self.submitted
                                  and Path(p).suffix.lower() not in CODE_SUFFIXES})
                    self.sinks.append(dict(line=node.lineno, kind='reward_write', comparisons=sorted(v for k,v in value.tags if k=='comparison'), paths=sorted(paths), final_state=state))
                return Value()
            if fn in EXEC_CALLS or method in {'communicate','recv','urlopen','getresponse'} or fn.split('.')[0] in {'requests','httpx','aiohttp'}:
                self.execution.append(dict(line=node.lineno,expression=ast.unparse(node)[:300]))
                if infrastructure_command(node):
                    return Value()  # git/pip/cp etc. do not run the agent's program
                self.ran = True
                return Value({('exec','')})  # Executed output is not raw submitted source.
            if fn and fn.split('.')[0] in self.agent_imports:
                return Value({('exec','')} | merge(*args,*kwargs.values()).tags)
            if method in {'strip','lstrip','rstrip','split','splitlines','decode','encode','replace','join','lower','upper','removeprefix','removesuffix','get','json','text','content','decode'} or fn in {'str','int','bool','list','tuple','sorted','set','enumerate','zip','re.sub','json.dumps'}:
                result = merge(receiver,*args,*kwargs.values())
                if method == 'split' and node.args and isinstance(node.args[0],ast.Constant) and any(c in str(node.args[0].value) for c in ':='):
                    result.tags.add(('report_parse',''))
                if method == 'lower':
                    result.tags.add(('lower',''))
                if method == 'splitlines' or (method == 'split' and node.args and isinstance(node.args[0],ast.Constant) and node.args[0].value == '\n'):
                    result.tags.add(('lines',''))
                return result
            if method in {'append','extend'} and isinstance(node.func,ast.Attribute) and isinstance(node.func.value,ast.Name):
                env[node.func.value.id] = merge(receiver,*args)
                return Value()
            # Infrastructure calls do not imply a data dependency.
            if method not in {'parse_args','add_argument','mkdir','ArgumentParser','is_file','exists','items','keys','values'}:
                self.limitations.add(f'Unmodeled call: {fn or ast.unparse(node.func)} at line {node.lineno}')
            callee = self.expr(node.func,env) if isinstance(node.func,ast.Name) else Value()
            ran = {t for t in merge(receiver,callee,*args,*kwargs.values()).tags if t[0] in ('exec','ran_output','state','field','report_parse')}
            if self.ran and method in DATA_READERS:
                ran.add(('ran_output',''))
            return Value(ran)
        if isinstance(node, ast.NamedExpr):
            value = self.expr(node.value,env); self.bind(node.target,value,env)
            return value
        if isinstance(node, ast.IfExp):
            # Control dependence: `"1" if ok else "0"` depends on how ok was computed.
            test = {t for t in self.expr(node.test,env).tags if t[0] in ('exec','ran_output','state')}
            return merge(self.expr(node.body,env),self.expr(node.orelse,env),Value(test))
        return merge(*(self.expr(child,env) for child in ast.iter_child_nodes(node) if isinstance(child,ast.expr)))

    def block(self, statements, env):
        returns = []
        for node in statements:
            if isinstance(node, ast.Assign):
                v=self.expr(node.value,env)
                for target in node.targets:self.bind(target,v,env)
            elif isinstance(node, ast.AnnAssign):self.bind(node.target,self.expr(node.value,env),env)
            elif isinstance(node, ast.AugAssign):self.bind(node.target,merge(self.expr(node.target,env),self.expr(node.value,env)),env)
            elif isinstance(node, ast.Return):
                returns.append(self.expr(node.value,env))
                break
            elif isinstance(node, ast.Expr) and (test := unittest_assert(node.value)) is not None:
                # self.assertEqual(a, b) etc.: analysed exactly like `assert a == b`.
                self.block([ast.copy_location(ast.Assert(test=test, msg=None), node)], env)
            elif isinstance(node, ast.Expr):self.expr(node.value,env)
            elif isinstance(node, ast.Assert):
                v=self.expr(node.test,env)
                keys=sorted(v for k,v in v.tags if k=='comparison')
                if keys and self.always_true(node.test):
                    self.suppressed.append(dict(line=node.lineno, reason='always_true_disjunction',
                                                expression=ast.unparse(node.test)[:500]))
                    keys=[]
                # If the assert reads an agent answer file, comparisons that only
                # reach it indirectly (variable or enclosing if) derive the
                # expected answer from a provided file; filtered in run().
                answer_files={p for k,p in v.tags if k=='file' and Path(p).suffix.lower() in DATA_SUFFIXES} - self.submitted - self.references
                direct={f'{n.lineno}:{n.col_offset}' for n in ast.walk(node.test) if isinstance(n,ast.Compare)}
                self.sinks.append(dict(line=node.lineno,kind='assert',comparisons=keys,
                                       answer_files=sorted(answer_files),direct=sorted(direct),
                                       behavioural=bool({('exec',''),('ran_output','')} & v.tags) or (self.ran and any(
                                           k=='file' and p not in self.submitted for k,p in v.tags)),
                                       final_state=self.final_state(node.test, v),
                                       expected_output=self.expected_output(node.test, env),
                                       independent_output=self.independent_output(node.test,v,env)))
            elif isinstance(node, ast.If):
                condition=self.expr(node.test,env)
                a,b=dict(env),dict(env)
                # Control dependence matters: equality may select reward 1/0.
                before=len(self.sinks)
                created_before = set(self.created)
                ra=self.block(node.body,a); created_a=set(self.created)
                self.created = set(created_before)
                rb=self.block(node.orelse,b)
                self.created &= created_a
                deps={v for k,v in condition.tags if k=='comparison'}
                for sink in self.sinks[before:]:sink['comparisons']=sorted(set(sink['comparisons'])|deps)
                ran_tags={t for t in condition.tags if t[0] in ('exec','ran_output')}
                for key in a.keys()|b.keys():env[key]=merge(a.get(key,Value()),b.get(key,Value()),Value({('comparison',v) for v in deps}|ran_tags))
                # `if <program output check>: pytest.fail(...)` is a behavioural test too.
                if ran_tags and any(isinstance(n,ast.Raise) or (isinstance(n,ast.Call) and name(n.func) in {'pytest.fail','sys.exit','exit','os._exit'})
                                    for part in node.body+node.orelse for n in ast.walk(part)):
                    self.sinks.append(dict(line=node.lineno,kind='fail_branch',comparisons=[],behavioural=True))
                elif any(isinstance(n,ast.Raise) or (isinstance(n,ast.Call) and name(n.func) in {'pytest.fail','sys.exit','exit'})
                         for part in node.body+node.orelse for n in ast.walk(part)):
                    state=self.final_state(node.test, condition)
                    expected=self.expected_output(node.test, env)
                    if state or expected: self.sinks.append(dict(line=node.lineno,kind='fail_branch',comparisons=[],
                                                                 final_state=state,expected_output=expected))
                # Values returned inside a branch depend on the condition (exec/state provenance).
                returns.append(merge(ra,rb,Value({t for t in condition.tags if t[0] in ('exec','ran_output','state')})) if (ra.tags or rb.tags or any(isinstance(n,ast.Return) for part in node.body+node.orelse for n in ast.walk(part))) else merge(ra,rb))
            elif isinstance(node,(ast.For,ast.While)):
                created_before = set(self.created)
                if isinstance(node,ast.For):self.bind(node.target,self.iterate(self.expr(node.iter,env)),env)
                returns.append(self.block(node.body,env));returns.append(self.block(node.orelse,env))
                self.created &= created_before
            elif isinstance(node,ast.With):
                for item in node.items:
                    v=self.expr(item.context_expr,env)
                    if item.optional_vars:self.bind(item.optional_vars,v,env)
                returns.append(self.block(node.body,env))
            elif isinstance(node,ast.Try):
                created_before = set(self.created)
                for body in [node.body,node.orelse,*[h.body for h in node.handlers],node.finalbody]:
                    self.created=set(created_before)
                    returns.append(self.block(body,env))
                self.created=created_before
        return merge(*returns)

    def required_name(self, value):
        """`def/class NAME` the tests use, or a name from a signature the instruction gives."""
        m = re.match(r'^\s*(?:async\s+def|def|class|fn|func|function)\s+(\w+)\s*\(?\s*$', value)
        if m: return m.group(1) in self.test_identifiers or m.group(1) in self.instruction_api
        return bool(re.fullmatch(r'[A-Za-z_]\w*', value)) and value in self.instruction_api

    def final_state(self, test, value):
        """Non-code files whose contents or attributes this check compares (not mere existence)."""
        compares = any(isinstance(n, ast.Compare) or (isinstance(n, ast.Call) and name(n.func).split('.')[-1] in
                       {'startswith','endswith','match','search','fullmatch','issubset','issuperset','isclose','allclose'})
                       for n in ast.walk(test))
        if not compares: return []
        return sorted({p for k,p in value.tags if k=='state' and not p.startswith('/tests') and p not in self.submitted
                       and Path(p).suffix.lower() not in CODE_SUFFIXES})

    def independent_output(self, test, value, env):
        """Conservative output oracles, distinct from broad file/state evidence."""
        files=self.agent_state(value)
        if not files: return []
        fields={p.lower() for k,p in value.tags if k=='field'}
        claims={'accuracy','test_accuracy','best_eval_accuracy','speedup','status','passed',
                'all_tests_passed','completed','checkpoints_used','output_records',
                'validation_errors','memory_mb','duration','elapsed','requests_per_second'}
        if fields & claims: return []
        # Explicit requested text/config/recovered artifacts are themselves the
        # product. No inference that the producing program was executed.
        artifacts=files & self.artifact_paths
        if artifacts and any(isinstance(n,ast.Compare) for n in ast.walk(test)):
            return sorted(artifacts)
        # Reported fields and parsed key/value summaries need an independent
        # oracle; constants alone cannot tell result data from progress claims.
        report_like = fields or any(k=='report_parse' for k,p in value.tags) or any(
            re.search(r'(summary|report|metrics|timing|solution|stats|info)',Path(p).name,re.I) for p in files)
        # Exact output collections are useful only outside that ambiguity.
        exact=self.expected_output(test,env)
        if exact and not report_like: return exact
        # Scalar result checks and tolerance checks. Reject schema/count/shape
        # tests and generic bounds; those do not establish output correctness.
        if fields & {'shape','count','num_rows','length','size'}: return []
        if any(isinstance(n,ast.Call) and name(n.func).split('.')[-1] in
               {'len','isinstance','issubset','is_file','exists'} for n in ast.walk(test)): return []
        # Keep independence visible: two distinct files is not enough by itself.
        # Only explicit reference/ground-truth paths qualify without a dataflow
        # proof of which other files are supplied inputs.
        if any(k=='state' and (p.startswith('/tests/') or
               re.search(r'(ground.?truth|expected|reference|golden)',Path(p).name,re.I))
               for k,p in value.tags):
            return sorted(files)
        if report_like: return []
        for n in ast.walk(test):
            if not isinstance(n,ast.Compare) or len(n.ops)!=1: continue
            sides=[self.expr(n.left,env),self.expr(n.comparators[0],env)]
            for actual,expected in (sides,sides[::-1]):
                if not self.agent_state(actual) or self.agent_state(expected): continue
                literals={v for k,v in expected.tags if k=='literal'}
                # Exact numbers are legitimate expected answers, not expected
                # source code. Boolean/status strings are not numerical answers.
                numeric=[]
                for v in literals:
                    try: numeric.append(float(v))
                    except ValueError: pass
                if isinstance(n.ops[0],ast.Eq) and numeric and not any(v in {'0','1','True','False'} for v in literals):
                    return sorted(files)
            # abs(actual - expected) < tolerance: expected must be a numeric
            # literal in the residual, not another submitted metric.
            if isinstance(n.ops[0],(ast.Lt,ast.LtE)) and isinstance(n.left,ast.Call) and name(n.left.func) in {'abs','np.abs'}:
                residual=n.left.args[0] if n.left.args else None
                if isinstance(residual,ast.BinOp) and isinstance(residual.op,ast.Sub):
                    a,b=self.expr(residual.left,env),self.expr(residual.right,env)
                    for actual,expected in [(a,b),(b,a)]:
                        if self.agent_state(actual) and not self.agent_state(expected) and any(
                            k=='literal' and re.fullmatch(r'-?\d+(\.\d+)?',v) and v not in {'0','1'}
                            for k,v in expected.tags):
                            return sorted(files)
        return []

    def agent_state(self, value):
        return {p for k,p in value.tags if k=='state' and not p.startswith('/tests') and p not in self.submitted
                and (Path(p).suffix.lower() not in CODE_SUFFIXES or p in self.artifact_paths)}

    def expected_output(self, test, env):
        """Output files compared for equality with exact expected data held by the verifier.

        Expected data: read from /tests, or at least three distinct literal values
        (lines, records). A single word or number (`== "FIXED"`) is guessable.
        """
        out = set()
        for n in ast.walk(test):
            if not (isinstance(n, ast.Compare) and len(n.ops) == 1 and isinstance(n.ops[0], (ast.Eq, ast.NotEq))):
                continue
            sides = [self.expr(n.left, env), self.expr(n.comparators[0], env)]
            for output, expected in (sides, sides[::-1]):
                files = self.agent_state(output)
                if not files or self.agent_state(expected):
                    continue
                verifier_data = any(k == 'state' and p.startswith('/tests') for k, p in expected.tags)
                literals = {v for k, v in expected.tags if k == 'literal'}
                if verifier_data or len(literals) >= 3:
                    out |= files
        return sorted(out)

    def iterate(self, value):
        """Iterating an open file handle after running a program reads its output."""
        # Only execution provenance; text-comparison semantics are unchanged.
        if ('handle','') not in value.tags or not self.ran: return value
        return Value(value.tags | {('ran_output','')})

    def always_true(self, test):
        """`X or True`, or a disjunction containing a generic-token containment."""
        if not (isinstance(test, ast.BoolOp) and isinstance(test.op, ast.Or)): return False
        for part in test.values:
            if isinstance(part, ast.Constant) and part.value is True: return True
            if (isinstance(part, ast.Compare) and len(part.ops)==1 and isinstance(part.ops[0], ast.In)
                    and isinstance(part.left, ast.Constant) and isinstance(part.left.value, str) and generic_literal(part.left.value)
                    and isinstance(part.comparators[0], (ast.Name, ast.Call))):
                return True
        return False

    def run(self):
        env={}
        self.block([n for n in self.tree.body if not isinstance(n,ast.FunctionDef)],env)
        module_created = set(self.created)
        # Analyze entry points even when launched by pytest or external code.
        module_ran = self.ran
        fixtures_ran = {f for f,node in self.functions.items() if any(
            isinstance(n,ast.Call) and (name(n.func) in EXEC_CALLS or name(n.func).split('.')[0] in {'requests','httpx'})
            for n in ast.walk(node))}
        for fn in self.functions:
            if fn=='main' or (fn.startswith('test_') and (self.selected_tests is None or fn in self.selected_tests)):
                self.ran = module_ran or any(a.arg in fixtures_ran for a in self.functions[fn].args.args)
                self.created = set(module_created)
                before=len(self.sinks)
                self.stack=[fn];self.block(self.functions[fn].body,dict(env));self.stack=[]
                if any(isinstance(n,ast.Call) and name(n.func)=='pytest.skip' for n in ast.walk(self.functions[fn])):
                    for sink in self.sinks[before:]:
                        sink['behavioural']=False; sink['independent_output']=[]
                    self.limitations.add('Conditional pytest.skip prevents a required-check proof in '+fn)
        # pytest test classes: test_* methods, with class attributes reachable as self.ATTR.
        for cls in [n for n in self.tree.body if isinstance(n,ast.ClassDef) and n.name.startswith('Test')]:
            fields={}
            for stmt in cls.body:
                if isinstance(stmt,ast.Assign):
                    value=self.expr(stmt.value,env)
                    fields.update({t.id:value for t in stmt.targets if isinstance(t,ast.Name)})
            for method in cls.body:
                if not (isinstance(method,ast.FunctionDef) and method.name.startswith('test_')): continue
                if self.selected_tests is not None and method.name not in self.selected_tests and cls.name not in self.selected_tests: continue
                self.ran = module_ran or any(a.arg in fixtures_ran for a in method.args.args)
                self.created = set(module_created)
                local=dict(env); local['self']=Value(set(),dict(fields))
                self.stack=[method.name];self.block(method.body,local);self.stack=[]
        findings=[]
        seen=set()
        for sink in self.sinks:
            if sink.get('answer_files'):
                indirect=[k for k in sink['comparisons'] if k not in sink['direct']]
                for k in indirect:
                    self.suppressed.append(dict(line=self.comparisons[k]['line'], reason='answer_derived_from_input',
                                                answer_files=sink['answer_files'], expression=self.comparisons[k]['expression']))
                sink['comparisons']=[k for k in sink['comparisons'] if k in sink['direct']]
            sink.pop('direct',None)
            for key in sink['comparisons']:
                identity=(key,sink['line'],sink['kind'])
                if identity not in seen:
                    seen.add(identity);findings.append(dict(comparison=self.comparisons[key],sink=sink))
        behavioural=sorted({s['line'] for s in self.sinks if s.get('behavioural')})
        final_state=sorted({s['line'] for s in self.sinks if s.get('final_state')})
        state_files=sorted({p for s in self.sinks for p in s.get('final_state') or []})
        independent_lines=sorted({s['line'] for s in self.sinks if s.get('independent_output')})
        expected_lines=sorted({s['line'] for s in self.sinks if s.get('expected_output')})
        return {'status':'review' if findings else 'not_evidenced', 'findings':findings,
                'behavioural_assert_lines':behavioural,
                'final_state_assert_lines':final_state, 'final_state_files':state_files[:30],
                'expected_output_assert_lines':expected_lines, 'independent_output_assert_lines':independent_lines,
                'execution_calls':self.execution, 'limitations':sorted(self.limitations),
                'suppressed':self.suppressed,
                'interpretation':'Bounded static dependency evidence, not proof of reward dominance or exploitability. Execution calls do not cancel text-comparison findings. Shell/native/imported helpers and dynamic aliases are not analyzed.'}


def infer_paths(task):
    """Infer candidate roles from literal paths and common wrapper CLI flags.

    These are role hypotheses, not manually supplied labels or an LLM judgment.
    Unresolved/dynamic paths stay unknown. .txt submissions may contain code or
    ordinary answer text; do not claim code semantics merely from their suffix.
    """
    submitted, references, flags = set(), set(), {}
    evidence=[]
    prediction_flags={'prediction','submission','answer','candidate','actual'}
    reference_flags={'reference','expected','gold','groundtruth','ground-truth','target'}
    for path in sorted((task/'tests').rglob('*.sh')):
        for number,line in enumerate(path.read_text(errors='replace').splitlines(),1):
            try:tokens=shlex.split(line,comments=True)
            except ValueError:continue
            for i,token in enumerate(tokens):
                if not token.startswith('--'):continue
                if '=' in token:key,value=token[2:].split('=',1)
                elif i+1<len(tokens):key,value=token[2:],tokens[i+1]
                else:continue
                if not value.startswith('/') or '$' in value:continue
                flags[key.replace('-','_')]=value
                if key in prediction_flags:submitted.add(value)
                elif key in reference_flags:references.add(value)
                else:continue
                evidence.append(dict(file=str(path.relative_to(task)),line=number,flag=key,path=value))
    bindings_by_file={}
    for path in sorted((task/'tests').rglob('*.py')):
        try:tree=ast.parse(path.read_text())
        except (SyntaxError,UnicodeError):continue
        argparse_names=set()
        for node in ast.walk(tree):
            if isinstance(node,ast.Assign) and isinstance(node.value,ast.Call) and name(node.value.func).endswith('.parse_args'):
                argparse_names.update(t.id for t in node.targets if isinstance(t,ast.Name))
            if isinstance(node,ast.Constant) and isinstance(node.value,str) and node.value.startswith('/') and '\n' not in node.value:
                value=node.value
                if value.startswith('/tests/') and any(w in Path(value).name.lower() for w in ['expected','reference','solution','gold','target']):references.add(value)
                elif value.startswith(('/app/','/workdir/','/workspace/')) and Path(value).suffix.lower() in {'.py','.js','.ts','.c','.cpp','.java','.cs','.rs','.go','.sh'}:submitted.add(value)
        bindings_by_file[str(path)]={f'{v}.{k}':p for v in argparse_names for k,p in flags.items()}
    return submitted,references,bindings_by_file,evidence


def selected_test_ids(task):
    """Recognize the static JSON-list pytest runner used by ScaleSWE.

    Do not trust a manifest merely because it exists. Require the launcher to
    pass it to a Python runner that loads argv[1] and splats that list into
    pytest.main. Unknown launch patterns retain all tests.
    """
    launcher = task/'tests/test.sh'
    if not launcher.exists(): return None, {'status':'unknown'}
    commands=[]
    for line in launcher.read_text(errors='replace').splitlines():
        # ScaleSWE wraps cp in a one-line if; extract only its literal operands.
        match=re.search(r'\bcp\s+(/tests/[\w./-]+\.py)\s+([\w./-]+\.py)(?:;|\s|$)',line)
        if match: commands.append(('copy',match.group(1),match.group(2)))
        try: tokens=shlex.split(line,comments=True)
        except ValueError: continue
        if len(tokens)>=3 and tokens[0] in {'python','python3'} and tokens[1].startswith('/tests/') and tokens[2].startswith('/tests/') and tokens[2].endswith('.json'):
            commands.append(('runner',tokens[1],tokens[2]))
    copies={dest:src for kind,src,dest in commands if kind=='copy'}
    runners=[(src,dest) for kind,src,dest in commands if kind=='runner']
    if len(runners)!=1: return None, {'status':'unknown'}
    runner,manifest=runners[0]
    def local(path):
        rel=Path(path).relative_to('/tests')
        if '..' in rel.parts: raise ValueError('Unsafe selection path')
        return task/'tests'/rel
    try:
        tree=ast.parse(local(runner).read_text())
        loaders=set()
        for n in ast.walk(tree):
            if isinstance(n,ast.Assign) and ast.dump(n.value)==ast.dump(ast.parse('json.load(open(sys.argv[1]))',mode='eval').body):
                loaders.update(t.id for t in n.targets if isinstance(t,ast.Name))
        calls=[n for n in ast.walk(tree) if isinstance(n,ast.Call) and name(n.func)=='pytest.main']
        if len(calls)!=1 or not calls[0].args or not isinstance(calls[0].args[0],ast.List):
            return None, {'status':'unknown'}
        args=calls[0].args[0].elts
        if any(isinstance(n,ast.Constant) and isinstance(n.value,str) and ('::' in n.value or n.value.endswith('.py')) for n in args):
            return None, {'status':'unknown', 'reason':'Runner includes additional test selectors'}
        stars=[n.value.id for n in args if isinstance(n,ast.Starred) and isinstance(n.value,ast.Name)]
        if len(stars)!=1 or stars[0] not in loaders: return None, {'status':'unknown'}
        # Reject runners that mutate/reassign the selected list after loading.
        for n in ast.walk(tree):
            if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and name(n.func.value)==stars[0]:
                return None, {'status':'unknown'}
            if isinstance(n,(ast.Assign,ast.AugAssign,ast.AnnAssign)):
                targets=n.targets if isinstance(n,ast.Assign) else [n.target]
                if any(name(t)==stars[0] or isinstance(t,ast.Subscript) and name(t.value)==stars[0] for t in targets):
                    if not isinstance(n,ast.Assign) or ast.dump(n.value)!=ast.dump(ast.parse('json.load(open(sys.argv[1]))',mode='eval').body):
                        return None, {'status':'unknown'}
        ids=json.loads(local(manifest).read_text())
        if not isinstance(ids,list) or not all(isinstance(x,str) and '::' in x for x in ids):
            return None, {'status':'unknown'}
        selected={}
        for node_id in ids:
            filename,entry=node_id.split('::',1)
            source=copies.get(filename,filename if filename.startswith('/tests/') else None)
            if source:
                selected.setdefault(str(local(source)),set()).add(entry.split('[')[0].split('::')[0])
        return selected, dict(status='resolved',manifest=manifest,runner=runner,
                              selected_ids=ids,copied_files=copies)
    except (OSError,ValueError,SyntaxError):
        return None, {'status':'unknown', 'reason':'Selection runner or manifest could not be resolved'}


def original_files(task, paths):
    """Map container paths to task-provided originals under environment/.

    Matches by the longest trailing path suffix. Ambiguous matches with
    different content, and files created by Dockerfile/setup scripts, stay
    unmapped, so no suppression applies to them.
    """
    env=task/'environment'
    if not env.is_dir(): return {}
    files=[f for f in env.rglob('*') if f.is_file()]
    out={}
    for path in paths:
        parts=Path(path).parts[1:]
        best,depth=[],0
        for f in files:
            rel=f.relative_to(env).parts
            n=0
            while n<min(len(rel),len(parts)) and rel[-1-n]==parts[-1-n]: n+=1
            if n>depth: best,depth=[f],n
            elif n==depth and n: best.append(f)
        if not best: continue
        try: texts={f.read_text(errors='replace') for f in best}
        except OSError: continue
        if len(texts)==1: out[path]=texts.pop()
    return out


PROTECT=re.compile(r"(do not|don't|must not|should not|never|without)\s+(modify|modifying|edit|editing|change|changing|alter|altering|touch|touching)|read-only|unmodified", re.I)


def protected_files(instruction, paths):
    """Paths the instruction forbids editing: not agent-submitted code."""
    sentences=re.split(r'(?<=[.!?])\s+|\n\s*[-*\d.]*\s*', instruction)
    return {p for p in paths for s in sentences if PROTECT.search(s) and Path(p).name in s}


def test_identifiers(task):
    """Names the tests use in code (imports, calls, attributes)."""
    names=set()
    for path in sorted((task/'tests').rglob('*.py')):
        try: tree=ast.parse(path.read_text())
        except (SyntaxError,UnicodeError,OSError): continue
        for n in ast.walk(tree):
            if isinstance(n,ast.Name): names.add(n.id)
            elif isinstance(n,ast.Attribute): names.add(n.attr)
            elif isinstance(n,ast.alias): names.add(n.name.split('.')[-1])
            elif isinstance(n,ast.Call) and name(n.func) in {'getattr','hasattr'} and len(n.args)>1 and isinstance(n.args[1],ast.Constant):
                names.add(str(n.args[1].value))
    return names



def verifier_dependencies(task, selection=None, selection_evidence=None, max_files=128):
    """Bounded, read-only dependency discovery; never execute task code.

    Edges are candidate dependencies, not proof a call controls reward. Missing
    or unmodeled helper bodies prevent an absence-of-behavior conclusion.
    Runtime paths use unique bundle matches; ambiguity is deliberately unresolved.
    """
    task = Path(task)
    files = [p for root in ('tests', 'environment', 'setup_files')
             if (task/root).is_dir() for p in (task/root).rglob('*')
             if p.is_file() and p.suffix in {'.py', '.sh', '.bash'}]
    edges, unresolved, extra = [], [], {}
    queue = [(p, None) for p in sorted((task/'tests').rglob('*.py'))
             if selection is None or selection.get(str(p))]
    launcher = task/'tests/test.sh'
    if launcher.is_file(): queue.append((launcher, None))
    seen = set()

    def resolve(target, caller):
        target = target.split('::')[0]
        rel = target.lstrip('/')
        candidates = []
        for p in (caller.parent/target, task/rel):
            if p in files and p not in candidates: candidates.append(p)
        if not candidates:
            parts = Path(rel).parts
            ranked = []
            for p in files:
                n = 0
                a = p.relative_to(task).parts
                while n < min(len(a), len(parts)) and a[-n-1] == parts[-n-1]: n += 1
                if n: ranked.append((n, p))
            if ranked:
                best = max(n for n, p in ranked)
                candidates = [p for n, p in ranked if n == best]
        return candidates[0] if len(candidates) == 1 else None

    def add(target, caller, line, kind, entry=None, imported_name=None):
        dest = resolve(target, caller)
        edge = dict(caller=str(caller.relative_to(task)), line=line, target=target,
                    kind=kind, resolved_file=str(dest.relative_to(task)) if dest else None,
                    imported_name=imported_name)
        if edge not in edges: edges.append(edge)
        if dest:
            if dest.suffix != '.py':
                # Shell may contain arbitrary control flow; discover further
                # calls, but do not treat its exit code as a behavioral oracle.
                unresolved.append(dict(**edge, reason='Shell helper semantics not analyzed'))
            if dest.parent != task/'tests' or entry:
                extra.setdefault(dest, set()).update([entry] if entry else [])
            queue.append((dest, entry))
        else:
            unresolved.append(dict(**edge, reason='Required source missing or ambiguous'))

    evidence = selection_evidence or {}
    if evidence.get('status') == 'resolved':
        copies = evidence.get('copied_files', {})
        for node_id in evidence.get('selected_ids', []):
            filename, entry = node_id.split('::', 1)
            if filename not in copies and not filename.startswith('/tests/'):
                add(filename, launcher, 0, 'selected_test', entry.split('[')[0].split('::')[-1])

    while queue and len(seen) < max_files:
        path, entry = queue.pop(0)
        key = (path, entry)
        if key in seen: continue
        seen.add(key)
        source = path.read_text(errors='replace')
        if path.suffix != '.py':
            for line, text in enumerate(source.splitlines(), 1):
                try: tokens = shlex.split(text, comments=True)
                except ValueError: continue
                # Literal interpreter/source commands only. pytest selection
                # is handled separately; command substitutions stay unknown.
                if len(tokens) >= 2 and Path(tokens[0]).name in {'python', 'python3', 'bash', 'sh', 'source', '.'}:
                    target = tokens[1]
                    if target.endswith(('.py', '.sh', '.bash')):
                        add(target, path, line, 'shell_launch')
            continue
        try: tree = ast.parse(source)
        except SyntaxError:
            unresolved.append(dict(caller=str(path.relative_to(task)), reason='Dependency has invalid Python syntax'))
            continue
        literals = {'sys.executable': 'python'}
        def value(n):
            if isinstance(n, ast.Constant): return n.value
            if isinstance(n, ast.Name): return literals.get(n.id)
            if isinstance(n, ast.Attribute): return literals.get(name(n))
            if isinstance(n, (ast.List, ast.Tuple)):
                return [value(x) for x in n.elts]
            if isinstance(n, ast.Call) and name(n.func) in {'str', 'Path', 'pathlib.Path'} and n.args:
                return value(n.args[0])
            return None
        for n in ast.walk(tree):
            if isinstance(n, ast.Assign):
                v = value(n.value)
                if v is not None:
                    for t in n.targets:
                        if isinstance(t, ast.Name): literals[t.id] = v
        imports = {}
        for n in ast.walk(tree):
            if isinstance(n, ast.ImportFrom) and n.module:
                for a in n.names: imports[a.asname or a.name] = (n.module.replace('.', '/')+'.py', a.name)
            elif isinstance(n, ast.Import):
                for a in n.names: imports[a.asname or a.name] = (a.name.replace('.', '/')+'.py', None)
        for n in ast.walk(tree):
            if not isinstance(n, ast.Call): continue
            fn = name(n.func)
            root = fn.split('.')[0]
            if root in imports:
                target, imported = imports[root]
                dest = resolve(target, path)
                # Third-party and submitted application imports are not
                # automatically verifier helpers.
                if dest and (dest.is_relative_to(task/'tests') or
                             re.search(r'(^|/)(test_|check_|verify|helper)', target)):
                    add(target, path, n.lineno, 'python_import',
                        imported or fn.split('.')[-1], imported_name=root)
            if fn not in {'subprocess.run', 'subprocess.call', 'subprocess.check_call',
                          'subprocess.check_output', 'subprocess.Popen', 'os.system',
                          'runpy.run_path'} or not n.args: continue
            command = value(n.args[0])
            if isinstance(command, str):
                try: command = shlex.split(command)
                except ValueError: command = None
            if not isinstance(command, list):
                unresolved.append(dict(caller=str(path.relative_to(task)), line=n.lineno,
                                       reason='Dynamic subprocess target not resolved'))
                continue
            for token in command:
                if not isinstance(token, str) or not token.endswith(('.py', '.sh', '.bash')): continue
                if (token.startswith('/tests/') or
                    re.search(r'(^|/)(test_|check_|verify|helper)', token)):
                    add(token, path, n.lineno, 'python_launch')
    if queue:
        unresolved.append(dict(reason='Dependency traversal budget exhausted'))
    # Imported helper arguments/cross-file return flow and unittest methods
    # are not summarized here. Their presence must not silently mean no tests.
    for path, entries in extra.items():
        unresolved.append(dict(caller=str(path.relative_to(task)),
                               reason='External helper call/return and reward linkage require review'))
    return dict(edges=edges, unresolved=unresolved, files_scanned=len(seen),
                status='incomplete' if unresolved else 'resolved'), extra


def output_artifact_paths(instruction, paths):
    artifacts={}
    recovery=bool(re.search(r'\b(recover|extract|restore|carve|recovery|extraction)\w*\b',instruction,re.I))
    for p in paths:
        if any(p in sentence and re.search(r'write\s+only\s+(the\s+)?(number|answer|text)',sentence,re.I)
               for sentence in instruction.splitlines()):
            artifacts[p]='Explicit plain answer deliverable despite code-like extension'
        elif (re.search(r'generated\s+(\w+\s+)?(source|code|class|file)',instruction,re.I)
              and '/output/' in p and p in instruction):
            artifacts[p]='Explicit generated source output, not generator implementation'
        elif recovery and re.search(r'/(output|recovered|extracted|restored)(/|$)',p):
            artifacts[p]='Recovered/extracted output contents, not recovery implementation'
        elif re.search(r'\bconfiguration\b',instruction,re.I) and (
                Path(p).name in {'config.py','gunicorn.conf.py','settings.py'} or
                Path(p).suffix in {'.conf','.ini','.toml','.service'}):
            artifacts[p]='Explicit configuration deliverable'
    return artifacts



def grading_completeness(task, results, dependencies):
    """Certify a deliberately small static grading language, default-deny.

    This gate is additional to (not a replacement for) oracle/dataflow analysis.
    Whole files are checked, including unused definitions: conservative extra
    abstentions are preferable to overlooking a hidden grading entry point.
    """
    task=Path(task)
    reasons=[]
    def reject(file, line, reason):
        item=dict(file=file,line=line,reason=reason)
        if item not in reasons: reasons.append(item)

    # Only an explicit direct pytest entrypoint is currently certified. Shell
    # wrappers, plugins, manifests and custom reward programs need their own
    # supported convention before they can authorize an absence flag.
    launcher=task/'tests/test.sh'
    if not launcher.is_file():
        reject('tests/test.sh',0,'No supported grading entrypoint')
    else:
        lines=[l.strip() for l in launcher.read_text(errors='replace').splitlines()
               if l.strip() and not l.lstrip().startswith('#')]
        allowed_setup={'set -e','set -eu','set -euo pipefail','cd /tests',
                       'mkdir -p /logs/verifier'}
        commands=[l for l in lines if l not in allowed_setup]
        if len(commands)!=1:
            reject('tests/test.sh',0,'Shell control flow/reward wrapper is outside the certified direct-pytest convention')
        else:
            try: tokens=shlex.split(commands[0])
            except ValueError: tokens=[]
            if tokens[:3] in [['python','-m','pytest'],['python3','-m','pytest']]:
                tokens=tokens[2:]
            valid=bool(tokens) and tokens[0]=='pytest'
            selectors=[]
            for token in tokens[1:]:
                if token in {'-q','-v','-vv','-rA','--disable-warnings'}:continue
                if re.fullmatch(r'/tests/[A-Za-z0-9_./-]+\.py',token) and '..' not in Path(token).parts:
                    selectors.append(token)
                else:valid=False
            if not valid or not selectors:
                reject('tests/test.sh',0,'Unsupported pytest command, option, or dynamic selector')
            for selector in selectors:
                if not (task/selector.lstrip('/')).is_file():
                    reject('tests/test.sh',0,'Selected verifier file missing: '+selector)
    for config in ['pytest.ini','pyproject.toml','setup.cfg','conftest.py']:
        if any(task.rglob(config)):
            reject(config,0,'Pytest configuration/hooks require separate analysis')

    allowed_nodes={
        ast.Module,ast.Import,ast.ImportFrom,ast.alias,ast.FunctionDef,ast.arguments,ast.arg,
        ast.Assign,ast.AnnAssign,ast.AugAssign,ast.Name,ast.Load,ast.Store,ast.Constant,
        ast.Expr,ast.Assert,ast.If,ast.For,ast.With,ast.withitem,ast.Return,ast.Pass,
        ast.Call,ast.keyword,ast.Attribute,ast.Subscript,ast.Slice,ast.List,ast.Tuple,
        ast.Set,ast.Dict,ast.BinOp,ast.UnaryOp,ast.BoolOp,ast.Compare,
        ast.ListComp,ast.SetComp,ast.GeneratorExp,ast.comprehension,
        ast.Add,ast.Sub,ast.Mult,ast.Div,ast.FloorDiv,ast.Mod,ast.Pow,
        ast.USub,ast.UAdd,ast.Not,ast.And,ast.Or,ast.Eq,ast.NotEq,
        ast.Lt,ast.LtE,ast.Gt,ast.GtE,ast.In,ast.NotIn,ast.Is,ast.IsNot,
        ast.JoinedStr,ast.FormattedValue,
    }
    modules={'pathlib','re','json','csv','ast','os','os.path','stat','math','pytest','subprocess','sys'}
    builtins={'open','str','int','float','bool','len','list','tuple','set','dict',
              'sorted','enumerate','zip','range','abs','min','max','sum','all','any',
              'isinstance','print','compile'}
    qualified={'pathlib.Path','re.search','re.match','re.fullmatch','re.findall',
               're.finditer','re.sub','re.compile','json.load','json.loads',
               'csv.reader','csv.DictReader','ast.parse','os.path.exists',
               'os.path.isfile','os.path.isdir','os.access'}
    methods={'read','read_text','read_bytes','readlines','readline','strip','lstrip',
             'rstrip','split','splitlines','lower','upper','replace','startswith',
             'endswith','join','count','get','keys','values','items','group',
             'isdigit','is_file','is_dir','exists'}
    scanned=0
    for path in sorted((task/'tests').rglob('*.py')):
        rel=str(path.relative_to(task));scanned+=1
        try:tree=ast.parse(path.read_text())
        except (SyntaxError,UnicodeError) as exc:
            reject(rel,0,'Cannot parse verifier: '+str(exc));continue
        aliases={}
        funcs={n.name:n for n in tree.body if isinstance(n,ast.FunctionDef)}
        calls={n:set() for n in funcs}
        bound={n.id for n in ast.walk(tree) if isinstance(n,ast.Name) and isinstance(n.ctx,ast.Store)}
        bound|={n.arg for n in ast.walk(tree) if isinstance(n,ast.arg)}
        for n in ast.walk(tree):
            if isinstance(n,(ast.Import,ast.ImportFrom)):
                for a in n.names:
                    aliases[a.asname or a.name]=(n.module+'.'+a.name if isinstance(n,ast.ImportFrom) and n.module else a.name)
        for node in ast.walk(tree):
            line=getattr(node,'lineno',0)
            if type(node) not in allowed_nodes:
                reject(rel,line,'Unsupported syntax: '+type(node).__name__)
            if isinstance(node,(ast.Import,ast.ImportFrom)):
                imports=[a.name for a in node.names] if isinstance(node,ast.Import) else [node.module or '']
                if isinstance(node,ast.ImportFrom) and (node.level or any(a.name=='*' for a in node.names)):
                    reject(rel,line,'Relative/wildcard imports are not certified')
                for module in imports:
                    if module not in modules:reject(rel,line,'Unsupported import: '+module)
                for a in node.names:
                    aliases[a.asname or a.name]=(node.module+'.'+a.name if isinstance(node,ast.ImportFrom) and node.module else a.name)
            if isinstance(node,ast.FunctionDef):
                if node not in tree.body:reject(rel,line,'Nested function definition')
                if node.decorator_list:reject(rel,line,'Decorators/fixtures/parametrization require analysis')
                for arg in [*node.args.args,*node.args.posonlyargs,*node.args.kwonlyargs]:
                    if arg.arg in aliases.keys()|builtins|funcs.keys():
                        reject(rel,line,'Parameter shadows imported/builtin/function name')
                if node.args.vararg or node.args.kwarg:reject(rel,line,'Dynamic function arguments')
                if node.name.startswith('test_') and (node.args.args or node.args.kwonlyargs):
                    reject(rel,line,'Test fixture parameters require analysis')
                for c in ast.walk(node):
                    # Nested functions/methods are rejected above; only top-level ones have a call graph.
                    if node in tree.body and isinstance(c,ast.Call) and name(c.func) in funcs:calls[node.name].add(name(c.func))
            if isinstance(node,ast.Name) and isinstance(node.ctx,ast.Load):
                if node.id not in bound|aliases.keys()|builtins|funcs.keys():
                    reject(rel,line,'Unresolved name: '+node.id)
            if isinstance(node,ast.Name) and isinstance(node.ctx,ast.Store):
                if node.id in aliases.keys()|builtins|funcs.keys():
                    reject(rel,line,'Rebinding an imported/builtin/function name')
            if isinstance(node,ast.Attribute):
                attr=name(node)
                base,*parts=attr.split('.')
                resolved_attr='.'.join([aliases.get(base,base),*parts])
                if base in aliases and not any(q==resolved_attr or q.startswith(resolved_attr+'.') for q in qualified):
                    reject(rel,line,'Unsupported module attribute: '+resolved_attr)
                elif base not in aliases and node.attr not in methods:
                    reject(rel,line,'Unsupported object attribute: '+node.attr)
            if isinstance(node,ast.Call):
                fn=name(node.func)
                root,*tail=fn.split('.')
                resolved='.'.join([aliases.get(root,root),*tail])
                method=tail[-1] if tail else ''
                if any(k.arg is None for k in node.keywords):
                    reject(rel,line,'Dynamic keyword expansion')
                # A familiar method name on a third-party module must not pass
                # merely because it is spelled "get", "compile", or "read".
                permitted=(resolved in qualified or
                           root not in aliases and fn in builtins|funcs.keys() or
                           root not in aliases and bool(method) and method in methods)
                if not permitted:reject(rel,line,'Unsupported call target: '+(resolved or '<dynamic>'))
            if isinstance(node,(ast.Assign,ast.AnnAssign,ast.AugAssign)):
                targets=node.targets if isinstance(node,ast.Assign) else [node.target]
                for target in targets:
                    for n in ast.walk(target):
                        if isinstance(n,ast.Attribute):reject(rel,line,'Attribute mutation/monkeypatching')
                        if isinstance(n,ast.Name) and n.id in aliases.keys()|builtins|funcs.keys():
                            reject(rel,line,'Rebinding an imported/builtin/function name')
        def cycle(start,current,visited):
            for dest in calls.get(current,()):
                if dest==start:return True
                if dest not in visited and cycle(start,dest,visited|{dest}):return True
            return False
        def depth(current,seen):
            if current in seen:return 999
            return 1+max([depth(n,seen|{current}) for n in calls.get(current,())] or [0])
        for fn in calls:
            if depth(fn,set())>8:reject(rel,funcs[fn].lineno,'Helper depth exceeds analysis budget')
            if cycle(fn,fn,{fn}):reject(rel,funcs[fn].lineno,'Recursive helper call graph')
    for issue in dependencies.get('unresolved',[]):
        reject(issue.get('caller',''),issue.get('line',0),issue['reason'])
    return dict(status='complete' if not reasons else 'incomplete',
                convention='direct_pytest_static_source_checks_v1',
                files_checked=scanned,reasons=reasons)


def analyze_task(task):
    task=Path(task)
    submitted,references,bindings,evidence=infer_paths(task)
    instruction=(task/'instruction.md').read_text(errors='replace') if (task/'instruction.md').is_file() else ''
    artifacts=output_artifact_paths(instruction,submitted)
    submitted-=set(artifacts)
    protected=protected_files(instruction,submitted)
    if re.search(r"(do not|don't|must not|should not|never)\s+(modify|edit|change|alter|touch)\s+(the\s+)?(existing\s+|original\s+)?(application|app|source|service)\s+code",instruction,re.I):
        # Provided code files the instruction tells the agent to leave alone.
        protected|=set(original_files(task,submitted))
    submitted-=protected
    identifiers=test_identifiers(task)
    selection,selection_evidence=selected_test_ids(task)
    originals=original_files(task,submitted)
    dependencies,helper_files=verifier_dependencies(task,selection,selection_evidence)
    results=[]
    for path in sorted((task/'tests').rglob('*.py')):
        try:
            selected=None if selection is None else selection.get(str(path),set())
            analyzer=Analyzer(path.read_text(),submitted,references,bindings.get(str(path),{}),selected_tests=selected,originals=originals,
                     instruction=instruction,test_identifiers=identifiers,artifact_paths=artifacts)
            # Verifier helpers are not submitted application modules. Calling
            # one is not itself evidence of exercising the solution.
            analyzer.agent_imports -= {e['imported_name'] for e in dependencies['edges']
                if e.get('imported_name') and e['caller']==str(path.relative_to(task))}
            r=analyzer.run()
        except (SyntaxError,UnicodeError,RecursionError) as exc:r={'status':'incomplete','error':str(exc)}
        r['file']=str(path.relative_to(task));results.append(r)
    # Flag only when no selected test checks program behaviour (stdout, HTTP
    # response, output files of a program the test ran, imported agent code).
    # A keyword check next to real tests is reported but not flagged.
    behavioural=sum(len(r.get('behavioural_assert_lines',[])) for r in results)
    independent=sum(len(r.get('independent_output_assert_lines',[])) for r in results)
    has_text_check=any(r['status']=='review' for r in results)
    status='not_evidenced' if not has_text_check else ('review' if behavioural+independent==0 else 'text_check_with_behavioural_tests')
    # Broad final-state evidence is not enough to certify a result, but it
    # cannot be silently ignored when asserting absence of meaningful checks.
    unsupported=[]
    harmless={'print','range','mkdir','is_file','exists','is_dir','compile','parse',
              'search','match','fullmatch','findall','finditer','startswith','endswith',
              'count','get','keys','values','items','split','strip','lower','upper',
              'isnan','isinf','isfinite','isdigit','sorted','set','list','read','open'}
    for r in results:
        for limitation in r.get('limitations',[]):
            m=re.match(r'Unmodeled call: (.*?) at line',limitation)
            if not m or m[1].split('.')[-1] not in harmless:
                unsupported.append(dict(file=r['file'],reason=limitation))
        for line in r.get('final_state_assert_lines',[]):
            if line not in r.get('independent_output_assert_lines',[]):
                unsupported.append(dict(file=r['file'],line=line,reason='Final-state check needs oracle/role interpretation'))
    if has_text_check and re.search(r'(script-writing task|headless)',instruction,re.I):
        unsupported.append(dict(reason='Explicit command-recipe/headless artifact contract needs role interpretation'))
    if has_text_check and behavioural==0 and any(r.get('execution_calls') for r in results):
        unsupported.append(dict(reason='Execution occurred but its success/output grading is not fully modeled'))
    completeness=grading_completeness(task,results,dependencies)
    if status=='review' and (completeness['status']!='complete' or unsupported or dependencies['unresolved'] or any(r['status']=='incomplete' for r in results)):
        status='incomplete'
    return dict(task=task.name,status=status,behavioural_asserts=behavioural,
        grading_completeness=completeness,
        dependency_analysis=dependencies,independent_output_asserts=independent,
        unsupported_evidence=unsupported,artifact_role_evidence=artifacts,
        originals_found=sorted(originals),protected_by_instruction=sorted(protected),
        inferred_submitted_artifacts=sorted(submitted),inferred_reference_files=sorted(references),
        role_inference_evidence=evidence,files=results,
        test_selection=selection_evidence,
        interpretation='Automatic role inference and bounded static dependencies only. A .txt submission may be code or a legitimate textual answer; matching is not itself a defect. Dynamic paths, shell comparisons and imported/native helpers are not fully analyzed.')


def main():
    p=argparse.ArgumentParser(description=__doc__,formatter_class=argparse.RawDescriptionHelpFormatter)
    source=p.add_mutually_exclusive_group(required=True)
    source.add_argument('--task',type=Path,help='Automatically infer paths and scan Python verifiers in this task')
    source.add_argument('--verifier',type=Path);p.add_argument('--submitted-source',action='append',default=[])
    p.add_argument('--reference',action='append',default=[]);p.add_argument('--bindings',type=Path);p.add_argument('--out',type=Path,required=True)
    a=p.parse_args()
    try:r=analyze_task(a.task) if a.task else Analyzer(a.verifier.read_text(),a.submitted_source,a.reference,json.loads(a.bindings.read_text()) if a.bindings else {}).run()
    except (SyntaxError,RecursionError) as exc:r={'status':'incomplete','error':str(exc)}
    if a.verifier:
        r['verifier']=str(a.verifier)
        r['grading_completeness']=dict(status='incomplete',reasons=[
            dict(reason='Standalone verifier has no task grading entrypoint context')])
        if r.get('status')=='review':r['status']='incomplete'
    a.out.parent.mkdir(parents=True,exist_ok=True);a.out.write_text(json.dumps(r,indent=2)+'\n')
    print(json.dumps(r,indent=2))


if __name__=='__main__':main()
