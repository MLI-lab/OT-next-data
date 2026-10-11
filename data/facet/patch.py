"""Prepare FACET task packages: unique task names, shared toolchain images, verifier preparation."""

# Support both direct execution and python -m data.<source>.patch.
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


import argparse
import ast
import collections
import copy
import io
import json
from pathlib import Path
import posixpath
import re
import shlex
import tarfile
import tempfile
import tomllib
import warnings

import pyarrow as pa
import pyarrow.parquet as pq


SCHEMA = pa.schema([("path", pa.string()), ("task_binary", pa.binary())])


def patched_toml(raw, task_id):
    text = raw.decode("utf-8")
    original = tomllib.loads(text)["task"]["name"]
    replacement = f"facet/{task_id}"
    if original == replacement:
        return raw
    if original != "FACET-Terminal":
        raise ValueError(f"unexpected FACET task name for {task_id}: {original}")
    needle = '\nname = "FACET-Terminal"\n'
    if text.count(needle) != 1:
        raise ValueError(f"ambiguous FACET task name for {task_id}")
    return text.replace(needle, f'\nname = "{replacement}"\n', 1).encode("utf-8")


# R1: Harbor's Apptainer builder emits every COPY as %files and every RUN as
# %post; the host /tmp hides %files content under /tmp during %post, so
# Dockerfiles that stage build scripts in /tmp/<X> fall back to a deferred
# overlay and fail. Stage them at /<X> instead.
DOCKERFILE = "environment/Dockerfile"
RELOCATED_PREFIXES = ("environment/build_scripts/",)
RELOCATED_FILES = (DOCKERFILE, "solution/solve.sh")
# Top-level directories of a base image that /<X> must not shadow.
RESERVED_ROOTS = frozenset(
    "bin boot dev etc home lib lib32 lib64 libx32 media mnt opt proc root run "
    "sbin srv sys tmp usr var app logs tests solution task_file".split())
COPY_RE = re.compile(r"^\s*(?:COPY|ADD)\s+(.*)$", re.IGNORECASE | re.DOTALL)
RUN_RE = re.compile(r"^\s*RUN\s", re.IGNORECASE)


def logical_lines(text):
    """Dockerfile instructions with backslash continuations joined."""
    lines, current = [], ""
    for line in text.splitlines():
        if not current and line.lstrip().startswith("#"):
            continue
        if line.rstrip().endswith("\\"):
            current += line.rstrip()[:-1] + " "
            continue
        lines.append(current + line)
        current = ""
    if current:
        lines.append(current)
    return lines


def copy_destination(arguments):
    arguments = arguments.strip()
    if arguments.startswith("["):
        try:
            tokens = json.loads(arguments)
        except ValueError:
            return None
    else:
        tokens = [token for token in arguments.split() if not token.startswith("--")]
    return tokens[-1] if len(tokens) >= 2 else None


def tmp_stage_roots(dockerfile):
    """Roots /tmp/<X> that a COPY/ADD populates and a later RUN references."""
    roots = []
    lines = logical_lines(dockerfile)
    for index, line in enumerate(lines):
        match = COPY_RE.match(line)
        destination = copy_destination(match.group(1)) if match else None
        if not destination or not destination.startswith("/tmp/"):
            continue
        root = destination[len("/tmp/"):].split("/", 1)[0]
        if not root or root in roots:
            continue
        reference = re.compile(rf"(?<![\w.\-/])/tmp/{re.escape(root)}(?![\w-])")
        if any(RUN_RE.match(later) and reference.search(later)
               for later in lines[index + 1:]):
            roots.append(root)
    return roots


def relocate_tmp_staging(files, task_id):
    """Rewrite /tmp/<X> to /<X> in Dockerfile, build scripts and solve.sh.

    Leaves environment/task_file/**, tests/ and instruction.md untouched.
    """
    dockerfile = files.get(DOCKERFILE)
    if dockerfile is None:
        return files
    roots = tmp_stage_roots(dockerfile.decode("utf-8"))
    if not roots:
        return files
    targets = [name for name in files
               if name in RELOCATED_FILES or name.startswith(RELOCATED_PREFIXES)]
    result = dict(files)
    for root in roots:
        if root in RESERVED_ROOTS:
            raise ValueError(f"/{root} collides with a base directory: {task_id}")
        old = rf"(?<![\w.\-/])/tmp/{re.escape(root)}(?![\w-])"
        new = rf"(?<![\w.\-/])/{re.escape(root)}(?![\w-])"
        for name in targets:
            try:
                text = result[name].decode("utf-8")
            except UnicodeDecodeError:
                if f"/tmp/{root}".encode() in result[name]:
                    raise ValueError(f"binary {name} references /tmp/{root}: {task_id}")
                continue
            if re.search(new, text):
                raise ValueError(f"/{root} already referenced in {name}: {task_id}")
            result[name] = re.sub(old, f"/{root}", text).encode("utf-8")
    return result


# Shared toolchain images and verifier preparation.
#
# A: tasks whose Dockerfile installs a toolchain and then only copies files or
# runs deterministic standard-library generators share one image per toolchain.
# Their remaining Dockerfile steps are replayed by /setup_files/setup.sh when the
# container starts. Anything the classifiers below do not recognize keeps the
# task on its own image.
# B: the installation commands of tests/test.sh run once at image build and
# move, unchanged, to tests/setup.sh.
SOURCE = {"dataset": "FACET-Terminal/FACET-Terminal-Tasks-6k",
          "revision": "b2d02645932e3989c8332a41e57d0f0855de7002"}
BASE_IMAGE = "ubuntu:22.04"
WORKDIR = "/task_file"
ENVIRONMENT = "environment/"
CONTEXT = "setup_files/context/"
SETUP_SCRIPT = "setup_files/setup.sh"
SETUP_CALL = "bash /setup_files/setup.sh"
SETUP_MARKER = "/var/lib/facet-task-setup.done"
VERIFIER_PACKAGES = ("ca-certificates", "curl")
# Top-level directories that task steps and COPY destinations may not touch,
# so installation steps can be hoisted into the image ahead of them.
SYSTEM_ROOTS = RESERVED_ROOTS - {"tmp", "task_file"}

# (fact of patch_task, report label, explanation); a task's reason joins the
# explanations of its changes.
CHANGES = (
    ("named", "unique-task-name", "Rename the task to facet/<task_id> so Harbor task names are unique."),
    ("shared", "shared-image",
     "Use one image per identical toolchain instead of one image per task. The task's "
     "initial files and its own fixture generation steps run unchanged, in their "
     "original order, in /setup_files/setup.sh at container start (with "
     "PYTHONHASHSEED=0 so every container gets the same fixtures). The reference "
     "solution gains the no-op setup call required by the runner. Outside this "
     "runner, run `bash /setup_files/setup.sh` as root before the agent starts."),
    ("relocated", "apptainer-build-compatibility",
     "Stage build scripts outside /tmp during the image build, because the "
     "Apptainer build hides copied files below /tmp."),
    ("baked", "verifier-dependencies-in-image",
     "Run the verifier's own installation commands (uv 0.9.7 and its pytest "
     "packages, or the script's pip and apt installs) at image build time instead "
     "of at each verification."),
    ("separated", "verifier-setup-separation",
     "Move the verifier's installation commands from tests/test.sh to "
     "tests/setup.sh unchanged so preparation is timed separately. The pytest "
     "command and reward logic are unchanged."),
)


