"""Defer the reviewed new-feature import until the consuming test runs."""
import ast
from pathlib import Path


def repair(root):
    statement = 'from f5.bigip.tm.asm.tasks import Import_Policy'
    for kind in ('functional', 'unit'):
        path = Path(root) / f'f5/bigip/tm/asm/test/{kind}/test_tasks.py'
        source = path.read_text()
        lines = source.splitlines(keepends=True)
        tree = ast.parse(source)
        imports = [n for n in tree.body if isinstance(n, ast.ImportFrom)
                   and ast.get_source_segment(source, n) == statement]
        if len(imports) != 1:
            raise ValueError(f'Expected one reviewed Import_Policy import in {path}')
        edits = [(imports[0].lineno - 1, imports[0].end_lineno, '')]
        covered = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            uses = [n for stmt in node.body for n in ast.walk(stmt)
                    if isinstance(n, ast.Name) and n.id == 'Import_Policy']
            if not uses:
                continue
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                first = node.body[1]
            edits.append((first.lineno - 1, first.lineno - 1,
                          ' ' * first.col_offset + statement + '\n'))
            covered.update(id(n) for n in uses)
        all_uses = {id(n) for n in ast.walk(tree)
                    if isinstance(n, ast.Name) and n.id == 'Import_Policy'}
        if not covered or covered != all_uses:
            raise ValueError(f'Unreviewed Import_Policy use in {path}')
        for start, end, replacement in sorted(edits, reverse=True):
            lines[start:end] = [replacement]
        result = ''.join(lines)
        ast.parse(result)
        path.write_text(result)


if __name__ == '__main__':
    repair('/testbed')
