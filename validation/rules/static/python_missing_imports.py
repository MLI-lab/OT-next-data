"""python_missing_imports: a Python file that grading or the reference solution runs does not parse,
or uses a common package alias (np, pd, plt, ...) without importing it. Nothing is executed."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))  # run as a stage-4 script
import ast
import re


def python_files(task, scopes=("tests", "solution")):
    return [p for scope in scopes for p in (task / scope).rglob("*.py") if (task / scope).exists()]


def import_check_files(task):
    """Test files, plus solution files solve.sh runs or copies (named in solve.sh). Other solution
    files need not be standalone Python, e.g. CrossCodeEval's completion fragment."""
    solve = task / "solution" / "solve.sh"
    script = solve.read_text(errors="replace") if solve.is_file() else ""
    named = lambda p: re.search(r"(?<![\w.-])" + re.escape(p.name) + r"(?![\w-])", script)
    return python_files(task, ("tests",)) + [p for p in python_files(task, ("solution",)) if named(p)]


def check(task):
    """Catch the high-value missing-import class without importing/running code.

    This intentionally reports only unambiguous aliases used by common scientific
    packages. General name resolution is too noisy for a model-free gate.
    """
    task = Path(task)
    aliases = {"np":"numpy", "pd":"pandas", "plt":"matplotlib", "torch":"torch",
               "tf":"tensorflow", "yaml":"yaml", "requests":"requests", "px":"plotly.express"}
    issues=[]
    for p in import_check_files(task):
        try: tree=ast.parse(p.read_text(errors="replace"), filename=str(p))
        except SyntaxError as e:
            issues.append(f"{p.relative_to(task)}: syntax error: {e.msg}"); continue
        imported=set()
        used=set()
        module_uses=set()
        bound=set()
        for n in ast.walk(tree):
            if isinstance(n, ast.Import): imported |= {a.asname or a.name.split('.')[0] for a in n.names}
            elif isinstance(n, ast.ImportFrom) and n.module:
                imported |= {a.asname or a.name for a in n.names}
            elif isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name): module_uses.add(n.value.id)
            elif isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load): used.add(n.id)
            elif isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)): bound.add(n.id)
            elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)): bound.add(n.name)
        for alias, package in aliases.items():
            if alias in module_uses and alias not in imported and alias not in bound:
                issues.append(f"{p.relative_to(task)}: uses `{alias}` ({package}) without an import")
    return ("flagged" if issues else "passed"), issues


if __name__ == '__main__':
    from validation.rules.static.script import run
    sys.exit(run(check))