def instructions(text):
    """(keyword, arguments) pairs; comment lines inside continuations are dropped, as Docker does."""
    result, current = [], None
    for raw in text.splitlines():
        if raw.lstrip().startswith("#") or not raw.strip():
            continue
        current = (current or "") + raw
        if current.rstrip().endswith("\\"):
            current = current.rstrip()[:-1] + " "
            continue
        result.append(current)
        current = None
    if current and current.strip():
        result.append(current)
    pairs = []
    for line in result:
        keyword, _, arguments = line.strip().partition(" ")
        pairs.append((keyword.upper(), arguments.strip()))
    return pairs


# Verifier scripts.
VERIFIER_HEAD = "#!/bin/bash\nset +e\nmkdir -p /logs/verifier\n"
CURL_BLOCK = (
    "if ! command -v curl >/dev/null 2>&1; then\n"
    "  apt-get update && apt-get install -y curl ca-certificates && rm -rf /var/lib/apt/lists/*\n"
    "elif ! dpkg -s ca-certificates >/dev/null 2>&1; then\n"
    "  apt-get update && apt-get install -y ca-certificates && rm -rf /var/lib/apt/lists/*\n"
    "fi\n")
ENV_BLOCK = 'if [ -f "$HOME/.local/bin/env" ]; then\n  source "$HOME/.local/bin/env"\nfi\n'
UV_BLOCKS = tuple(
    f"if ! command -v {tool} >/dev/null 2>&1; then\n"
    "  curl -LsSf https://astral.sh/uv/0.9.7/install.sh | sh\n"
    '  source "$HOME/.local/bin/env"\nfi\n' for tool in ("uvx", "uv"))
VERIFIER_TAIL = (
    '\nstatus=$?\nif [ "$status" -eq 0 ]; then\n  echo 1 > /logs/verifier/reward.txt\n'
    "else\n  echo 0 > /logs/verifier/reward.txt\nfi\nexit 0\n")
_INSTALL = (
    r"(?:apt-get update && apt-get install -y [a-z0-9][a-z0-9.+ -]* && rm -rf /var/lib/apt/lists/\*"
    r"|(?:python3? -m pip|pip3?|uv pip) install(?: [A-Za-z0-9._=-]+)+(?: 2>/dev/null)?"
    r"|python3 -m venv (?:--system-site-packages )?/[\w./-]+"
    r"|uv venv /[\w./-]+)")
INSTALL_LINE = re.compile(rf"{_INSTALL}(?: \|\| {_INSTALL})*(?: \|\| true)?")
ACTIVATE_LINE = re.compile(r"source /[\w./-]+/bin/activate")
GUARDED_INSTALL = re.compile(
    r'if ! (?:command -v \w+ >/dev/null 2>&1|python3 -c "import \w+" 2>/dev/null); then\n'
    rf"(?:  {INSTALL_LINE.pattern}\n)+fi\n")
INSTALLS = re.compile(r"apt-get|pip3? install|uv pip|\bvenv\b|astral\.sh")
UVX_COMMAND = re.compile(r"^uvx \\\n((?:  \S[^\n]*? \\\n)*)  pytest ", re.MULTILINE)
UVX_OPTION = re.compile(r"--with \S+|--index-strategy unsafe-best-match|--python python3")
RETRY_VENV = re.compile(
    r'VENV_DIR="(/opt/[\w-]+)"\nif \[ ! -d "\$VENV_DIR" \]; then\n  python3 -m venv "\$VENV_DIR"\n'
    r'.*?"\$VENV_DIR/bin/pip" (install [A-Za-z0-9 .=_-]+); then\n.*?\nfi\n', re.DOTALL)
BAKE_PREFIX = ("(set +e ; export HOME=/root ; unset XDG_BIN_HOME XDG_CACHE_HOME XDG_DATA_HOME "
               "UV_CACHE_DIR UV_INSTALL_DIR UV_TOOL_DIR UV_PYTHON_INSTALL_DIR ; cd / ; ")


def one_line(block):
    """A statement of the verifier script as one POSIX sh line."""
    lines = [line.strip() for line in block.splitlines()
             if line.strip() and not line.lstrip().startswith("#")]
    text = ""
    for line in lines:
        if text:
            # " ; " with a space: the static pip pinning check ends a command only there.
            text += " " if text.endswith(("then", "else", "do")) else " ; "
        text += line
    return re.sub(r"(?<![\w./-])source (?=[\"/])", ". ", text)


def verifier_statements(body):
    """Leading installation statements of a verifier script and the rest.

    Statement kinds: install (moves to setup.sh), shell (PATH or venv
    activation, needed in both scripts), note (comments and blank lines).
    """
    statements, position = [], 0
    while position < len(body):
        rest = body[position:]
        for kind, block in (("install", CURL_BLOCK), ("shell", ENV_BLOCK),
                            *(("install", block) for block in UV_BLOCKS)):
            if rest.startswith(block):
                text = block
                break
        else:
            guarded = GUARDED_INSTALL.match(rest)
            line = rest.split("\n", 1)[0]
            if guarded:
                kind, text = "install", guarded.group(0)
            elif not line.strip() or line.startswith("#"):
                kind, text = "note", line + "\n"
            elif INSTALL_LINE.fullmatch(line):
                kind, text = "install", line + "\n"
            elif ACTIVATE_LINE.fullmatch(line):
                kind, text = "shell", line + "\n"
            else:
                break
        if not rest.startswith(text):
            break                       # last line without a newline
        statements.append((kind, text))
        position += len(text)
    while statements and statements[-1][0] == "note":
        position -= len(statements.pop()[1])
    return statements, body[position:]


def test_command(script):
    """The script's pytest launcher with its options, for a build-time warm-up."""
    match = UVX_COMMAND.search(script)
    if match:
        options = [line.strip()[:-1].strip() for line in match.group(1).splitlines()]
        if all(UVX_OPTION.fullmatch(option) for option in options):
            return " ".join(["uvx", *options, "pytest", "--version"])
        return None
    match = re.search(r"^(?:(python3?) -m )?pytest ", script, re.MULTILINE)
    if match and match.group(1):
        # A script whose interpreter is missing scores 0 upstream; the image must still build.
        python = match.group(1)
        return f"if command -v {python} >/dev/null 2>&1 ; then {python} -m pytest --version ; fi"
    return "pytest --version" if match else None


