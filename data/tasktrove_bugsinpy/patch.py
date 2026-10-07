"""Generate source-backed Harbor candidates from every BugsInPy project.

The default run walks projects and bugs in order. Use --stop-after-project for
an incremental audit. Verifier tests use a clean venv from a pinned lock; pip
reuses cached downloads. Conversion warnings are recorded in each manifest.
"""

from __future__ import annotations

# Support both direct execution and python -m data.<source>.patch.
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


import argparse
import ast
import configparser
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import urllib.request


# Parse the quoted key=value lines used by both project.info and bug.info.
INFO_LINE = re.compile(r'^\s*([A-Za-z_][A-Za-z_0-9]*)\s*=\s*"([^"]*)"\s*$')
# Reject malformed Git commit IDs before using them in archive and diff commands.
COMMIT = re.compile(r"^[0-9a-f]{40}$")
# Find an issue explicitly linked by the fixing commit for the instruction draft.
ISSUE_REF = re.compile(r"\b(?:fix(?:e[sd])?|clos(?:e[sd])?|resolv(?:e[sd])?)\s+#(\d+)\b", re.I)
# Keep all phrases that may reveal a fix in one place. An exact "patch_label"
# paragraph has its following code removed; every other match is review-only.
SOLUTION_CUES = (
    ("my solution is", "patch_label"),
    ("proposed fix", "patch_label"),
    ("proposed solution", "patch_label"),
    ("suggested fix", "patch_label"),
    ("suggested solution", "patch_label"),
    ("patch", "patch_label"),
    ("i changed", "review"),
    ("and add", "review"),
    ("should probably read", "review"),
    ("the fix is", "review"),
    ("replace it with", "review"),
    ("replace this with", "review"),
    ("replace the line with", "review"),
)
SOLUTION_CUE = re.compile(
    r"\b(?:" + "|".join(re.escape(phrase) for phrase, _ in SOLUTION_CUES) + r")\b", re.I)


def is_patch_label(paragraph: str) -> bool:
    return any(action == "patch_label" and
               re.fullmatch(re.escape(phrase) + r"\s*:?\s*", paragraph, re.I)
               for phrase, action in SOLUTION_CUES)


def after_first_sentence(paragraph: str) -> str:
    """Keep prose after its first sentence, if there is any."""
    boundary = re.search(r"[.!?][\"'’”)]*\s+", paragraph)
    return paragraph[boundary.end():].lstrip() if boundary else ""


def read_metadata_file(path: Path) -> dict[str, str]:
    """Read a BugsInPy project.info or bug.info file as quoted key=value pairs."""
    result = {}
    for line in path.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = INFO_LINE.fullmatch(line)
        if not match:
            raise ValueError(f"Unexpected metadata line in {path}: {line!r}")
        result[match[1]] = match[2]
    return result


def read_text_bom(path: Path) -> str:
    """Read UTF-8 or BOM-marked UTF-16 dependency inventories."""
    raw = path.read_bytes()
    return raw.decode("utf-16" if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig")


def pyproject_runtime_requirements(repo: Path, revision: str) -> tuple[list[str], list[str]]:
    """Read static runtime dependencies from common declarative pyproject metadata."""
    try:
        data = tomllib.loads(git(repo, "show", f"{revision}:pyproject.toml").decode())
    except subprocess.CalledProcessError:
        return [], []
    project = data.get("project", {})
    if not isinstance(project, dict):
        raise ValueError("Unexpected [project] metadata in pyproject.toml")
    flit = data.get("tool", {}).get("flit", {}).get("metadata", {})
    if not isinstance(flit, dict):
        raise ValueError("Unexpected [tool.flit.metadata] in pyproject.toml")

    # PEP 621 is the standard source when present. Older Flit projects such as
    # FastAPI store the runtime list under tool.flit.metadata.requires.
    for section, key in ((project, "dependencies"), (flit, "requires")):
        if key not in section:
            continue
        requirements = section[key]
        if not isinstance(requirements, list) or not all(
            isinstance(requirement, str) for requirement in requirements
        ):
            raise ValueError(f"pyproject runtime {key} must be a literal list of strings")
        return list(requirements), []

    if "dependencies" in project.get("dynamic", []):
        return [], ["Runtime dependencies are dynamic in pyproject.toml; inspect the build metadata"]
    if "dependencies" in data.get("tool", {}).get("poetry", {}):
        return [], ["Poetry runtime dependencies need a reviewed conversion to PEP 508 requirements"]
    return [], []


def pyproject_test_requirements(repo: Path, revision: str) -> tuple[list[str], list[str], bool]:
    """Read declared test extras from PEP 621 or legacy Flit metadata."""
    try:
        data = tomllib.loads(git(repo, "show", f"{revision}:pyproject.toml").decode())
    except subprocess.CalledProcessError:
        return [], [], False
    project = data.get("project", {})
    optional = project.get("optional-dependencies", {}) if isinstance(project, dict) else {}
    flit = data.get("tool", {}).get("flit", {}).get("metadata", {})
    flit_extras = flit.get("requires-extra", {}) if isinstance(flit, dict) else {}
    if not isinstance(optional, dict) or not isinstance(flit_extras, dict):
        raise ValueError("Unexpected test extras in pyproject.toml")
    for groups in (optional, flit_extras):
        found_groups = [group for group in ("test", "tests") if group in groups]
        if not found_groups:
            continue
        requirements = []
        warnings = []
        for group in found_groups:
            values = groups[group]
            if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
                warnings.append(f"Dynamic pyproject {group} dependencies need review")
                continue
            requirements.extend(values)
        return sorted(set(requirements)), warnings, True
    return [], [], False


def packaging_only_setup_kwargs(tree: ast.Module, name: str) -> bool:
    """Prove conditional kwargs contain only packaging fields, without execution.

    Used for youtube-dl's normal-install/py2exe branches. Unknown keys, writes,
    mutations and escapes remain unsupported rather than hiding dependencies.
    """
    packaging = {"data_files", "scripts", "entry_points", "console", "options", "zipfile"}
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    visiting = set()

    def check(variable):
        if variable in visiting:
            return False
        visiting.add(variable)
        definitions = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Name) or node.id != variable:
                continue
            parent = parents.get(node)
            if isinstance(parent, ast.Assign) and node in parent.targets:
                if len(parent.targets) != 1:
                    return False
                definitions.append(parent.value)
            elif isinstance(parent, ast.Assign) and parent.value is node:
                # An alias is allowed only if its own uses are checked by the caller.
                if not all(isinstance(t, ast.Name) and t.id in visiting for t in parent.targets):
                    return False
            elif isinstance(parent, ast.keyword) and parent.arg is None:
                call = parents.get(parent)
                if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                        and call.func.id == "setup"):
                    return False
            elif isinstance(parent, ast.Subscript) and parent.value is node:
                assignment = parents.get(parent)
                if not (isinstance(assignment, ast.Assign) and parent in assignment.targets
                        and isinstance(parent.slice, ast.Constant) and parent.slice.value in packaging):
                    return False
            else:
                return False
        for value in definitions:
            if isinstance(value, ast.Dict):
                if not all(isinstance(k, ast.Constant) and k.value in packaging for k in value.keys):
                    return False
            elif isinstance(value, ast.Name):
                if not check(value.id):
                    return False
            else:
                return False
        visiting.remove(variable)
        return bool(definitions)

    return check(name)


def expanded_setup_dependencies(tree: ast.Module) -> dict:
    """Read static dependency fields passed through setup(**kwargs), without exec.

    Resolve module-level literals, names, concatenations and dict assignments.
    Unsupported dependency expressions fail review rather than run setup.py.
    Platform-dependent Sanic dependencies already carry their PEP 508 markers.
    """
    values = {}
    fields = {"install_requires", "tests_require", "extras_require"}

    def literal(node):
        if isinstance(node, ast.Name):
            return values[node.id]
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            return literal(node.left) + literal(node.right)
        if isinstance(node, (ast.List, ast.Tuple)):
            return [literal(item) for item in node.elts]
        if isinstance(node, ast.Dict):
            result = {}
            for key, value in zip(node.keys, node.values):
                try:
                    result[literal(key)] = literal(value)
                except (KeyError, ValueError, TypeError):
                    if isinstance(key, ast.Constant) and key.value in fields:
                        raise ValueError(f"Dynamic setup dependency needs review: {key.value}")
            return result
        return ast.literal_eval(node)

    for statement in tree.body:
        if isinstance(statement, ast.Assign):
            for target in statement.targets:
                try:
                    value = literal(statement.value)
                    if isinstance(target, ast.Name):
                        values[target.id] = value
                    elif isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name):
                        values[target.value.id][literal(target.slice)] = value
                except (KeyError, ValueError, TypeError):
                    if (isinstance(target, ast.Subscript) and isinstance(target.slice, ast.Constant)
                            and target.slice.value in fields):
                        raise ValueError(f"Dynamic setup dependency needs review: {target.slice.value}")
                    if isinstance(target, ast.Name):
                        values.pop(target.id, None)
        if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call):
            call = statement.value
            if not ((isinstance(call.func, ast.Name) and call.func.id == "setup") or
                    (isinstance(call.func, ast.Attribute) and call.func.attr == "setup")):
                continue
            for keyword in call.keywords:
                if keyword.arg is None:
                    try:
                        kwargs = literal(keyword.value)
                    except (KeyError, ValueError, TypeError) as exc:
                        if (isinstance(keyword.value, ast.Name)
                                and packaging_only_setup_kwargs(tree, keyword.value.id)):
                            continue
                        raise ValueError("Dynamic expanded setup kwargs need review") from exc
                    return {key: kwargs[key] for key in fields if key in kwargs}
    return {}


def literal_runtime_requirements(repo: Path, revision: str) -> list[str]:
    """Read the project's declared runtime dependencies without executing setup."""
    pyproject_requirements, _warnings = pyproject_runtime_requirements(repo, revision)
    if pyproject_requirements:
        return pyproject_requirements
    try:
        setup_source = git(repo, "show", f"{revision}:setup.py").decode()
    except (subprocess.CalledProcessError, UnicodeDecodeError):
        return []
    tree = ast.parse(setup_source)
    expanded = expanded_setup_dependencies(tree)
    if "install_requires" in expanded:
        requirements = expanded["install_requires"]
        if not isinstance(requirements, (list, tuple)) or not all(isinstance(r, str) for r in requirements):
            raise ValueError("Expanded install_requires must be a sequence of strings")
        return list(requirements)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg == "install_requires":
                    value = kw.value
                    if isinstance(value, ast.Name):
                        # Some setup.py files pass a module-level literal list.
                        # Resolve only a single simple assignment; never execute setup.py.
                        assignments = [
                            statement.value
                            for statement in tree.body
                            if isinstance(statement, ast.Assign)
                            and any(isinstance(target, ast.Name) and target.id == value.id
                                    for target in statement.targets)
                        ]
                        if len(assignments) != 1:
                            raise ValueError(
                                f"Dynamic install_requires variable needs review: {value.id}"
                            )
                        value = assignments[0]
                    requirements = ast.literal_eval(value)
                    if not isinstance(requirements, (list, tuple)) or not all(
                        isinstance(requirement, str) for requirement in requirements
                    ):
                        raise ValueError("install_requires must be a literal sequence of strings")
                    return list(requirements)
    return []


# Projects whose recipes below were reviewed and runtime-validated one by one.
# They keep those recipes; every other project uses the shared rules.
VALIDATED_RECIPES = {"PySnooper", "ansible", "black", "cookiecutter", "fastapi",
                     "httpie", "keras", "luigi", "sanic"}

# Import names whose distribution on the package index has another name.
IMPORT_DISTRIBUTIONS = {"PIL": "Pillow", "dateutil": "python-dateutil", "yaml": "PyYAML",
                        "attr": "attrs", "pkg_resources": "setuptools"}

# Standard-library modules of the tasks' Python 3.6–3.8 that newer
# interpreters running this script no longer list.
LEGACY_STDLIB = {"distutils", "imp", "asyncore", "asynchat", "smtpd", "aifc", "audioop",
                 "cgi", "cgitb", "chunk", "crypt", "imghdr", "mailcap", "msilib", "nis",
                 "nntplib", "ossaudiodev", "pipes", "sndhdr", "spwd", "sunau", "telnetlib",
                 "uu", "xdrlib", "lib2to3", "formatter", "parser", "symbol", "macpath",
                 "binhex", "dummy_threading", "_dummy_thread"}

# Release dates of the Python versions in BugsInPy metadata. Packages published
# before the interpreter existed have no installable build for it.
PYTHON_RELEASED = {"3.6.9": "2019-07-02", "3.7.0": "2018-06-27", "3.7.3": "2019-03-25",
                   "3.7.4": "2019-07-08", "3.7.7": "2020-03-10", "3.8.1": "2019-12-18",
                   "3.8.3": "2020-05-13"}


def setup_keyword(repo: Path, revision: str, keyword: str):
    """Return the AST value of a setup.py keyword, following one plain assignment."""
    try:
        tree = ast.parse(git(repo, "show", f"{revision}:setup.py").decode())
    except (subprocess.CalledProcessError, SyntaxError, UnicodeDecodeError):
        return None
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg == keyword:
                    value = kw.value
                    if isinstance(value, ast.Name):
                        assignments = [n.value for n in ast.walk(tree) if isinstance(n, ast.Assign)
                                       and any(isinstance(t, ast.Name) and t.id == value.id
                                               for t in n.targets)]
                        return assignments[0] if len(assignments) == 1 else value
                    return value
    return None


def package_dir_root(repo: Path, revision: str) -> str | None:
    """Directory that setup.py declares as the root of its packages, e.g. ``lib``."""
    value = setup_keyword(repo, revision, "package_dir")
    try:
        root = ast.literal_eval(value).get("") if value is not None else None
    except (ValueError, TypeError, SyntaxError, AttributeError):
        return None
    if not isinstance(root, str) or root in ("", "."):
        return None
    path = PurePosixPath(root)
    return None if path.is_absolute() or ".." in path.parts else str(path)


