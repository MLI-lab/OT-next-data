"""Resolve field ownership conservatively from pinned Java source."""
import hashlib
import re
import subprocess
from tree_sitter_languages import get_parser
parser = get_parser("java")

class Sources:
    def __init__(self, checkout, fixture):
        self.checkout, self.fixture = checkout, fixture
        self.paths = self.git('ls-tree', '-r', '--name-only', checkout['revision']).decode().splitlines()
        self.classes, self.files, self.loading = {}, {}, set()

    def git(self, *args):
        return subprocess.check_output(['git', '-C', self.checkout['repository'], *args], timeout=180)

    def load(self, fq):
        if fq in self.classes:
            return self.classes[fq]
        outer = fq.split('$')[0]
        suffix = outer.replace('.', '/') + '.java'
        paths = [p for p in self.paths if p == suffix or p.endswith('/' + suffix)]
        assert len(paths) == 1, ('ambiguous/missing class file', fq, paths)
        path = paths[0]
        assert path not in self.loading, ('unresolved class in loaded file', fq)
        self.loading.add(path)
        data = self.git('show', self.checkout['revision'] + ':' + path)
        if path == self.checkout['target']:
            assert data == self.fixture, 'target fixture differs from pinned source'
        tree = parser.parse(data)
        assert not tree.root_node.has_error, ('Java parse error', path)
        def txt(node):
            return data[node.start_byte:node.end_byte].decode()
        package = next((txt(n).removeprefix('package ').rstrip(';').strip() for n in tree.root_node.named_children if n.type == 'package_declaration'), '')
        imports = [txt(n).removeprefix('import ').rstrip(';').strip() for n in tree.root_node.named_children if n.type == 'import_declaration' and not txt(n).startswith('import static ')]
        def classes(node, prefix):
            if node.type in ('class_declaration', 'interface_declaration', 'enum_declaration'):
                name = txt(node.child_by_field_name('name'))
                qualified = prefix + name
                body = node.child_by_field_name('body')
                fields = {}
                for child in body.named_children:
                    if child.type == 'field_declaration':
                        typ = txt(child.child_by_field_name('type'))
                        for var in child.named_children:
                            if var.type == 'variable_declarator':
                                fields[txt(var.child_by_field_name('name'))] = typ
                superclass = node.child_by_field_name('superclass')
                parent = txt(superclass).removeprefix('extends ').strip() if superclass else None
                self.classes[qualified] = {'name': qualified, 'fields': fields, 'parent': parent, 'package': package, 'imports': imports, 'path': path}
                for child in body.named_children:
                    classes(child, qualified + '$')
            # Do not mistake local classes inside method bodies for member types.
        for node in tree.root_node.named_children:
            classes(node, package + '.' if package else '')
        self.files[path] = hashlib.sha256(data).hexdigest()
        assert fq in self.classes, ('class not declared', fq)
        return self.classes[fq]

    def resolve_type(self, typ, context):
        typ = re.sub(r'<.*>', '', typ).strip()
        assert re.fullmatch(r'[\w$.]+', typ), ('unsupported type', typ)
        candidates = []
        if '.' in typ and typ[0].islower():
            candidates.append(typ)
        else:
            owner = context['name']
            candidates.extend([owner + '$' + typ, context['package'] + '.' + typ])
            for imp in context['imports']:
                if imp.endswith('.*'):
                    candidates.append(imp[:-1] + typ)
                elif imp.split('.')[-1] == typ:
                    candidates.append(imp)
        found = []
        for candidate in dict.fromkeys(candidates):
            try:
                self.load(candidate)
                found.append(candidate)
            except AssertionError:
                pass
        assert len(found) == 1, ('ambiguous/missing type resolution', typ, found)
        return found[0]

    def field(self, cls, field, seen=None):
        seen = set() if seen is None else seen
        assert cls not in seen, 'inheritance cycle'
        seen.add(cls)
        info = self.load(cls)
        if field in info['fields']:
            return info, info['fields'][field], sorted(seen)
        assert info['parent'], ('no field', cls, field)
        return self.field(self.resolve_type(info['parent'], info), field, seen)