def verifier_plan(script):
    """How to prepare a tests/test.sh: {test, setup, bake, standard, split}.

    setup is None when nothing moves; bake is the Dockerfile RUN command that
    performs the script's installations, or None when none is recognized.
    """
    plan = {"test": script, "setup": None, "bake": None, "standard": False, "split": None}
    check = test_command(script)
    retry = RETRY_VENV.search(script)
    if retry and CURL_BLOCK in script:
        # The script installs into this venv only when it is missing.
        venv = retry.group(1)
        plan["bake"] = BAKE_PREFIX + " ; ".join([
            one_line(CURL_BLOCK), f"python3 -m venv {venv}",
            f"{venv}/bin/pip {retry.group(2)}", f"{venv}/bin/pytest --version"]) + ")"
        plan["split"] = "installation is guarded by a retry loop under set -e"
        return plan
    if not script.startswith(VERIFIER_HEAD):
        plan["split"] = "unrecognized script header"
        return plan
    statements, rest = verifier_statements(script[len(VERIFIER_HEAD):])
    installs = [text for kind, text in statements if kind == "install"]
    if INSTALLS.search(rest) or not installs:
        # Installation follows other commands: leave the script as it is and
        # bake only the standard blocks it contains.
        blocks = [block for block in (CURL_BLOCK, ENV_BLOCK, *UV_BLOCKS) if block in script]
        if check and check.startswith("uvx") and len(blocks) == 3 and not installs:
            plan["bake"] = BAKE_PREFIX + " ; ".join([*map(one_line, blocks), check]) + ")"
        plan["split"] = ("installation commands follow other commands" if INSTALLS.search(rest)
                         else "no installation commands recognized")
        return plan
    baked = []
    for kind, text in statements:
        if kind == "note":
            continue
        if re.search(r"venv (?:--system-site-packages )?/tmp/", text):
            # A venv below /tmp cannot be part of the image.
            check = "command -v curl >/dev/null"
            break
        baked.append(one_line(text))
    if check is None:
        plan["split"] = "no pytest command recognized"
        return plan
    plan["bake"] = BAKE_PREFIX + " ; ".join([*baked, check]) + ")"
    plan["setup"] = ("#!/bin/bash\nset +e\n\n" + "".join(
        text for _, text in statements).lstrip("\n").rstrip("\n") + "\n\nexit 0\n")
    kept = "".join(text + "\n" for kind, text in statements if kind == "shell")
    plan["test"] = VERIFIER_HEAD + "\n" + kept + rest.lstrip("\n")
    standard = [text for kind, text in statements if kind != "note"]
    plan["standard"] = (
        standard == [CURL_BLOCK, ENV_BLOCK, UV_BLOCKS[0]] and check.startswith("uvx ")
        and rest.endswith(VERIFIER_TAIL)
        and bool(re.fullmatch(r"\n*uvx \\\n(?:  [^\n]* \\\n)+  pytest [^\n;&|<>$`]*\n",
                              rest[:-len(VERIFIER_TAIL)])))
    original = collections.Counter(script.splitlines())
    result = collections.Counter(plan["test"].splitlines() + plan["setup"].splitlines())
    if original - result:
        raise ValueError(f"verifier lines lost in split: {sorted(original - result)}")
    return plan


# Build scripts that may run at container start.
DATA_SUFFIXES = (".json", ".txt", ".md", ".csv", ".tsv", ".yaml", ".yml", ".gitkeep")
# Left out on purpose, measured by replaying the generators (2026-10-11): struct,
# zlib, binascii, array and wave synthesize images and audio in pure Python (up to
# more than 90 s per task); tarfile, zipfile, gzip, sqlite3 and email write the
# current time or random identifiers into their output.
PYTHON_MODULES = frozenset(
    "__future__ abc argparse base64 bisect calendar codecs collections "
    "colorsys configparser contextlib copy csv dataclasses datetime decimal difflib "
    "enum fnmatch fractions functools glob hashlib heapq html io itertools "
    "json math mimetypes numbers operator os pathlib pickle plistlib posixpath pprint "
    "random re shutil stat statistics string sys textwrap time "
    "types typing unicodedata uuid xml zoneinfo".split())
# Clock, entropy, host identity, process spawning and dynamic code.
PYTHON_DENIED = re.compile(
    r"\bnow\(|utcnow|\btoday\(|time\.time|time_ns|localtime|gmtime\(\s*\)|time\.strftime\(\s*['\"][^'\"]*['\"]\s*\)"
    r"|\bctime\(\s*\)|monotonic|perf_counter|process_time|uuid1|uuid4|urandom|SystemRandom|getrandbits"
    r"|randbytes|getpid|getppid|gethostname|getlogin|getuid|os\.system|os\.popen|os\.exec|os\.spawn"
    r"|os\.fork|os\.times|os\.uname|\beval\(|\bexec\(|__import__|\binput\(|\bcompile\(|urllib\.request"
    r"|\burlopen\b|\bid\(")
SEEDED = re.compile(r"(?:\bseed|\bRandom)\(\s*[^\s)]")
SHELL_KEYWORDS = frozenset("if then elif else fi do done while until ! { } esac".split())
SHELL_COMMANDS = frozenset(
    ". : [ [[ awk base64 basename break cat cd chmod chown command continue cp cut dirname "
    "echo exit export false find grep head local ln ls md5sum mkdir mv printf pwd read "
    "readonly realpath return rm sed seq set sha1sum sha256sum shift sort tail tee test "
    "touch tr true truncate uniq unset wc xargs".split())
SHELL_DENIED = re.compile(r"\$\{?(?:S?RANDOM|\$|BASHPID|SECONDS|EPOCH\w*|HOSTNAME|PPID)\b|\$\$|/dev/u?random")
HEREDOC = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_]\w*)\1")
DYNAMIC = "\0"