def unconditional_import_requirements(source: bytes, tests: bytes, paths: list[str],
                                      roots: list[str]) -> tuple[list[str], dict]:
    """Packages that importing the selected tests always imports.

    Starts at the selected test files and their conftest/__init__ files and
    follows module-level imports through the project's own modules. Imports
    inside functions, ``try`` or ``if`` blocks are optional and are left out.
    """
    files = {}
    for archive in (source, tests):
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tree:
            for member in tree:
                if member.isfile() and member.name.endswith(".py"):
                    files[member.name] = tree.extractfile(member).read()
    pending = list(paths)
    for path in paths:
        for parent in PurePosixPath(path).parents:
            pending.extend(str(parent / name) for name in ("conftest.py", "__init__.py"))
    visited, external = set(), {}
    while pending:
        path = pending.pop()
        if path in visited or path not in files:
            continue
        visited.add(path)
        try:
            body = ast.parse(files[path]).body
        except (SyntaxError, ValueError):
            continue
        imports = []
        for node in body:
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ""
                if node.level:
                    parents = PurePosixPath(path).parent.parts
                    base = "/".join(parents[:len(parents) - node.level + 1]) + "/" + base
                    base = base.replace(".", "/").strip("/")
                    imports.append("/" + base)
                    imports.extend("/" + base + "/" + alias.name for alias in node.names)
                else:
                    imports.append(base)
                    imports.extend(base + "." + alias.name for alias in node.names)
        for module in imports:
            if module.startswith("/"):
                stems = [PurePosixPath(module[1:])]
            else:
                parts = module.split(".")
                # pytest puts a test file's directory on sys.path when it is
                # not a package, so sibling helpers import by bare name
                # (tqdm 1, 6, 7: tqdm/tests/tests_tqdm.py).
                folder = str(PurePosixPath(path).parent)
                siblings = [] if folder + "/__init__.py" in files else [folder]
                stems = [PurePosixPath(root).joinpath(*parts[:length])
                         for root in [*roots, *siblings] for length in range(1, len(parts) + 1)]
            local = [str(candidate) for stem in stems
                     for candidate in (stem.with_suffix(".py"), stem / "__init__.py")
                     if str(candidate) in files]
            if local:
                pending.extend(local)
                continue
            name = module.split(".")[0]
            if (module.startswith("/") or not name or name in sys.stdlib_module_names
                    or name in LEGACY_STDLIB):
                continue
            if any(key.startswith(PurePosixPath(root, name).as_posix() + "/")
                   for root in roots for key in files):
                continue  # Compiled or generated module of the project itself.
            external.setdefault(IMPORT_DISTRIBUTIONS.get(name, name), set()).add(path)
    evidence = {name: sorted(origins) for name, origins in sorted(external.items())}
    return sorted(external), evidence


def inventory_requirements(path: Path, runtime: list[str]) -> list[str]:
    """Exact pins from a bug's BugsInPy requirements.txt inventory.

    An inventory pin below the project's own declared ``>=`` floor is replaced
    by that floor (HTTPie 1 and 4 record requests 2.0.0 against ``>=2.3.0``).
    """
    floors = {}
    for requirement in runtime:
        match = re.match(r"^([\w.-]+)\s*>=\s*([\d.]+)\s*$", requirement.split(";", 1)[0])
        if match:
            floors[match[1].lower().replace("_", "-")] = match[2]
    pins = []
    for raw in read_text_bom(path).splitlines():
        match = re.match(r"^([\w.-]+)==([\d.]+)\s*$", raw.strip())
        if not match:
            continue
        name, version = match[1], match[2]
        floor = floors.get(name.lower().replace("_", "-"))
        if floor and ([int(part) for part in version.split(".")]
                      < [int(part) for part in floor.split(".")]):
            version = floor
        pins.append(f"{name}=={version}")
    return pins


def compatible_inventory_pins(requirements: list[str], inventory: Path,
                              python_version: str) -> tuple[list[str], list[str]]:
    """Pin only discovered dependencies, respecting all active declarations.

    A recorded environment is evidence, not authority over upstream bounds.
    Inactive markers remain on declarations and never acquire unconditional pins.
    """
    from packaging.markers import default_environment
    from packaging.requirements import InvalidRequirement, Requirement
    from packaging.utils import canonicalize_name

    environment = default_environment()
    environment.update(python_version=".".join(python_version.split(".")[:2]),
                       python_full_version=python_version, sys_platform="linux",
                       platform_system="Linux", os_name="posix", extra="")
    active = {}
    for raw in requirements:
        requirement = Requirement(raw)
        if not requirement.marker or requirement.marker.evaluate(environment):
            active.setdefault(canonicalize_name(requirement.name), []).append(requirement)
    pins, warnings = [], []
    if not inventory.is_file():
        return pins, ["No recorded dependency inventory"]
    for raw in read_text_bom(inventory).splitlines():
        try:
            pin = Requirement(raw.strip())
        except InvalidRequirement:
            continue  # Includes editable project installs; never install task source.
        specs = list(pin.specifier)
        if pin.url or pin.extras or len(specs) != 1 or specs[0].operator != "==" or "*" in specs[0].version:
            continue
        if pin.marker and not pin.marker.evaluate(environment):
            continue
        declarations = active.get(canonicalize_name(pin.name), [])
        if not declarations:
            continue
        if not all(r.specifier.contains(specs[0].version, prereleases=True) for r in declarations):
            warnings.append(f"Ignored incompatible inventory pin {pin}; keeping declared bounds")
            continue
        for declaration in declarations:
            extras = "[" + ",".join(sorted(declaration.extras)) + "]" if declaration.extras else ""
            marker = f"; {declaration.marker}" if declaration.marker else ""
            pins.append(f"{declaration.name}{extras}=={specs[0].version}{marker}")
    return requirement_seeds(pins), warnings


def scrapy_compatibility_requirements(requirements: list[str], inventory: Path) -> tuple[list[str], dict]:
    """Apply two reviewed compatibility exceptions for historical Scrapy tasks.

    Old pyOpenSSL releases need the matching cryptography pin even though it
    is transitive and therefore absent from the selected-test import scan.
    Twisted 20.3.0 is supplied from its immutable upstream source commit because
    uv cannot consume that release's PyPI .tar.bz2 archive.
    """
    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name

    inventory_pins = {}
    for raw in read_text_bom(inventory).splitlines():
        raw = raw.strip()
        if not raw or raw.startswith("#"):
            continue
        try:
            requirement = Requirement(raw)
        except Exception:
            continue
        if requirement.specifier:
            exact = [item.version for item in requirement.specifier
                     if item.operator == "==" and "*" not in item.version]
            if len(exact) == 1 and len(list(requirement.specifier)) == 1:
                inventory_pins[canonicalize_name(requirement.name)] = raw

    result = list(requirements)
    metadata = {"inventory_cryptography_pin": None, "twisted_source": None}
    pyopenssl_pin = inventory_pins.get("pyopenssl")
    crypto_pin = inventory_pins.get("cryptography")
    if pyopenssl_pin == "pyOpenSSL==19.1.0" and crypto_pin == "cryptography==2.9.2":
        result.append(crypto_pin)
        metadata["inventory_cryptography_pin"] = crypto_pin

    twisted_url = (
        "Twisted @ https://github.com/twisted/twisted/archive/"
        "121c98e006a31750661107d390ec2dc4ffe28e8a.tar.gz"
    )
    rewritten = []
    for raw in result:
        try:
            requirement = Requirement(raw)
        except Exception:
            rewritten.append(raw)
            continue
        if (canonicalize_name(requirement.name) == "twisted"
                and str(requirement.specifier) == "==20.3.0"
                and requirement.url is None):
            rewritten.append(twisted_url)
            metadata["twisted_source"] = twisted_url
        else:
            rewritten.append(raw)
    return requirement_seeds(rewritten), metadata


def reachable_import_requirements(source: bytes, tests: bytes, paths: list[str],
                                 inventory: Path) -> tuple[list[str], dict, list[str]]:
    """Follow static local imports from selected tests; never scan unrelated tests.

    Distribution names need evidence: use same-name inventory entries or the
    explicit import/distribution map. This does not infer dynamic imports.
    """
    from packaging.requirements import InvalidRequirement, Requirement
    from packaging.utils import canonicalize_name

    aliases = {"psycopg2": "psycopg2-binary", "sqlalchemy": "SQLAlchemy",
               "dateutil": "python-dateutil", "daemon": "python-daemon",
               "cached_property": "cached-property", "six": "six"}
    recorded = {}
    if inventory.is_file():
        for raw in read_text_bom(inventory).splitlines():
            try:
                requirement = Requirement(raw.strip())
            except InvalidRequirement:
                continue
            if not requirement.url:
                recorded[canonicalize_name(requirement.name)] = requirement.name
    files = {}
    for archive in (source, tests):
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tree:
            for member in tree:
                if member.isfile() and member.name.endswith(".py"):
                    files[member.name] = tree.extractfile(member).read()
    pending = list(paths)
    for path in paths:
        for parent in PurePosixPath(path).parents:
            for name in ("conftest.py", "__init__.py"):
                candidate = str(parent / name)
                if candidate in files:
                    pending.append(candidate)
    visited, external, warnings = set(), {}, []
    while pending:
        path = pending.pop()
        if path in visited or path not in files:
            continue
        visited.add(path)
        try:
            syntax = ast.parse(files[path])
        except (SyntaxError, UnicodeDecodeError):
            warnings.append(f"Could not scan reachable imports: {path}")
            continue
        imports = []
        for node in ast.walk(syntax):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ""
                if node.level:
                    parents = PurePosixPath(path).parent.parts
                    prefix = parents[:len(parents) - node.level + 1]
                    base = ".".join((*prefix, base)).strip(".")
                imports.append(base)
                imports.extend(base + "." + alias.name for alias in node.names if alias.name != "*")
        for module in imports:
            parts = module.split(".")
            local = []
            for prefix in (PurePosixPath('.'), PurePosixPath(path).parent):
                for length in range(1, len(parts) + 1):
                    stem = prefix.joinpath(*parts[:length])
                    local.extend(str(candidate) for candidate in
                                 (stem.with_suffix('.py'), stem / '__init__.py') if str(candidate) in files)
            if local:
                pending.extend(local)
                continue
            name = parts[0]
            if name in sys.stdlib_module_names:
                continue
            distribution = aliases.get(name) or recorded.get(canonicalize_name(name))
            if distribution:
                external.setdefault(distribution, set()).add(path)
    evidence = {name: sorted(origins) for name, origins in sorted(external.items())}
    return sorted(external), evidence, warnings


def black_test_server_requirements(repo: Path, revision: str) -> list[str]:
    """Black 1–3 import an aiohttp base class even for formatter-only tests."""
    tests = ast.parse(git(repo, "show", f"{revision}:tests/test_black.py").decode())
    if not any(isinstance(n, ast.ClassDef) and any(
            isinstance(b, ast.Name) and b.id == "AioHTTPTestCase" for b in n.bases)
            for n in ast.walk(tests)):
        return []
    setup = ast.parse(git(repo, "show", f"{revision}:setup.py").decode())
    for node in ast.walk(setup):
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg == "extras_require":
                    return ast.literal_eval(kw.value)["d"]
    raise ValueError("aiohttp test base class without declared server extra")


def fastapi_form_test_requirements(tests: bytes, paths: list[str], inventory: Path) -> list[str]:
    """Add FastAPI's optional multipart parser when selected tests exercise forms/files."""
    with tarfile.open(fileobj=io.BytesIO(tests), mode="r:gz") as archive:
        members = {member.name: archive.extractfile(member).read()
                   for member in archive if member.isfile()}
    exercises_form = False
    for path in paths:
        source = members.get(path)
        if source is None or not path.endswith(".py"):
            continue
        try:
            tree = ast.parse(source)
        except (SyntaxError, UnicodeDecodeError):
            continue
        form_names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("fastapi"):
                form_names.update(alias.asname or alias.name for alias in node.names
                                  if alias.name in {"Form", "File"})
        if any(isinstance(node, ast.Call)
               and ((isinstance(node.func, ast.Name) and node.func.id in form_names)
                    or (isinstance(node.func, ast.Attribute) and node.func.attr in {"Form", "File"}))
               for node in ast.walk(tree)):
            exercises_form = True
            break
    if not exercises_form:
        return []

    from packaging.requirements import InvalidRequirement, Requirement
    from packaging.utils import canonicalize_name

    for raw in read_text_bom(inventory).splitlines():
        try:
            requirement = Requirement(raw.strip())
        except InvalidRequirement:
            continue
        specs = list(requirement.specifier)
        if (canonicalize_name(requirement.name) == "python-multipart"
                and not requirement.extras and not requirement.url
                and len(specs) == 1 and specs[0].operator == "=="
                and "*" not in specs[0].version):
            return [f"python-multipart=={specs[0].version}"]
    raise ValueError("Selected FastAPI Form/File tests need python-multipart, but the bug inventory has no exact pin")


def scrapy_bug33_verifier_tests(tests: bytes, bug_id: int) -> tuple[bytes, list[str]]:
    """Make Bug 33's fixed-only helper absence a normal no-op assertion failure."""
    if bug_id != 33:
        return tests, []
    replacements = {
        "tests/test_pipeline_media.py": (
            "from scrapy.utils.log import failure_to_exc_info",
            "import scrapy.utils.log as scrapy_log\nfailure_to_exc_info = getattr("
            "scrapy_log, 'failure_to_exc_info', None)",
            "    def test_default_item_completed(self):\n",
            "    def test_default_item_completed(self):\n"
            "        self.assertTrue(callable(failure_to_exc_info), "
            "'Scrapy must provide failure_to_exc_info')\n",
        ),
        "tests/test_utils_log.py": (
            "from scrapy.utils.log import (failure_to_exc_info, TopLevelFormatter,",
            "import scrapy.utils.log as scrapy_log\nfailure_to_exc_info = getattr("
            "scrapy_log, 'failure_to_exc_info', None)\nfrom scrapy.utils.log import (TopLevelFormatter,",
            "    def test_failure(self):\n",
            "    def test_failure(self):\n"
            "        self.assertTrue(callable(failure_to_exc_info), "
            "'Scrapy must provide failure_to_exc_info')\n",
        ),
    }
    members = {}
    with tarfile.open(fileobj=io.BytesIO(tests), mode="r:gz") as archive:
        for member in archive:
            if member.isfile():
                members[member.name] = archive.extractfile(member).read()
    notes = []
    for path, (old_import, new_import, old_method, new_method) in replacements.items():
        content = members.get(path)
        if content is None:
            raise ValueError(f"Scrapy 33 verifier archive lacks {path}")
        source = content.decode("utf-8")
        if source.count(old_import) != 1 or source.count(old_method) != 1:
            raise ValueError(f"Scrapy 33 expected verifier code changed in {path}")
        source = source.replace(old_import, new_import, 1).replace(old_method, new_method, 1)
        members[path] = source.encode("utf-8")
        notes.append(f"{path}: check fixed-only helper inside test assertion")
    return compressed(task_tar(members)), notes