def python_issue(text, siblings=()):
    """Why a Python generator may not run at container start, or None."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return "Python generator does not parse"
    modules, unseeded = set(), False
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level or not node.module:
                return "relative import in generator"
            modules.add(node.module)
            if node.module == "random" and {alias.name for alias in node.names} - {"Random", "seed"}:
                unseeded = True
            if node.module == "time" and {alias.name for alias in node.names} - {"sleep"}:
                return "generator imports clock functions"
        elif (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
              and node.value.id == "random" and node.attr not in ("Random", "seed")):
            unseeded = True
    for module in sorted(modules):
        if module.split(".")[0] not in PYTHON_MODULES and module not in siblings:
            return f"generator imports {module}"
    denied = PYTHON_DENIED.search(text)
    if denied:
        return f"generator uses {denied.group(0).strip('(')}"
    if "random" in modules:
        seeded = SEEDED.search(text)
        if not seeded or (unseeded and not re.search(r"random\.seed\(\s*[^\s)]", text)
                          and not re.search(r"(?<![\w.])seed\(\s*[^\s)]", text)):
            return "generator uses unseeded randomness"
    return None


def shell_commands(text):
    """Command word lists and function names of a shell script; ValueError when unsure.

    Quoted text and expansions become DYNAMIC inside a word; commands inside
    command substitutions are reported like any other command.
    """
    commands, functions, size = [], set(), len(text)

    def quoted(index):
        literal = ""
        while text[index] != '"':
            if text[index] == "\\":
                literal += text[index + 1]
                index += 2
            elif text.startswith("$((", index):
                index = text.index("))", index) + 2
                literal += DYNAMIC
            elif text.startswith("$(", index):
                index = parse(index + 2, ")")
                literal += DYNAMIC
            elif text[index] == "`":
                index = parse(index + 1, "`")
                literal += DYNAMIC
            elif text[index] == "$":
                literal += DYNAMIC
                index += 1
            else:
                literal += text[index]
                index += 1
        return index + 1, literal

    def parse(index, closer):
        words, word, skip = [], None, False

        def end_word():
            nonlocal word, skip
            if word is not None and not skip:
                words.append(word)
            if word is not None:
                skip = False
            word = None

        def end_command():
            nonlocal words
            end_word()
            if words:
                commands.append(words)
            words = []

        while index < size:
            char = text[index]
            if closer and char == closer:
                end_command()
                return index + 1
            if char == "\\":
                if text[index + 1:index + 2] != "\n":
                    word = (word or "") + text[index + 1:index + 2]
                index += 2
            elif char == "'":
                end = text.index("'", index + 1)
                word = (word or "") + text[index + 1:end]
                index = end + 1
            elif char == '"':
                index, literal = quoted(index + 1)
                word = (word or "") + literal
            elif text.startswith("$((", index):
                index = text.index("))", index) + 2
                word = (word or "") + DYNAMIC
            elif text.startswith("$(", index):
                index = parse(index + 2, ")")
                word = (word or "") + DYNAMIC
            elif text.startswith("${", index):
                index = text.index("}", index) + 1
                word = (word or "") + DYNAMIC
            elif char == "`":
                index = parse(index + 1, "`")
                word = (word or "") + DYNAMIC
            elif char == "$":
                word = (word or "") + DYNAMIC
                index += 1
            elif char == "#" and word is None:
                while index < size and text[index] != "\n":
                    index += 1
            elif char in " \t":
                end_word()
                index += 1
            elif char in "<>":
                if word is not None and word.isdigit():
                    word = None
                end_word()
                index += 1
                while text[index:index + 1] in (">", "&"):
                    index += 1
                skip = True               # the redirection target is not a command word
            elif char in "\n;|&":
                end_command()
                index += 1
            elif char == "(":
                if word is not None and word.endswith("="):
                    index = text.index(")", index) + 1    # array literal
                    word += DYNAMIC
                elif text.startswith("()", index) and word is not None and not words:
                    functions.add(word)
                    word = None
                    index += 2
                elif word is None and not words:
                    index += 1                            # subshell
                else:
                    raise ValueError("unsupported parenthesis")
            elif char == ")":
                if closer:
                    raise ValueError("unbalanced parenthesis")
                word, words = None, []                    # case pattern
                index += 1
            else:
                word = (word or "") + char
                index += 1
        if closer:
            raise ValueError("unterminated substitution")
        end_command()
        return index

    parse(0, None)
    return commands, functions


def shell_issue(text, scripts):
    """Why a shell generator may not run at container start, or None.

    scripts maps the basenames of sibling build scripts to their own issue.
    """
    lines, code, bodies, index = text.split("\n"), [], [], 0
    while index < len(lines):
        line = lines[index]
        index += 1
        heredoc = None if line.lstrip().startswith("#") else HEREDOC.search(line)
        if not heredoc:
            code.append(line)
            continue
        body = []
        while index < len(lines) and lines[index].strip() != heredoc.group(2):
            body.append(lines[index])
            index += 1
        if index >= len(lines) or len(HEREDOC.findall(line)) != 1:
            return "shell generator has an unsupported heredoc"
        index += 1
        command = line[:heredoc.start()].split("|")[-1].split()
        body = "\n".join(body)
        if command[:1] == ["cat"] and len(line.split("|")) == 1:
            if not heredoc.group(1) and re.search(r"[$`]", body):
                return "shell generator expands variables in a heredoc"
        elif command in (["python3", "-"], ["python3"], ["python", "-"], ["python"]) and heredoc.group(1):
            issue = python_issue(body)
            if issue:
                return issue
        else:
            return "shell generator feeds a heredoc to " + (command[0] if command else "a command")
        code.append(line[:heredoc.start()] + line[heredoc.end():])
    code = "\n".join(code)
    denied = SHELL_DENIED.search(code)
    if denied:
        return f"shell generator uses {denied.group(0)}"
    try:
        commands, functions = shell_commands(code)
    except (ValueError, IndexError):
        return "shell generator is not parsed"
    for words in commands:
        while words and (words[0] in SHELL_KEYWORDS or re.fullmatch(r"\w+\+?=.*", words[0], re.DOTALL)):
            words = words[1:]
        if not words or words[0] in ("for", "case", "function") or words[0] in functions:
            continue
        name = words[0]
        if name in ("python3", "python", "bash", "sh"):
            arguments = [word for word in words[1:] if word != "-"]
            if not arguments:
                continue                  # code from a checked heredoc
            if name.startswith("python") and arguments[0] == "-c" and len(arguments) == 2 and DYNAMIC not in arguments[1]:
                issue = python_issue(arguments[1])
                if issue:
                    return issue
                continue
            name = posixpath.basename(arguments[0])
            if arguments[0].startswith("-") or DYNAMIC in name or scripts.get(name, "") is not None:
                return f"shell generator runs {words[0]} {arguments[0].replace(DYNAMIC, '$')}"
        elif name not in SHELL_COMMANDS:
            return f"shell generator runs {name.replace(DYNAMIC, '$')}"
    return None


def script_issues(files):
    """{context name: issue or None} for every file below environment/build_scripts/."""
    names = [name for name in files if name.startswith(ENVIRONMENT + "build_scripts/")]
    siblings = {posixpath.basename(name)[:-3] for name in names if name.endswith(".py")}
    issues = {}
    for name in names:
        if name.endswith(".py"):
            try:
                issues[name] = python_issue(files[name].decode("utf-8"), siblings)
            except UnicodeDecodeError:
                issues[name] = "generator is not UTF-8"
        elif not name.endswith((*DATA_SUFFIXES, ".sh")):
            issues[name] = "build script of another kind: " + posixpath.basename(name)
    by_basename = {posixpath.basename(name): issue for name, issue in issues.items()}
    for name in names:
        if name.endswith(".sh"):
            try:
                issues[name] = shell_issue(files[name].decode("utf-8"), by_basename)
            except UnicodeDecodeError:
                issues[name] = "generator is not UTF-8"
            by_basename[posixpath.basename(name)] = issues[name]
    return issues


# Dockerfile steps.
REDIRECT = re.compile(r"\s*(?:2>\s*/dev/null|>\s*/dev/null|2>&1)")
STEP_KEYWORD = re.compile(r"(?:if|then|elif|else|fi|!)(?:\s+|$)")
FILE_COMMANDS = frozenset("mkdir touch chmod chown rm".split())
APT_INSTALL = re.compile(
    r"apt-get update(?: -y| -q| -qq)* && apt-get install((?: -y| -q| -qq| --no-install-recommends)+)"
    r"((?: [a-z0-9][a-z0-9.+-]*)+)(?: && apt-get clean)? && rm -rf /var/lib/apt/lists/\*(?: && apt-get clean)?")
PIP_INSTALL = re.compile(r"(?:python3 -m pip|pip3|pip) install((?: \S+)+)")
PIP_OPTIONS = frozenset("--no-cache-dir --upgrade -U --break-system-packages -q --quiet".split())
REQUIREMENT = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9._-]*(?:\[[A-Za-z0-9_,.-]+\])?"
    r"(?:(?:==|>=|<=|~=|!=|<|>)[A-Za-z0-9.*]+(?:,(?:==|>=|<=|~=|!=|<|>)[A-Za-z0-9.*]+)*)?")
ENV_VALUE = re.compile(r"[A-Za-z0-9_./:@+=,-]*")


def simple_commands(command):
    """Word lists of an &&, || and ; chain; None for pipes, variables, loops and the like."""
    text = REDIRECT.sub("", command).replace("&&", ";").replace("||", ";")
    if re.search(r"[`$<>|&()\\\n]", text):
        return None
    commands = []
    for part in text.split(";"):
        part = part.strip()
        while STEP_KEYWORD.match(part):
            part = part[STEP_KEYWORD.match(part).end():]
        try:
            words = shlex.split(part)
        except ValueError:
            return None
        if words:
            commands.append(words)
    return commands


def task_step(command, cwd, copied, roots):
    """Context names of the scripts a task step runs; None when it is not a task step.

    A task step only creates, removes or changes files below the task's own
    directories and runs copied scripts.
    """
    commands = simple_commands(command)
    if commands is None:
        return None

    def inside(path):
        path = posixpath.normpath(posixpath.join(cwd, path))
        return any(path == root or path.startswith(root + "/") for root in roots)

    # Directories that received build scripts: a script named there but not
    # copied does not exist, upstream or here.
    staged = {posixpath.dirname(path) for path, name in copied.items()
              if name.startswith(ENVIRONMENT + "build_scripts/")}
    scripts = []
    for words in commands:
        if words[0] == "timeout" and len(words) > 2 and words[1].isdigit():
            words = words[2:]
        name, arguments = words[0], [word for word in words[1:] if not word.startswith("-")]
        if name in ("true", "[", "test"):
            continue
        if name == "cd":
            if len(arguments) != 1 or not inside(arguments[0]):
                return None
            cwd = posixpath.normpath(posixpath.join(cwd, arguments[0]))
            continue
        if name in FILE_COMMANDS:
            paths = arguments[1:] if name in ("chmod", "chown") else arguments
            if not paths or not all(inside(path) for path in paths):
                return None
            continue
        if name in ("python3", "python", "bash", "sh"):
            if len(words) < 2 or words[1].startswith("-"):
                return None
            name = words[1]
        path = posixpath.normpath(posixpath.join(cwd, name))
        if path in copied:
            scripts.append(copied[path])
        elif posixpath.dirname(path) not in staged:
            return None
    return scripts


def copy_operations(arguments, files, cwd):
    """[(context name, destination path)] of a COPY, or a reason string."""
    try:
        words = shlex.split(arguments)
    except ValueError:
        return "COPY is not parsed"
    if len(words) < 2 or any(word.startswith("--") or re.search(r"[*?\[$]", word) for word in words):
        return "COPY with options, patterns or variables"
    *sources, destination = words
    if not destination.startswith("/"):
        return "COPY to a relative destination"
    root = posixpath.normpath(destination).split("/")[1]
    if root in SYSTEM_ROOTS or not root:
        return f"COPY into /{root}"
    operations = []
    for source in sources:
        source = posixpath.normpath(source)
        if source.startswith(("..", "/")) or source == ".":
            return "COPY of the whole build context"
        name = ENVIRONMENT + source
        members = sorted(member for member in files if member.startswith(name + "/"))
        if name in files:
            if destination.endswith("/") or len(sources) > 1:
                target = posixpath.join(destination, posixpath.basename(source))
            elif posixpath.basename(destination) == posixpath.basename(source):
                target = destination
            else:
                return "COPY renames a file"
            operations.append((name, posixpath.normpath(target)))
        elif members:
            operations.extend((member, posixpath.normpath(posixpath.join(
                destination, member[len(name) + 1:]))) for member in members)
        else:
            return "COPY source is missing"
    return operations


def image_plan(files, modes, verifier):
    """Plan for running a task on a shared toolchain image, or the reason it keeps its own.

    The plan holds the image key, the image's installation steps and the
    Dockerfile steps to replay at container start.
    """
    dockerfile = files[DOCKERFILE].decode("utf-8")
    if "<<" in dockerfile:
        return "Dockerfile heredoc"
    if not verifier["standard"]:
        return "irregular verifier script"
    if any(name.startswith("setup_files/") for name in files) or "tests/setup.sh" in files:
        return "task already has setup files"
    extra = [name for name in files if name.startswith(ENVIRONMENT) and name != DOCKERFILE
             and not name.startswith((ENVIRONMENT + "task_file/", ENVIRONMENT + "build_scripts/"))]
    if extra:
        return "unexpected build context file"
    steps = instructions(dockerfile)
    if not steps or steps[0] != ("FROM", BASE_IMAGE):
        return "base image"
    cwd, pristine, located = "/", True, False
    environment, arguments, packages, image, key, replay = {}, [], set(), [], [], []
    copied, roots, executed, command = {}, {WORKDIR, "/tmp"}, set(), None
    for keyword, value in steps[1:]:
        if keyword == "ENV":
            try:
                pairs = [word.partition("=") for word in shlex.split(value)]
            except ValueError:
                return "ENV is not parsed"
            if any(not separator or not ENV_VALUE.fullmatch(text) for _, separator, text in pairs):
                return "ENV with expansion or legacy syntax"
            for name, _, text in pairs:
                environment[name] = text
                replay.append(("ENV", name, text))
        elif keyword == "ARG":
            name = value.partition("=")[0]
            if not re.fullmatch(r"\w+(=[\w.-]*)?", value) or len(re.findall(rf"\b{name}\b", dockerfile)) != 1:
                return "ARG that is used"
            arguments.append(value)
        elif keyword == "WORKDIR":
            if value != WORKDIR:
                return "WORKDIR other than /task_file"
            cwd, located = value, True
            replay.append(("WORKDIR", value))
        elif keyword == "CMD":
            command = value
        elif keyword == "COPY":
            operations = copy_operations(value, files, cwd)
            if isinstance(operations, str):
                return operations
            pristine = False
            roots.add("/" + posixpath.normpath(shlex.split(value)[-1]).split("/")[1])
            copied.update((destination, name) for name, destination in operations)
            replay.append(("COPY", value, [(name, destination, modes[name] & 0o7777)
                                           for name, destination in operations]))
        elif keyword == "RUN":
            normalized = " ".join(value.split())
            scripts = task_step(value, cwd, copied, roots)
            apt = APT_INSTALL.fullmatch(normalized)
            pip = PIP_INSTALL.fullmatch(normalized)
            words = None
            if pip and not re.search(r"[<>;&|$`\\]", re.sub(r"'[^']*'|\"[^\"]*\"", "", normalized)):
                try:
                    words = shlex.split(pip.group(1))
                except ValueError:
                    words = None
                if words and not all(word in PIP_OPTIONS or REQUIREMENT.fullmatch(word) for word in words):
                    words = None
            if scripts is not None:
                pristine = False
                executed.update(scripts)
                replay.append(("RUN", value, cwd))
            elif apt and "--no-install-recommends" in apt.group(1) and "-y" in apt.group(1).split():
                packages.update(apt.group(2).split())
            elif words:
                image.append((located, "RUN " + value))
                key.append((located, "pip", tuple(sorted(word.lower() for word in words))))
            elif pristine:
                # Nothing of the task is in the image yet, so this step cannot depend on it.
                image.append((located, "RUN " + value))
                key.append((located, "run", normalized))
            else:
                return "RUN after task files that is neither a package installation nor a task step"
        else:
            return f"{keyword} instruction"
    if not located:
        return "no WORKDIR"
    issues = script_issues(files)
    for name in sorted(executed):
        if name not in issues and name.endswith(".py"):
            issues[name] = python_issue(files[name].decode("utf-8", "replace"))
        elif name not in issues:
            issues[name] = "task step runs " + posixpath.basename(name)
    for name in sorted(issues):
        if issues[name]:
            return issues[name]
    solution = files.get("solution/solve.sh", b"").decode("utf-8", "replace")
    if not solution.startswith("#!") or "setup_files" in solution:
        return "reference solution without a shebang or with its own setup call"
    mentions = re.compile(rb"build[_-]scripts")
    if any(mentions.search(files[name]) for name in files if name.startswith(("solution/", "tests/"))):
        return "solution or tests mention the build scripts"
    packages = tuple(sorted(packages | set(VERIFIER_PACKAGES)))
    return {"key": (tuple(sorted(environment.items())), tuple(arguments), packages, tuple(key),
                    command, verifier["bake"]),
            "environment": environment, "arguments": arguments, "packages": packages,
            "image": image, "command": command, "replay": replay}


def shared_dockerfile(plan, bake):
    """The image definition every member of a group receives."""
    lines = ["FROM " + BASE_IMAGE]
    lines += [f"ENV {name}={value}" for name, value in sorted(plan["environment"].items())]
    lines += ["ARG " + argument for argument in plan["arguments"]]
    lines.append("RUN apt-get update && apt-get install -y --no-install-recommends "
                 + " ".join(plan["packages"]) + " && rm -rf /var/lib/apt/lists/*")
    lines += [line for located, line in plan["image"] if not located]
    lines.append("WORKDIR " + WORKDIR)
    lines += [line for located, line in plan["image"] if located]
    lines.append("RUN " + bake)
    if plan["command"]:
        lines.append("CMD " + plan["command"])
    return "\n".join(lines) + "\n"


def setup_script(plan):
    """Replay of the task's Dockerfile steps that follow its toolchain."""
    quote = shlex.quote
    lines = ["#!/bin/bash",
             "# Task initialization: the Dockerfile steps that follow the shared toolchain image,",
             "# in their original order. Runs once, as root, before the agent starts.",
             "set -euo pipefail",
             f"marker={SETUP_MARKER}",
             'if [ -f "$marker" ]; then exit 0; fi',
             "umask 022",
             "export PYTHONHASHSEED=0",
             "cd /"]
    for step in plan["replay"]:
        if step[0] == "ENV":
            lines.append(f"export {step[1]}={quote(step[2])}")
        elif step[0] == "WORKDIR":
            lines += ["# WORKDIR " + step[1], f"mkdir -p {quote(step[1])}", f"cd {quote(step[1])}"]
        elif step[0] == "COPY":
            lines.append("# COPY " + step[1])
            lines += [f"install -D -m {mode:04o} {quote('/' + CONTEXT + name[len(ENVIRONMENT):])} "
                      f"{quote(destination)}" for name, destination, mode in step[2]]
        else:
            lines += ["# RUN " + step[1], f"/bin/sh -c {quote(step[1])}"]
    lines += [f"rm -rf /{CONTEXT.rstrip('/')}",
              'mkdir -p "$(dirname "$marker")"',
              'touch "$marker"']
    return "\n".join(lines) + "\n"


def with_setup_call(solution):
    """solve.sh with the setup call after its first `set -e` line (or its shebang)."""
    lines = solution.split("\n")
    position = next((index for index, line in enumerate(lines)
                     if re.fullmatch(r"set -[a-z]*e[a-z]*(?: -o pipefail)?", line.strip())), 0)
    return "\n".join(lines[:position + 1] + [SETUP_CALL] + lines[position + 1:])


def detected_setup(solution, script):
    """The setup command the shared runner detects for this solution and setup script."""
    from validation.stages.task_setup import detect
    with tempfile.TemporaryDirectory() as directory:
        task = Path(directory)
        (task / "solution").mkdir()
        (task / "setup_files").mkdir()
        (task / "solution/solve.sh").write_bytes(solution)
        (task / SETUP_SCRIPT).write_bytes(script)
        return detect(task)


def share_image(files, modes, plan, bake, task_id):
    """Apply a group's image and move the task's own steps to runtime setup."""
    files, modes = dict(files), dict(modes)
    moved = {}
    for name in [name for name in files if name.startswith(ENVIRONMENT) and name != DOCKERFILE]:
        target = CONTEXT + name[len(ENVIRONMENT):]
        moved[name] = target
        files[target], modes[target] = files.pop(name), modes.pop(name)
    files[DOCKERFILE] = shared_dockerfile(plan, bake).encode("utf-8")
    files[SETUP_SCRIPT], modes[SETUP_SCRIPT] = setup_script(plan).encode("utf-8"), 0o755
    files["solution/solve.sh"] = with_setup_call(files["solution/solve.sh"].decode("utf-8")).encode("utf-8")
    if detected_setup(files["solution/solve.sh"], files[SETUP_SCRIPT]) != SETUP_CALL:
        raise ValueError(f"runner does not detect the setup call: {task_id}")
    return files, modes, moved