def youtube_dl_verifier_tests(tests: bytes, bug_id: int) -> tuple[bytes, list[str]]:
    """Record targeted upstream-test adaptations without supplying production code."""
    helpers = {38: "urlencode_postdata", 39: "limit_length",
               40: "struct_unpack", 42: "fix_xml_ampersands"}
    if bug_id not in {*helpers, 14}:
        return tests, []
    members = {}
    with tarfile.open(fileobj=io.BytesIO(tests), mode="r:gz") as archive:
        for member in archive:
            if member.isfile():
                members[member.name] = archive.extractfile(member).read()
    path = "test/test_youtube_chapters.py" if bug_id == 14 else "test/test_utils.py"
    source = members[path].decode("utf-8")
    if bug_id in helpers:
        helper = helpers[bug_id]
        imported = f"    {helper},\n"
        method = f"    def test_{helper}(self):\n"
        if source.count(imported) != 1 or source.count(method) != 1:
            raise ValueError(f"youtube-dl {bug_id}: expected helper import/test changed")
        source = source.replace(imported, "", 1).replace(
            method, method + "        import youtube_dl.utils as utils\n"
            + f"        {helper} = getattr(utils, {helper!r}, None)\n"
            + f"        self.assertTrue(callable({helper}), 'Implement youtube_dl.utils.{helper}')\n", 1)
        notes = [f"{path}: resolve {helper} inside its test and assert callable before original assertions"]
    else:
        method = "    def test_youtube_chapters(self):\n"
        if source.count(method) != 1:
            raise ValueError("youtube-dl 14: expected chapter test changed")
        source = source.replace(method, method + '''        # Additional local regression: JSON chapters and description fallback.
        import json
        from test.helper import FakeYDL
        ie = YoutubeIE(FakeYDL())
        json_chapters = getattr(ie, '_extract_chapters_from_json', None)
        description_chapters = getattr(ie, '_extract_chapters_from_description', None)
        self.assertTrue(callable(json_chapters), 'Implement JSON chapter extraction')
        self.assertTrue(callable(description_chapters), 'Keep description chapter extraction')
        expected = [
            {'title': 'JSON intro', 'start_time': 0.0, 'end_time': 12.5},
            {'title': 'JSON ending', 'start_time': 12.5, 'end_time': 30},
        ]
        chapters = [{'chapterRenderer': {
            'timeRangeStartMillis': start, 'title': {'simpleText': title}}}
            for start, title in [(0, 'JSON intro'), (12500, 'JSON ending')]]
        response = chapters
        for key in reversed(['playerOverlays', 'playerOverlayRenderer',
                             'decoratedPlayerBarRenderer', 'decoratedPlayerBarRenderer',
                             'playerBar', 'chapteredPlayerBarRenderer', 'chapters']):
            response = {key: response}
        player = {'watch_next_response': json.dumps(response)}
        webpage = '"RELATED_PLAYER_ARGS": ' + json.dumps(player) + ',\\n'
        description = ('<a onclick="yt.www.watch.player.seekTo(0)">0:00</a> Intro'
                       '<br /><a onclick="yt.www.watch.player.seekTo(20)">0:20</a> Outro')
        fallback = [
            {'title': 'Intro', 'start_time': 0, 'end_time': 20},
            {'title': 'Outro', 'start_time': 20, 'end_time': 30},
        ]
        self.assertEqual(json_chapters(webpage, 'fixture', 30), expected)
        self.assertEqual(ie._extract_chapters(webpage, description, 'fixture', 30), expected)
        for missing in ['', '"RELATED_PLAYER_ARGS": {},\\n',
                        '"RELATED_PLAYER_ARGS": {"watch_next_response": "{}"},\\n']:
            self.assertEqual(ie._extract_chapters(missing, description, 'fixture', 30), fallback)

''', 1)
        notes = [f"{path}: add offline JSON chapter titles/times, JSON precedence and description fallback assertions; retain original cases"]
    members[path] = source.encode("utf-8")
    return compressed(task_tar(members)), notes


def scm_version_setup(repo: Path, revision: str, python_version: str
                      ) -> tuple[list[str], list[str], dict | None]:
    """Prepare legacy setuptools-scm metadata without shipping Git history.

    Match packaging configuration, not project names or comments. Modern TOML
    activation and dynamic configuration require a reviewed build recipe.
    """
    try:
        setup_source = git(repo, "show", f"{revision}:setup.py").decode()
    except subprocess.CalledProcessError:
        setup_source = ""
    try:
        pyproject = tomllib.loads(git(repo, "show", f"{revision}:pyproject.toml").decode())
    except subprocess.CalledProcessError:
        pyproject = {}
    toml_scm = pyproject.get("tool", {}).get("setuptools_scm")
    if "use_scm_version" not in setup_source and toml_scm is None:
        return [], [], None
    tree = ast.parse(setup_source)
    setup_names, module_names = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "setuptools":
            setup_names.update(alias.asname or alias.name for alias in node.names if alias.name == "setup")
        elif isinstance(node, ast.Import):
            module_names.update(alias.asname or alias.name for alias in node.names if alias.name == "setuptools")
    configs = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        is_setup = (isinstance(node.func, ast.Name) and node.func.id in setup_names
                    or isinstance(node.func, ast.Attribute) and node.func.attr == "setup"
                    and isinstance(node.func.value, ast.Name) and node.func.value.id in module_names)
        if is_setup:
            configs.extend(kw.value for kw in node.keywords if kw.arg == "use_scm_version")
    if not configs:
        if toml_scm is not None:
            raise ValueError("TOML setuptools-scm configuration needs a reviewed metadata build recipe")
        return [], [], None
    if len(configs) != 1:
        raise ValueError("Multiple setuptools-scm declarations need review")
    try:
        config = ast.literal_eval(configs[0])
    except (ValueError, TypeError, SyntaxError) as exc:
        raise ValueError("Dynamic setuptools-scm configuration needs review") from exc
    if config is False:
        return [], [], None
    if config is not True and not isinstance(config, dict):
        raise ValueError("Unexpected setuptools-scm configuration")
    if toml_scm is not None or isinstance(config, dict) and set(config) - {"write_to", "write_to_template"}:
        raise ValueError("Custom setuptools-scm options need a reviewed metadata build recipe")
    # Keep the tested legacy tooling; the resolver selects a compatible
    # setuptools on older Python and writes an exact pin in the task lock.
    minor = tuple(map(int, python_version.split(".")[:2]))
    setuptools = "setuptools==69.5.1" if minor >= (3, 8) else "setuptools"
    # Do not force old tooling on a project declaring incompatible build tools.
    from packaging.requirements import Requirement
    for raw in pyproject.get("build-system", {}).get("requires", []):
        requirement = Requirement(raw)
        name = requirement.name.lower().replace("_", "-").replace(".", "-")
        chosen = {"setuptools-scm": "3.5.0", "setuptools": "69.5.1" if minor >= (3, 8) else None}.get(name)
        if chosen and not requirement.specifier.contains(chosen):
            raise ValueError(f"Declared build tool {raw} is incompatible with the reviewed SCM metadata recipe")
    version = git(repo, "describe", "--tags", "--abbrev=0", revision).decode().strip()
    command = ("cd /app && SETUPTOOLS_SCM_PRETEND_VERSION=" + shlex.quote(version)
               + " python3 setup.py egg_info")
    return [setuptools, "setuptools-scm==3.5.0"], [command], {
        "backend": "setuptools-scm", "configuration_source": "setup.py:use_scm_version",
        "configuration": config, "version": version, "source_commit": revision,
        "version_source": "nearest_ancestral_tag", "setup_command": command,
    }


def git(repo: Path, *args: str) -> bytes:
    return subprocess.check_output(["git", "-C", str(repo), *args], stderr=subprocess.PIPE)


def normal_url(url: str) -> str:
    return url.rstrip("/").removesuffix(".git")


def github_project(url: str) -> tuple[str, str]:
    from urllib.parse import urlparse

    parsed = urlparse(normal_url(url))
    parts = parsed.path.strip("/").split("/")
    if parsed.scheme != "https" or parsed.netloc != "github.com" or len(parts) != 2:
        raise ValueError(f"Unsupported upstream project URL: {url}")
    return parts[0], parts[1]


def checkout(path: Path, url: str, revision: str | None = None) -> Path:
    """Clone or reuse a source repo; verify its URL and optional exact commit."""
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "clone", "--filter=blob:none", url, str(path)], check=True)
    if normal_url(git(path, "remote", "get-url", "origin").decode().strip()) != normal_url(url):
        raise ValueError(f"Cached checkout has a different origin: {path}")
    if revision:
        if not COMMIT.fullmatch(revision):
            raise ValueError(f"Invalid revision: {revision}")
        ensure_commit(path, revision)
        if git(path, "rev-parse", "HEAD").decode().strip() != revision:
            subprocess.run(["git", "-C", str(path), "checkout", "--detach", revision], check=True)
    return path


def ensure_commit(repo: Path, revision: str) -> None:
    if not COMMIT.fullmatch(revision):
        raise ValueError(f"Invalid commit: {revision}")
    try:
        git(repo, "cat-file", "-e", f"{revision}^{{commit}}")
    except subprocess.CalledProcessError:
        subprocess.run(["git", "-C", str(repo), "fetch", "origin", revision], check=True)
        git(repo, "cat-file", "-e", f"{revision}^{{commit}}")


def projects(metadata_root: Path) -> list[dict]:
    records = []
    for project_dir in sorted((metadata_root / "projects").iterdir()):
        info_path = project_dir / "project.info"
        if not project_dir.is_dir() or not info_path.is_file():
            continue
        info = read_metadata_file(info_path)
        url = info["github_url"]
        github_project(url)
        bugs = sorted((project_dir / "bugs").glob("*/bug.info"), key=lambda path: int(path.parent.name))
        records.append({"project": project_dir.name, "repo": url,
                        "bug_ids": [int(path.parent.name) for path in bugs]})
    return records