def patch_task(files, modes, task_id, plan=None):
    """Patched (files, modes, moved names, facts) of one task.

    plan is the task's image_plan when it is a member of a shared group.
    """
    files, modes, moved = dict(files), dict(modes), {}
    if "task.toml" not in files:
        raise ValueError(f"task.toml missing: {task_id}")
    upstream = files.get(DOCKERFILE, b"")
    renamed = patched_toml(files["task.toml"], task_id)
    facts = {"named": renamed != files["task.toml"], "shared": plan is not None,
             "relocated": False, "baked": False, "separated": False, "split": None}
    files["task.toml"] = renamed
    script = files.get("tests/test.sh")
    try:
        verifier = verifier_plan(script.decode("utf-8")) if script is not None else None
    except UnicodeDecodeError:
        verifier = None
    if plan is not None:
        files, modes, moved = share_image(files, modes, plan, verifier["bake"], task_id)
    else:
        relocated = relocate_tmp_staging(files, task_id)
        facts["relocated"] = relocated != files
        files = relocated
    if verifier is None:
        facts["split"] = "no readable tests/test.sh"
        return files, modes, moved, facts
    facts["split"] = verifier["split"]
    if verifier["bake"] and DOCKERFILE in files:
        dockerfile = files[DOCKERFILE].decode("utf-8")
        step = "RUN " + verifier["bake"] + "\n"
        if step not in dockerfile:
            files[DOCKERFILE] = (dockerfile + "\n" * (not dockerfile.endswith("\n")) + step).encode("utf-8")
        facts["baked"] = step.encode("utf-8") not in upstream
        if verifier["setup"] is not None:
            facts["separated"] = True
            files["tests/test.sh"] = verifier["test"].encode("utf-8")
            files["tests/setup.sh"], modes["tests/setup.sh"] = verifier["setup"].encode("utf-8"), 0o755
    elif verifier["split"] is None:
        facts["split"] = "no Dockerfile to prepare"
    return files, modes, moved, facts


def classify(files, modes):
    """image_plan of a task, or the reason it keeps its own image."""
    try:
        if DOCKERFILE not in files or "tests/test.sh" not in files:
            return "no Dockerfile or tests/test.sh"
        return image_plan(files, modes, verifier_plan(files["tests/test.sh"].decode("utf-8")))
    except UnicodeDecodeError:
        return "Dockerfile or script is not UTF-8"


def group_tasks(plans):
    """{task_id: plan} of the tasks that share an image with at least one other task.

    Every member receives the installation steps of the member with the
    smallest task ID; the key guarantees that they are equivalent.
    """
    groups = collections.defaultdict(list)
    for task_id, plan in sorted(plans.items()):
        if isinstance(plan, dict):
            groups[plan["key"]].append(task_id)
    shared = {}
    for members in groups.values():
        if len(members) < 2:
            continue
        first = plans[members[0]]
        for task_id in members:
            shared[task_id] = {**plans[task_id], "image": first["image"], "group": members[0]}
    return shared


def read_task(blob, task_id):
    members, files, modes = [], {}, {}
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:*") as reader:
        for member in reader:
            if not member.isfile():
                raise ValueError(f"unexpected tar member for {task_id}: {member.name}")
            if member.name in files:
                raise ValueError(f"duplicate tar member for {task_id}: {member.name}")
            members.append(member)
            files[member.name] = reader.extractfile(member).read()
            modes[member.name] = member.mode
    return members, files, modes


def patch_binary(blob, task_id, plan=None, facts=None):
    members, files, modes = read_task(blob, task_id)
    patched, modes, moved, result = patch_task(files, modes, task_id, plan)
    if facts is not None:
        facts.update(result)
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as writer:
        written = set()
        for member in members:
            member = copy.copy(member)
            if member.name in moved:
                # A long name also lives in the PAX header, which would win over the new name.
                member.name = moved[member.name]
                member.pax_headers = {key: value for key, value in member.pax_headers.items() if key != "path"}
            content = patched[member.name]
            member.size, member.mode = len(content), modes[member.name]
            writer.addfile(member, io.BytesIO(content))
            written.add(member.name)
        for name in sorted(set(patched) - written):
            member = tarfile.TarInfo(name)
            member.size, member.mode = len(patched[name]), modes[name]
            writer.addfile(member, io.BytesIO(patched[name]))
    return output.getvalue()


def change_report(task_id, facts):
    """(labels, reason) of a patched task for the patch report."""
    applied = [(label, text) for fact, label, text in CHANGES if facts[fact]]
    return ([label for label, _ in applied],
            " ".join(text for _, text in applied).replace("<task_id>", task_id))


def summarize(plans, shared, facts):
    """Counts and per-task decisions for the image plan written beside the patch report."""
    groups = collections.Counter(plan["group"] for plan in shared.values())
    own = {task_id: plan if isinstance(plan, str) else "no other task has the same toolchain"
           for task_id, plan in plans.items() if task_id not in shared}
    return {
        "tasks": len(plans),
        "shared_images": len(groups),
        "tasks_on_shared_images": len(shared),
        "tasks_on_their_own_image": len(own),
        "images": len(groups) + len(own),
        "labels": dict(collections.Counter(label for fact in facts.values()
                                           for name, label, _ in CHANGES if fact[name])),
        "own_image_reasons": dict(collections.Counter(own.values()).most_common()),
        "verifier_not_separated": {task_id: fact["split"] for task_id, fact in sorted(facts.items())
                                   if fact["split"] is not None},
        "groups": [{"first_task": first, "tasks": count,
                    "packages": list(shared[first]["packages"]),
                    "other_steps": [line for _, line in shared[first]["image"]],
                    "members": sorted(task_id for task_id, plan in shared.items() if plan["group"] == first)}
                   for first, count in sorted(groups.items(), key=lambda pair: (-pair[1], pair[0]))],
        "own_image": own,
    }


def patch_parquet(source, output, revision=SOURCE["revision"]):
    from data.utils.patch_reporting import write_patch_report
    source = Path(source)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    target = output / "tasks.parquet"
    if target.exists():
        raise FileExistsError(target)
    plans = {}
    for batch in pq.ParquetFile(source).iter_batches(batch_size=16):
        for row in batch.to_pylist():
            _, files, modes = read_task(row["task_binary"], row["path"])
            plans[row["path"]] = classify(files, modes)
    shared = group_tasks(plans)
    temporary = target.with_suffix(".parquet.tmp")
    facts, labels, reasons = {}, {}, {}
    try:
        with pq.ParquetWriter(temporary, SCHEMA, compression="zstd") as writer:
            for batch in pq.ParquetFile(source).iter_batches(batch_size=16):
                rows = batch.to_pylist()
                binaries = []
                for row in rows:
                    task_id = row["path"]
                    facts[task_id] = {}
                    binaries.append(patch_binary(row["task_binary"], task_id, shared.get(task_id), facts[task_id]))
                    labels[task_id], reasons[task_id] = change_report(task_id, facts[task_id])
                writer.write_table(pa.table({"path": [row["path"] for row in rows],
                                             "task_binary": binaries}, schema=SCHEMA))
        temporary.replace(target)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    summary = summarize(plans, shared, facts)
    (output / "tasks.image-plan.json").write_text(json.dumps(summary, indent=2) + "\n")
    counts = {name: value for name, value in summary.items() if isinstance(value, int) or name == "labels"}
    write_patch_report(
        source, target, patcher=__file__, dropped={},
        source={**SOURCE, "revision": revision,
                "url": f"https://huggingface.co/datasets/{SOURCE['dataset']}/tree/{revision}"},
        change_labels=labels, change_reasons=reasons,
        patches=[{"version": "facet-shared-images-v1", **counts}])
    print(json.dumps(counts, indent=2))
    return summary