def sha256(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


def compressed(blob: bytes) -> bytes:
    return subprocess.run(["gzip", "-n", "-9"], input=blob, stdout=subprocess.PIPE,
                          check=True).stdout


def task_tar(files: dict[str, bytes]) -> bytes:
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w") as archive:
        for name, contents in sorted(files.items()):
            info = tarfile.TarInfo(name)
            info.size = len(contents)
            info.mode = 0o755 if name.endswith(".sh") else 0o644
            info.mtime = 0
            archive.addfile(info, io.BytesIO(contents))
    return out.getvalue()


def issue_for_fix_range(repo: Path, project_url: str, buggy: str, fixed: str,
                           cache: dict[str, dict]) -> tuple[dict | None, str | None]:
    message = git(repo, "show", "-s", "--format=%B", fixed).decode()
    refs = sorted(set(ISSUE_REF.findall(message)))
    if not refs:
        # A multi-commit fix may mention its issue before the final commit.
        messages = git(repo, "log", "--format=%B", f"{buggy}..{fixed}").decode()
        refs = sorted(set(ISSUE_REF.findall(messages)))
    if len(refs) != 1:
        return None, f"Expected one unambiguous issue reference in fix range, found {refs}"
    owner, project = github_project(project_url)
    url = f"https://github.com/{owner}/{project}/issues/{refs[0]}"
    if url not in cache:
        headers = {"User-Agent": "BugsInPy-Harbor-converter", "Accept": "application/vnd.github+json"}
        if os.getenv("GITHUB_TOKEN"):
            headers["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"
        request = urllib.request.Request(
            f"https://api.github.com/repos/{owner}/{project}/issues/{refs[0]}", headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                data = json.load(response)
        except Exception as exc:
            return None, f"Could not fetch {url}: {type(exc).__name__}: {exc}"
        if "pull_request" in data or normal_url(data.get("html_url", "")) != normal_url(url):
            return None, f"Issue reference does not resolve to {url}"
        cache[url] = {"url": url, "title": data["title"], "body": data.get("body") or ""}
    return cache[url], None


def instruction_from_issue(project: str, issue: dict | None, issue_error: str | None,
                           added_lines: set[str]) -> tuple[str, dict]:
    """Only strip explicit patch material; flag all uncertain instructions."""
    title = issue["title"].strip() if issue else "Bug reported by the upstream regression test"
    paragraphs = re.split(r"\n\s*\n", (issue["body"] if issue else "").replace("\r\n", "\n"))
    retained, removed = [], []
    pending_patch_label = dropping_labeled_code = False
    for paragraph in paragraphs:
        part = paragraph.strip()
        if not part:
            continue
        code = part.startswith(("```", "~~~", "`")) or paragraph.lstrip("\n").startswith("    ")
        if is_patch_label(part):
            removed.append("explicit patch label")
            pending_patch_label = True
            dropping_labeled_code = False
            continue
        if pending_patch_label:
            pending_patch_label = False
            if code:
                dropping_labeled_code = True
                removed.append("labeled code")
                continue
            part = after_first_sentence(part)
            removed.append("first sentence after patch label")
            if not part:
                continue
        elif dropping_labeled_code and code:
            removed.append("labeled code")
            continue
        dropping_labeled_code = False
        normalized = {re.sub(r"\s+", "", line.lstrip("+- ")) for line in part.splitlines()}
        matches_fix = bool({line for line in normalized if len(line) >= 16} & added_lines)
        if matches_fix:
            removed.append("fixed-diff match")
            continue
        if re.fullmatch(r"https?://\S+", part):
            removed.append("bare source link")
            continue
        retained.append(part)
    body = "\n\n".join(retained)
    possible_hint = bool(SOLUTION_CUE.search(body))
    needs_review = (issue_error is not None or len(body) < 60 or possible_hint or
                    "first sentence after patch label" in removed)
    content = (f"# Task\n\nWork in the {project} project at `/app` to fix the issue described below.\n\n"
               f"# Issue\n\n{title}\n" + (f"\n{body}\n" if body else "")
               + "\nModify the project files to resolve this issue.\n")
    report = {"status": "needs_review" if needs_review else "draft",
              "issue_url": issue["url"] if issue else None, "issue_error": issue_error,
              "possible_hint": possible_hint, "removed_spans": removed,
              "instruction_sha256": sha256(content.encode())}
    return content, report


def test_paths(raw: str) -> list[str]:
    paths = [item.strip() for item in raw.split(";")]
    if not paths or any(not item for item in paths):
        raise ValueError(f"Invalid test_file: {raw!r}")
    for item in paths:
        path = PurePosixPath(item)
        if path.is_absolute() or ".." in path.parts or path.parts[0] in ("", "."):
            raise ValueError(f"Unsafe test_file: {item}")
    return paths


def test_roots(paths: list[str]) -> list[str]:
    """Ship the selected upstream test tree, including fixtures and helpers."""
    roots = set()
    for item in paths:
        parts = PurePosixPath(item).parts
        matches = [index for index, part in enumerate(parts) if part in ("test", "tests")]
        if not matches:
            raise ValueError(f"No test directory in test_file: {item}")
        roots.add("/".join(parts[:matches[0] + 1]))
    return sorted(roots)


def setup_recipe(path: Path) -> tuple[list[str], list[str], list[str]]:
    """Apply common setup commands; report other recipes for later review."""
    commands, warnings, requirements = [], [], []
    if not path.exists():
        return commands, warnings, requirements
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            words = shlex.split(line)
        except ValueError:
            warnings.append(f"Could not parse setup command: {line}")
            continue
        if words[:2] == ["python", "setup.py"] and words[2:] in (["install"], ["develop"]):
            commands.append("# Source is imported from /app; avoid a separate installed copy")
        elif words[:2] == ["python", "setup.py"] and words[2:3] in (["build_ext"], ["build"]):
            commands.append("cd /app && python3 " + shlex.join(words[1:]))
        elif words[:2] in (["pip", "install"], ["pip3", "install"]):
            if "-e" in words or "--editable" in words:
                warnings.append(f"Editable dependency setup needs review: {line}")
            elif len(words) == 3 and not words[2].startswith("-"):
                requirements.append(words[2])
            else:
                warnings.append(f"Dependency setup needs review: {line}")
        elif words[:1] == ["touch"] and len(words) == 2:
            path_to_touch = PurePosixPath(words[1])
            if path_to_touch.is_absolute() or ".." in path_to_touch.parts:
                warnings.append(f"Unsafe touch command: {line}")
            else:
                commands.append("touch " + shlex.quote("/app/" + words[1]))
        else:
            warnings.append(f"Unsupported setup command: {line}")
    return commands, warnings, requirements


def project_test_requirements(repo: Path, revision: str) -> tuple[list[str], list[str]]:
    """Read literal test extras from declarative metadata without executing it."""
    pyproject_requirements, pyproject_warnings, found = pyproject_test_requirements(repo, revision)
    if found:
        return pyproject_requirements, pyproject_warnings
    try:
        tree = ast.parse(git(repo, "show", f"{revision}:setup.py").decode())
    except (subprocess.CalledProcessError, SyntaxError, UnicodeDecodeError):
        return [], ["Could not read setup.py test extras"]
    expanded = expanded_setup_dependencies(tree)
    if expanded:
        extras = expanded.get("extras_require", {})
        requirements = [*expanded.get("tests_require", []),
                        *extras.get("test", []), *extras.get("tests", [])]
        if not all(isinstance(r, str) for r in requirements):
            raise ValueError("Expanded test dependencies must be strings")
        return sorted(set(requirements)), []
    requirements, warnings = [], []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not any(k.arg == "extras_require" for k in node.keywords):
            continue
        for keyword in node.keywords:
            if keyword.arg != "extras_require":
                continue
            try:
                extras = ast.literal_eval(keyword.value)
            except (ValueError, TypeError, SyntaxError):
                warnings.append("Dynamic setup.py test extras need review")
                continue
            if not isinstance(extras, dict):
                warnings.append("Unexpected setup.py test extras need review")
                continue
            for group in ("test", "tests"):
                values = extras.get(group, [])
                if isinstance(values, (set, tuple, list)) and all(isinstance(v, str) for v in values):
                    requirements.extend(values)
                elif values:
                    warnings.append(f"Dynamic setup.py {group} extras need review")
    return sorted(set(requirements)), warnings


def project_requirement_file(repo: Path, revision: str, filename: str) -> tuple[list[str], list[str]]:
    """Read literal pinned or bounded requirements without running upstream code."""
    requirements, warnings = [], []
    try:
        content = git(repo, "show", f"{revision}:{filename}").decode()
    except (subprocess.CalledProcessError, UnicodeDecodeError):
        return requirements, warnings
    for raw in content.splitlines():
        line = raw.split(" #", 1)[0].strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("-") or line.startswith((".", "/", "git+", "https://")):
            warnings.append(f"Unsupported {revision[:12]}:{filename} entry: {line}")
            continue
        try:
            requirement_seeds([line])
        except ValueError:
            warnings.append(f"Unsupported {revision[:12]}:{filename} entry: {line}")
            continue
        requirements.append(line)
    return requirements, warnings


def project_requirement_files(repo: Path, buggy: str, fixed: str) -> tuple[list[str], list[str], list[str], list[str]]:
    """Keep buggy dependencies in setup and report fixed-only test dependencies."""
    buggy_runtime, warnings = project_requirement_file(repo, buggy, "requirements.txt")
    buggy_tests, extra = project_requirement_file(repo, buggy, "test_requirements.txt")
    warnings.extend(extra)
    fixed_tests, extra = project_requirement_file(repo, fixed, "test_requirements.txt")
    warnings.extend(extra)
    fixed_runtime, extra = project_requirement_file(repo, fixed, "requirements.txt")
    warnings.extend(extra)
    added_runtime = sorted(set(fixed_runtime) - set(buggy_runtime))
    if added_runtime:
        warnings.append("Fixed commit changes runtime requirements; review before adding to agent setup")
    return buggy_runtime + buggy_tests, fixed_tests, warnings, added_runtime


def fixed_test_import_requirements(archive: bytes) -> tuple[list[str], list[str], list[str]]:
    """Add confidently named external imports from shipped test helpers."""
    known = {"six": "six"}
    found, warnings = set(), []
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tree:
        for member in tree:
            if not member.isfile() or not member.name.endswith(".py"):
                continue
            handle = tree.extractfile(member)
            if handle is None:
                continue
            try:
                syntax = ast.parse(handle.read().decode("utf-8"))
            except (SyntaxError, UnicodeDecodeError):
                warnings.append(f"Could not scan test imports: {member.name}")
                continue
            for node in ast.walk(syntax):
                if isinstance(node, ast.Import):
                    found.update(alias.name.split(".", 1)[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    found.add(node.module.split(".", 1)[0])
    return [known[name] for name in sorted(found & known.keys())], warnings, sorted(found)


def local_modules_in_archives(*archives: bytes) -> set[str]:
    """Find top-level modules shipped with the buggy project or fixed tests."""
    modules = set()
    for archive in archives:
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tree:
            for member in tree:
                parts = PurePosixPath(member.name).parts
                if not parts:
                    continue
                if len(parts) > 1:
                    modules.add(parts[0])
                elif parts[0].endswith(".py"):
                    modules.add(parts[0][:-3])
    return modules


def needs_non_utf8_default(issue: dict | None, reference_patch: bytes, fixed_tests: bytes) -> bool:
    """Reproduce encoding reports only when the fix and selected tests support it."""
    report = ((issue.get("title", "") + "\n" + issue.get("body", "")) if issue else "")
    if ("UnicodeEncodeError" not in report and "not the default on Windows" not in report
            and "wrong encoding on Windows" not in report):
        return False
    if not any(line.startswith("+") and re.search(r"encoding\s*=\s*['\"]utf-?8['\"]", line, re.I)
               for line in reference_patch.decode(errors="replace").splitlines()):
        return False
    with tarfile.open(fileobj=io.BytesIO(fixed_tests), mode="r:gz") as tree:
        return any(member.isfile() and member.name.endswith(".py") and
                   any(byte >= 128 for byte in tree.extractfile(member).read())
                   for member in tree)


def compile_lock(requirements: list[str], python_version: str,
                 cache: dict[tuple[str, tuple[str, ...]], str],
                 build_python: str | None = None,
                 overrides: tuple[str, ...] = (),
                 exclude_newer: str | None = None) -> str:
    """Resolve a lock for the task's Python version.

    ``build_python`` selects a uv-managed interpreter for building metadata of
    pins that ship no wheel. It does not change the version resolved for.
    ``overrides`` are pins that replace what other packages declare for them.
    ``exclude_newer`` is a date; releases published after it are ignored.
    """
    minor = ".".join(python_version.split(".")[:2])
    seed = tuple(sorted(set(requirements)))
    key = (minor, seed + tuple(f"override:{pin}" for pin in overrides)
           + ((f"exclude-newer:{exclude_newer}",) if exclude_newer else ()))
    if key not in cache:
        build = ["--managed-python", "--python", build_python] if build_python else []
        with tempfile.NamedTemporaryFile("w", suffix=".txt") as override_file:
            override_file.write("\n".join(overrides) + "\n")
            override_file.flush()
            process = subprocess.run(
                ["uv", "pip", "compile", "-", *build, "--python-version", minor,
                 *(["--override", override_file.name] if overrides else []),
                 *(["--exclude-newer", exclude_newer + "T23:59:59Z"] if exclude_newer else []),
                 "--no-header", "--no-annotate", "-q"],
                input="\n".join(seed) + "\n", text=True, capture_output=True)
        if process.returncode:
            raise ValueError(f"Dependency resolution failed for Python {minor}: {process.stderr.strip()}")
        cache[key] = process.stdout
    return cache[key]


def requirement_seeds(requirements: list[str]) -> list[str]:
    """Normalize spelling and drop an unconstrained duplicate of a package."""
    by_name: dict[str, set[str]] = {}
    for requirement in requirements:
        requirement = requirement.strip()
        match = re.match(r"^[A-Za-z0-9][A-Za-z0-9_.-]*", requirement)
        if not match:
            raise ValueError(f"Unsupported dependency requirement: {requirement!r}")
        name = match[0].replace("_", "-").replace(".", "-").lower()
        normalized = name + requirement[match.end():]
        by_name.setdefault(name, set()).add(normalized)
    result = []
    for name, values in sorted(by_name.items()):
        if len(values) > 1:
            values.discard(name)
        result.extend(sorted(values))
    return result


def tornado_dependency_seeds(requirements: list[str]) -> tuple[list[str], list[dict[str, str]]]:
    """Keep stdlib unittest and the editable Tornado source out of pip locks."""
    from packaging.requirements import Requirement

    reasons = {"unittest": "Python standard library",
               "tornado": "Project source is provided in /app"}
    retained, excluded = [], []
    for raw in requirements:
        name = Requirement(raw).name.lower()
        if name in reasons:
            excluded.append({"requirement": raw, "reason": reasons[name]})
        else:
            retained.append(raw)
    return retained, excluded


def setup_script(source_hash: str, lock_hash: str, commands: list[str], python_path: str,
                 install_prelude: str = "", install_flags: str = "") -> str:
    recipe = "\n".join(commands)
    ready_hash = sha256((source_hash + "\n" + lock_hash + "\n" + recipe + "\n" + python_path).encode())
    if install_prelude or install_flags:
        ready_hash = sha256((ready_hash + "\n" + install_prelude + "\n" + install_flags).encode())
    return f"""#!/bin/bash
set -euo pipefail
if [ -f /app/.bugsinpy-ready ]; then
  grep -qx '{ready_hash}' /app/.bugsinpy-ready
  exit 0
fi
if [ -f /app/.bugsinpy-source-hash ]; then
  grep -qx '{source_hash}' /app/.bugsinpy-source-hash
elif [ -n "$(find /app -mindepth 1 -maxdepth 1 -print -quit)" ]; then
  echo 'Project exists without setup marker; refusing to overwrite it' >&2
  exit 2
else
  mkdir -p /app
  echo '{source_hash}  /setup_files/project.tar.gz' | sha256sum -c -
  tar -xzf /setup_files/project.tar.gz -C /app
  printf '%s\\n' '{source_hash}' > /app/.bugsinpy-source-hash
fi
mkdir -p /app/.deps
echo '{lock_hash}  /setup_files/requirements.lock' | sha256sum -c -
{install_prelude}\
python3 -m pip install {install_flags}--no-deps --target /app/.deps -r /setup_files/requirements.lock
export PYTHONPATH=/app/.deps:{python_path}
export PATH=/app/.deps/bin:$PATH
{recipe}
printf '%s\\n' '{ready_hash}' > /app/.bugsinpy-ready
"""


def loopback_hosts_setup() -> str:
    """Populate standard localhost entries missing from isolated OCI images."""
    return """python3 -I - <<'PYLOCALHOST'
from pathlib import Path
hosts = Path('/etc/hosts')
text = hosts.read_text() if hosts.exists() else ''
entries = [line.split('#', 1)[0].split() for line in text.splitlines()]
missing = [address + ' localhost' for address in ('127.0.0.1', '::1')
           if not any(len(parts) > 1 and parts[0] == address and 'localhost' in parts[1:]
                      for parts in entries)]
if missing:
    with hosts.open('a') as stream:
        stream.write(('\\n' if text and not text.endswith('\\n') else '')
                     + '\\n'.join(missing) + '\\n')
PYLOCALHOST"""


def unittest_reporter() -> str:
    """Report actual unittest execution; reject collection failures and skips."""
    return '''import json
import sys
import traceback
import unittest
from pathlib import Path

class RecordingResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.executed = []
        self.infrastructure_errors = []

    def startTest(self, test):
        self.executed.append({"id": test.id(), "class": type(test).__name__})
        super().startTest(test)

    def addError(self, test, err):
        frames = traceback.extract_tb(err[2])
        if (type(test).__name__ in {"_FailedTest", "_ErrorHolder"}
                or any(frame.name in {"setUp", "tearDown", "setUpClass", "tearDownClass",
                                      "setUpModule", "tearDownModule"} for frame in frames)):
            self.infrastructure_errors.append(test.id())
        super().addError(test, err)

class RecordingRunner(unittest.TextTestRunner):
    resultclass = RecordingResult

try:
    program = unittest.main(module=None, argv=["unittest", *sys.argv[1:]],
                            testRunner=RecordingRunner, exit=False)
except Exception:
    traceback.print_exc()
    raise SystemExit(2)
result = program.result
invalid = (not result.testsRun or bool(result.skipped) or bool(result.expectedFailures)
           or bool(result.unexpectedSuccesses) or bool(result.infrastructure_errors)
           or any(t["class"] == "_FailedTest" for t in result.executed))
record = {"arguments": sys.argv[1:], "tests_run": result.testsRun,
          "executed": result.executed, "skipped": [(t.id(), reason) for t, reason in result.skipped],
          "failures": [(t.id(), trace) for t, trace in result.failures],
          "errors": [(t.id(), trace) for t, trace in result.errors],
          "infrastructure_errors": result.infrastructure_errors,
          "expected_failures": len(result.expectedFailures),
          "successful": result.wasSuccessful(), "invalid": invalid}
with Path('/logs/verifier/unittest.jsonl').open('a') as stream:
    stream.write(json.dumps(record) + "\\n")
raise SystemExit(2 if invalid else (0 if result.wasSuccessful() else 1))
'''


def unittest_run_script(command: str) -> str:
    """Run every selected invocation, retaining failures and infrastructure errors."""
    lines = ["#!/bin/bash", "set +e", "overall=0", "rm -f /logs/verifier/unittest.jsonl"]
    for line in command.splitlines():
        words = shlex.split(line)
        if len(words) < 3 or words[0] not in {"python", "python3"} or words[1:3] != ["-m", "unittest"]:
            raise ValueError(f"Unittest reporter requires a unittest invocation: {line}")
        lines.extend(["python3 /tests/run_unittest.py " + shlex.join(words[3:]),
                      "status=$?", 'if [ "$status" -gt "$overall" ]; then overall=$status; fi'])
    lines.append('exit "$overall"')
    return "\n".join(lines) + "\n"


def test_script(command: str, test_hash: str, verifier_lock_hash: str,
                python_path: str, pytest_report: bool = False,
                local_http: bool = False, install_prelude: str = "",
                install_flags: str = "", pre_test_commands: str = "",
                unittest_report: bool = False) -> str:
    if not command.strip():
        raise ValueError("Expected at least one test command")
    if not verifier_lock_hash:
        raise ValueError("Expected a complete verifier dependency lock")
    execution_check = ""
    execution_environment = ""
    if local_http:
        execution_environment += "unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy\n"
    if pytest_report:
        if len(command.splitlines()) != 1 or not re.match(r"^(pytest|py.test|python3? -m pytest)(?:\s|$)", command):
            raise ValueError("JUnit guard currently requires one pytest invocation")
        execution_environment += ("rm -f /logs/verifier/junit.xml\n"
                                  "export PYTEST_ADDOPTS=--junitxml=/logs/verifier/junit.xml\n")
        execution_check = """python3 -I - <<'PY'
from pathlib import Path
import xml.etree.ElementTree as ET
report = Path('/logs/verifier/junit.xml')
if not report.is_file():
    raise SystemExit('Test infrastructure failure: missing pytest report')
root = ET.parse(report).getroot()
if not root.findall('.//testcase') or root.findall('.//error') or root.findall('.//skipped'):
    raise SystemExit('Test infrastructure failure: missing, errored or skipped regression')
PY
"""
    if all(re.match(r"^python3? -m unittest(?:\s|$)", line)
           for line in command.splitlines()):
        # Black 1–3 exposed import errors returning 1 just like regressions.
        # Require evidence of execution before assigning an oracle/NOP reward.
        execution_check = """python3 -I - <<'PY'
from pathlib import Path
import re
output = Path('/logs/verifier/test_output.txt').read_text(errors='replace')
if not re.search(r'^Ran [1-9][0-9]* tests? in ', output, re.M) or '_FailedTest' in output:
    raise SystemExit('Test infrastructure failure: unittest did not execute the selected tests')
PY
"""
    if unittest_report:
        unittest_run_script(command)  # Reject unsupported invocation forms.
        execution_check = f"""python3 -I - <<'PY'
import json
from pathlib import Path
report = Path('/logs/verifier/unittest.jsonl')
rows = [json.loads(line) for line in report.read_text().splitlines()] if report.is_file() else []
if len(rows) != {len(command.splitlines())} or any(row['invalid'] or not row['tests_run'] for row in rows):
    raise SystemExit('Test infrastructure failure: missing or invalid unittest execution reports')
PY
"""
    return f"""#!/bin/bash
set -euo pipefail
bash /setup_files/setup.sh
mkdir -p /logs/verifier
echo '{test_hash}  /tests/fixed-tests.tar.gz' | sha256sum -c -
tar -xzf /tests/fixed-tests.tar.gz -C /app
echo '{verifier_lock_hash}  /tests/requirements.lock' | sha256sum -c -
venv_start=$SECONDS
rm -rf /tests/.venv
python3 -m venv /tests/.venv
{install_prelude}\
env -u PYTHONPATH /tests/.venv/bin/python -m pip install {install_flags}--no-deps -r /tests/requirements.lock
echo "verifier venv ready in $((SECONDS - venv_start))s"
cd /app
export PYTHONNOUSERSITE=1
export PYTHONPATH=/app:{python_path}
export PATH=/tests/.venv/bin:$PATH
{pre_test_commands}\
{execution_environment}\
set +e
bash /tests/run_test.sh > /logs/verifier/test_output.txt 2>&1
status=$?
set -e
cat /logs/verifier/test_output.txt
{execution_check}\
if [ "$status" -eq 0 ]; then
  printf '1\\n' > /logs/verifier/reward.txt
  exit 0
fi
if [ "$status" -eq 1 ]; then
  printf '0\\n' > /logs/verifier/reward.txt
  exit 0
fi
echo "Test infrastructure failure: $status" >&2
exit "$status"
"""


def solution_script() -> str:
    """Restore only files changed by the upstream fix; Python slim need not ship Git."""
    return """#!/bin/bash
set -euo pipefail
bash /setup_files/setup.sh
# Keep project dependencies from shadowing this helper's standard-library imports.
python3 -I - <<'PY'
import json
from pathlib import Path, PurePosixPath
import shutil

for name in json.loads(Path('/solution/changed-paths.json').read_text()):
    relative = PurePosixPath(name)
    if relative.is_absolute() or not relative.parts or any(part in ('', '.', '..') for part in relative.parts):
        raise ValueError(f'Unsafe changed path: {name!r}')
    target = Path('/app').joinpath(*relative.parts)
    if target.is_symlink() or target.is_file():
        target.unlink()
    elif target.is_dir():
        shutil.rmtree(target)
PY
tar -xzf /solution/fixed-files.tar.gz -C /app
"""


def reviewed_test_commands(project: str, bug_id: int, fixed: str,
                           commands: list[str]) -> tuple[list[str], list[str]]:
    """Apply documented upstream selector corrections without changing tests."""
    if (project == "ansible" and bug_id == 16
            and fixed == "93d9d640380252084855885ad27873b4377898ec"):
        path = "test/units/module_utils/facts/hardware/test_linux_get_cpu_info.py"
        original = f"pytest {path}::test_get_cpu_info_missing_arch"
        if commands != [original]:
            raise ValueError("Ansible 16 upstream test command changed; review correction")
        # The upstream selector omits architecture, so both endpoints pass.
        # Keep that check and add the existing architecture-aware regression:
        # PowerPC processor/cpu fields otherwise double the reported CPU count.
        return [original + f" {path}::test_get_cpu_info"], [
            "ansible-16: retain upstream missing-architecture test and add upstream "
            "test_get_cpu_info to exercise the PowerPC CPU-count fix"]
    return list(commands), []


# Pandas: historical dependency and native-build recipe, with an isolated verifier.
PANDAS_TOOLCHAIN = ["setuptools==59.8.0", "wheel==0.37.1"]
PANDAS_OPTIONAL = {
    "fastparquet", "gcsfs", "numba", "llvmlite", "thrift", "fsspec",
    "google-auth", "google-auth-oauthlib", "cachetools", "decorator", "requests",
    "requests-oauthlib", "oauthlib", "pyasn1", "pyasn1-modules", "rsa",
    "certifi", "chardet", "idna", "urllib3",
}


def pandas_metadata_commits(repo: Path, info: dict) -> tuple[str, str]:
    revisions = []
    for value in (info["buggy_commit_id"], info["fixed_commit_id"]):
        if not re.fullmatch(r"[0-9a-f]{7,40}", value):
            raise ValueError(f"Invalid metadata commit: {value}")
        revisions.append(git(repo, "rev-parse", "--verify", f"{value}^{{commit}}").decode().strip())
    return tuple(revisions)


def pandas_test_paths(raw: str) -> list[str]:
    # Bugs 36/39/93: canonical separators and a trailing metadata delimiter.
    # Keep the shared traversal/absolute-path checks and reject interior empties.
    return [PurePosixPath(path).as_posix() for path in test_paths(raw.rstrip().rstrip(";"))]


def pandas_inherited_fixtures(repo: Path, buggy: str, fixed: str,
                              paths: list[str], roots: list[str]) -> list[str]:
    fixtures = set()
    for path in paths:
        for parent in PurePosixPath(path).parents:
            candidate = (parent / "conftest.py").as_posix()
            if any(candidate == root or candidate.startswith(root + "/") for root in roots):
                continue
            try:
                after = git(repo, "rev-parse", "--verify", f"{fixed}:{candidate}")
            except subprocess.CalledProcessError:
                continue
            try:
                before = git(repo, "rev-parse", "--verify", f"{buggy}:{candidate}")
            except subprocess.CalledProcessError:
                before = None
            if before != after:
                fixtures.add(candidate)
    return sorted(fixtures)


def pandas_requirements(inventory: Path, paths: list[str]) -> tuple[list[str], dict[str, str]]:
    # Do not install the editable fixed pandas checkout from the pip freeze.
    relevant = {"numpy", "cython", "pytest", "hypothesis", "python-dateutil", "pytz",
                "six", "scipy", "matplotlib", "openpyxl", "xlrd", "xlsxwriter", "xlwt",
                "beautifulsoup4", "html5lib", "lxml", "numexpr", "packaging"}
    pins = {line.split("==")[0].lower(): line.strip()
            for line in read_text_bom(inventory).splitlines()
            if re.fullmatch(r"[\w.-]+==\S+", line.strip())}
    requirements = requirement_seeds([*(pins[name] for name in sorted(relevant & pins.keys())),
                                       "pytest-xdist==1.32.0", *PANDAS_TOOLCHAIN])
    optional = {}
    if any(path.endswith("/test_gcs.py") for path in paths):
        # Bug 149 uses mocked GCS. Its optional dependency closure is pinned
        # explicitly so fastparquet cannot pull a released pandas over /app.
        missing = PANDAS_OPTIONAL - pins.keys()
        if missing:
            raise ValueError(f"Missing optional engine pins: {sorted(missing)}")
        optional = {name: pins[name] for name in sorted(PANDAS_OPTIONAL)}
    return requirements, optional


def pandas_native_commands(commands: list[str]) -> list[str]:
    result = []
    for command in commands:
        if "build_ext" in command:
            command = command.replace("-j 0", "-j 2")
            if "-j" not in command:
                command += " -j 2"
            # Bug 156: shared intermediate objects can race in parallel builds.
            command += " || ( " + command.replace("-j 2", "-j 1") + " )"
        result.append(command)
    return result


def pandas_optional_lock(lock: str, optional: dict[str, str]) -> str:
    if not optional:
        return lock
    locked = {line.split("==")[0].lower(): line for line in lock.splitlines() if line.strip()}
    locked.update(optional)
    return "\n".join(locked[name] for name in sorted(locked)) + "\n"


def pandas_bootstrap(lock: str, verifier: bool) -> str:
    # Both environments build historical fastparquet without build isolation.
    # NumPy/Cython must exist before pip prepares its metadata.
    pins = {line.split("==")[0].lower(): line for line in lock.splitlines()}
    bootstrap = [pins[name] for name in ("numpy", "cython", "setuptools", "wheel")]
    command = ("env -u PYTHONPATH /tests/.venv/bin/python -m pip install --no-deps "
               if verifier else "python3 -m pip install --no-deps --target /app/.deps ")
    prefix = "" if verifier else "export PYTHONPATH=/app/.deps:/app\n"
    return prefix + command + " ".join(map(shlex.quote, bootstrap)) + "\n"


def build_task(metadata_root: Path, repo: Path, project: dict, bug_id: int,
               issue_cache: dict[str, dict], base_image_template: str,
               lock_cache: dict[tuple[str, tuple[str, ...]], str]) -> tuple[str, dict[str, bytes], dict]:
    project_name = project["project"]
    bug_dir = metadata_root / "projects" / project_name / "bugs" / str(bug_id)
    info = read_metadata_file(bug_dir / "bug.info")
    buggy, fixed = info["buggy_commit_id"], info["fixed_commit_id"]
    if project_name == "pandas":
        buggy, fixed = pandas_metadata_commits(repo, info)
    if buggy == fixed:
        # Keras 12: bug.info names one commit for both states and bug_patch.txt
        # is empty, so no reference fix exists. Such a bug is not converted.
        raise ValueError("Discarded: identical buggy and fixed commits, no reference fix")
    if (project_name, bug_id) == ("keras", 15):
        # Keras 15 fixes blank CSVLogger rows caused by Windows line-ending
        # translation. On Linux the unchanged source passes the selected test
        # (NOP reward 1, job 940231), so the task cannot be graded here.
        raise ValueError("Discarded: Windows-only bug, unchanged source passes the test on Linux")
    ensure_commit(repo, buggy)
    ensure_commit(repo, fixed)
    commit_count = int(git(repo, "rev-list", "--count", f"{buggy}..{fixed}").decode())
    if commit_count < 1:
        raise ValueError("Fixed commit has no commits beyond buggy commit")
    python_version = info["python_version"]
    if not re.fullmatch(r"\d+\.\d+\.\d+", python_version):
        raise ValueError(f"Invalid Python version: {python_version}")
    major, minor, _ = python_version.split(".")
    image = base_image_template.format(major=major, minor=minor, version=python_version)
    if project_name == "tornado" and (major, minor) == ("3", "7"):
        # Preserve the recorded interpreter minor; Bookworm supports the
        # cluster's fakeroot libraries, unlike the default Bullseye image.
        image = "python:3.7.17-bookworm"
    if project_name == "black" and (major, minor) == ("3", "8"):
        # All reviewed Black bugs target 3.8; Bookworm supports host fakeroot.
        image = "python:3.8-slim-bookworm"
    if project_name == "scrapy" and (major, minor) == ("3", "8"):
        # Twisted 20.3.0 builds a small C extension; slim images lack gcc.
        image = "python:3.8-bookworm"
    if project_name == "fastapi" and (major, minor) == ("3", "8"):
        # All retained FastAPI bugs target 3.8; Bookworm supports host fakeroot.
        image = "python:3.8-slim-bookworm"
    if project_name in {"cookiecutter", "httpie", "luigi", "matplotlib", "sanic"}:
        # Cookiecutter 1–4, HTTPie 1–5, Luigi 1–33, Matplotlib 1–30: support host fakeroot.
        image = "python:3.8-slim-bookworm"
    if project_name == "tqdm":
        # tqdm 1–9 record Python 3.6.9. The 3.6 images are Bullseye and cannot
        # start under Helma's fakeroot helper (job 943813); 3.7 is the nearest
        # minor with a Bookworm image.
        image = "python:3.7-slim-bookworm"
    if project_name == "spacy":
        # spaCy 1–10 compile Cython extensions. Keep each task's metadata Python
        # minor and use the full official image for its compiler toolchain. The
        # Python 3.7 Bullseye image cannot start under Helma's fakeroot helper;
        # the final 3.7 patch release is available on Bookworm with newer glibc.
        image = "python:3.7.17-bookworm" if minor == "7" else f"python:{major}.{minor}-bookworm"
    # Some BugsInPy project metadata points at setup.py's build/lib output.
    # When the buggy source itself has importable packages in lib/, add the
    # editable source location instead of installing a detached copy.
    source_roots = ["/app"]
    metadata_pythonpath = info.get("pythonpath", "").strip().rstrip("/")
    if metadata_pythonpath.endswith("/build/lib"):
        lib_paths = git(repo, "ls-tree", "-r", "--name-only", buggy, "--", "lib").decode().splitlines()
        if any(path.startswith("lib/") and path.endswith("/__init__.py") for path in lib_paths):
            source_roots.insert(0, "/app/lib")
    shared_rules = project_name not in VALIDATED_RECIPES and project_name != "pandas"
    # setup.py may keep its packages in a subdirectory (Matplotlib: lib/).
    # Import them from there, as an editable install would.
    package_root = package_dir_root(repo, buggy) if shared_rules else None
    if package_root and "/app/" + package_root not in source_roots:
        source_roots.insert(0, "/app/" + package_root)
    python_path = ":".join(source_roots)
    dockerfile = (f"FROM {image}\n"
                  f"ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app/.deps:{python_path}\n"
                  "WORKDIR /app\n").encode()
    # A project with compiled extensions is built in its source tree, which
    # needs a compiler in the image.
    builds_extensions = shared_rules and setup_keyword(repo, buggy, "ext_modules") is not None
    if builds_extensions:
        # The full official Python images used for spaCy already include the
        # compiler toolchain; other source builds need these explicit packages.
        native_packages = [] if project_name == "spacy" else ["build-essential", "pkg-config"]
        if project_name == "matplotlib":
            # Matplotlib 1–30 compile against FreeType and libpng headers and
            # draw without a display.
            native_packages += ["libfreetype6-dev", "libpng-dev"]
            dockerfile += b"ENV MPLBACKEND=Agg\n"
        if native_packages:
            dockerfile += ("RUN apt-get update && apt-get install -y --no-install-recommends "
                           + " ".join(native_packages) + " && rm -rf /var/lib/apt/lists/*\n").encode()
    if project_name == "pandas":
        image = "python:3.8-slim-bookworm"
        dockerfile = (f"FROM {image}\n"
                      "RUN apt-get update && apt-get install -y --no-install-recommends build-essential "
                      "&& rm -rf /var/lib/apt/lists/*\n"
                      "RUN python3 -m pip install --no-cache-dir setuptools==59.8.0 wheel==0.37.1\n"
                      "ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app/.deps:/app CFLAGS=-Wno-error=array-bounds\n"
                      "WORKDIR /app\n").encode()
    source = compressed(git(repo, "archive", "--format=tar", buggy))
    paths = (pandas_test_paths(info["test_file"]) if project_name == "pandas"
             else test_paths(info["test_file"]))
    for path in paths:
        git(repo, "cat-file", "-e", f"{fixed}:{path}")
    roots = test_roots(paths)
    inherited_fixtures = (pandas_inherited_fixtures(repo, buggy, fixed, paths, roots)
                          if project_name == "pandas" else [])
    if inherited_fixtures:
        roots = sorted(set(roots) | set(inherited_fixtures))
    fixed_tests = compressed(git(repo, "archive", "--format=tar", fixed, *roots))
    verifier_test_corrections = []
    if project_name == "scrapy":
        fixed_tests, verifier_test_corrections = scrapy_bug33_verifier_tests(
            fixed_tests, bug_id)
    if project_name == "youtube-dl":
        fixed_tests, verifier_test_corrections = youtube_dl_verifier_tests(fixed_tests, bug_id)
    test_lines = [line.strip() for line in (bug_dir / "run_test.sh").read_text().splitlines()
                  if line.strip()]
    if not test_lines:
        raise ValueError("Empty run_test.sh")
    if any(";" in line for line in test_lines):
        # Matplotlib 8 joins two pytest commands with ';'. Each becomes its own
        # line; run_test.sh stops at the first failing one.
        if any(quote in line for line in test_lines for quote in "'\""):
            raise ValueError("Quoted test command with ';' needs review")
        test_lines = [command.strip() for line in test_lines
                      for command in line.split(";") if command.strip()]
    for line in test_lines:
        words = shlex.split(line)
        if not words or words[0] not in ("pytest", "py.test", "tox", "python", "python3"):
            raise ValueError(f"Unsupported test command: {line!r}")
        if any(token in (";", "&&", "||", "|", ">", "<", "&") for token in words):
            raise ValueError(f"Shell operators in test command need review: {line!r}")
    if project_name == "cookiecutter":
        # The reviewed Cookiecutter tox [testenv] delegates selectors to pytest.
        # Run those same selectors in the prepared interpreter, without tox's
        # unrelated lint or interpreter-matrix environments.
        tox_config = configparser.ConfigParser(interpolation=None)
        tox_config.read_string(git(repo, "show", f"{buggy}:tox.ini").decode())
        tox_commands = [line.strip() for line in
                        tox_config.get("testenv", "commands", fallback="").splitlines()]
        if not any(command in tox_commands for command in (
            "pytest --cov=cookiecutter {posargs:tests}",
            "py.test --cov=cookiecutter {posargs:tests}",
        )):
            raise ValueError("Unrecognized upstream Cookiecutter tox testenv")
        test_lines = [
            "python3 -m pytest --cov=cookiecutter " + line[len("tox "):].strip()
            if line.startswith("tox ") else line
            for line in test_lines
        ]
    upstream_test_command = "\n".join(test_lines)
    test_lines, test_command_corrections = reviewed_test_commands(
        project_name, bug_id, fixed, test_lines)
    raw_test_command = "\n".join(test_lines)
    reference_patch = git(repo, "diff", "--binary", buggy, fixed)
    if not reference_patch:
        raise ValueError("Fixing commit produces an empty diff")
    changed = [path for path in git(repo, "diff", "--no-renames", "--name-only", "-z",
                                     buggy, fixed).decode().split("\0") if path]
    fixed_paths = []
    for path in changed:
        if subprocess.run(["git", "-C", str(repo), "cat-file", "-e", f"{fixed}:{path}"],
                          capture_output=True).returncode == 0:
            fixed_paths.append(path)
    if fixed_paths:
        fixed_files = compressed(git(repo, "archive", "--format=tar", fixed, *fixed_paths))
    else:
        empty_tar = io.BytesIO()
        with tarfile.open(fileobj=empty_tar, mode="w"):
            pass
        fixed_files = compressed(empty_tar.getvalue())
    added_lines = {re.sub(r"\s+", "", line[1:]) for line in reference_patch.decode(errors="replace").splitlines()
                   if line.startswith("+") and not line.startswith("+++")}
    issue, issue_error = issue_for_fix_range(repo, project["repo"], buggy, fixed, issue_cache)
    instruction, instruction_report = instruction_from_issue(project_name, issue, issue_error, added_lines)
    non_utf8_default = needs_non_utf8_default(issue, reference_patch, fixed_tests)
    if project_name in {"black", "cookiecutter"}:
        # These reviewed projects document the encoding prerequisite in the
        # fixing commit. Require matching UTF-8 fix and non-ASCII test evidence.
        encoding_report = {"title": "", "body": git(repo, "show", "-s", "--format=%B", fixed).decode()}
        non_utf8_default = (non_utf8_default
                           or needs_non_utf8_default(encoding_report, reference_patch, fixed_tests))
    commands, setup_warnings, setup_requirements = setup_recipe(bug_dir / "setup.sh")
    project_runtime, extra_warnings = pyproject_runtime_requirements(repo, buggy)
    setup_warnings.extend(extra_warnings)
    # Historical pandas builds setup(**setuptools_kwargs) dynamically. Its
    # reviewed inventory recipe below supplies the test/runtime pins without
    # evaluating setup.py or applying the generic dependency parser to it.
    test_requirements, extra_warnings = (([], []) if project_name == "pandas"
                                         else project_test_requirements(repo, buggy))
    setup_warnings.extend(extra_warnings)
    fixed_test_extras, extra_warnings = (([], []) if project_name == "pandas"
                                        else project_test_requirements(repo, fixed))
    setup_warnings.extend(extra_warnings)
    file_requirements, fixed_file_tests, extra_warnings, fixed_runtime_added = project_requirement_files(repo, buggy, fixed)
    setup_warnings.extend(extra_warnings)
    import_requirements, extra_warnings, fixed_test_imports = fixed_test_import_requirements(fixed_tests)
    setup_warnings.extend(extra_warnings)
    fastapi_form_requirements = (
        fastapi_form_test_requirements(fixed_tests, paths, bug_dir / "requirements.txt")
        if project_name == "fastapi" else []
    )
    if (bug_dir / "requirements.txt").is_file():
        contents = read_text_bom(bug_dir / "requirements.txt")
        if any(line.strip() and not line.lstrip().startswith("#") for line in contents.splitlines()):
            setup_warnings.append("Bug has additional requirements.txt; inspect before runtime validation")
    requirements = requirement_seeds(["pytest<8", *setup_requirements, *project_runtime,
                                      *test_requirements, *file_requirements,
                                      *fastapi_form_requirements])
    if project_name == "fastapi":
        # Match the BugInPy inventory only where its exact pin satisfies the
        # project's declared runtime or test-extra constraint.
        from packaging.requirements import Requirement
        inventory_pins = {
            Requirement(raw.strip()).name.lower().replace("_", "-").replace(".", "-"): raw.strip()
            for raw in read_text_bom(bug_dir / "requirements.txt").splitlines()
            if re.match(r"^[\w.-]+==", raw.strip())
        }
        compatible_pins = []
        for raw in [*project_runtime, *test_requirements]:
            requirement = Requirement(raw)
            pin = inventory_pins.get(requirement.name.lower().replace("_", "-").replace(".", "-"))
            if not pin:
                continue
            version = pin.split("==", 1)[1]
            if requirement.specifier.contains(version, prereleases=True):
                marker = f"; {requirement.marker}" if requirement.marker else ""
                compatible_pins.append(pin + marker)
        requirements = requirement_seeds([*requirements, *compatible_pins])
    if project_name == "black":
        # Black 1 exposes missing setup.py runtime dependencies and SCM-generated
        # version metadata. Use original dependency pins, never the inventory's
        # editable Black checkout (which would replace the task source).
        runtime = [*literal_runtime_requirements(repo, buggy),
                   *black_test_server_requirements(repo, buggy)]
        inventory_pins = {r.split("==")[0].lower().replace("_", "-"): r.strip()
                          for r in read_text_bom(bug_dir / "requirements.txt").splitlines()
                          if re.match(r"^[\w.-]+==", r)}
        pins = []
        for requirement in runtime:
            name = re.match(r"[\w.-]+", requirement)[0].lower().replace("_", "-")
            if name in inventory_pins:
                # Black 1–3: preserve the Python<3.7 marker on dataclasses.
                # An unconditional inventory pin shadows the 3.8 stdlib module.
                marker = ";" + requirement.split(";", 1)[1] if ";" in requirement else ""
                pins.append(inventory_pins[name] + marker)
        requirements = requirement_seeds([*requirements, *runtime, *pins])
    scm_requirements, scm_commands, scm_metadata = scm_version_setup(repo, buggy, python_version)
    requirements = requirement_seeds([*requirements, *scm_requirements])
    if project_name == "cookiecutter":
        # Install this bug's declared runtime dependencies and exact inventory
        # pins. The inventory's editable project checkout is intentionally
        # omitted because the task already supplies its buggy source in /app.
        runtime = literal_runtime_requirements(repo, buggy)
        inventory = []
        for raw in read_text_bom(bug_dir / "requirements.txt").splitlines():
            requirement = raw.strip()
            match = re.match(r"^([\w.-]+)==", requirement)
            if match and match[1].lower().replace("_", "-").replace(".", "-") != "pkg-resources":
                inventory.append(requirement)

        # Cookiecutter 4 appends ruamel.yaml only on Python 3. Bound it to the
        # legacy API used by this revision while preserving its declared floor.
        setup_tree = ast.parse(git(repo, "show", f"{buggy}:setup.py").decode())
        for node in ast.walk(setup_tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "append" and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "requirements" and len(node.args) == 1):
                try:
                    appended = ast.literal_eval(node.args[0])
                except (ValueError, TypeError, SyntaxError):
                    continue
                if isinstance(appended, str) and appended.lower().startswith("ruamel.yaml"):
                    runtime.append(appended + ",<0.17")

        requirements = requirement_seeds([*requirements, *runtime, *inventory,
                                         "pytest<6", "pytest-mock<3", "pytest-cov<3"])
    if project_name == "httpie":
        # HTTPie 1–5: declared runtime dependencies plus the bug's inventory
        # pins. Bug 1 keeps its recorded pytest 3.2.1; its conftest redecorates
        # a pytest-httpbin fixture, which pytest >=3.6 rejects.
        runtime = literal_runtime_requirements(repo, buggy)
        requirements = requirement_seeds([
            *requirements, *runtime,
            *inventory_requirements(bug_dir / "requirements.txt", runtime)])
    if project_name == "spacy":
        # These spaCy revisions predate Cython 3; bugs 6/9 use pytest.warns(None),
        # removed in pytest 6. Keep the historical build/test APIs compatible.
        # The old native dependency constraints admit current releases whose
        # source builds require Cython 3.1 (which no longer supports Python 3.7).
        # When a bug inventory has no pin, use the mutually compatible versions
        # recorded by BugsInPy bug 2; actual per-bug inventory pins still win.
        spacy_compatibility = {
            "blis": "blis==0.4.1",
            "catalogue": "catalogue==1.0.0",
            "cymem": "cymem==2.0.3",
            "murmurhash": "murmurhash==1.0.2",
            "preshed": "preshed==3.0.2",
            "srsly": "srsly==1.0.2",
            "wasabi": "wasabi==0.6.0",
        }
        inventory_names = {
            match[1].lower().replace("_", "-")
            for raw in read_text_bom(bug_dir / "requirements.txt").splitlines()
            if (match := re.match(r"^([\w.-]+)==", raw.strip()))
        }
        spacy_fallbacks = [pin for name, pin in spacy_compatibility.items()
                           if name not in inventory_names]
        requirements = requirement_seeds([
            *requirements, *spacy_fallbacks,
            # catalogue 1.x calls entry_points().get(); importlib-metadata 5
            # changed that return value to EntryPoints, breaking this API.
            "importlib-metadata==4.13.0",
            "cython==0.29.36", "pytest<6", "setuptools==46.4.0"])
        dependency_discovery = {
            "spacy_compatibility_fallbacks": spacy_fallbacks,
            "spacy_compatibility_source": "BugsInPy bug 2 requirements inventory",
            "importlib_metadata_pin": "4.13.0 for catalogue 1.x legacy entry_points API",
        }
    dependency_discovery = {}
    if project_name == "sanic":
        runtime = literal_runtime_requirements(repo, buggy)
        discovered, evidence, extra_warnings = reachable_import_requirements(
            source, fixed_tests, paths, bug_dir / "requirements.txt")
        setup_warnings.extend(extra_warnings)
        # Reviewed compatibility exceptions from the passing overnight tasks:
        # uvloop is absent from Windows inventories; modern setuptools plugin
        # autoload breaks this historical pytest. Neither changes task source.
        requirements = requirement_seeds([
            *requirements, *runtime, *discovered, "uvloop==0.14.0", "setuptools==46.4.0"])
        if bug_id == 2:
            if raw_test_command != "pytest tests/test_app.py::test_asyncio_server_start_serving":
                raise ValueError("Sanic 2 selector changed; review no-uvloop prerequisite")
            requirements = [r for r in requirements if not r.startswith("uvloop")]
            fixed_test_extras = [r for r in fixed_test_extras if not r.startswith("uvloop")]
            setup_warnings.append("Sanic 2: omit uvloop so the asyncio regression executes its assertions")
        pins, extra_warnings = compatible_inventory_pins(
            requirements, bug_dir / "requirements.txt", python_version)
        requirements = requirement_seeds([*requirements, *pins])
        setup_warnings.extend(extra_warnings)
        dependency_discovery = {"import_evidence": evidence, "inventory_pins": pins}
    if project_name == "luigi":
        # Roll out shared discovery on Luigi first; other audited projects keep
        # their existing recipes until independently regenerated and validated.
        runtime = literal_runtime_requirements(repo, buggy)
        discovered, evidence, extra_warnings = reachable_import_requirements(
            source, fixed_tests, paths, bug_dir / "requirements.txt")
        setup_warnings.extend(extra_warnings)
        requirements = requirement_seeds([*requirements, *runtime, *discovered])
        # Explicit upstream-metadata exception: Luigi 33 unconditionally
        # declares Python-2-only snakebite, unused by its parameter regression.
        # Keep this bounded to the reviewed bug and selected test file.
        if bug_id == 33 and major == "3" and paths == ["test/parameter_test.py"]:
            requirements = [r for r in requirements if re.match(r"^[\w.-]+", r)[0] != "snakebite"]
            setup_warnings.append("Luigi 33: omit optional Python-2-only snakebite for parameter tests")
        pins, extra_warnings = compatible_inventory_pins(
            requirements, bug_dir / "requirements.txt", python_version)
        setup_warnings.extend(extra_warnings)
        requirements = requirement_seeds([*requirements, *pins])
        dependency_discovery = {"import_evidence": evidence, "inventory_pins": pins}
    if project_name == "keras":
        # Keras 1–45: the tests need the TensorFlow 1.x environment recorded in
        # the bug's inventory. Keras itself is left out; the task supplies the
        # buggy source in /app.
        inventory = []
        for raw in read_text_bom(bug_dir / "requirements.txt").splitlines():
            match = re.match(r"^([\w.-]+)==(\S+)$", raw.strip())
            if not match or match[1].lower() == "keras":
                continue
            name, version = match[1].lower().replace("_", "-"), match[2]
            # 34 inventories record release candidates that PyPI no longer
            # serves; use the final release of the same version.
            if (name, version) in {("numpy", "1.19.0rc2"), ("scipy", "1.5.0rc1")}:
                version = version.split("rc")[0]
            inventory.append(f"{name}=={version}")
        # Older setup.py files pin keras-preprocessing and keras-applications
        # below what the recorded TensorFlow requires; the inventory wins.
        runtime = [requirement for requirement in literal_runtime_requirements(repo, buggy)
                   if re.match(r"^[\w.-]+", requirement)[0].lower().replace("_", "-")
                   not in {"keras-preprocessing", "keras-applications"}]
        # Test imports that only some inventories record. scikit-learn and
        # pydot appear in none; their versions match the inventory's date.
        requirements = requirement_seeds([
            *requirements, *runtime, *inventory, "scikit-learn==0.23.2",
            "Pillow==7.1.2", "pydot==1.4.1", "mock==4.0.2", "flaky==3.6.1"])
    exclude_newer = None
    if shared_rules:
        # Declared runtime dependencies, plus what the selected tests always
        # import, pinned to the bug's recorded inventory where one exists.
        try:
            runtime = literal_runtime_requirements(repo, buggy)
        except ValueError as exc:
            runtime = []
            setup_warnings.append(f"setup.py dependencies not read: {exc}")
        discovered, evidence = unconditional_import_requirements(
            source, fixed_tests, paths, [root[len("/app/"):] or "." for root in source_roots])
        requirements = requirement_seeds([*requirements, *runtime, *discovered])
        pins, extra_warnings = compatible_inventory_pins(
            requirements, bug_dir / "requirements.txt", python_version)
        setup_warnings.extend(extra_warnings)
        requirements = requirement_seeds([*requirements, *pins])
        dependency_discovery = {"import_evidence": evidence, "inventory_pins": pins}
        if project_name == "scrapy":
            requirements, scrapy_compatibility = scrapy_compatibility_requirements(
                requirements, bug_dir / "requirements.txt")
            dependency_discovery["scrapy_compatibility"] = scrapy_compatibility
        if not pins and project_name not in {"spacy", "tornado"}:
            # No recorded versions (Matplotlib 1–30 have an empty inventory):
            # resolve with the releases that existed at the buggy commit, but
            # not before the task's Python version was released.
            committed = git(repo, "show", "-s", "--format=%cs", buggy).decode().strip()
            exclude_newer = max(committed, PYTHON_RELEASED[python_version])
            dependency_discovery["exclude_newer"] = exclude_newer
        elif project_name == "spacy" and not pins:
            # Cython 0.29.36 is the reviewed compatibility pin for old spaCy;
            # it postdates the source commit, so an inventory-date cutoff
            # would make this task's otherwise compatible lock unsatisfiable.
            dependency_discovery["exclude_newer_skipped"] = (
                "historical Cython compatibility pin 0.29.36")
        if project_name == "tornado":
            # The recorded inventory contains only the project itself. A
            # commit-date cutoff selects python-gettext sdists whose build
            # backend requirements cannot resolve under that same cutoff.
            # Retain the overnight recipe: resolve external dependencies for
            # Python 3.7 without imposing an undocumented inventory date.
            dependency_discovery["exclude_newer_skipped"] = (
                "Tornado inventory has no external dependency pins; historical "
                "python-gettext build requirements conflict with commit-date cutoff")
        if builds_extensions and not any("build_ext" in command for command in commands):
            # Matplotlib 30 and older read MPLLOCALFREETYPE to build the bundled
            # FreeType that upstream image comparisons expect.
            commands.append("cd /app && " + ("MPLLOCALFREETYPE=1 " if project_name == "matplotlib" else "")
                            + "python3 setup.py build_ext --inplace")
        # An install compiles the sources once; later imports then skip
        # compile-time warnings that pytest would otherwise turn into errors
        # (Matplotlib 1 and 2: invalid escape in a widgets.py docstring).
        tracked = git(repo, "ls-tree", "-r", "--name-only", buggy).decode().splitlines()
        packages = sorted({root + "/" + PurePosixPath(path).relative_to(root[len("/app/"):] or ".").parts[0]
                           for root in source_roots for path in tracked
                           if path.endswith("/__init__.py")
                           and PurePosixPath(path).is_relative_to(root[len("/app/"):] or ".")
                           and len(PurePosixPath(path).relative_to(root[len("/app/"):] or ".").parts) == 2})
        if packages:
            commands.append("python3 -m compileall -q " + " ".join(map(shlex.quote, packages)) + " || true")
    pandas_optional = {}
    if project_name == "pandas":
        requirements, pandas_optional = pandas_requirements(bug_dir / "requirements.txt", paths)
        commands = pandas_native_commands(commands)
    commands.extend(scm_commands)
    if project_name == "tornado":
        # Older upstream HTTP fixtures bind/connect by hostname. The isolated
        # OCI image has an empty /etc/hosts, so localhost otherwise hits DNS.
        # Provision the environment identically for oracle, NOP and agents.
        commands.insert(0, loopback_hosts_setup())
    verifier_candidates = requirement_seeds([*fixed_test_extras, *fixed_file_tests,
                                             *import_requirements])
    declared_modules = {re.match(r"^[A-Za-z0-9][A-Za-z0-9_.-]*", item)[0].replace("-", "_")
                        for item in [*requirements, *verifier_candidates]}
    undeclared_test_imports = sorted(set(fixed_test_imports) - declared_modules
                                     - local_modules_in_archives(source, fixed_tests)
                                     - set(sys.stdlib_module_names))
    if undeclared_test_imports:
        setup_warnings.append("Undeclared fixed-test imports need dependency review: "
                              + ", ".join(undeclared_test_imports))
    # The verifier gets its own complete lock: project/task runtime dependencies
    # plus verifier dependencies. This keeps test imports independent of the
    # agent's mutable /app/.deps while still exposing the shared source tree.
    verifier_requirements = requirement_seeds([*requirements, *verifier_candidates])
    if project_name == "tornado":
        # Upstream setup.sh asks pip for stdlib unittest (all 16 bugs) and
        # sometimes Tornado itself. Filter both complete seed sets so private
        # test dependencies cannot reintroduce a released project copy.
        requirements, setup_excluded = tornado_dependency_seeds(requirements)
        verifier_requirements, verifier_excluded = tornado_dependency_seeds(verifier_requirements)
        dependency_discovery["excluded_dependency_seeds"] = {
            "setup": setup_excluded, "verifier": verifier_excluded}
    if project_name == "sanic":
        # Package/version recovery: original requests-async 0.5.0 release
        # archive, used by Sanic 4 and no longer served by PyPI overnight.
        source_fallback = {
            "requests-async==0.5.0": "requests-async @ https://github.com/encode/requests-async/archive/23c21e05721d1bc6fbd270727782a495ae4ca228.tar.gz"}
        requirements = [source_fallback.get(r, r) for r in requirements]
        verifier_requirements = [source_fallback.get(r, r) for r in verifier_requirements]
    lock_python_version = {"cookiecutter": "3.8.20", "tqdm": "3.7.17"}.get(project_name, python_version)
    # Keras 1–45 pin absl-py 0.9.0, which ships no wheel and whose setup.py
    # fails on Python >=3.10. uv builds its metadata with its own Python 3.8.
    build_python = "3.8" if project_name == "keras" else None
    # Keras 21–23 record keras-applications 1.0.7 next to tensorflow 1.15.0,
    # which declares >=1.0.8, but their source needs set_keras_submodules,
    # removed in 1.0.8. The recorded pin overrides the declared minimum.
    overrides = tuple(pin for pin in requirements
                      if project_name == "keras" and pin.startswith("keras-applications=="))
    lock = compile_lock(requirements, lock_python_version, lock_cache, build_python, overrides,
                        exclude_newer)
    verifier_lock = compile_lock(verifier_requirements, lock_python_version, lock_cache,
                                 build_python, overrides, exclude_newer)
    if dependency_discovery and re.search(r"(?m)^psutil==", lock + "\n" + verifier_lock):
        # Package-level build prerequisite, independent of project/bug IDs:
        # historical psutil releases may have no wheel for the target Python.
        # Official Python images already supply matching Python headers.
        dockerfile += ("RUN apt-get update && apt-get install -y --no-install-recommends "
                       "gcc libc6-dev && rm -rf /var/lib/apt/lists/*\n").encode()
        dependency_discovery["native_build_packages"] = ["gcc", "libc6-dev"]
    if project_name == "pandas":
        lock = pandas_optional_lock(lock, pandas_optional)
        verifier_lock = pandas_optional_lock(verifier_lock, pandas_optional)
        requirements = requirement_seeds([*requirements, *pandas_optional.values()])
        verifier_requirements = requirement_seeds([*verifier_requirements, *pandas_optional.values()])
    source_hash, test_hash = sha256(source), sha256(fixed_tests)
    lock_hash = sha256(lock.encode())
    verifier_lock_hash = sha256(verifier_lock.encode())
    files = {
        "environment/Dockerfile": dockerfile,
        "instruction.md": instruction.encode(),
        "setup_files/project.tar.gz": source,
        "setup_files/setup.sh": setup_script(
            source_hash, lock_hash, commands, python_path,
            install_prelude=pandas_bootstrap(lock, False) if pandas_optional else "",
            install_flags="--upgrade --no-build-isolation " if pandas_optional else "").encode(),
        "setup_files/requirements.lock": lock.encode(),
        "tests/run_test.sh": ("#!/bin/bash\nset -e\n" + raw_test_command + "\n").encode(),
        "tests/fixed-tests.tar.gz": fixed_tests,
        "tests/test.sh": test_script(raw_test_command, test_hash, verifier_lock_hash, python_path,
                                     pytest_report=project_name in {"sanic", "spacy"},
                                     local_http=project_name in {"sanic", "spacy", "tornado"},
                                     unittest_report=project_name in {"tornado", "youtube-dl"},
                                     install_prelude=pandas_bootstrap(verifier_lock, True) if pandas_optional else "",
                                     install_flags="--no-build-isolation " if pandas_optional else "",
                                     pre_test_commands=("python3 setup.py build_ext --inplace -j 2 || "
                                                        "python3 setup.py build_ext --inplace -j 1\n")
                                     if project_name == "pandas" else
                                     "python3 setup.py build_ext --inplace > /logs/verifier/build.txt 2>&1\n"
                                     if project_name == "spacy" else "").encode(),
        "solution/solve.sh": solution_script().encode(),
        "solution/changed-paths.json": (json.dumps(changed) + "\n").encode(),
        "solution/fixed-files.tar.gz": fixed_files,
        "solution/reference.patch": reference_patch,
        "task.toml": ('''version = "1.0"\n[metadata]\nsetup_command = "bash /setup_files/setup.sh"\n[agent]\ntimeout_sec = 900.0\n[verifier]\ntimeout_sec = 300.0\n[environment]\nstorage_mb = 4096\n'''
                      + ('''[environment.env]\nLC_ALL = "C"\nPYTHONUTF8 = "0"\nPYTHONCOERCECLOCALE = "0"\n'''
                         if non_utf8_default else "")).encode(),
    }
    if any("compileall" in command for command in commands):
        # A cached .pyc is reused while the source keeps its size and mtime.
        # tqdm 1: the fixed file matches the buggy one in both, so the oracle
        # extracts with the current time instead of the archive's.
        files["solution/solve.sh"] = files["solution/solve.sh"].replace(
            b"tar -xzf /solution/fixed-files.tar.gz", b"tar -xzmf /solution/fixed-files.tar.gz")
    if project_name == "pandas":
        # Archive mtimes predate the initial native build; the restored oracle
        # sources must be newer so Cython does not reuse buggy extensions.
        files["solution/solve.sh"] += b"""python3 - <<'PYMTIME'
import json
import os
from pathlib import Path
for name in json.loads(Path('/solution/changed-paths.json').read_text()):
    path = Path('/app') / name
    if path.is_file():
        os.utime(path, None)
PYMTIME
"""
        files["task.toml"] = files["task.toml"].replace(b"900.0", b"3600.0").replace(
            b"300.0", b"3600.0").replace(b"storage_mb = 4096",
            b"storage_mb = 12288\nbuild_timeout_sec = 3600.0\ncpus = 2\nmemory_mb = 8192")
    files["tests/requirements.lock"] = verifier_lock.encode()
    if project_name in {"tornado", "youtube-dl"}:
        files["tests/run_unittest.py"] = unittest_reporter().encode()
        files["tests/run_test.sh"] = unittest_run_script(raw_test_command).encode()
    if project_name in {"sanic", "tornado", "youtube-dl"}:
        files["task.toml"] = files["task.toml"].replace(
            b"storage_mb = 4096", b"storage_mb = 4096\nallow_internet = true")
    if project_name == "spacy":
        # spaCy's native extensions are rebuilt after the oracle/NOP source is
        # in place; allow the selected test environment to install/download.
        files["task.toml"] = files["task.toml"].replace(
            b"storage_mb = 4096", b"storage_mb = 4096\nallow_internet = true")
        files["task.toml"] = files["task.toml"].replace(
            b"timeout_sec = 300.0", b"timeout_sec = 1800.0")
    name = f"bugsinpy-original-{project_name.lower()}-{bug_id}"
    record = {"task_id": name, "project": project_name, "bug_id": bug_id,
              "buggy_commit": buggy, "fixed_commit": fixed, "python_version_metadata": python_version,
              "commits_in_fix_range": commit_count,
              "reference_review": ("needs_review: endpoint diff spans multiple commits"
                                   if commit_count > 1 else "single_commit"),
              "base_image": image, "dockerfile_sha256": sha256(dockerfile),
              "source_pythonpath": python_path,
              "source_sha256": source_hash, "test_files": paths, "test_roots": roots,
              "test_command": raw_test_command,
              "upstream_test_command": upstream_test_command,
              "test_command_corrections": test_command_corrections,
              "verifier_test_corrections": verifier_test_corrections,
              "fixed_commit_changed_paths": changed, "setup_commands": commands,
              "scm_version_metadata": scm_metadata,
              "setup_requirement_seeds": sorted(set(requirements)),
              "dependency_discovery": dependency_discovery,
              "project_file_requirements": file_requirements,
              "fixed_test_file_requirements": fixed_file_tests,
              "verifier_requirement_seeds": verifier_requirements,
              "verifier_requirements_lock_sha256": verifier_lock_hash,
              "fixed_runtime_requirements_added": fixed_runtime_added,
              "fixed_test_import_requirements": import_requirements,
              "undeclared_fixed_test_imports": undeclared_test_imports,
              "non_utf8_default_for_encoding_regression": non_utf8_default,
              "requirements_lock_sha256": lock_hash,
              "setup_warnings": setup_warnings, "instruction_review": instruction_report}
    if project_name == "pandas":
        record["inherited_fixture_files"] = inherited_fixtures
    return name, files, record


def render(root: Path, name: str, files: dict[str, bytes]) -> None:
    task = root / name
    for relative, contents in files.items():
        path = task / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.read_bytes() != contents:
            raise ValueError(f"Refusing to overwrite differing task file: {path}")
        path.write_bytes(contents)
        if relative.endswith(".sh"):
            path.chmod(0o755)


def task_exclusion(project: str, bug_id: int) -> dict | None:
    if project == "youtube-dl" and bug_id == 40:
        return {"category": "environment_task_mismatch", "reason": (
            "Recorded Python 3.7 cannot exercise the older-Python struct Unicode-format "
            "compatibility bug. The selected test checks the helper interface, but an "
            "incomplete compatibility fix also passes. Excluded by review, not a NOP false positive.")}
    return None


def write_project(output: Path, rows: list[dict], manifest: list[dict], errors: list[dict],
                  cache: dict[str, dict], source_revision: str, project: dict,
                  archived: list[dict] = (), exclusions: list[dict] = (),
                  metadata_url: str = 'https://github.com/soarsmu/BugsInPy') -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    output.mkdir(parents=True, exist_ok=True)
    target = output / "tasks.parquet"
    if target.exists():
        raise FileExistsError(f"Refusing to overwrite existing candidates: {target}")
    table = pa.Table.from_pylist(rows, schema=pa.schema([("path", pa.string()), ("task_binary", pa.binary())]))
    pq.write_table(table, target, compression="zstd")
    if archived:
        pq.write_table(pa.Table.from_pylist(archived), output / "archive.parquet", compression="zstd")
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from data.utils.patch_reporting import write_conversion_report
    write_conversion_report(output, rows, archived, patcher=__file__,
        source={'dataset': 'BugsInPy/' + project['project'], 'revision': source_revision,
                'url': metadata_url.rstrip('/') + '/tree/' + source_revision,
                'task_count': len(project['bug_ids'])},
        records={entry['task_id']: entry for entry in
                 [*manifest, *(entry['task_manifest'] for entry in exclusions)]}, errors=errors)
    report = {"bugsinpy_revision": source_revision, "project_repo": project["repo"],
              "candidate_count": len(rows), "error_count": len(errors),
              "excluded_count": len(exclusions), "exclusions": list(exclusions),
              "unique_dockerfiles": len({item["dockerfile_sha256"] for item in manifest}),
              "tasks": manifest, "errors": errors}
    (output / "manifest.json").write_text(json.dumps(report, indent=2) + "\n")
    (output / "issues.json").write_text(json.dumps(cache, indent=2, sort_keys=True) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True,
                        help="Root for per-project candidate Parquets and reports")
    parser.add_argument("--source-cache", type=Path, default=Path(__file__).parent / ".source")
    parser.add_argument("--metadata-url", default="https://github.com/soarsmu/BugsInPy",
                        help="Original BugsInPy Git repository")
    parser.add_argument("--metadata-revision", help="Optional 40-character commit to check out")
    parser.add_argument("--start-at-project", help="Start here when continuing the project-by-project audit")
    parser.add_argument("--stop-after-project", help="Include this project and stop")
    parser.add_argument("--base-image-template", default="python:{major}.{minor}-slim",
                        help="Shared image by Python version; fields: major, minor, version")
    parser.add_argument("--render-dir", type=Path, help="Optional folder for inspectable Harbor tasks")
    parser.add_argument("--issue-cache", type=Path, help="Optional saved issue responses to reuse")
    args = parser.parse_args()
    try:
        import pyarrow  # noqa: F401 - fail before writing any rendered tasks
    except ImportError as exc:
        parser.error(f"pyarrow is required to write Harbor Parquet: {exc}")
    metadata_root = checkout(args.source_cache / "BugsInPy", args.metadata_url, args.metadata_revision)
    source_revision = git(metadata_root, "rev-parse", "HEAD").decode().strip()
    inventory = projects(metadata_root)
    names = {item["project"] for item in inventory}
    if args.start_at_project and args.start_at_project not in names:
        parser.error(f"Unknown project: {args.start_at_project}")
    if args.stop_after_project and args.stop_after_project not in names:
        parser.error(f"Unknown project: {args.stop_after_project}")
    if args.start_at_project and args.stop_after_project:
        ordered = [item["project"] for item in inventory]
        if ordered.index(args.start_at_project) > ordered.index(args.stop_after_project):
            parser.error("--start-at-project comes after --stop-after-project")
    issue_cache = json.loads(args.issue_cache.read_text()) if args.issue_cache else {}
    lock_cache = {}
    summary_path = args.output / "summary.json"
    previous = json.loads(summary_path.read_text()) if summary_path.exists() else None
    if previous and previous["bugsinpy_revision"] != source_revision:
        raise ValueError("Output contains candidates from a different BugsInPy revision")
    summary = previous["projects"] if previous else []
    started = args.start_at_project is None
    for project in inventory:
        name = project["project"]
        if name == args.start_at_project:
            started = True
        if not started:
            continue
        print(f"Converting {name}: {len(project['bug_ids'])} bugs", flush=True)
        output = args.output / name
        if (output / "tasks.parquet").exists():
            raise FileExistsError(f"Project already converted: {output}")
        repo = checkout(args.source_cache / "projects" / name, project["repo"])
        rows, manifest, errors, archived, exclusions = [], [], [], [], []
        for bug_id in project["bug_ids"]:
            try:
                task_name, files, record = build_task(metadata_root, repo, project, bug_id,
                                                      issue_cache, args.base_image_template, lock_cache)
                exclusion = task_exclusion(name, bug_id)
                if exclusion:
                    reason = exclusion['category'] + ': ' + exclusion['reason']
                    archived.append({"path": task_name, "task_binary": task_tar(files),
                                     "archive_category": exclusion['category'], "archive_reason": reason})
                    exclusions.append({"task_id": task_name, "bug_id": bug_id, **exclusion,
                                       "task_manifest": record})
                    print(f"  excluded {task_name}: {reason}", flush=True)
                    continue
                if args.render_dir:
                    render(args.render_dir, task_name, files)
                rows.append({"path": task_name, "task_binary": task_tar(files)})
                manifest.append(record)
            except Exception as exc:
                errors.append({"bug_id": bug_id, "error": f"{type(exc).__name__}: {exc}"})
                print(f"  bug {bug_id}: {type(exc).__name__}: {exc}", flush=True)
        write_project(output, rows, manifest, errors, issue_cache, source_revision, project, archived, exclusions,
                      metadata_url=args.metadata_url)
        summary.append({"project": name, "candidates": len(rows), "errors": len(errors),
                        "excluded": len(exclusions),
                        "unique_dockerfiles": len({item["dockerfile_sha256"] for item in manifest})})
        summary_path.write_text(json.dumps({"bugsinpy_revision": source_revision,
                                            "projects": summary}, indent=2) + "\n")
        print(f"  candidates={len(rows)} excluded={len(exclusions)} errors={len(errors)}", flush=True)
        if name == args.stop_after_project:
            break


def instruction_loop_cli():
    from data.utils.instruction_loop import main as instruction_loop_main
    instruction_loop_main(placeholder_markers=("Bug reported by the upstream regression test",))


if __name__ == "__main__":
    from data.utils.cli import dispatch
    dispatch({'instruction-loop': instruction_loop_cli})
    main()