def patch_directory(tasks):
    """Patch extracted task directories in place; images are shared within the directory."""
    directories = [task for task in sorted(tasks.iterdir()) if task.is_dir()]

    def read(task):
        paths = [path for path in sorted(task.rglob("*")) if path.is_file()]
        return ({path.relative_to(task).as_posix(): path.read_bytes() for path in paths},
                {path.relative_to(task).as_posix(): path.stat().st_mode & 0o7777 for path in paths})

    shared = group_tasks({task.name: classify(*read(task)) for task in directories})
    for task in directories:
        files, modes = read(task)
        patched, modes, _, facts = patch_task(files, modes, task.name, shared.get(task.name))
        for name in set(files) - set(patched):
            (task / name).unlink()
        for name, content in patched.items():
            if files.get(name) != content:
                (task / name).parent.mkdir(parents=True, exist_ok=True)
                (task / name).write_bytes(content)
                (task / name).chmod(modes[name])
        for directory in sorted((path for path in task.rglob("*") if path.is_dir()), reverse=True):
            if not any(directory.iterdir()):
                directory.rmdir()
        print(task.name, *change_report(task.name, facts)[0], facts["split"] or "")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("tasks", type=Path, nargs="?")
    parser.add_argument("--source-parquet", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--source-revision", default=SOURCE["revision"])
    args = parser.parse_args()
    if args.source_parquet or args.output:
        if args.tasks or not args.source_parquet or not args.output:
            parser.error("--source-parquet and --output are required together")
        patch_parquet(args.source_parquet, args.output, args.source_revision)
        return
    if not args.tasks:
        parser.error("tasks directory is required")
    patch_directory(args.tasks)


# Build source
"""Pack a pinned FACET release ZIP into a full Harbor-task Parquet source."""

import argparse
import io
from pathlib import Path, PurePosixPath
import stat
import tarfile
import zipfile

import pyarrow as pa
import pyarrow.parquet as pq


build_source_SCHEMA = pa.schema([("path", pa.string()), ("task_binary", pa.binary())])


def build(archive, output):
    archive = Path(archive)
    output = Path(output)
    if output.exists():
        raise FileExistsError(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as release:
        grouped = {}
        for item in release.infolist():
            if item.is_dir():
                continue
            parts = PurePosixPath(item.filename).parts
            if (len(parts) < 3 or parts[0] != "FACET-Terminal-Tasks" or
                    not parts[1].startswith("task_") or ".." in parts):
                raise ValueError(f"unexpected archive member: {item.filename}")
            mode = item.external_attr >> 16
            if mode and not stat.S_ISREG(mode):
                raise ValueError(f"nonregular archive member: {item.filename}")
            grouped.setdefault(parts[1], []).append((item, "/".join(parts[2:])))
        if not grouped:
            raise ValueError("no FACET tasks found")
        temporary = output.with_suffix(output.suffix + ".tmp")
        count = 0
        try:
            with pq.ParquetWriter(temporary, build_source_SCHEMA, compression="zstd") as writer:
                for task_id, members in sorted(grouped.items()):
                    if "task.toml" not in {name for _, name in members}:
                        raise ValueError(f"task.toml missing: {task_id}")
                    buffer = io.BytesIO()
                    with tarfile.open(fileobj=buffer, mode="w") as tar:
                        for item, relative in sorted(members, key=lambda pair: pair[1]):
                            content = release.read(item)
                            info = tarfile.TarInfo(relative)
                            info.size = len(content)
                            info.mode = (item.external_attr >> 16) & 0o777 or 0o644
                            info.mtime = 0
                            tar.addfile(info, io.BytesIO(content))
                    writer.write_table(pa.table({"path": [task_id],
                                                 "task_binary": [buffer.getvalue()]}, schema=build_source_SCHEMA))
                    count += 1
            temporary.replace(output)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    return count


def build_source_main():
    parser = argparse.ArgumentParser(description='Pack a pinned FACET release ZIP into a full Harbor-task Parquet source.')
    parser.add_argument("archive", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    print(f"packed {build(args.archive, args.output)} tasks into {args.output}")


def build_source_cli():
    build_source_main()


# Prepare pilot
"""Sample twenty complete Harbor tasks from the FACET release archive."""

import argparse
import json
from pathlib import Path, PurePosixPath
import random
import zipfile

from huggingface_hub import HfApi, hf_hub_download


def prepare_pilot_main():
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    repo = "FACET-Terminal/FACET-Terminal-Tasks-6k"
    revision = HfApi().dataset_info(repo).sha
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    archive = hf_hub_download(repo_id=repo, repo_type="dataset", filename="FACET-Terminal-Tasks.zip",
                              revision=revision, cache_dir=str(output / "cache"))
    with zipfile.ZipFile(archive) as source:
        roots = sorted({str(PurePosixPath(name).parent) for name in source.namelist()
                        if name.endswith("/task.toml")})
        selected = sorted(random.Random(args.seed).sample(roots, 20))
        records = []
        for root in selected:
            task_id = PurePosixPath(root).name
            target = output / "input/tasks" / task_id
            files = []
            for member in source.infolist():
                if not member.filename.startswith(root + "/") or member.is_dir():
                    continue
                relative = PurePosixPath(member.filename).relative_to(root)
                if ".." in relative.parts or relative.is_absolute():
                    raise ValueError(f"Unsafe archive member: {member.filename}")
                destination = target.joinpath(*relative.parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(source.read(member))
                files.append(str(relative))
            records.append({"task_id": task_id, "source_path": root, "files": files})
            print(task_id, len(files), flush=True)
    (output / "selection.json").write_text(json.dumps({
        "dataset": repo, "revision": revision, "seed": args.seed,
        "selection": "uniform random among archive task roots",
        "tasks": records,
    }, indent=2) + "\n")


def prepare_pilot_cli():
    prepare_pilot_main()


if __name__ == "__main__":
    from data.utils.cli import dispatch
    dispatch({'build-source': build_source_cli, 'prepare-pilot': prepare_pilot_cli})
    main()
