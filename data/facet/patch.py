"""Prepare FACET task packages: unique task names, shared toolchain images, verifier preparation."""

# Support both direct execution and python -m data.<source>.patch.
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


import argparse
import ast
import collections
import hashlib
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

# Reviewed exclusions with their evidence are listed at the end of this file (EXCLUSIONS); the
# original payloads are kept in the archive. The function name predates exclusions other than
# ownership and is kept for existing callers.


def ownership_exclusion(task_id, blob):
    decision = EXCLUSIONS.get(task_id)
    if decision is None:
        return None
    if hashlib.sha256(blob).hexdigest() != decision["source_binary_sha256"]:
        raise ValueError(f"{task_id}: excluded source changed; review it again")
    return {key: decision[key] for key in ("category", "reason")}


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


def instruction_spans(lines):
    """(first line, last line, keyword, arguments) of each Dockerfile instruction, parsed like instructions()."""
    spans, start, current = [], None, None
    for index, raw in enumerate(lines):
        if raw.lstrip().startswith("#") or not raw.strip():
            continue
        if current is None:
            start, current = index, ""
        current += raw
        if current.rstrip().endswith("\\"):
            current = current.rstrip()[:-1] + " "
            continue
        keyword, _, arguments = current.strip().partition(" ")
        spans.append((start, index, keyword.upper(), arguments.strip()))
        current = None
    if current and current.strip():
        keyword, _, arguments = current.strip().partition(" ")
        spans.append((start, len(lines) - 1, keyword.upper(), arguments.strip()))
    return spans


# R2: hpc/offline_prep/image_cache.py runs every RUN as `/bin/sh -ec`, Docker as `/bin/sh -c`.
# In a RUN with a command list (`a; b`, loop or if bodies) a failing non-final
# command, such as `chmod +x /build_scripts/*.sh 2>/dev/null` without any .sh
# file, then stops the Apptainer build where Docker continues. A leading
# `set +e;` restores Docker's behavior and does nothing under Docker.
DOCKER_SHELL = "set +e; "


def has_command_list(body):
    """Whether a shell command line separates commands with an unquoted ; or newline."""
    quote, index = None, 0
    while index < len(body):
        char = body[index]
        if char == "\\" and quote != "'":
            index += 2
            continue
        if quote:
            quote = None if char == quote else quote
        elif char in "'\"":
            quote = char
        elif char == "\n" or (char == ";" and not body.startswith(";;", index)
                              and not (index and body[index - 1] == ";")):
            return True
        index += 1
    return False


def docker_shell_semantics(files):
    """Dockerfile whose RUN command lists keep Docker's behavior on failed non-final commands."""
    dockerfile = files.get(DOCKERFILE)
    if dockerfile is None:
        return files
    lines = dockerfile.decode("utf-8").split("\n")
    changed = False
    for start, _, keyword, arguments in instruction_spans(lines):
        if (keyword != "RUN" or arguments.startswith(("[", "--", "set +e", "(set +e"))
                or not has_command_list(arguments)):
            continue
        lines[start] = re.sub(r"^(\s*RUN\s+)", lambda match: match.group(1) + DOCKER_SHELL,
                              lines[start], count=1, flags=re.IGNORECASE)
        changed = True
    return {**files, DOCKERFILE: "\n".join(lines).encode("utf-8")} if changed else files


# R3: Harbor's Apptainer builder applies every COPY (as %files) before the
# first RUN. A Dockerfile that deletes its build scripts after one RUN and
# copies them again for a later RUN therefore loses them. Keep the scripts
# until the last copy; its RUN still removes them.
def keep_recopied_files(files):
    """Dockerfile without the deletions that a later COPY to the same place would undo."""
    dockerfile = files.get(DOCKERFILE)
    if dockerfile is None:
        return files
    lines = dockerfile.decode("utf-8").split("\n")
    spans = instruction_spans(lines)
    copies = [(position, copy_destination(arguments)) for position, (_, _, keyword, arguments)
              in enumerate(spans) if keyword in ("COPY", "ADD")]
    replacements = {}
    for index, (position, destination) in enumerate(copies):
        later = [other for other, target in copies[index + 1:] if target == destination]
        if not destination or not later:
            continue
        deletion = re.compile(rf"\s*&&\s*rm\s+-rf\s+{re.escape(destination.rstrip('/'))}/?\s*$")
        for start, end, keyword, arguments in spans[position + 1:later[0]]:
            if keyword == "RUN" and deletion.search(arguments):
                replacements[start] = (end, "RUN " + deletion.sub("", arguments))
    if not replacements:
        return files
    result, index = [], 0
    while index < len(lines):
        if index in replacements:
            end, line = replacements[index]
            result.append(line)
            index = end + 1
        else:
            result.append(lines[index])
            index += 1
    return {**files, DOCKERFILE: "\n".join(result).encode("utf-8")}


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
    ("libreoffice_libraries", "apptainer-build-compatibility",
     "Launch LibreOffice with its private library directory first, retaining inherited library "
     "paths for that process only; leave unrelated programs and grading unchanged."),
    ("verifier_precheck_removed", "verifier-execution-evidence",
     "Removed the verifier's early exit that wrote reward 0 without running the tests when required "
     "outputs were missing. The tests now run and fail on their own in that case, so the no-op stage can "
     "see that the test suite executed; rewards are unchanged."),
    ("missing_tools", "upstream-environment-repair",
     "Install the tool the task's own scripts call but the image lacks (python3 and pip "
     "for solve.sh, python-is-python3 for a test.sh that runs `python`, r-cran-jsonlite for an "
     "R script that loads jsonlite, or binutils for `strings`); nothing else changes."),
    ("local_pdf_tmp", "apptainer-build-compatibility",
     "Compile PDF fixtures in a temporary directory on the destination filesystem "
     "so os.rename works across Apptainer mount layouts; retain final paths and fixture content."),
    ("resource_limits", "measured-resource-limits",
     "Raise this task's CPU, memory or agent time limit to what its reference solution was measured "
     "to need: the reference ran out of time or memory under the upstream limits (1 CPU, 2048 MiB, "
     "120 s) and passed with generous limits. Memory is the measured peak rounded up to whole GiB "
     "plus 1 GiB; the agent timeout is about twice the measured time; tasks whose reference keeps "
     "more than one CPU busy get 2 CPUs. Grading is unchanged."),
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
    ("recopied", "apptainer-build-compatibility",
     "Keep the build scripts until their last COPY instead of deleting them after an "
     "earlier RUN, because the Apptainer build applies every COPY before the first RUN. "
     "The last RUN that uses them still deletes them."),
    ("sequenced", "apptainer-build-compatibility",
     "Start each RUN that runs several commands in sequence with `set +e;`. The Apptainer "
     "build runs RUN commands with `sh -e`, which stops at the first failed command, "
     "such as a chmod over a glob that matches no file. Docker continues and fails only "
     "if the last command fails; `set +e` keeps that behavior and changes nothing under Docker."),
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


# Reviewed LibreOffice fixture failures on the pinned Ubuntu 22.04 tasks. Every
# other task whose Dockerfile installs LibreOffice gets the same wrapper: under
# Apptainer soffice.bin can fail to load its private libraries (libreglo.so),
# seen in five reference runs of the 2026-10-16 validation.
LIBREOFFICE_TASKS = frozenset('task_' + number for number in (
    '000718 000938 001447 002559 002899 003012 003522 003750 '
    '004504 004890 005109 005161 006097 006844 007765').split())
LIBREOFFICE_INSTALL = re.compile(r'\bapt(?:-get)?\s+install\b[^\n]*\blibreoffice')


def office_wrapper(command):
    return ('#!/bin/sh\n'
            '# FACET LibreOffice loader compatibility; only this process is affected.\n'
            'export LD_LIBRARY_PATH="/usr/lib/libreoffice/program${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"\n'
            f'exec /usr/bin/{command} "$@"\n')


LIBREOFFICE_WRAPPER = office_wrapper('libreoffice')
SOFFICE_WRAPPER = office_wrapper('soffice')
LIBREOFFICE_MARKER = '# FACET scoped LibreOffice library compatibility'


def installs_libreoffice(dockerfile):
    return bool(LIBREOFFICE_INSTALL.search(dockerfile.replace('\\\n', ' ')))


# Reviewed 2026-10-16 reference failures: the task's own scripts call a tool the
# image never installs, so they fail under Docker too.
#   task_001011: bare ubuntu:22.04; solve.sh runs `pip install pyyaml`, then python3.
#   task_003697: test.sh runs `python -m pytest`; the image has only python3.
# Reviewed 2026-10-17 (reference job 1038707, all bare ubuntu:22.04 images):
#   task_000701, task_000814, task_001579, task_002463, task_004655, task_006478,
#   task_006651, task_007718: solve.sh runs python3 ("python3: command not found").
#   task_002689: solve.sh loads the R package jsonlite; its CRAN install fails and the
#     image has none ("there is no package called 'jsonlite'"); Debian's r-cran-jsonlite.
#   task_003456: solve.sh falls back to `strings` (binutils), which the image lacks.
PYTHON_TOOLS = ('python3', 'python3-pip')
MISSING_TOOLS = {'task_001011': PYTHON_TOOLS, 'task_003697': ('python-is-python3',),
                 **{task_id: PYTHON_TOOLS for task_id in (
                     'task_000701', 'task_000814', 'task_001579', 'task_002463',
                     'task_004655', 'task_006478', 'task_006651', 'task_007718')},
                 'task_002689': ('r-cran-jsonlite',), 'task_003456': ('binutils',)}
MISSING_TOOLS_MARKER = '# FACET install tools the task scripts call'


def install_missing_tools(files, task_id):
    """Install, right after the base image, packages the task's scripts need but never install."""
    packages = MISSING_TOOLS.get(task_id)
    if not packages:
        return files
    text = files[DOCKERFILE].decode('utf-8')
    if MISSING_TOOLS_MARKER in text:
        return files
    lines = text.splitlines(keepends=True)
    bases = [i for i, line in enumerate(lines) if line.strip().upper().startswith('FROM ')]
    if len(bases) != 1 or lines[bases[0]].strip() != 'FROM ubuntu:22.04':
        raise ValueError(f'{task_id}: review changed base image before installing missing tools')
    step = (MISSING_TOOLS_MARKER + '\nRUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y '
            '--no-install-recommends ' + ' '.join(packages) + ' && rm -rf /var/lib/apt/lists/*\n')
    result = dict(files)
    result[DOCKERFILE] = (''.join(lines[:bases[0] + 1]) + step + ''.join(lines[bases[0] + 1:])).encode('utf-8')
    return result


def libreoffice_library_compatibility(files, task_id):
    """Select LibreOffice's private libraries only when launching LibreOffice."""
    if DOCKERFILE not in files:
        return files
    text = files[DOCKERFILE].decode('utf-8')
    if task_id not in LIBREOFFICE_TASKS and not installs_libreoffice(text):
        return files
    if LIBREOFFICE_MARKER in text:
        return files
    lines = text.splitlines(keepends=True)
    bases = [i for i, line in enumerate(lines) if line.strip().upper().startswith('FROM ')]
    if len(bases) != 1 or lines[bases[0]].strip() != 'FROM ubuntu:22.04':
        raise ValueError(f'{task_id}: review changed LibreOffice base image')
    if 'LD_LIBRARY_PATH' in text or '/usr/local/bin/libreoffice' in text or '/usr/local/bin/soffice' in text:
        raise ValueError(f'{task_id}: review existing LibreOffice library configuration')
    step = (LIBREOFFICE_MARKER + "\nRUN mkdir -p /usr/local/bin && printf '%b' "
            + shlex.quote(LIBREOFFICE_WRAPPER.replace('\n', r'\n'))
            + " > /usr/local/bin/libreoffice && printf '%b' "
            + shlex.quote(SOFFICE_WRAPPER.replace('\n', r'\n'))
            + ' > /usr/local/bin/soffice && chmod 755 /usr/local/bin/libreoffice /usr/local/bin/soffice\n')
    result = dict(files)
    result[DOCKERFILE] = (''.join(lines[:bases[0] + 1]) + step
                         + ''.join(lines[bases[0] + 1:])).encode('utf-8')
    return result


PDF_TMP_GENERATORS = {
    'task_001379': {'generate_fixtures.py': ('generate_pdf', 'output_dir')},
    'task_004304': {'generate_corpus_pdfs.py': ('generate_pdf', 'OUTPUT_DIR')},
    'task_007439': {
        'generate_arxiv_preprint.py': ('generate_preprint', "'/task_file/input'"),
        'generate_paper_pdf.py': ('generate_latex_paper', "'/task_file/input'"),
    },
}


def destination_local_pdf_tmp(files, task_id):
    """Patch only the reviewed build scripts; retain PDF contents and final names."""
    result = dict(files)
    for basename, (function_name, destination) in PDF_TMP_GENERATORS.get(task_id, {}).items():
        name = 'environment/build_scripts/' + basename
        text = result[name].decode('utf-8')
        if 'with tempfile.TemporaryDirectory(prefix=".facet-pdf-",' in text:
            continue
        tree = ast.parse(text)
        functions = [node for node in tree.body
                     if isinstance(node, ast.FunctionDef) and node.name == function_name]
        if len(functions) != 1:
            raise ValueError(f'{task_id}: unexpected PDF generator {name}')
        function = functions[0]
        starts = [node.lineno - 1 for node in function.body
                  if isinstance(node, ast.Assign)
                  and any(isinstance(target, ast.Name) and target.id in ('tex_path', 'tex_file')
                          for target in node.targets)]
        if len(starts) != 1:
            raise ValueError(f'{task_id}: unexpected temporary file setup in {name}')
        lines = text.splitlines(keepends=True)
        start, end = starts[0], function.end_lineno
        body = ''.join(lines[start:end])
        if 'os.rename(' not in body or '/tmp' not in body:
            raise ValueError(f'{task_id}: expected cross-filesystem rename in {name}')
        body = body.replace('f"/tmp/', 'f"{_facet_tmp}/')
        body = body.replace('"-output-directory=/tmp"', '"-output-directory=" + _facet_tmp')
        body = re.sub(r'''(["'])/tmp(?:/([^"']*))?\1''',
                      lambda m: ('os.path.join(_facet_tmp, ' + repr(m[2]) + ')'
                                 if m[2] else '_facet_tmp'), body)
        if '/tmp' in body:
            raise ValueError(f'{task_id}: unhandled temporary path in {name}')
        replacement = (f'    os.makedirs({destination}, exist_ok=True)\n'
                       '    with tempfile.TemporaryDirectory(prefix=".facet-pdf-", '
                       f'dir={destination}) as _facet_tmp:\n'
                       + ''.join('    ' + line if line.strip() else line
                                 for line in body.splitlines(keepends=True)))
        text = ''.join(lines[:start]) + replacement + ''.join(lines[end:])
        if not re.search(r'^import os$', text, flags=re.M):
            raise ValueError(f'{task_id}: unexpected imports in {name}')
        text = re.sub(r'^import os$', 'import os\nimport tempfile', text, count=1, flags=re.M)
        ast.parse(text)
        result[name] = text.encode('utf-8')
    return result


# Measured 2026-10-17 (job 1039989, /hnvme/workspace/y500bb12-optiagent/facet-validation-20261017/measure5):
# each reference ran once with 4 CPUs, 8192 MiB and 480 s; all five passed. Values from the trial's
# `resources` (cgroup memory.peak, cpu.stat usage, step lifetime). Upstream limits were 1 CPU,
# 2048 MiB, agent 120 s; reference job 1038707 hit the agent timeout (000536, 002890, 004333, 004857)
# or the memory limit (004399). New memory = ceil(peak GiB) + 1 GiB; new agent timeout = 2 x
# max(step seconds, CPU seconds / new CPUs), rounded up to a minute and never below 120 s.
#   task      peak MiB  CPU s   step s  CPU s/wall  -> cpus  memory_mb  agent timeout_sec
#   000536       934.6  1791.3   462.5    3.9            2       2048       1800
#   002890      2149.7    91.2    66.0    1.8            2       4096        180
#   004333      2237.2    93.4    66.0    1.8            2       4096        180
#   004857      1675.3   181.1    66.0    3.5            2       4096        240
#   004399      4882.8    30.9    31.2    1.0            1       8192        120 (unchanged)
# Memory is then rounded up to a size check-resource-sizes allows (1, 2, 4, 8, 16 or 32 GiB):
# 004857 3072 -> 4096 MiB, 004399 6144 -> 8192 MiB (full run 1040937 failed that check).
#
# Measured 2026-10-17 (job 1044307, .../facet-validation-20261017/measure2), same method: both
# references hit the 120 s agent timeout in rerun 1044061 and pass with 4 CPUs, 8192 MiB, 480 s.
#   task      peak MiB  CPU s   step s  CPU s/wall  -> cpus  memory_mb  agent timeout_sec
#   000733       606.9   212.1    68.8    3.1            2       2048        240
#   002085      2342.9   117.3    68.9    1.7            2       4096        180
RESOURCE_LIMITS = {
    'task_000536': {'cpus': 2, 'memory_mb': 2048, 'agent_timeout_sec': 1800},
    'task_002890': {'cpus': 2, 'memory_mb': 4096, 'agent_timeout_sec': 180},
    'task_004333': {'cpus': 2, 'memory_mb': 4096, 'agent_timeout_sec': 180},
    'task_004857': {'cpus': 2, 'memory_mb': 4096, 'agent_timeout_sec': 240},
    'task_004399': {'cpus': 1, 'memory_mb': 8192, 'agent_timeout_sec': 120},
    'task_000733': {'cpus': 2, 'memory_mb': 2048, 'agent_timeout_sec': 240},
    'task_002085': {'cpus': 2, 'memory_mb': 4096, 'agent_timeout_sec': 180},
}
UPSTREAM_LIMITS = ('\n[agent]\ntimeout_sec = 120.0\n', '\ncpus = 1\n', '\nmemory_mb = 2048\n')


# Reviewed 2026-10-17 (full run 1040937 and rerun 1044061): task_006168's test.sh writes reward 0 and
# exits before pytest when outputs are missing, so the no-op never shows that the tests ran
# ("verifier execution evidence is missing" in 4 of 4 attempts). Without the shortcut the tests fail
# on the missing outputs themselves; rewards are unchanged.
VERIFIER_PRECHECKS = {'task_006168': (
    "\n# Fast pre-check: if the solution didn't run, required outputs won't exist.\n"
    "# Fail immediately to save time and ensure no-op gets reward 0.\n"
    'REQUIRED_OUTPUTS=(\n  "/task_file/output/dashboard_spec.json"\n  "/task_file/output/deployment_manifest.json"\n'
    '  "/task_file/output/content_bundle.json"\n  "/task_file/clipboard/preview_url.txt"\n)\n'
    'for f in "${REQUIRED_OUTPUTS[@]}"; do\n  if [ ! -f "$f" ] || [ ! -s "$f" ]; then\n'
    '    echo "ERROR: Required output missing or empty: $f"\n    echo 0 > /logs/verifier/reward.txt\n'
    '    exit 0\n  fi\ndone\n')}


def remove_verifier_precheck(files, task_id):
    """Drop a reviewed early exit that skips the test suite."""
    block = VERIFIER_PRECHECKS.get(task_id)
    if block is None or 'tests/test.sh' not in files:
        return files
    text = files['tests/test.sh'].decode('utf-8')
    if block not in text:
        if 'Fast pre-check' in text:
            raise ValueError(f'{task_id}: review the verifier pre-check again')
        return files
    result = dict(files)
    result['tests/test.sh'] = text.replace(block, '', 1).encode('utf-8')
    return result


def measured_resource_limits(files, task_id):
    """Apply the reviewed measured limits of one task to its task.toml."""
    limits = RESOURCE_LIMITS.get(task_id)
    if limits is None:
        return files
    text = files['task.toml'].decode('utf-8')
    replacements = (f"\n[agent]\ntimeout_sec = {float(limits['agent_timeout_sec'])}\n",
                    f"\ncpus = {limits['cpus']}\n", f"\nmemory_mb = {limits['memory_mb']}\n")
    for old, new in zip(UPSTREAM_LIMITS, replacements):
        if text.count(old) != 1:
            raise ValueError(f'{task_id}: upstream resource limits changed; measure again')
        text = text.replace(old, new)
    parsed = tomllib.loads(text)
    if (parsed['agent']['timeout_sec'], parsed['environment']['cpus'], parsed['environment']['memory_mb']) != (
            limits['agent_timeout_sec'], limits['cpus'], limits['memory_mb']):
        raise ValueError(f'{task_id}: resource limits were not applied')
    result = dict(files)
    result['task.toml'] = text.encode('utf-8')
    return result


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
             "relocated": False, "recopied": False, "sequenced": False,
             "baked": False, "separated": False, "split": None}
    files["task.toml"] = renamed
    limited = measured_resource_limits(files, task_id)
    facts["resource_limits"] = limited != files
    files = limited
    local_tmp = destination_local_pdf_tmp(files, task_id)
    facts["local_pdf_tmp"] = local_tmp != files
    files = local_tmp
    prechecked = remove_verifier_precheck(files, task_id)
    facts["verifier_precheck_removed"] = prechecked != files
    files = prechecked
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
        recopied = keep_recopied_files(relocated)
        facts["recopied"] = recopied != relocated
        files = docker_shell_semantics(recopied)
        facts["sequenced"] = files != recopied
    office_files = libreoffice_library_compatibility(files, task_id)
    facts["libreoffice_libraries"] = office_files != files
    files = office_files
    tool_files = install_missing_tools(files, task_id)
    facts["missing_tools"] = tool_files != files
    files = tool_files
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
    return (list(dict.fromkeys(label for label, _ in applied)),
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
                                           for label in {label for name, label, _ in CHANGES if fact[name]})),
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
    plans, dropped = {}, {}
    for batch in pq.ParquetFile(source).iter_batches(batch_size=16):
        for row in batch.to_pylist():
            excluded = ownership_exclusion(row["path"], row["task_binary"])
            if excluded:
                dropped[row["path"]] = excluded
                continue
            _, files, modes = read_task(row["task_binary"], row["path"])
            plans[row["path"]] = classify(files, modes)
    shared = group_tasks(plans)
    temporary = target.with_suffix(".parquet.tmp")
    facts, labels, reasons = {}, {}, {}
    try:
        with pq.ParquetWriter(temporary, SCHEMA, compression="zstd") as writer:
            for batch in pq.ParquetFile(source).iter_batches(batch_size=16):
                rows = [row for row in batch.to_pylist() if row["path"] not in dropped]
                if not rows:
                    continue
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
    summary["archived_tasks"] = len(dropped)
    (output / "tasks.image-plan.json").write_text(json.dumps(summary, indent=2) + "\n")
    counts = {name: value for name, value in summary.items() if isinstance(value, int) or name == "labels"}
    write_patch_report(
        source, target, patcher=__file__, dropped=dropped,
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


def prepare_runtime(files, image, probe, binding, bundle, catalog):
    """Adapt an already-patched, image-bound task using measured FACET evidence.

    Called separately from source conversion because its image identity must be
    verified after the original patcher's image and setup transformations.
    """
    from data.facet import verifier_runtime
    planned = verifier_runtime.plan(files, image, probe, binding, bundle)
    return verifier_runtime.apply(files, planned, catalog), planned


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("tasks", type=Path, nargs="?")
    parser.add_argument("--source-parquet", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--source-revision", default=SOURCE["revision"])
    parser.add_argument("--published-input", type=Path)
    parser.add_argument("--published-revision")
    parser.add_argument("--published-subset", choices=['facet_v1', 'facet_v2', 'facet_v3'])
    parser.add_argument("--runtime-image-inventory", type=Path)
    parser.add_argument("--runtime-probe", type=Path)
    parser.add_argument("--runtime-cache", type=Path)
    args = parser.parse_args()
    runtime_options = (args.published_input, args.published_revision, args.published_subset,
                       args.runtime_image_inventory, args.runtime_probe, args.runtime_cache)
    if any(runtime_options):
        if not all(runtime_options) or not args.output or args.tasks or args.source_parquet:
            parser.error('Published runtime migration requires all --published-* and --runtime-* options plus --output')
        from data.facet.verifier_runtime import migrate_published
        result = migrate_published(args.published_input, args.output, revision=args.published_revision,
            subset=args.published_subset, image=json.loads(args.runtime_image_inventory.read_text()),
            probe=json.loads(args.runtime_probe.read_text()), cache=args.runtime_cache)
        print(json.dumps(result['counts'], indent=2))
        return
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


# Archived tasks: task ID -> category, reason, evidence and the SHA-256 of the original source task
# binary (a changed source must be reviewed again). All reviewed by hand; see each reason.
EXCLUSIONS = {
    "task_000241": {
        "category": "hidden-output-requirement",
        "image_key": "n/a",
        "reason": "task_000241: the tests require files the instruction doesn't name: original_current.txt. The solution creates them and the tests check them, but instruction.md and task.toml never mention them, so an agent cannot know they are required. Observed in stage-4 check-test-file-references.sh in FACET full run 2026-10-17 (array 1040937), rechecked with the improved check (URL routes, regular expressions, PDF name objects, archive members, harness scripts, environment-variable defaults and punctuation no longer count) on the validated task versions. Archived with the user's authorization on 2026-10-17; original task payload retained.",
        "evidence": "stage-4 check-test-file-references.sh in FACET full run 2026-10-17 (array 1040937), rechecked with the improved check (URL routes, regular expressions, PDF name objects, archive members, harness scripts, environment-variable defaults and punctuation no longer count) on the validated task versions",
        "source_binary_sha256": "938625a19208213c346d2f9b25be30c9aecaad9250e3e74b56d4c9703ec30952"
    },
    "task_000387": {
        "category": "hidden-output-requirement",
        "image_key": "n/a",
        "reason": "task_000387: the tests require files the instruction doesn't name: /task_file/input/ppt_gen_7c2d4f8a1b3e.pptx. The solution creates them and the tests check them, but instruction.md and task.toml never mention them, so an agent cannot know they are required. Observed in stage-4 check-test-file-references.sh in FACET full run 2026-10-17 (array 1040937), rechecked with the improved check (URL routes, regular expressions, PDF name objects, archive members, harness scripts, environment-variable defaults and punctuation no longer count) on the validated task versions. Archived with the user's authorization on 2026-10-17; original task payload retained.",
        "evidence": "stage-4 check-test-file-references.sh in FACET full run 2026-10-17 (array 1040937), rechecked with the improved check (URL routes, regular expressions, PDF name objects, archive members, harness scripts, environment-variable defaults and punctuation no longer count) on the validated task versions",
        "source_binary_sha256": "52cb2304ae6891cab955c8d9c7e2252d210fb9b69cff4e69673dd376a3dfa95a"
    },
    "task_000397": {
        "category": "hidden-output-requirement",
        "image_key": "n/a",
        "reason": "task_000397: the tests require files the instruction doesn't name: config.yaml. The solution creates them and the tests check them, but instruction.md and task.toml never mention them, so an agent cannot know they are required. Observed in stage-4 check-test-file-references.sh in FACET full run 2026-10-17 (array 1040937), rechecked with the improved check (URL routes, regular expressions, PDF name objects, archive members, harness scripts, environment-variable defaults and punctuation no longer count) on the validated task versions. Archived with the user's authorization on 2026-10-17; original task payload retained.",
        "evidence": "stage-4 check-test-file-references.sh in FACET full run 2026-10-17 (array 1040937), rechecked with the improved check (URL routes, regular expressions, PDF name objects, archive members, harness scripts, environment-variable defaults and punctuation no longer count) on the validated task versions",
        "source_binary_sha256": "b90deac5bfb97e924d70bf7733522e6e0f11f16ec62b3a5bb86998dab82f1ca7"
    },
    "task_000447": {
        "category": "hidden-output-requirement",
        "image_key": "n/a",
        "reason": "task_000447: the tests require files the instruction doesn't name: new_version.docx, old_version.docx. The solution creates them and the tests check them, but instruction.md and task.toml never mention them, so an agent cannot know they are required. Observed in stage-4 check-test-file-references.sh in FACET full run 2026-10-17 (array 1040937), rechecked with the improved check (URL routes, regular expressions, PDF name objects, archive members, harness scripts, environment-variable defaults and punctuation no longer count) on the validated task versions. Archived with the user's authorization on 2026-10-17; original task payload retained.",
        "evidence": "stage-4 check-test-file-references.sh in FACET full run 2026-10-17 (array 1040937), rechecked with the improved check (URL routes, regular expressions, PDF name objects, archive members, harness scripts, environment-variable defaults and punctuation no longer count) on the validated task versions",
        "source_binary_sha256": "807663cc00eb17b02b4636092bc38986e8773ebbf5ead4e7b7ae7b548759bb58"
    },
    "task_000546": {
        "category": "hidden-output-requirement",
        "image_key": "n/a",
        "reason": "task_000546: the tests require files the instruction doesn't name: /task_file/input/segment_02_transcoded.mp4, /task_file/input/segment_03_transcoded.mp4, /task_file/input/segment_04_transcoded.mp4. The solution creates them and the tests check them, but instruction.md and task.toml never mention them, so an agent cannot know they are required. Observed in stage-4 check-test-file-references.sh in FACET full run 2026-10-17 (array 1040937), rechecked with the improved check (URL routes, regular expressions, PDF name objects, archive members, harness scripts, environment-variable defaults and punctuation no longer count) on the validated task versions. Archived with the user's authorization on 2026-10-17; original task payload retained.",
        "evidence": "stage-4 check-test-file-references.sh in FACET full run 2026-10-17 (array 1040937), rechecked with the improved check (URL routes, regular expressions, PDF name objects, archive members, harness scripts, environment-variable defaults and punctuation no longer count) on the validated task versions",
        "source_binary_sha256": "ae4f0bf184147f01475e2ad3233e3471c1fcf17ae6474f0c29fd9b2c7cc4e27d"
    },
    "task_000601": {
        "category": "hidden-output-requirement",
        "image_key": "n/a",
        "reason": "task_000601: the tests require files the instruction doesn't name: enriched_results.json. The solution creates them and the tests check them, but instruction.md and task.toml never mention them, so an agent cannot know they are required. Observed in stage-4 check-test-file-references.sh in FACET full run 2026-10-17 (array 1040937), rechecked with the improved check (URL routes, regular expressions, PDF name objects, archive members, harness scripts, environment-variable defaults and punctuation no longer count) on the validated task versions. Archived with the user's authorization on 2026-10-17; original task payload retained.",
        "evidence": "stage-4 check-test-file-references.sh in FACET full run 2026-10-17 (array 1040937), rechecked with the improved check (URL routes, regular expressions, PDF name objects, archive members, harness scripts, environment-variable defaults and punctuation no longer count) on the validated task versions",
        "source_binary_sha256": "737432e2249af195e58f3077ed6a263ac87fe9d610a510f40f4ea774177a8af4"
    },
    "task_000641": {
        "category": "hidden-output-requirement",
        "image_key": "n/a",
        "reason": "task_000641: the tests require files the instruction doesn't name: video_metadata.json. The solution creates them and the tests check them, but instruction.md and task.toml never mention them, so an agent cannot know they are required. Observed in stage-4 check-test-file-references.sh in FACET full run 2026-10-17 (array 1040937), rechecked with the improved check (URL routes, regular expressions, PDF name objects, archive members, harness scripts, environment-variable defaults and punctuation no longer count) on the validated task versions. Archived with the user's authorization on 2026-10-17; original task payload retained.",
        "evidence": "stage-4 check-test-file-references.sh in FACET full run 2026-10-17 (array 1040937), rechecked with the improved check (URL routes, regular expressions, PDF name objects, archive members, harness scripts, environment-variable defaults and punctuation no longer count) on the validated task versions",
        "source_binary_sha256": "d3704d49c410189c71d9e5277c27187efc741970bc58e88ea3b819f85f36580e"
    },
    "task_000648": {
        "category": "upstream-reference-failure",
        "image_key": "n/a",
        "reason": "task_000648: the upstream reference solution fails the task's own tests: solve.sh stops with KeyError: 'generated_by' before writing its output, so test_state.py fails with AssertionError: /task_file/knowledge_base.json does not exist. Observed in FACET manual validation 2026-10-16, reference job 1038707, /hnvme/workspace/y500bb12-optiagent/facet-manual-validation-20261016/reference/array/shards/0024/evidence.tar.gz member results/stage_3_oracle_validation/76d1d2aa7e77/jobs/job/task_000648__f6P46Qc/attempts/000/verifier/test-stdout.txt. Archived on 2026-10-17 per the user's handover decision; original task payload retained.",
        "evidence": "FACET manual validation 2026-10-16, reference job 1038707, /hnvme/workspace/y500bb12-optiagent/facet-manual-validation-20261016/reference/array/shards/0024/evidence.tar.gz member results/stage_3_oracle_validation/76d1d2aa7e77/jobs/job/task_000648__f6P46Qc/attempts/000/verifier/test-stdout.txt",
        "source_binary_sha256": "170c31197e13439344c8049c811af2a112a42787d8112be0b0edc83b02491353"
    },
    "task_000757": {
        "category": "hidden-output-requirement",
        "image_key": "n/a",
        "reason": "task_000757: the tests require files the instruction doesn't name: /task_file/output/shortened_clip.mp4. The solution creates them and the tests check them, but instruction.md and task.toml never mention them, so an agent cannot know they are required. Observed in stage-4 check-test-file-references.sh in FACET full run 2026-10-17 (array 1040937), rechecked with the improved check (URL routes, regular expressions, PDF name objects, archive members, harness scripts, environment-variable defaults and punctuation no longer count) on the validated task versions. Archived with the user's authorization on 2026-10-17; original task payload retained.",
        "evidence": "stage-4 check-test-file-references.sh in FACET full run 2026-10-17 (array 1040937), rechecked with the improved check (URL routes, regular expressions, PDF name objects, archive members, harness scripts, environment-variable defaults and punctuation no longer count) on the validated task versions",
        "source_binary_sha256": "7c0a79831d3a7263d554c09664e329bdbd46f09e614009a489dfd3a604a18c1c"
    },
    "task_000761": {
        "category": "hidden-output-requirement",
        "image_key": "n/a",
        "reason": "task_000761: the tests require files the instruction doesn't name: behavioral_constraints.json, biographical_memories.json, bound_actions.json, skill_pack.json, thinking_patterns.json, voice_style.json. The solution creates them and the tests check them, but instruction.md and task.toml never mention them, so an agent cannot know they are required. Observed in stage-4 check-test-file-references.sh in FACET full run 2026-10-17 (array 1040937), rechecked with the improved check (URL routes, regular expressions, PDF name objects, archive members, harness scripts, environment-variable defaults and punctuation no longer count) on the validated task versions. Archived with the user's authorization on 2026-10-17; original task payload retained.",
        "evidence": "stage-4 check-test-file-references.sh in FACET full run 2026-10-17 (array 1040937), rechecked with the improved check (URL routes, regular expressions, PDF name objects, archive members, harness scripts, environment-variable defaults and punctuation no longer count) on the validated task versions",
        "source_binary_sha256": "46f39b43ffe18d5d0d3ac6dd72f0e3a74c736e92dffd953e36aabe7338d2008f"
    },
    "task_000771": {
        "category": "hidden-output-requirement",
        "image_key": "n/a",
        "reason": "task_000771: the tests require files the instruction doesn't name: quarterly_review.pdf. The solution creates them and the tests check them, but instruction.md and task.toml never mention them, so an agent cannot know they are required. Observed in stage-4 check-test-file-references.sh in FACET full run 2026-10-17 (array 1040937), rechecked with the improved check (URL routes, regular expressions, PDF name objects, archive members, harness scripts, environment-variable defaults and punctuation no longer count) on the validated task versions. Archived with the user's authorization on 2026-10-17; original task payload retained.",
        "evidence": "stage-4 check-test-file-references.sh in FACET full run 2026-10-17 (array 1040937), rechecked with the improved check (URL routes, regular expressions, PDF name objects, archive members, harness scripts, environment-variable defaults and punctuation no longer count) on the validated task versions",
        "source_binary_sha256": "278d95a195142a3be440bbe2b6ed5b3f7c6ec805b1c4bc6cd5f8a91f43257e6a"
    },
    "task_000816": {
        "category": "hidden-output-requirement",
        "image_key": "n/a",
        "reason": "task_000816: the tests require files the instruction doesn't name: index.json. The solution creates them and the tests check them, but instruction.md and task.toml never mention them, so an agent cannot know they are required. Observed in stage-4 check-test-file-references.sh in FACET full run 2026-10-17 (array 1040937), rechecked with the improved check (URL routes, regular expressions, PDF name objects, archive members, harness scripts, environment-variable defaults and punctuation no longer count) on the validated task versions. Archived with the user's authorization on 2026-10-17; original task payload retained.",
        "evidence": "stage-4 check-test-file-references.sh in FACET full run 2026-10-17 (array 1040937), rechecked with the improved check (URL routes, regular expressions, PDF name objects, archive members, harness scripts, environment-variable defaults and punctuation no longer count) on the validated task versions",
        "source_binary_sha256": "2f0b8042eef75d83c99dfed4d924ada6e36cb473d3bb7a39a8b3ea1313e814da"
    },
    "task_000822": {
        "category": "hidden-output-requirement",
        "image_key": "n/a",
        "reason": "task_000822: the tests require files the instruction doesn't name: segment_page.pdf. The solution creates them and the tests check them, but instruction.md and task.toml never mention them, so an agent cannot know they are required. Observed in stage-4 check-test-file-references.sh in FACET full run 2026-10-17 (array 1040937), rechecked with the improved check (URL routes, regular expressions, PDF name objects, archive members, harness scripts, environment-variable defaults and punctuation no longer count) on the validated task versions. Archived with the user's authorization on 2026-10-17; original task payload retained.",
        "evidence": "stage-4 check-test-file-references.sh in FACET full run 2026-10-17 (array 1040937), rechecked with the improved check (URL routes, regular expressions, PDF name objects, archive members, harness scripts, environment-variable defaults and punctuation no longer count) on the validated task versions",
        "source_binary_sha256": "68e24433e5e7811bacdb55b30fd32a32bebf5291c5c9b654bf8fcc0c039fa33e"
    },
    "task_000830": {
        "category": "upstream-reference-failure",
        "image_key": "n/a",
        "reason": "task_000830: the upstream reference solution fails the task's own tests: 1 of 38 tests fails: the report written by the reference contains an https:// URL that the test forbids (AssertionError: Report contains https://). Observed in FACET manual validation 2026-10-16, reference job 1038707, /hnvme/workspace/y500bb12-optiagent/facet-manual-validation-20261016/reference/array/shards/0047/evidence.tar.gz member results/stage_3_oracle_validation/592d9786fdf3/jobs/job/task_000830__bLrNoGe/attempts/000/verifier/test-stdout.txt. Archived on 2026-10-17 per the user's handover decision; original task payload retained.",
        "evidence": "FACET manual validation 2026-10-16, reference job 1038707, /hnvme/workspace/y500bb12-optiagent/facet-manual-validation-20261016/reference/array/shards/0047/evidence.tar.gz member results/stage_3_oracle_validation/592d9786fdf3/jobs/job/task_000830__bLrNoGe/attempts/000/verifier/test-stdout.txt",
        "source_binary_sha256": "7b7644cff460576c00808ddc54957772b203a46fa1b8fb5194c16e4cfd1c2aec"
    },
    "task_000938": {
        "category": "backend_build_ownership",
        "image_key": "472c935b9833",
        "reason": "task_000938: fontconfig-config installation fails because its post-installation script changes ownership of /usr/local/share/fonts to a group unavailable in the single-UID Apptainer build namespace on Helma/CLAIX (no subordinate UID/GID mappings; chown: Invalid argument). The dependent font/graphics packages (LibreOffice, cairo, poppler) remain unconfigured and the image build fails. The Dockerfile builds under Docker; this is a runtime incompatibility of our build backend, not a task defect. Observed in FACET manual image retry 2026-10-16, CLAIX job 5074096 (/hnvme/workspace/y500bb12-optiagent/facet-manual-image-retry-20261016/rwth-results/472c935b9833.build.log). Archived with the user's authorization on 2026-10-16; original task payload retained.",
        "evidence": "FACET manual image retry 2026-10-16, CLAIX job 5074096 (/hnvme/workspace/y500bb12-optiagent/facet-manual-image-retry-20261016/rwth-results/472c935b9833.build.log)",
        "source_binary_sha256": "dc0bc5203acd19cfbb21845c65226db14969022939a829174d1d9a472eba97f8"
    },
    "task_001020": {
        "category": "upstream-reference-failure",
        "image_key": "n/a",
        "reason": "task_001020: the upstream reference solution fails the task's own tests: 1 of 19 tests fails: AssertionError: manifest_id does not match source ('unknown' == ''). Observed in FACET manual validation 2026-10-16, reference job 1038707, /hnvme/workspace/y500bb12-optiagent/facet-manual-validation-20261016/reference/array/shards/0011/evidence.tar.gz member results/stage_3_oracle_validation/2031e3030599/jobs/job/task_001020__ufVLuYr/attempts/000/verifier/test-stdout.txt. Archived on 2026-10-17 per the user's handover decision; original task payload retained.",
        "evidence": "FACET manual validation 2026-10-16, reference job 1038707, /hnvme/workspace/y500bb12-optiagent/facet-manual-validation-20261016/reference/array/shards/0011/evidence.tar.gz member results/stage_3_oracle_validation/2031e3030599/jobs/job/task_001020__ufVLuYr/attempts/000/verifier/test-stdout.txt",
        "source_binary_sha256": "a5c1b64aaadb4f17196933d8768d342a77ea61be80b357ddc7d3098d66721d70"
    },
    "task_001224": {
        "category": "upstream-reference-failure",
        "image_key": "n/a",
        "reason": "task_001224: the upstream reference solution fails the task's own tests: 1 of 14 tests fails (test_report_xlsx_fixture_provenance): AssertionError: task_id appears to be a placeholder: 'unknown'. Observed in FACET manual validation 2026-10-16, reference job 1038707, /hnvme/workspace/y500bb12-optiagent/facet-manual-validation-20261016/reference/array/shards/0039/evidence.tar.gz member results/stage_3_oracle_validation/2e334a5cad24/jobs/job/task_001224__XV6wGGD/attempts/000/verifier/test-stdout.txt. Archived on 2026-10-17 per the user's handover decision; original task payload retained.",
        "evidence": "FACET manual validation 2026-10-16, reference job 1038707, /hnvme/workspace/y500bb12-optiagent/facet-manual-validation-20261016/reference/array/shards/0039/evidence.tar.gz member results/stage_3_oracle_validation/2e334a5cad24/jobs/job/task_001224__XV6wGGD/attempts/000/verifier/test-stdout.txt",
        "source_binary_sha256": "0f5514187859063f1bc7e86f347826faff0e14e2de40dd5dc08e25cb6cfa905e"
    },
    "task_001335": {
        "category": "upstream-reference-failure",
        "image_key": "e784bc7dc1ad",
        "reason": "task_001335: the upstream reference solution fails its own test: test_manifest_summary_declared_fixtures requires every declared fixture in the summary to carry a `path` key, but the reference copies the task's fixture manifest, which names them `file` (AssertionError: Declared fixture missing 'path'). 26 of 27 tests pass; the reference and the test disagree, so the task cannot be scored reliably. Observed in FACET manual validation 2026-10-16, reference job 1038707, /hnvme/workspace/y500bb12-optiagent/facet-manual-validation-20261016/reference/array/shards/0002/evidence.tar.gz member results/stage_3_oracle_validation/046894cb0ed0/jobs/job/task_001335__iCd9aRd/attempts/000/verifier/test-stdout.txt. Archived with the user's authorization on 2026-10-16; original task payload retained.",
        "evidence": "FACET manual validation 2026-10-16, reference job 1038707, /hnvme/workspace/y500bb12-optiagent/facet-manual-validation-20261016/reference/array/shards/0002/evidence.tar.gz member results/stage_3_oracle_validation/046894cb0ed0/jobs/job/task_001335__iCd9aRd/attempts/000/verifier/test-stdout.txt",
        "source_binary_sha256": "4c7bb64f9c942d608ce34cc536847b391cdced16502e87687beb0ed72d09d722"
    },
    "task_001342": {
        "category": "upstream-reference-failure",
        "image_key": "n/a",
        "reason": "task_001342: the upstream reference solution fails the task's own tests: 1 of 26 tests fails: AssertionError: Output must not contain https:// URLs; the reference writes one. Observed in FACET manual validation 2026-10-16, reference job 1038707, /hnvme/workspace/y500bb12-optiagent/facet-manual-validation-20261016/reference/array/shards/0008/evidence.tar.gz member results/stage_3_oracle_validation/d0a7188ed9cc/jobs/job/task_001342__9jLYL3q/attempts/000/verifier/test-stdout.txt. Archived on 2026-10-17 per the user's handover decision; original task payload retained.",
        "evidence": "FACET manual validation 2026-10-16, reference job 1038707, /hnvme/workspace/y500bb12-optiagent/facet-manual-validation-20261016/reference/array/shards/0008/evidence.tar.gz member results/stage_3_oracle_validation/d0a7188ed9cc/jobs/job/task_001342__9jLYL3q/attempts/000/verifier/test-stdout.txt",
        "source_binary_sha256": "7e8d2b70f1d87ca17afe55653d4754ec4a15d1d5bc5da3a2a2de82071411c94b"
    },
    "task_001581": {
        "category": "upstream-image-build-failure",
        "image_key": "d992f27b19cb",
        "reason": "task_001581: the Dockerfile step `mkdir -p /task_file/workspace/.ai-dev/{docs,specs,tasks,notes} && ... touch /task_file/workspace/.ai-dev/docs/.gitkeep` uses bash brace expansion, but Docker runs RUN with /bin/sh (dash on ubuntu:22.04), which creates one directory literally named `{docs,specs,tasks,notes}`; the following touch fails (`touch: cannot touch '/task_file/workspace/.ai-dev/docs/.gitkeep': No such file or directory`). The step fails the same way under Docker, so the upstream environment cannot be built. Observed in FACET manual validation 2026-10-16: stage 1 failed in build shard 0033 (array 1035791) and again in rerun job 1038816 (/hnvme/workspace/y500bb12-optiagent/facet-manual-validation-20261016/build-rerun-1/results/submissions/47dd37117e70/evidence.tar.gz, member results/stage_1_build_validation/4762b031e5a6/00024-task_001581/preparation-review.json). Archived with the user's authorization on 2026-10-16; original task payload retained.",
        "evidence": "FACET manual validation 2026-10-16: stage 1 failed in build shard 0033 (array 1035791) and again in rerun job 1038816 (/hnvme/workspace/y500bb12-optiagent/facet-manual-validation-20261016/build-rerun-1/results/submissions/47dd37117e70/evidence.tar.gz, member results/stage_1_build_validation/4762b031e5a6/00024-task_001581/preparation-review.json)",
        "source_binary_sha256": "dae6dd2e5910823c42328e0c26f2fd9027a1439fb97126be9c3f1c41cb4976d7"
    },
    "task_001763": {
        "category": "upstream-reference-failure",
        "image_key": "n/a",
        "reason": "task_001763: the upstream reference solution fails the task's own tests: solve.sh stops with FileNotFoundError: /task_file/input/credentials.age, so both tests fail (output report missing). The fixture is created by build_scripts/generate_age_keys.sh, whose first command `age-keygen -o /task_file/input/age_keys.txt` refuses to overwrite the age_keys.txt the task ships in task_file/input; under the Apptainer build every COPY is applied before the first RUN, so the generator stops and credentials.age is never created. Under Docker the generator runs first and the later COPY then replaces the generated key with the shipped one, so the encrypted bundle no longer matches the shipped key (inferred from the Dockerfile; not run under Docker). The fixture design is inconsistent in both orders. Observed in FACET manual validation 2026-10-16, reference job 1038707, /hnvme/workspace/y500bb12-optiagent/facet-manual-validation-20261016/reference/array/shards/0031/evidence.tar.gz member results/stage_3_oracle_validation/463ba20f7e02/jobs/job/task_001763__F9iXQaW/attempts/000/verifier/test-stdout.txt. Archived on 2026-10-17 per the user's handover decision; original task payload retained.",
        "evidence": "FACET manual validation 2026-10-16, reference job 1038707, /hnvme/workspace/y500bb12-optiagent/facet-manual-validation-20261016/reference/array/shards/0031/evidence.tar.gz member results/stage_3_oracle_validation/463ba20f7e02/jobs/job/task_001763__F9iXQaW/attempts/000/verifier/test-stdout.txt",
        "source_binary_sha256": "0b4c3c52c53138e98f1c8f699c52b85068583f975a41d76bfff6ddf9303454ef"
    },
    "task_001912": {
        "category": "hidden-output-requirement",
        "image_key": "n/a",
        "reason": "task_001912: the tests require files the instruction doesn't name: braindb.sqlite. The solution creates them and the tests check them, but instruction.md and task.toml never mention them, so an agent cannot know they are required. Observed in stage-4 check-test-file-references.sh in FACET full run 2026-10-17 (array 1040937), rechecked with the improved check (URL routes, regular expressions, PDF name objects, archive members, harness scripts, environment-variable defaults and punctuation no longer count) on the validated task versions. Archived with the user's authorization on 2026-10-17; original task payload retained.",
        "evidence": "stage-4 check-test-file-references.sh in FACET full run 2026-10-17 (array 1040937), rechecked with the improved check (URL routes, regular expressions, PDF name objects, archive members, harness scripts, environment-variable defaults and punctuation no longer count) on the validated task versions",
        "source_binary_sha256": "399126ab1720f627b2e29e87d212bf8f82cd8ff9f64109fbdf4df84cae6b4762"
    },
    "task_001920": {
        "category": "fragile-fixture-size",
        "image_key": "n/a",
        "reason": "task_001920: the test fails on \"Archive size changed: 4881 != 4880\": it pins the exact byte size of a fixture archive that is generated at container start, and in our environment that archive is 1 byte larger. The size depends on the node and the archiving tools, so the test is fragile and the task is archived. Observed in FACET full run 2026-10-17, array 1040937, /hnvme/workspace/y500bb12-optiagent/facet-validation-20261017/full-run/shards/0055/evidence.tar.gz member results/stage_2_oracle_validation/1ce6f8333d34/jobs/job/task_001920__rDZWSe3/attempts/000/verifier/test-stdout.txt. Archived with the user's authorization on 2026-10-17; original task payload retained.",
        "evidence": "FACET full run 2026-10-17, array 1040937, /hnvme/workspace/y500bb12-optiagent/facet-validation-20261017/full-run/shards/0055/evidence.tar.gz member results/stage_2_oracle_validation/1ce6f8333d34/jobs/job/task_001920__rDZWSe3/attempts/000/verifier/test-stdout.txt",
        "source_binary_sha256": "5233b4faf5be7b679450233c06687c3cde0af836eca38ad54571743ab9f605ab"
    },
    "task_002047": {
        "category": "upstream-image-build-failure",
        "image_key": "1fda1059251f",
        "reason": "task_002047: The fixture step `chmod +x /build_scripts/*.sh && bash /build_scripts/generate_media.sh && rm -rf /build_scripts` creates git repositories with plain `git init` and then uses a branch named main. Git 2.34.1 in ubuntu:22.04 names the initial branch master, so the step fails (`error: pathspec 'main' did not match any file(s) known to git`). The step fails the same way under Docker, so the upstream environment cannot be built. Observed in FACET generation 5 image-prebuild array 1014877, shard 0020, evidence.tar.gz member image-build-logs/1fda1059251f.build.log; original task payload retained.",
        "evidence": "FACET generation 5 image-prebuild array 1014877, shard 0020, evidence.tar.gz member image-build-logs/1fda1059251f.build.log",
        "source_binary_sha256": "b1406c481dfa16915abac3385510b4bd174af7f2711a574f96bc54fc9daf7507"
    },
    "task_002127": {
        "category": "hidden-output-requirement",
        "image_key": "n/a",
        "reason": "task_002127: the tests require files the instruction doesn't name: sunset_session.zip. The solution creates them and the tests check them, but instruction.md and task.toml never mention them, so an agent cannot know they are required. Observed in stage-4 check-test-file-references.sh in FACET full run 2026-10-17 (array 1040937), rechecked with the improved check (URL routes, regular expressions, PDF name objects, archive members, harness scripts, environment-variable defaults and punctuation no longer count) on the validated task versions. Archived with the user's authorization on 2026-10-17; original task payload retained.",
        "evidence": "stage-4 check-test-file-references.sh in FACET full run 2026-10-17 (array 1040937), rechecked with the improved check (URL routes, regular expressions, PDF name objects, archive members, harness scripts, environment-variable defaults and punctuation no longer count) on the validated task versions",
        "source_binary_sha256": "f478d3aabe7c2fa71b14ac955d2570ebca0e1c2c8b8696a165109e880a3423b8"
    },
    "task_002206": {
        "category": "hidden-output-requirement",
        "image_key": "n/a",
        "reason": "task_002206: the tests require files the instruction doesn't name: unknown.pdf. The solution creates them and the tests check them, but instruction.md and task.toml never mention them, so an agent cannot know they are required. Observed in stage-4 check-test-file-references.sh in FACET full run 2026-10-17 (array 1040937), rechecked with the improved check (URL routes, regular expressions, PDF name objects, archive members, harness scripts, environment-variable defaults and punctuation no longer count) on the validated task versions. Archived with the user's authorization on 2026-10-17; original task payload retained.",
        "evidence": "stage-4 check-test-file-references.sh in FACET full run 2026-10-17 (array 1040937), rechecked with the improved check (URL routes, regular expressions, PDF name objects, archive members, harness scripts, environment-variable defaults and punctuation no longer count) on the validated task versions",
        "source_binary_sha256": "052d71c4c7af6dc065ccd256c3f1daaa6d5b57a9520bdc07c95458701af8169d"
    },
    "task_002216": {
        "category": "hidden-output-requirement",
        "image_key": "n/a",
        "reason": "task_002216: the tests require files the instruction doesn't name: unknown.zip. The solution creates them and the tests check them, but instruction.md and task.toml never mention them, so an agent cannot know they are required. Observed in stage-4 check-test-file-references.sh in FACET full run 2026-10-17 (array 1040937), rechecked with the improved check (URL routes, regular expressions, PDF name objects, archive members, harness scripts, environment-variable defaults and punctuation no longer count) on the validated task versions. Archived with the user's authorization on 2026-10-17; original task payload retained.",
        "evidence": "stage-4 check-test-file-references.sh in FACET full run 2026-10-17 (array 1040937), rechecked with the improved check (URL routes, regular expressions, PDF name objects, archive members, harness scripts, environment-variable defaults and punctuation no longer count) on the validated task versions",
        "source_binary_sha256": "5ca26384274ae13bf1317e2e3e822af1c691b7ce329640a78b62203ae31a1d4c"
    },
    "task_002217": {
        "category": "hidden-output-requirement",
        "image_key": "n/a",
        "reason": "task_002217: the tests require files the instruction doesn't name: story_text.txt. The solution creates them and the tests check them, but instruction.md and task.toml never mention them, so an agent cannot know they are required. Observed in stage-4 check-test-file-references.sh in FACET full run 2026-10-17 (array 1040937), rechecked with the improved check (URL routes, regular expressions, PDF name objects, archive members, harness scripts, environment-variable defaults and punctuation no longer count) on the validated task versions. Archived with the user's authorization on 2026-10-17; original task payload retained.",
        "evidence": "stage-4 check-test-file-references.sh in FACET full run 2026-10-17 (array 1040937), rechecked with the improved check (URL routes, regular expressions, PDF name objects, archive members, harness scripts, environment-variable defaults and punctuation no longer count) on the validated task versions",
        "source_binary_sha256": "01646cc7cc709b08fcede7bf9b737ddbf80f8aa03582b69c01c07708ee3cfa57"
    },
    "task_002326": {
        "category": "upstream-reference-failure",
        "image_key": "n/a",
        "reason": "task_002326: the upstream reference solution fails the task's own tests: 1 of 28 tests fails: AssertionError: Input file =10.0.0 is missing or empty. The test treats `/task_file/=10.0.0` as a required input; that file is only the side effect of the unquoted `pip install ... Pillow>=10.0.0` in the Dockerfile (shell redirection into a file named =10.0.0 in the build's working directory). The Apptainer build runs RUN steps outside WORKDIR, so the file lands elsewhere; the test depends on an accidental build artifact. Observed in FACET manual validation 2026-10-16, reference job 1038707, /hnvme/workspace/y500bb12-optiagent/facet-manual-validation-20261016/reference/array/shards/0042/evidence.tar.gz member results/stage_3_oracle_validation/3368b2627952/jobs/job/task_002326__TsdCEwd/attempts/000/verifier/test-stdout.txt. Archived on 2026-10-17 per the user's handover decision; original task payload retained.",
        "evidence": "FACET manual validation 2026-10-16, reference job 1038707, /hnvme/workspace/y500bb12-optiagent/facet-manual-validation-20261016/reference/array/shards/0042/evidence.tar.gz member results/stage_3_oracle_validation/3368b2627952/jobs/job/task_002326__TsdCEwd/attempts/000/verifier/test-stdout.txt",
        "source_binary_sha256": "2e5906b8c7126120b8d8cc0468cf9283711c51075b3e84900fee284522b0e02b"
    },
    "task_002471": {
        "category": "upstream-image-build-failure",
        "image_key": "16765d6409d3",
        "reason": "task_002471: The Dockerfile step `python3 -m pip install --no-cache-dir blake3 pyblake2 python-magic` builds pyblake2 from source because the package has no binary wheel for the image's Python 3.10, but the image installs no C compiler (`error: command 'x86_64-linux-gnu-gcc' failed: No such file or directory`). The step fails the same way under Docker, so the upstream environment cannot be built. Observed in FACET generation 5 image-prebuild array 1014877, shard 0010, evidence.tar.gz member image-build-logs/16765d6409d3.build.log; original task payload retained.",
        "evidence": "FACET generation 5 image-prebuild array 1014877, shard 0010, evidence.tar.gz member image-build-logs/16765d6409d3.build.log",
        "source_binary_sha256": "5e0ac8df93622c653f4ae033b254d27358093805a3fc7e71cad25cc7f50e3462"
    },
    "task_002550": {
        "category": "external-service-dependency",
        "image_key": "n/a",
        "reason": "task_002550: the reference solution synthesizes speech through the Bing/Edge read-aloud service (wss://speech.platform.bing.com/...), which rejects the connection (aiohttp WSServerHandshakeError: 403, Invalid response status); 6 of 8 tests fail because consolidated_report.json is never written. The task depends on an external service outside our control. Observed in FACET manual validation 2026-10-16, reference job 1038707, /hnvme/workspace/y500bb12-optiagent/facet-manual-validation-20261016/reference/array/shards/0028/evidence.tar.gz member results/stage_3_oracle_validation/1413a6d527c4/jobs/job/task_002550__xHTBQQp/attempts/000/agent/oracle.txt. Archived on 2026-10-17 per the user's handover decision; original task payload retained.",
        "evidence": "FACET manual validation 2026-10-16, reference job 1038707, /hnvme/workspace/y500bb12-optiagent/facet-manual-validation-20261016/reference/array/shards/0028/evidence.tar.gz member results/stage_3_oracle_validation/1413a6d527c4/jobs/job/task_002550__xHTBQQp/attempts/000/agent/oracle.txt",
        "source_binary_sha256": "148747c82b392aec565bbebf0187e515e4eae7b6374683203ff71e7ab61305fb"
    },
    "task_002555": {
        "category": "hidden-output-requirement",
        "image_key": "n/a",
        "reason": "task_002555: the tests require files the instruction doesn't name: index.json, /task_file/pkm/logged/design_log.md, /task_file/pkm/vectorized/vector_metadata.json, vector_metadata.json. The solution creates them and the tests check them, but instruction.md and task.toml never mention them, so an agent cannot know they are required. Observed in stage-4 check-test-file-references.sh in FACET full run 2026-10-17 (array 1040937), rechecked with the improved check (URL routes, regular expressions, PDF name objects, archive members, harness scripts, environment-variable defaults and punctuation no longer count) on the validated task versions. Archived with the user's authorization on 2026-10-17; original task payload retained.",
        "evidence": "stage-4 check-test-file-references.sh in FACET full run 2026-10-17 (array 1040937), rechecked with the improved check (URL routes, regular expressions, PDF name objects, archive members, harness scripts, environment-variable defaults and punctuation no longer count) on the validated task versions",
        "source_binary_sha256": "c20b6f2e213ae0fbb62c81ff9d33d7290cbd8142192fc8102569e16ed1cf6d46"
    },
    "task_002576": {
        "category": "hidden-output-requirement",
        "image_key": "n/a",
        "reason": "task_002576: the tests require files the instruction doesn't name: final_dubbed_video.mp4. The solution creates them and the tests check them, but instruction.md and task.toml never mention them, so an agent cannot know they are required. Observed in stage-4 check-test-file-references.sh in FACET full run 2026-10-17 (array 1040937), rechecked with the improved check (URL routes, regular expressions, PDF name objects, archive members, harness scripts, environment-variable defaults and punctuation no longer count) on the validated task versions. Archived with the user's authorization on 2026-10-17; original task payload retained.",
        "evidence": "stage-4 check-test-file-references.sh in FACET full run 2026-10-17 (array 1040937), rechecked with the improved check (URL routes, regular expressions, PDF name objects, archive members, harness scripts, environment-variable defaults and punctuation no longer count) on the validated task versions",
        "source_binary_sha256": "2d316f0ffc1356115d2286075b0c38d6165468c0f37e4a292ffce677f958b677"
    },
    "task_002730": {
        "category": "upstream-image-build-failure",
        "image_key": "64b4d3746cce",
        "reason": "task_002730: The fixture step `chmod +x /build_scripts/*.sh && /build_scripts/generate_repo.sh && /build_scripts/generate_api_fixtures.py && /build_scripts/generate_diagram.py && rm -rf /build_scripts` creates git repositories with plain `git init` and then uses a branch named main. Git 2.34.1 in ubuntu:22.04 names the initial branch master, so the step fails (`error: pathspec 'main' did not match any file(s) known to git`). The step fails the same way under Docker, so the upstream environment cannot be built. Observed in FACET generation 5 image-prebuild array 1014877, shard 0015, evidence.tar.gz member image-build-logs/64b4d3746cce.build.log; original task payload retained.",
        "evidence": "FACET generation 5 image-prebuild array 1014877, shard 0015, evidence.tar.gz member image-build-logs/64b4d3746cce.build.log",
        "source_binary_sha256": "7acf3b139243c8266b7e4deefd7862fe2c486832653c87b069b1599c88e16dc2"
    },
    "task_002746": {
        "category": "hidden-output-requirement",
        "image_key": "n/a",
        "reason": "task_002746: the tests require files the instruction doesn't name: lead_profiles.json, optimized_reply_templates.json. The solution creates them and the tests check them, but instruction.md and task.toml never mention them, so an agent cannot know they are required. Observed in stage-4 check-test-file-references.sh in FACET full run 2026-10-17 (array 1040937), rechecked with the improved check (URL routes, regular expressions, PDF name objects, archive members, harness scripts, environment-variable defaults and punctuation no longer count) on the validated task versions. Archived with the user's authorization on 2026-10-17; original task payload retained.",
        "evidence": "stage-4 check-test-file-references.sh in FACET full run 2026-10-17 (array 1040937), rechecked with the improved check (URL routes, regular expressions, PDF name objects, archive members, harness scripts, environment-variable defaults and punctuation no longer count) on the validated task versions",
        "source_binary_sha256": "df6a76fda9f65736b0a73ef67681dd4d9063d3d504f864a99b3bc037214afdeb"
    },
    "task_002823": {
        "category": "upstream-reference-failure",
        "image_key": "n/a",
        "reason": "task_002823: the upstream reference solution fails the task's own tests: 1 of 23 tests fails (test_video_minimum_size): AssertionError: Video file too small (112749 bytes), likely not a valid encode (assert 112749 > 115000); the reference's encode is below the test's size floor. Observed in FACET manual validation 2026-10-16, reference job 1038707, /hnvme/workspace/y500bb12-optiagent/facet-manual-validation-20261016/reference/array/shards/0052/evidence.tar.gz member results/stage_3_oracle_validation/40c3c9a36306/jobs/job/task_002823__xk8A3qM/attempts/000/verifier/test-stdout.txt. Archived on 2026-10-17 per the user's handover decision; original task payload retained.",
        "evidence": "FACET manual validation 2026-10-16, reference job 1038707, /hnvme/workspace/y500bb12-optiagent/facet-manual-validation-20261016/reference/array/shards/0052/evidence.tar.gz member results/stage_3_oracle_validation/40c3c9a36306/jobs/job/task_002823__xk8A3qM/attempts/000/verifier/test-stdout.txt",
        "source_binary_sha256": "84ffa8264bc762879cb93fbc7f016eb52c69af9c49c135739ed3fe0802d0ca31"
    },
    "task_003221": {
        "category": "hidden-output-requirement",
        "image_key": "n/a",
        "reason": "task_003221: the tests require files the instruction doesn't name: translated_pdf_sample.pdf. The solution creates them and the tests check them, but instruction.md and task.toml never mention them, so an agent cannot know they are required. Observed in stage-4 check-test-file-references.sh in FACET full run 2026-10-17 (array 1040937), rechecked with the improved check (URL routes, regular expressions, PDF name objects, archive members, harness scripts, environment-variable defaults and punctuation no longer count) on the validated task versions. Archived with the user's authorization on 2026-10-17; original task payload retained.",
        "evidence": "stage-4 check-test-file-references.sh in FACET full run 2026-10-17 (array 1040937), rechecked with the improved check (URL routes, regular expressions, PDF name objects, archive members, harness scripts, environment-variable defaults and punctuation no longer count) on the validated task versions",
        "source_binary_sha256": "05d2bf3dfe79d86af0de530000affba1e2ed31715e411414c32f990d868d7ebe"
    },
    "task_003224": {
        "category": "upstream-reference-failure",
        "image_key": "n/a",
        "reason": "task_003224: the upstream reference solution fails the task's own tests: 2 of 33 tests fail: the reference writes graph lines of type 'Project', which the test's VALID_TYPES set does not allow (AssertionError: Line 1 has invalid type 'Project'). Observed in FACET manual validation 2026-10-16, reference job 1038707, /hnvme/workspace/y500bb12-optiagent/facet-manual-validation-20261016/reference/array/shards/0050/evidence.tar.gz member results/stage_3_oracle_validation/ba470245a3f3/jobs/job/task_003224__m2FwZdi/attempts/000/verifier/test-stdout.txt. Archived on 2026-10-17 per the user's handover decision; original task payload retained.",
        "evidence": "FACET manual validation 2026-10-16, reference job 1038707, /hnvme/workspace/y500bb12-optiagent/facet-manual-validation-20261016/reference/array/shards/0050/evidence.tar.gz member results/stage_3_oracle_validation/ba470245a3f3/jobs/job/task_003224__m2FwZdi/attempts/000/verifier/test-stdout.txt",
        "source_binary_sha256": "46bf138683485c9e713f38531ca1dc98f9c99ed8aa1b6bbd9766c05064d7938d"
    },
    "task_003358": {
        "category": "hidden-output-requirement",
        "image_key": "n/a",
        "reason": "task_003358: the tests require files the instruction doesn't name: concatenated_output.mp4. The solution creates them and the tests check them, but instruction.md and task.toml never mention them, so an agent cannot know they are required. Observed in stage-4 check-test-file-references.sh in FACET full run 2026-10-17 (array 1040937), rechecked with the improved check (URL routes, regular expressions, PDF name objects, archive members, harness scripts, environment-variable defaults and punctuation no longer count) on the validated task versions. Archived with the user's authorization on 2026-10-17; original task payload retained.",
        "evidence": "stage-4 check-test-file-references.sh in FACET full run 2026-10-17 (array 1040937), rechecked with the improved check (URL routes, regular expressions, PDF name objects, archive members, harness scripts, environment-variable defaults and punctuation no longer count) on the validated task versions",
        "source_binary_sha256": "f6fc5e9b55656c79f8f94e250b64989b167677e4faa431487f3fbc1b6540bfe8"
    },
    "task_003362": {
        "category": "upstream-reference-failure",
        "image_key": "n/a",
        "reason": "task_003362: the upstream reference solution fails the task's own tests: 1 of 34 tests fails: AssertionError: Unexpected output file found: /task_file/output/.gitkeep. solve.sh stops before its own `rm -f /task_file/output/.gitkeep` (rm: cannot remove '/task_file/solve.sh', exit 1 under set -e). Observed in FACET manual validation 2026-10-16, reference job 1038707, /hnvme/workspace/y500bb12-optiagent/facet-manual-validation-20261016/reference/array/shards/0037/evidence.tar.gz member results/stage_3_oracle_validation/ec75ad284114/jobs/job/task_003362__5hM2BuD/attempts/000/verifier/test-stdout.txt. Archived on 2026-10-17 per the user's handover decision; original task payload retained.",
        "evidence": "FACET manual validation 2026-10-16, reference job 1038707, /hnvme/workspace/y500bb12-optiagent/facet-manual-validation-20261016/reference/array/shards/0037/evidence.tar.gz member results/stage_3_oracle_validation/ec75ad284114/jobs/job/task_003362__5hM2BuD/attempts/000/verifier/test-stdout.txt",
        "source_binary_sha256": "5e3b9c61130b354b377128d35bfbb74d54704c6932e504c1459e9f886285bfcc"
    },
    "task_003414": {
        "category": "conflicting-fixture-versions",
        "image_key": "n/a",
        "reason": "task_003414: the task ships two conflicting versions of input/fixture_manifest.json: build_scripts/generate_fixtures.py writes one (paths \"task_file/input/...\", no role field) and COPY task_file/ ships another (paths \"input/...\", role \"visible starting fixture\"). Docker runs the RUN first and the COPY then overwrites, so the shipped version wins; Apptainer applies COPY before RUN, so the generated version wins. The reference solution only lists entries of the shipped version, and the test requires the path of the manifest's second entry in the DOCX, so the outcome depends on which build order produced the image (passed in one run, failed in another: AssertionError: Fixture manifest path missing in DOCX). Observed in FACET full run 2026-10-17, array 1040937, /hnvme/workspace/y500bb12-optiagent/facet-validation-20261017/full-run/shards/0038/evidence.tar.gz member results/stage_2_oracle_validation/03f6b5c505d0/jobs/job/task_003414__RvT3tnU/attempts/000/verifier/test-stdout.txt (the earlier reference run 1038707 passed it). Archived with the user's authorization on 2026-10-17; original task payload retained.",
        "evidence": "FACET full run 2026-10-17, array 1040937, /hnvme/workspace/y500bb12-optiagent/facet-validation-20261017/full-run/shards/0038/evidence.tar.gz member results/stage_2_oracle_validation/03f6b5c505d0/jobs/job/task_003414__RvT3tnU/attempts/000/verifier/test-stdout.txt (the earlier reference run 1038707 passed it)",
        "source_binary_sha256": "c9675a3b97b9eb7c3e7adec156d956112bf003c9e1d83216c1a3cade99005a2e"
    },
    "task_003453": {
        "category": "upstream-image-build-failure",
        "image_key": "a7f351bba85c",
        "reason": "task_003453: The Dockerfile step `python3 -m pip install --no-cache-dir pyblake2` builds pyblake2 from source because the package has no binary wheel for the image's Python 3.10, but the image installs no C compiler (`error: command 'x86_64-linux-gnu-gcc' failed: No such file or directory`). The step fails the same way under Docker, so the upstream environment cannot be built. Observed in FACET generation 6 image-prebuild array 1025882, shard 0024, evidence.tar.gz member image-build-logs/a7f351bba85c.build.log; original task payload retained.",
        "evidence": "FACET generation 6 image-prebuild array 1025882, shard 0024, evidence.tar.gz member image-build-logs/a7f351bba85c.build.log",
        "source_binary_sha256": "c3a7e8365b913e15cc4fcd98bfdbd8ddb0d7016020cdc3181d3d1f940030e055"
    },
    "task_003573": {
        "category": "upstream-image-build-failure",
        "image_key": "98b63ba4045b",
        "reason": "task_003573: The Dockerfile step `python3 -m pip install --no-cache-dir python-docx==1.1.2 chromadb==0.5.23 sentence-transformers==3.3.1` aborts inside pip 22.0.2 from Ubuntu 22.04 (`assert len(weights) == expected_node_count`, AssertionError in the resolver's get_topological_weights, a pip defect fixed in later releases). The step fails the same way under Docker, so the upstream environment cannot be built. Observed in FACET generation 6 image-prebuild array 1025882, shard 0005, evidence.tar.gz member image-build-logs/98b63ba4045b.build.log; original task payload retained.",
        "evidence": "FACET generation 6 image-prebuild array 1025882, shard 0005, evidence.tar.gz member image-build-logs/98b63ba4045b.build.log",
        "source_binary_sha256": "cfa8b45ec7b640f198989ede2b53ce0cdc032f7179a3035af19b0f9fb944d8fb"
    },
    "task_003607": {
        "category": "hidden-output-requirement",
        "image_key": "n/a",
        "reason": "task_003607: the tests require files the instruction doesn't name: memory_pipeline_state.json. The solution creates them and the tests check them, but instruction.md and task.toml never mention them, so an agent cannot know they are required. Observed in stage-4 check-test-file-references.sh in FACET full run 2026-10-17 (array 1040937), rechecked with the improved check (URL routes, regular expressions, PDF name objects, archive members, harness scripts, environment-variable defaults and punctuation no longer count) on the validated task versions. Archived with the user's authorization on 2026-10-17; original task payload retained.",
        "evidence": "stage-4 check-test-file-references.sh in FACET full run 2026-10-17 (array 1040937), rechecked with the improved check (URL routes, regular expressions, PDF name objects, archive members, harness scripts, environment-variable defaults and punctuation no longer count) on the validated task versions",
        "source_binary_sha256": "9940ccf99aeb2ca301de2643798428ae2b90c13858bd40cdb6e6e449b172e08d"
    },
    "task_003630": {
        "category": "hidden-output-requirement",
        "image_key": "n/a",
        "reason": "task_003630: the tests require files the instruction doesn't name: decrypted.tar, detected_language.txt. The solution creates them and the tests check them, but instruction.md and task.toml never mention them, so an agent cannot know they are required. Observed in stage-4 check-test-file-references.sh in FACET full run 2026-10-17 (array 1040937), rechecked with the improved check (URL routes, regular expressions, PDF name objects, archive members, harness scripts, environment-variable defaults and punctuation no longer count) on the validated task versions. Archived with the user's authorization on 2026-10-17; original task payload retained.",
        "evidence": "stage-4 check-test-file-references.sh in FACET full run 2026-10-17 (array 1040937), rechecked with the improved check (URL routes, regular expressions, PDF name objects, archive members, harness scripts, environment-variable defaults and punctuation no longer count) on the validated task versions",
        "source_binary_sha256": "65dc911a0a4d051c6f3110fb03ca4c9eb0e3cbdca8f5e90a4630fbd1ab955079"
    },
    "task_003746": {
        "category": "hidden-output-requirement",
        "image_key": "n/a",
        "reason": "task_003746: the tests require files the instruction doesn't name: package.json. The solution creates them and the tests check them, but instruction.md and task.toml never mention them, so an agent cannot know they are required. Observed in stage-4 check-test-file-references.sh in FACET full run 2026-10-17 (array 1040937), rechecked with the improved check (URL routes, regular expressions, PDF name objects, archive members, harness scripts, environment-variable defaults and punctuation no longer count) on the validated task versions. Archived with the user's authorization on 2026-10-17; original task payload retained.",
        "evidence": "stage-4 check-test-file-references.sh in FACET full run 2026-10-17 (array 1040937), rechecked with the improved check (URL routes, regular expressions, PDF name objects, archive members, harness scripts, environment-variable defaults and punctuation no longer count) on the validated task versions",
        "source_binary_sha256": "22eec2be17e0cb4b66339fb3ae9b89f857f112cf2c21bca2e92add26764b7599"
    },
    "task_003862": {
        "category": "upstream-image-build-failure",
        "image_key": "4f5d4dad0c35",
        "reason": "task_003862: The Dockerfile step `python3 -m pip install --no-cache-dir pyblake2` builds pyblake2 from source because the package has no binary wheel for the image's Python 3.10, but the image installs no C compiler (`error: command 'x86_64-linux-gnu-gcc' failed: No such file or directory`). The step fails the same way under Docker, so the upstream environment cannot be built. Observed in FACET generation 6 image-prebuild array 1025882, shard 0007, evidence.tar.gz member image-build-logs/4f5d4dad0c35.build.log; original task payload retained.",
        "evidence": "FACET generation 6 image-prebuild array 1025882, shard 0007, evidence.tar.gz member image-build-logs/4f5d4dad0c35.build.log",
        "source_binary_sha256": "56cb4fb6cf03998c415bd7eb2dc9d0ec9c2c077b258d2e401dd5989397442ae4"
    },
    "task_003902": {
        "category": "hidden-output-requirement",
        "image_key": "n/a",
        "reason": "task_003902: the tests require files the instruction doesn't name: departments_reference.json, drugs_reference.json. The solution creates them and the tests check them, but instruction.md and task.toml never mention them, so an agent cannot know they are required. Observed in stage-4 check-test-file-references.sh in FACET full run 2026-10-17 (array 1040937), rechecked with the improved check (URL routes, regular expressions, PDF name objects, archive members, harness scripts, environment-variable defaults and punctuation no longer count) on the validated task versions. Archived with the user's authorization on 2026-10-17; original task payload retained.",
        "evidence": "stage-4 check-test-file-references.sh in FACET full run 2026-10-17 (array 1040937), rechecked with the improved check (URL routes, regular expressions, PDF name objects, archive members, harness scripts, environment-variable defaults and punctuation no longer count) on the validated task versions",
        "source_binary_sha256": "175b52ad3ae4a939147da8f329727ad6fd192d828fb36a93163dc93f20782b40"
    },
    "task_003903": {
        "category": "upstream-image-build-failure",
        "image_key": "bab28fb58899",
        "reason": "task_003903: The Dockerfile only runs `apt-cache policy` for python3, pip, venv and git and never installs them, so the fixture step `python3 /build_scripts/generate_csv_fixtures.py && python3 /build_scripts/generate_flight_fixture.py && python3 /build_scripts/generate_design_fixture.py && python3 /build_scripts/generate_platforms_fixture.py && python3 /build_scripts/generate_topics_fixture.py && rm -rf /build_scripts` fails with `/bin/sh: 1: python3: not found`. The step fails the same way under Docker, so the upstream environment cannot be built. Observed in FACET generation 6 image-prebuild array 1025882, shard 0011, evidence.tar.gz member image-build-logs/bab28fb58899.build.log; original task payload retained.",
        "evidence": "FACET generation 6 image-prebuild array 1025882, shard 0011, evidence.tar.gz member image-build-logs/bab28fb58899.build.log",
        "source_binary_sha256": "2ed4e735975472a847f94f7a4e62676fa066912c534bf0441cc5cba5b443122b"
    },
    "task_003959": {
        "category": "hidden-output-requirement",
        "image_key": "n/a",
        "reason": "task_003959: the tests require files the instruction doesn't name: /task_file/input/chapter1_intro.wav, /task_file/input/cover_generated.png. The solution creates them and the tests check them, but instruction.md and task.toml never mention them, so an agent cannot know they are required. Observed in stage-4 check-test-file-references.sh in FACET full run 2026-10-17 (array 1040937), rechecked with the improved check (URL routes, regular expressions, PDF name objects, archive members, harness scripts, environment-variable defaults and punctuation no longer count) on the validated task versions. Archived with the user's authorization on 2026-10-17; original task payload retained.",
        "evidence": "stage-4 check-test-file-references.sh in FACET full run 2026-10-17 (array 1040937), rechecked with the improved check (URL routes, regular expressions, PDF name objects, archive members, harness scripts, environment-variable defaults and punctuation no longer count) on the validated task versions",
        "source_binary_sha256": "c70f8181bc59e63c29769811b05691f7168f81cbddde271568f2485acd247c72"
    },
    "task_003971": {
        "category": "hidden-output-requirement",
        "image_key": "n/a",
        "reason": "task_003971: the tests require files the instruction doesn't name: frames_archive.zip. The solution creates them and the tests check them, but instruction.md and task.toml never mention them, so an agent cannot know they are required. Observed in stage-4 check-test-file-references.sh in FACET full run 2026-10-17 (array 1040937), rechecked with the improved check (URL routes, regular expressions, PDF name objects, archive members, harness scripts, environment-variable defaults and punctuation no longer count) on the validated task versions. Archived with the user's authorization on 2026-10-17; original task payload retained.",
        "evidence": "stage-4 check-test-file-references.sh in FACET full run 2026-10-17 (array 1040937), rechecked with the improved check (URL routes, regular expressions, PDF name objects, archive members, harness scripts, environment-variable defaults and punctuation no longer count) on the validated task versions",
        "source_binary_sha256": "b3362ed3fd254216fbd3a942930a24fd6eb0fb6453a363cec3b428d73c806933"
    },
    "task_004092": {
        "category": "hidden-output-requirement",
        "image_key": "n/a",
        "reason": "task_004092: the tests require files the instruction doesn't name: summary.txt. The solution creates them and the tests check them, but instruction.md and task.toml never mention them, so an agent cannot know they are required. Observed in stage-4 check-test-file-references.sh in FACET full run 2026-10-17 (array 1040937), rechecked with the improved check (URL routes, regular expressions, PDF name objects, archive members, harness scripts, environment-variable defaults and punctuation no longer count) on the validated task versions. Archived with the user's authorization on 2026-10-17; original task payload retained.",
        "evidence": "stage-4 check-test-file-references.sh in FACET full run 2026-10-17 (array 1040937), rechecked with the improved check (URL routes, regular expressions, PDF name objects, archive members, harness scripts, environment-variable defaults and punctuation no longer count) on the validated task versions",
        "source_binary_sha256": "54cbbbadb1d7a9128f3f22b49f5feff71b9d847ab724dc98cef44b7c7ce4e184"
    },
    "task_004336": {
        "category": "upstream-image-build-failure",
        "image_key": "3eb1b17f55b6",
        "reason": "task_004336: The fixture step `chmod +x /build_scripts/*.sh && python3 /build_scripts/generate_fixtures.py && bash /build_scripts/setup_repos.sh && rm -rf /build_scripts` creates git repositories with plain `git init` and then uses a branch named main. Git 2.34.1 in ubuntu:22.04 names the initial branch master, so the step fails (`error: pathspec 'main' did not match any file(s) known to git`). The step fails the same way under Docker, so the upstream environment cannot be built. Observed in FACET generation 5 image-prebuild array 1014877, shard 0002, evidence.tar.gz member image-build-logs/3eb1b17f55b6.build.log; original task payload retained.",
        "evidence": "FACET generation 5 image-prebuild array 1014877, shard 0002, evidence.tar.gz member image-build-logs/3eb1b17f55b6.build.log",
        "source_binary_sha256": "5617fc66fafe4b71231a8ff128fa44e16d862a9eb1619991c4c341672a1ee16f"
    },
    "task_004403": {
        "category": "hidden-output-requirement",
        "image_key": "n/a",
        "reason": "task_004403: the tests require files the instruction doesn't name: product_snapshot.pdf. The solution creates them and the tests check them, but instruction.md and task.toml never mention them, so an agent cannot know they are required. Observed in stage-4 check-test-file-references.sh in FACET full run 2026-10-17 (array 1040937), rechecked with the improved check (URL routes, regular expressions, PDF name objects, archive members, harness scripts, environment-variable defaults and punctuation no longer count) on the validated task versions. Archived with the user's authorization on 2026-10-17; original task payload retained.",
        "evidence": "stage-4 check-test-file-references.sh in FACET full run 2026-10-17 (array 1040937), rechecked with the improved check (URL routes, regular expressions, PDF name objects, archive members, harness scripts, environment-variable defaults and punctuation no longer count) on the validated task versions",
        "source_binary_sha256": "d8b48315436830402c69d39eb0aad1da82895e345334927f4d1b60fdbdd9321c"
    },
    "task_004488": {
        "category": "upstream-image-build-failure",
        "image_key": "527b91952d5e",
        "reason": "task_004488: The package step installs python3 together with python3-pptx and ends in `|| true`. python3-pptx is not an Ubuntu 22.04 package (`E: Unable to locate package python3-pptx`), so apt installs nothing, and the fixture step `python3 /FACET-Terminal-build-scripts/generate_fixtures.py && rm -rf /FACET-Terminal-build-scripts` fails with `/bin/sh: 1: python3: not found`. The step fails the same way under Docker, so the upstream environment cannot be built. Observed in FACET generation 6 image-prebuild array 1025882, shard 0005, evidence.tar.gz member image-build-logs/527b91952d5e.build.log; original task payload retained.",
        "evidence": "FACET generation 6 image-prebuild array 1025882, shard 0005, evidence.tar.gz member image-build-logs/527b91952d5e.build.log",
        "source_binary_sha256": "896cd5c4b8905b43ef23d614d0706d8e47fa0b352835bde07755b47e21b51308"
    },
    "task_004579": {
        "category": "upstream-reference-failure",
        "image_key": "n/a",
        "reason": "task_004579: the upstream reference solution fails the task's own tests: 1 of 34 tests fails: AssertionError: Branch ref missing: /task_file/input/snapshot_repo.git/refs/heads/main (the reference's snapshot repository has no loose main ref). Observed in FACET manual validation 2026-10-16, reference job 1038707, /hnvme/workspace/y500bb12-optiagent/facet-manual-validation-20261016/reference/array/shards/0014/evidence.tar.gz member results/stage_3_oracle_validation/25cae088b365/jobs/job/task_004579__7hmfNSg/attempts/000/verifier/test-stdout.txt. Archived on 2026-10-17 per the user's handover decision; original task payload retained.",
        "evidence": "FACET manual validation 2026-10-16, reference job 1038707, /hnvme/workspace/y500bb12-optiagent/facet-manual-validation-20261016/reference/array/shards/0014/evidence.tar.gz member results/stage_3_oracle_validation/25cae088b365/jobs/job/task_004579__7hmfNSg/attempts/000/verifier/test-stdout.txt",
        "source_binary_sha256": "72a65159f4e5833f63df2a26468881f816d05abed1f0aa3bf54e435f5a3b52e1"
    },
    "task_004958": {
        "category": "hidden-output-requirement",
        "image_key": "n/a",
        "reason": "task_004958: the tests require files the instruction doesn't name: tsconfig.json. The solution creates them and the tests check them, but instruction.md and task.toml never mention them, so an agent cannot know they are required. Observed in stage-4 check-test-file-references.sh in FACET full run 2026-10-17 (array 1040937), rechecked with the improved check (URL routes, regular expressions, PDF name objects, archive members, harness scripts, environment-variable defaults and punctuation no longer count) on the validated task versions. Archived with the user's authorization on 2026-10-17; original task payload retained.",
        "evidence": "stage-4 check-test-file-references.sh in FACET full run 2026-10-17 (array 1040937), rechecked with the improved check (URL routes, regular expressions, PDF name objects, archive members, harness scripts, environment-variable defaults and punctuation no longer count) on the validated task versions",
        "source_binary_sha256": "641b9f7f1ee62238dd6adbee9cf1960127e35f96e06b9f4d2424a0776b11d257"
    },
    "task_005103": {
        "category": "runtime-file-permissions",
        "image_key": "n/a",
        "reason": "task_005103: the reference sets config/.env to mode 400 and the test requires exactly 400, but the test sees 600 (AssertionError: .env permissions are 0o600, expected 0o400). Cause is our runtime, not the task: on Helma, Apptainer falls back to LD_PRELOAD fakeroot (no subordinate UID/GID mappings); fakeroot records the requested mode in its session database but always leaves owner read/write (and execute for directories) on the real file, and the verifier runs in a new fakeroot session that sees the real mode (reproduced 2026-10-17 with /usr/bin/fakeroot: chmod 400 file / 600 dir read back as 600 / 700 outside the session). Passing would need agent and verifier in one fakeroot state (a shared-runtime change); until then the mode-exact test cannot pass here. Observed in FACET manual validation 2026-10-16, reference job 1038707, /hnvme/workspace/y500bb12-optiagent/facet-manual-validation-20261016/reference/array/shards/0045/evidence.tar.gz member results/stage_3_oracle_validation/1c8a6c87ef30/jobs/job/task_005103__DANx3XY/attempts/000/verifier/test-stdout.txt. Archived on 2026-10-17 per the user's handover decision; original task payload retained.",
        "evidence": "FACET manual validation 2026-10-16, reference job 1038707, /hnvme/workspace/y500bb12-optiagent/facet-manual-validation-20261016/reference/array/shards/0045/evidence.tar.gz member results/stage_3_oracle_validation/1c8a6c87ef30/jobs/job/task_005103__DANx3XY/attempts/000/verifier/test-stdout.txt",
        "source_binary_sha256": "ca5b0590b1792c499da28e2f478b2887f85b7cbe681c8282feec11671a137c6d"
    },
    "task_005304": {
        "category": "upstream-image-build-failure",
        "image_key": "60b735b226af",
        "reason": "task_005304: the Dockerfile's pip install builds pyblake2 from source, but the image lacks the Python development headers (pyblake2module.c: fatal error: Python.h: No such file or directory). The step fails the same way under Docker, so the upstream environment cannot be built. Observed in FACET manual image retry 2026-10-16, CLAIX job 5074096 (/hnvme/workspace/y500bb12-optiagent/facet-manual-image-retry-20261016/rwth-results/60b735b226af.build.log). Archived with the user's authorization on 2026-10-16; original task payload retained.",
        "evidence": "FACET manual image retry 2026-10-16, CLAIX job 5074096 (/hnvme/workspace/y500bb12-optiagent/facet-manual-image-retry-20261016/rwth-results/60b735b226af.build.log)",
        "source_binary_sha256": "144c10017625db6ff4f5b3aad7f3ceb902ad739410d87fd311853f779907784e"
    },
    "task_005341": {
        "category": "hidden-output-requirement",
        "image_key": "n/a",
        "reason": "task_005341: the tests require files the instruction doesn't name: daily_ledger_2025-03-15.csv. The solution creates them and the tests check them, but instruction.md and task.toml never mention them, so an agent cannot know they are required. Observed in stage-4 check-test-file-references.sh in FACET full run 2026-10-17 (array 1040937), rechecked with the improved check (URL routes, regular expressions, PDF name objects, archive members, harness scripts, environment-variable defaults and punctuation no longer count) on the validated task versions. Archived with the user's authorization on 2026-10-17; original task payload retained.",
        "evidence": "stage-4 check-test-file-references.sh in FACET full run 2026-10-17 (array 1040937), rechecked with the improved check (URL routes, regular expressions, PDF name objects, archive members, harness scripts, environment-variable defaults and punctuation no longer count) on the validated task versions",
        "source_binary_sha256": "d347b445814d61fc0d680e2ea25c085f337d7a3107371bce163665efb6aa636b"
    },
    "task_005366": {
        "category": "hidden-output-requirement",
        "image_key": "n/a",
        "reason": "task_005366: the tests require files the instruction doesn't name: /opt/scripts/aggregate_signals.py. The solution creates them and the tests check them, but instruction.md and task.toml never mention them, so an agent cannot know they are required. Observed in stage-4 check-test-file-references.sh in FACET full run 2026-10-17 (array 1040937), rechecked with the improved check (URL routes, regular expressions, PDF name objects, archive members, harness scripts, environment-variable defaults and punctuation no longer count) on the validated task versions. Archived with the user's authorization on 2026-10-17; original task payload retained.",
        "evidence": "stage-4 check-test-file-references.sh in FACET full run 2026-10-17 (array 1040937), rechecked with the improved check (URL routes, regular expressions, PDF name objects, archive members, harness scripts, environment-variable defaults and punctuation no longer count) on the validated task versions",
        "source_binary_sha256": "bdf601aa44e318aa42a524b1dcb0959de7db2eacd00251f9a54d1c83e0b58a84"
    },
    "task_005728": {
        "category": "hidden-output-requirement",
        "image_key": "n/a",
        "reason": "task_005728: the tests require files the instruction doesn't name: /task_file/media/supplementary_reference.pdf, /task_file/media/y2k_visual.jpg. The solution creates them and the tests check them, but instruction.md and task.toml never mention them, so an agent cannot know they are required. Observed in stage-4 check-test-file-references.sh in FACET full run 2026-10-17 (array 1040937), rechecked with the improved check (URL routes, regular expressions, PDF name objects, archive members, harness scripts, environment-variable defaults and punctuation no longer count) on the validated task versions. Archived with the user's authorization on 2026-10-17; original task payload retained.",
        "evidence": "stage-4 check-test-file-references.sh in FACET full run 2026-10-17 (array 1040937), rechecked with the improved check (URL routes, regular expressions, PDF name objects, archive members, harness scripts, environment-variable defaults and punctuation no longer count) on the validated task versions",
        "source_binary_sha256": "0b066600e82f627ea86cdff39cf9c41c5ad858ec9a8f15c9669dce70b278a566"
    },
    "task_005798": {
        "category": "hidden-output-requirement",
        "image_key": "n/a",
        "reason": "task_005798: the tests require files the instruction doesn't name: dashboard_audio_report_transcript.txt. The solution creates them and the tests check them, but instruction.md and task.toml never mention them, so an agent cannot know they are required. Observed in stage-4 check-test-file-references.sh in FACET full run 2026-10-17 (array 1040937), rechecked with the improved check (URL routes, regular expressions, PDF name objects, archive members, harness scripts, environment-variable defaults and punctuation no longer count) on the validated task versions. Archived with the user's authorization on 2026-10-17; original task payload retained.",
        "evidence": "stage-4 check-test-file-references.sh in FACET full run 2026-10-17 (array 1040937), rechecked with the improved check (URL routes, regular expressions, PDF name objects, archive members, harness scripts, environment-variable defaults and punctuation no longer count) on the validated task versions",
        "source_binary_sha256": "35711bad1ddf2b98b8c128d167c55d40ed1210d441169444c79fe96c27efeaeb"
    },
    "task_005975": {
        "category": "upstream-reference-failure",
        "image_key": "n/a",
        "reason": "task_005975: the upstream reference solution fails the task's own tests: 1 of 29 tests fails: AssertionError: Report contains external hyperlinks: ['https://media.internal.example.com/recordings/ci-flaky-137-debug.mp4']; the reference writes it. Observed in FACET manual validation 2026-10-16, reference job 1038707, /hnvme/workspace/y500bb12-optiagent/facet-manual-validation-20261016/reference/array/shards/0037/evidence.tar.gz member results/stage_3_oracle_validation/ec75ad284114/jobs/job/task_005975__pnk6i5u/attempts/000/verifier/test-stdout.txt. Archived on 2026-10-17 per the user's handover decision; original task payload retained.",
        "evidence": "FACET manual validation 2026-10-16, reference job 1038707, /hnvme/workspace/y500bb12-optiagent/facet-manual-validation-20261016/reference/array/shards/0037/evidence.tar.gz member results/stage_3_oracle_validation/ec75ad284114/jobs/job/task_005975__pnk6i5u/attempts/000/verifier/test-stdout.txt",
        "source_binary_sha256": "06ab21f45e56fe73b1b7cb96be14dd5de725d14e2eb89cfd72b0e9bf6e323097"
    },
    "task_005981": {
        "category": "hidden-output-requirement",
        "image_key": "n/a",
        "reason": "task_005981: the tests require files the instruction doesn't name: reference_snapshot.pdf. The solution creates them and the tests check them, but instruction.md and task.toml never mention them, so an agent cannot know they are required. Observed in stage-4 check-test-file-references.sh in FACET full run 2026-10-17 (array 1040937), rechecked with the improved check (URL routes, regular expressions, PDF name objects, archive members, harness scripts, environment-variable defaults and punctuation no longer count) on the validated task versions. Archived with the user's authorization on 2026-10-17; original task payload retained.",
        "evidence": "stage-4 check-test-file-references.sh in FACET full run 2026-10-17 (array 1040937), rechecked with the improved check (URL routes, regular expressions, PDF name objects, archive members, harness scripts, environment-variable defaults and punctuation no longer count) on the validated task versions",
        "source_binary_sha256": "f1d897ba4cafd58e4637d83ca40088a23cbd97b0bf2b0ab82c7e1e941b6a052e"
    },
    "task_005983": {
        "category": "hidden-output-requirement",
        "image_key": "n/a",
        "reason": "task_005983: the tests require files the instruction doesn't name: flight_search_pek_nrt.json. The solution creates them and the tests check them, but instruction.md and task.toml never mention them, so an agent cannot know they are required. Observed in stage-4 check-test-file-references.sh in FACET full run 2026-10-17 (array 1040937), rechecked with the improved check (URL routes, regular expressions, PDF name objects, archive members, harness scripts, environment-variable defaults and punctuation no longer count) on the validated task versions. Archived with the user's authorization on 2026-10-17; original task payload retained.",
        "evidence": "stage-4 check-test-file-references.sh in FACET full run 2026-10-17 (array 1040937), rechecked with the improved check (URL routes, regular expressions, PDF name objects, archive members, harness scripts, environment-variable defaults and punctuation no longer count) on the validated task versions",
        "source_binary_sha256": "bc7ef93248ba77b1828184dea515b4d36f38f8353e86ed01896134ab0e8e74cd"
    },
    "task_006013": {
        "category": "hidden-output-requirement",
        "image_key": "n/a",
        "reason": "task_006013: the tests require files the instruction doesn't name: memory_export_restored.json. The solution creates them and the tests check them, but instruction.md and task.toml never mention them, so an agent cannot know they are required. Observed in stage-4 check-test-file-references.sh in FACET full run 2026-10-17 (array 1040937), rechecked with the improved check (URL routes, regular expressions, PDF name objects, archive members, harness scripts, environment-variable defaults and punctuation no longer count) on the validated task versions. Archived with the user's authorization on 2026-10-17; original task payload retained.",
        "evidence": "stage-4 check-test-file-references.sh in FACET full run 2026-10-17 (array 1040937), rechecked with the improved check (URL routes, regular expressions, PDF name objects, archive members, harness scripts, environment-variable defaults and punctuation no longer count) on the validated task versions",
        "source_binary_sha256": "6d1029bfed8aa91325acec54bf30de7f0d8971f414787dc81fa6b1603a208ed5"
    },
    "task_006029": {
        "category": "upstream-image-build-failure",
        "image_key": "ecfcbd2c1814",
        "reason": "task_006029: The fixture step `chmod +x /build_scripts/*.sh && python3 /build_scripts/generate_fixtures.py && /build_scripts/generate_repo.sh && rm -rf /build_scripts` creates git repositories with plain `git init` and then uses a branch named main. Git 2.34.1 in ubuntu:22.04 names the initial branch master, so the step fails (`fatal: ambiguous argument 'main': unknown revision or path not in the working tree.`). The step fails the same way under Docker, so the upstream environment cannot be built. Observed in FACET generation 6 image-prebuild array 1025882, shard 0023, evidence.tar.gz member image-build-logs/ecfcbd2c1814.build.log; original task payload retained.",
        "evidence": "FACET generation 6 image-prebuild array 1025882, shard 0023, evidence.tar.gz member image-build-logs/ecfcbd2c1814.build.log",
        "source_binary_sha256": "a446f11807d40fbcf6489473a868a2586e4b57fc65169bac68d4f2d42e32cf2c"
    },
    "task_006112": {
        "category": "hidden-output-requirement",
        "image_key": "n/a",
        "reason": "task_006112: the tests require files the instruction doesn't name: script.sh. The solution creates them and the tests check them, but instruction.md and task.toml never mention them, so an agent cannot know they are required. Observed in stage-4 check-test-file-references.sh in FACET full run 2026-10-17 (array 1040937), rechecked with the improved check (URL routes, regular expressions, PDF name objects, archive members, harness scripts, environment-variable defaults and punctuation no longer count) on the validated task versions. Archived with the user's authorization on 2026-10-17; original task payload retained.",
        "evidence": "stage-4 check-test-file-references.sh in FACET full run 2026-10-17 (array 1040937), rechecked with the improved check (URL routes, regular expressions, PDF name objects, archive members, harness scripts, environment-variable defaults and punctuation no longer count) on the validated task versions",
        "source_binary_sha256": "cf637891e3817858d6a7000931ff1fd92ee69f12acbc71cafdd94eec7c8031a3"
    },
    "task_006127": {
        "category": "hidden-output-requirement",
        "image_key": "n/a",
        "reason": "task_006127: the tests require files the instruction doesn't name: unknown.mp4. The solution creates them and the tests check them, but instruction.md and task.toml never mention them, so an agent cannot know they are required. Observed in stage-4 check-test-file-references.sh in FACET full run 2026-10-17 (array 1040937), rechecked with the improved check (URL routes, regular expressions, PDF name objects, archive members, harness scripts, environment-variable defaults and punctuation no longer count) on the validated task versions. Archived with the user's authorization on 2026-10-17; original task payload retained.",
        "evidence": "stage-4 check-test-file-references.sh in FACET full run 2026-10-17 (array 1040937), rechecked with the improved check (URL routes, regular expressions, PDF name objects, archive members, harness scripts, environment-variable defaults and punctuation no longer count) on the validated task versions",
        "source_binary_sha256": "fcf5a3eec8a3eee01820d19da22e01e7dd9373a7d807752e5c90307d5cac720f"
    },
    "task_006235": {
        "category": "hidden-output-requirement",
        "image_key": "n/a",
        "reason": "task_006235: the tests require files the instruction doesn't name: final_output_mountain_lake.mp4. The solution creates them and the tests check them, but instruction.md and task.toml never mention them, so an agent cannot know they are required. Observed in stage-4 check-test-file-references.sh in FACET full run 2026-10-17 (array 1040937), rechecked with the improved check (URL routes, regular expressions, PDF name objects, archive members, harness scripts, environment-variable defaults and punctuation no longer count) on the validated task versions. Archived with the user's authorization on 2026-10-17; original task payload retained.",
        "evidence": "stage-4 check-test-file-references.sh in FACET full run 2026-10-17 (array 1040937), rechecked with the improved check (URL routes, regular expressions, PDF name objects, archive members, harness scripts, environment-variable defaults and punctuation no longer count) on the validated task versions",
        "source_binary_sha256": "d10c004b42382af189882c4a4146c74658de6e3b65d227ff5956ffc6a8697046"
    },
    "task_006306": {
        "category": "upstream-image-build-failure",
        "image_key": "d0a33217b9c8",
        "reason": "task_006306: the Dockerfile's pip install builds pycld2 from source, but the image installs no C compiler (command 'x86_64-linux-gnu-gcc' failed: No such file or directory). The step fails the same way under Docker, so the upstream environment cannot be built. Observed in FACET manual image retry 2026-10-16, CLAIX job 5074096 (/hnvme/workspace/y500bb12-optiagent/facet-manual-image-retry-20261016/rwth-results/d0a33217b9c8.build.log). Archived with the user's authorization on 2026-10-16; original task payload retained.",
        "evidence": "FACET manual image retry 2026-10-16, CLAIX job 5074096 (/hnvme/workspace/y500bb12-optiagent/facet-manual-image-retry-20261016/rwth-results/d0a33217b9c8.build.log)",
        "source_binary_sha256": "4b232d55dec263aac4dea9d0537d5329b88b4e49a719bb9e62f1d854ce23f68d"
    },
    "task_006315": {
        "category": "hidden-output-requirement",
        "image_key": "n/a",
        "reason": "task_006315: the tests require files the instruction doesn't name: __init__.py. The solution creates them and the tests check them, but instruction.md and task.toml never mention them, so an agent cannot know they are required. Observed in stage-4 check-test-file-references.sh in FACET full run 2026-10-17 (array 1040937), rechecked with the improved check (URL routes, regular expressions, PDF name objects, archive members, harness scripts, environment-variable defaults and punctuation no longer count) on the validated task versions. Archived with the user's authorization on 2026-10-17; original task payload retained.",
        "evidence": "stage-4 check-test-file-references.sh in FACET full run 2026-10-17 (array 1040937), rechecked with the improved check (URL routes, regular expressions, PDF name objects, archive members, harness scripts, environment-variable defaults and punctuation no longer count) on the validated task versions",
        "source_binary_sha256": "4c80674b455404d4ffc310792db88cce8c73011e64e20375de9567a94d1faff4"
    },
    "task_006374": {
        "category": "hidden-output-requirement",
        "image_key": "n/a",
        "reason": "task_006374: the tests require files the instruction doesn't name: annotations.json. The solution creates them and the tests check them, but instruction.md and task.toml never mention them, so an agent cannot know they are required. Observed in stage-4 check-test-file-references.sh in FACET full run 2026-10-17 (array 1040937), rechecked with the improved check (URL routes, regular expressions, PDF name objects, archive members, harness scripts, environment-variable defaults and punctuation no longer count) on the validated task versions. Archived with the user's authorization on 2026-10-17; original task payload retained.",
        "evidence": "stage-4 check-test-file-references.sh in FACET full run 2026-10-17 (array 1040937), rechecked with the improved check (URL routes, regular expressions, PDF name objects, archive members, harness scripts, environment-variable defaults and punctuation no longer count) on the validated task versions",
        "source_binary_sha256": "6106a9b0fa81d2dd58b9f4c0b161a156b0f62c71116cbaa5c4c6a299de49c463"
    },
    "task_006381": {
        "category": "upstream-image-build-failure",
        "image_key": "aee67a0ff75a",
        "reason": "task_006381: The Dockerfile step `python3 -m pip install --no-cache-dir jsonschema pyblake2 && rm -rf /root/.cache/pip` builds pyblake2 from source because the package has no binary wheel for the image's Python 3.10, but the image installs no C compiler (`error: command 'x86_64-linux-gnu-gcc' failed: No such file or directory`). The step fails the same way under Docker, so the upstream environment cannot be built. Observed in FACET generation 6 image-prebuild array 1025882, shard 0019, evidence.tar.gz member image-build-logs/aee67a0ff75a.build.log; original task payload retained.",
        "evidence": "FACET generation 6 image-prebuild array 1025882, shard 0019, evidence.tar.gz member image-build-logs/aee67a0ff75a.build.log",
        "source_binary_sha256": "5365c884f7a6aee7d97bb96202e7e923156b9b36d56ed0227cbc396f0e5e78ec"
    },
    "task_006418": {
        "category": "upstream-image-build-failure",
        "image_key": "05c0943e6e26",
        "reason": "task_006418: The fixture step `python3 /build_scripts/generate_all_fixtures.py && rm -rf /build_scripts` creates git repositories with plain `git init` and then uses a branch named main. Git 2.34.1 in ubuntu:22.04 names the initial branch master, so the step fails (`error: failed to push some refs to '/task_file/input/repos/repo_beta.git'`). The step fails the same way under Docker, so the upstream environment cannot be built. Observed in FACET generation 6 image-prebuild array 1025882, shard 0019, evidence.tar.gz member image-build-logs/05c0943e6e26.build.log; original task payload retained.",
        "evidence": "FACET generation 6 image-prebuild array 1025882, shard 0019, evidence.tar.gz member image-build-logs/05c0943e6e26.build.log",
        "source_binary_sha256": "e8774be848d89360d9b20688e88aaa76e93494bd69e9cac3000e71ff90ee6c5f"
    },
    "task_006449": {
        "category": "upstream-image-build-failure",
        "image_key": "faf5049453c6",
        "reason": "task_006449: The fixture step `chmod +x /build_scripts/*.sh && /build_scripts/generate_git_repo.sh && rm -rf /build_scripts` creates git repositories with plain `git init` and then uses a branch named main. Git 2.34.1 in ubuntu:22.04 names the initial branch master, so the step fails (`error: pathspec 'main' did not match any file(s) known to git`). The step fails the same way under Docker, so the upstream environment cannot be built. Observed in FACET generation 6 image-prebuild array 1025882, shard 0012, evidence.tar.gz member image-build-logs/faf5049453c6.build.log; original task payload retained.",
        "evidence": "FACET generation 6 image-prebuild array 1025882, shard 0012, evidence.tar.gz member image-build-logs/faf5049453c6.build.log",
        "source_binary_sha256": "4bd2fc99c9bec0a103b1c099e45fd05b20e25df5e02d8ac77c5229e05a387693"
    },
    "task_006466": {
        "category": "upstream-image-build-failure",
        "image_key": "5314f7c80487",
        "reason": "task_006466: The Dockerfile step `python3 -m pip install --no-cache-dir jsonschema pyblake2` builds pyblake2 from source because the package has no binary wheel for the image's Python 3.10, but the image installs no C compiler (`error: command 'x86_64-linux-gnu-gcc' failed: No such file or directory`). The step fails the same way under Docker, so the upstream environment cannot be built. Observed in FACET generation 6 image-prebuild array 1025882, shard 0022, evidence.tar.gz member image-build-logs/5314f7c80487.build.log; original task payload retained.",
        "evidence": "FACET generation 6 image-prebuild array 1025882, shard 0022, evidence.tar.gz member image-build-logs/5314f7c80487.build.log",
        "source_binary_sha256": "4c95d4d4c86ce1a5787a526e448e71760cdb37cc654ea5235635e38ec265108b"
    },
    "task_006487": {
        "category": "hidden-output-requirement",
        "image_key": "n/a",
        "reason": "task_006487: the tests require files the instruction doesn't name: /task_file/input/memory_checkpoints/checkpoint_002.json, /task_file/input/memory_checkpoints/checkpoint_004.json, /task_file/input/memory_checkpoints/checkpoint_005.json. The solution creates them and the tests check them, but instruction.md and task.toml never mention them, so an agent cannot know they are required. Observed in stage-4 check-test-file-references.sh in FACET full run 2026-10-17 (array 1040937), rechecked with the improved check (URL routes, regular expressions, PDF name objects, archive members, harness scripts, environment-variable defaults and punctuation no longer count) on the validated task versions. Archived with the user's authorization on 2026-10-17; original task payload retained.",
        "evidence": "stage-4 check-test-file-references.sh in FACET full run 2026-10-17 (array 1040937), rechecked with the improved check (URL routes, regular expressions, PDF name objects, archive members, harness scripts, environment-variable defaults and punctuation no longer count) on the validated task versions",
        "source_binary_sha256": "cad856ab1df4fb2486a825f7d616b9dfbcfc1502ed8cd659858f3c10b3658221"
    },
    "task_006495": {
        "category": "hidden-output-requirement",
        "image_key": "n/a",
        "reason": "task_006495: the tests require files the instruction doesn't name: blueprint.json. The solution creates them and the tests check them, but instruction.md and task.toml never mention them, so an agent cannot know they are required. Observed in stage-4 check-test-file-references.sh in FACET full run 2026-10-17 (array 1040937), rechecked with the improved check (URL routes, regular expressions, PDF name objects, archive members, harness scripts, environment-variable defaults and punctuation no longer count) on the validated task versions. Archived with the user's authorization on 2026-10-17; original task payload retained.",
        "evidence": "stage-4 check-test-file-references.sh in FACET full run 2026-10-17 (array 1040937), rechecked with the improved check (URL routes, regular expressions, PDF name objects, archive members, harness scripts, environment-variable defaults and punctuation no longer count) on the validated task versions",
        "source_binary_sha256": "286188e374f5b9735aa85ec052bd7d1ee4af589cfbaefff0a56e140985780a36"
    },
    "task_006520": {
        "category": "hidden-output-requirement",
        "image_key": "n/a",
        "reason": "task_006520: the tests require files the instruction doesn't name: /opt/exports. The solution creates them and the tests check them, but instruction.md and task.toml never mention them, so an agent cannot know they are required. Observed in stage-4 check-test-file-references.sh in FACET full run 2026-10-17 (array 1040937), rechecked with the improved check (URL routes, regular expressions, PDF name objects, archive members, harness scripts, environment-variable defaults and punctuation no longer count) on the validated task versions. Archived with the user's authorization on 2026-10-17; original task payload retained.",
        "evidence": "stage-4 check-test-file-references.sh in FACET full run 2026-10-17 (array 1040937), rechecked with the improved check (URL routes, regular expressions, PDF name objects, archive members, harness scripts, environment-variable defaults and punctuation no longer count) on the validated task versions",
        "source_binary_sha256": "5e78df2e7a8bf0cc650ac25efeb65037e82df5769f22dd5bd61a8ef5c65c0c59"
    },
    "task_006580": {
        "category": "upstream-image-build-failure",
        "image_key": "79d100065149",
        "reason": "task_006580: The Dockerfile step `python3 -m pip install --no-cache-dir pyblake2` builds pyblake2 from source because the package has no binary wheel for the image's Python 3.10, but the image installs no C compiler (`error: command 'x86_64-linux-gnu-gcc' failed: No such file or directory`). The step fails the same way under Docker, so the upstream environment cannot be built. Observed in FACET generation 6 image-prebuild array 1025882, shard 0018, evidence.tar.gz member image-build-logs/79d100065149.build.log; original task payload retained.",
        "evidence": "FACET generation 6 image-prebuild array 1025882, shard 0018, evidence.tar.gz member image-build-logs/79d100065149.build.log",
        "source_binary_sha256": "23e815a7545f04705558d12c86928bff6188268df1e50be39098e5a21cfeaa80"
    },
    "task_006762": {
        "category": "upstream-image-build-failure",
        "image_key": "3b734e4632d3",
        "reason": "task_006762: The fixture step `chmod +x /build_scripts/*.sh && /build_scripts/generate_git_repo.sh && rm -rf /build_scripts` creates git repositories with plain `git init` and then uses a branch named main. Git 2.34.1 in ubuntu:22.04 names the initial branch master, so the step fails (`error: pathspec 'main' did not match any file(s) known to git`). The step fails the same way under Docker, so the upstream environment cannot be built. Observed in FACET generation 6 image-prebuild array 1025882, shard 0006, evidence.tar.gz member image-build-logs/3b734e4632d3.build.log; original task payload retained.",
        "evidence": "FACET generation 6 image-prebuild array 1025882, shard 0006, evidence.tar.gz member image-build-logs/3b734e4632d3.build.log",
        "source_binary_sha256": "daa5f2e412dd55c7c32d2783d36af4691e19764390f46a0a62542409265477c4"
    },
    "task_006847": {
        "category": "hidden-output-requirement",
        "image_key": "n/a",
        "reason": "task_006847: the tests require files the instruction doesn't name: default_template.docx. The solution creates them and the tests check them, but instruction.md and task.toml never mention them, so an agent cannot know they are required. Observed in stage-4 check-test-file-references.sh in FACET full run 2026-10-17 (array 1040937), rechecked with the improved check (URL routes, regular expressions, PDF name objects, archive members, harness scripts, environment-variable defaults and punctuation no longer count) on the validated task versions. Archived with the user's authorization on 2026-10-17; original task payload retained.",
        "evidence": "stage-4 check-test-file-references.sh in FACET full run 2026-10-17 (array 1040937), rechecked with the improved check (URL routes, regular expressions, PDF name objects, archive members, harness scripts, environment-variable defaults and punctuation no longer count) on the validated task versions",
        "source_binary_sha256": "036359d4d951337cc315aecb2ab6b09ac729d0e286f048468383e47156d5f172"
    },
    "task_007015": {
        "category": "hidden-output-requirement",
        "image_key": "n/a",
        "reason": "task_007015: the tests require files the instruction doesn't name: /task_file/corpus/documents/doc_002.json, /task_file/corpus/documents/doc_003.json, /task_file/corpus/documents/doc_004.json. The solution creates them and the tests check them, but instruction.md and task.toml never mention them, so an agent cannot know they are required. Observed in stage-4 check-test-file-references.sh in FACET full run 2026-10-17 (array 1040937), rechecked with the improved check (URL routes, regular expressions, PDF name objects, archive members, harness scripts, environment-variable defaults and punctuation no longer count) on the validated task versions. Archived with the user's authorization on 2026-10-17; original task payload retained.",
        "evidence": "stage-4 check-test-file-references.sh in FACET full run 2026-10-17 (array 1040937), rechecked with the improved check (URL routes, regular expressions, PDF name objects, archive members, harness scripts, environment-variable defaults and punctuation no longer count) on the validated task versions",
        "source_binary_sha256": "6c0277525ef27c71667ee3d0f5add80cee05c9ff23b1a48f8ca7767d5d364fb3"
    },
    "task_007125": {
        "category": "hidden-output-requirement",
        "image_key": "n/a",
        "reason": "task_007125: the tests require files the instruction doesn't name: clawshi_leaderboard_summary.json. The solution creates them and the tests check them, but instruction.md and task.toml never mention them, so an agent cannot know they are required. Observed in stage-4 check-test-file-references.sh in FACET full run 2026-10-17 (array 1040937), rechecked with the improved check (URL routes, regular expressions, PDF name objects, archive members, harness scripts, environment-variable defaults and punctuation no longer count) on the validated task versions. Archived with the user's authorization on 2026-10-17; original task payload retained.",
        "evidence": "stage-4 check-test-file-references.sh in FACET full run 2026-10-17 (array 1040937), rechecked with the improved check (URL routes, regular expressions, PDF name objects, archive members, harness scripts, environment-variable defaults and punctuation no longer count) on the validated task versions",
        "source_binary_sha256": "c13ae76b2e8190f3f2effe3609bc440b44be630c12559c483ceb9e45b2171eed"
    },
    "task_007176": {
        "category": "hidden-output-requirement",
        "image_key": "n/a",
        "reason": "task_007176: the tests require files the instruction doesn't name: export-dry-run.json. The solution creates them and the tests check them, but instruction.md and task.toml never mention them, so an agent cannot know they are required. Observed in stage-4 check-test-file-references.sh in FACET full run 2026-10-17 (array 1040937), rechecked with the improved check (URL routes, regular expressions, PDF name objects, archive members, harness scripts, environment-variable defaults and punctuation no longer count) on the validated task versions. Archived with the user's authorization on 2026-10-17; original task payload retained.",
        "evidence": "stage-4 check-test-file-references.sh in FACET full run 2026-10-17 (array 1040937), rechecked with the improved check (URL routes, regular expressions, PDF name objects, archive members, harness scripts, environment-variable defaults and punctuation no longer count) on the validated task versions",
        "source_binary_sha256": "95238ea0f4e74b6a48466a0611bf5d12a714c1b035e1a52f50f36c083972b916"
    },
    "task_007253": {
        "category": "runtime-file-permissions",
        "image_key": "n/a",
        "reason": "task_007253: the reference sets /task_file/image_gen_config to mode 600 and the test requires exactly 600, but the test sees 700 (AssertionError: Expected permissions 600, got 0o700). Cause is our runtime, not the task: on Helma, Apptainer falls back to LD_PRELOAD fakeroot (no subordinate UID/GID mappings); fakeroot records the requested mode in its session database but always leaves owner read/write (and execute for directories) on the real file, and the verifier runs in a new fakeroot session that sees the real mode (reproduced 2026-10-17 with /usr/bin/fakeroot: chmod 400 file / 600 dir read back as 600 / 700 outside the session). Passing would need agent and verifier in one fakeroot state (a shared-runtime change); until then the mode-exact test cannot pass here. Observed in FACET manual validation 2026-10-16, reference job 1038707, /hnvme/workspace/y500bb12-optiagent/facet-manual-validation-20261016/reference/array/shards/0038/evidence.tar.gz member results/stage_3_oracle_validation/50360523cf1e/jobs/job/task_007253__5ZxNMoA/attempts/000/verifier/test-stdout.txt. Archived on 2026-10-17 per the user's handover decision; original task payload retained.",
        "evidence": "FACET manual validation 2026-10-16, reference job 1038707, /hnvme/workspace/y500bb12-optiagent/facet-manual-validation-20261016/reference/array/shards/0038/evidence.tar.gz member results/stage_3_oracle_validation/50360523cf1e/jobs/job/task_007253__5ZxNMoA/attempts/000/verifier/test-stdout.txt",
        "source_binary_sha256": "7acee25d8477694a3acfe3acb3545026fc9ab7f92b1c3b3549e6ef69a9d2fb3a"
    },
    "task_007440": {
        "category": "upstream-reference-failure",
        "image_key": "455ab54df279",
        "reason": "task_007440: the upstream reference solution fails two of its own tests: test_lesson_narrative_headings expects exactly the level-2 headings ['Professional Profile Summary', 'Football Standings', 'Financial Highlight'], while test_lesson_narrative_football_table_verbatim requires the football table copied verbatim, which brings four further level-2 headings ('📊 League Standings (Top 8)', '⚽ Week 22 Results', '🔥 Top Scorers', '📝 Match Notes'). The two tests contradict each other. Observed in FACET manual validation 2026-10-16, reference job 1038707, /hnvme/workspace/y500bb12-optiagent/facet-manual-validation-20261016/reference/array/shards/0001/evidence.tar.gz member results/stage_3_oracle_validation/ef458b45c169/jobs/job/task_007440__EtyABtU/attempts/000/verifier/test-stdout.txt. Archived with the user's authorization on 2026-10-16; original task payload retained.",
        "evidence": "FACET manual validation 2026-10-16, reference job 1038707, /hnvme/workspace/y500bb12-optiagent/facet-manual-validation-20261016/reference/array/shards/0001/evidence.tar.gz member results/stage_3_oracle_validation/ef458b45c169/jobs/job/task_007440__EtyABtU/attempts/000/verifier/test-stdout.txt",
        "source_binary_sha256": "3c347216c14edf89a6a865e18acc035446140d2658650abe873c7241329f2c81"
    },
    "task_007474": {
        "category": "upstream-reference-failure",
        "image_key": "n/a",
        "reason": "task_007474: the upstream reference solution fails the task's own tests: 2 of 30 tests fail (TestNetworkIdentity::test_content_is_valid_json): json.decoder.JSONDecodeError: Extra data: line 37 column 1 in the file the reference writes. Observed in FACET manual validation 2026-10-16, reference job 1038707, /hnvme/workspace/y500bb12-optiagent/facet-manual-validation-20261016/reference/array/shards/0025/evidence.tar.gz member results/stage_3_oracle_validation/6ab32db53bb1/jobs/job/task_007474__3NMStrM/attempts/000/verifier/test-stdout.txt. Archived on 2026-10-17 per the user's handover decision; original task payload retained.",
        "evidence": "FACET manual validation 2026-10-16, reference job 1038707, /hnvme/workspace/y500bb12-optiagent/facet-manual-validation-20261016/reference/array/shards/0025/evidence.tar.gz member results/stage_3_oracle_validation/6ab32db53bb1/jobs/job/task_007474__3NMStrM/attempts/000/verifier/test-stdout.txt",
        "source_binary_sha256": "0a56341905d6cd5c50be112a9b63bd80881586cb97d4028446bc9d39a4184861"
    },
    "task_007526": {
        "category": "hidden-output-requirement",
        "image_key": "n/a",
        "reason": "task_007526: the tests require files the instruction doesn't name: project_brief.pdf, /task_file/output/project_brief.pdf, /task_file/output/video_metadata.json, /task_file/output/weather_image.png, video_metadata.json. The solution creates them and the tests check them, but instruction.md and task.toml never mention them, so an agent cannot know they are required. Observed in stage-4 check-test-file-references.sh in FACET full run 2026-10-17 (array 1040937), rechecked with the improved check (URL routes, regular expressions, PDF name objects, archive members, harness scripts, environment-variable defaults and punctuation no longer count) on the validated task versions. Archived with the user's authorization on 2026-10-17; original task payload retained.",
        "evidence": "stage-4 check-test-file-references.sh in FACET full run 2026-10-17 (array 1040937), rechecked with the improved check (URL routes, regular expressions, PDF name objects, archive members, harness scripts, environment-variable defaults and punctuation no longer count) on the validated task versions",
        "source_binary_sha256": "fadba495acab9c43a47e2716a2b33848ed7e2d401c3fcfd1ef6f14416b56e28b"
    },
    "task_007584": {
        "category": "hidden-output-requirement",
        "image_key": "n/a",
        "reason": "task_007584: the tests require files the instruction doesn't name: /task_file/input/hero_image_no_bg.png. The solution creates them and the tests check them, but instruction.md and task.toml never mention them, so an agent cannot know they are required. Observed in stage-4 check-test-file-references.sh in FACET full run 2026-10-17 (array 1040937), rechecked with the improved check (URL routes, regular expressions, PDF name objects, archive members, harness scripts, environment-variable defaults and punctuation no longer count) on the validated task versions. Archived with the user's authorization on 2026-10-17; original task payload retained.",
        "evidence": "stage-4 check-test-file-references.sh in FACET full run 2026-10-17 (array 1040937), rechecked with the improved check (URL routes, regular expressions, PDF name objects, archive members, harness scripts, environment-variable defaults and punctuation no longer count) on the validated task versions",
        "source_binary_sha256": "9371f1cb0d17adc4296bb66b658c69fdfaa7c9533bb6a6340703b705589a515c"
    },
    "task_007641": {
        "category": "hidden-output-requirement",
        "image_key": "n/a",
        "reason": "task_007641: the tests require files the instruction doesn't name: /task_file/input/pptx_parse_output/slide_02.md, /task_file/input/pptx_parse_output/slide_03.md, /task_file/input/pptx_parse_output/slide_04.md, /task_file/input/pptx_parse_output/slide_05.md. The solution creates them and the tests check them, but instruction.md and task.toml never mention them, so an agent cannot know they are required. Observed in stage-4 check-test-file-references.sh in FACET full run 2026-10-17 (array 1040937), rechecked with the improved check (URL routes, regular expressions, PDF name objects, archive members, harness scripts, environment-variable defaults and punctuation no longer count) on the validated task versions. Archived with the user's authorization on 2026-10-17; original task payload retained.",
        "evidence": "stage-4 check-test-file-references.sh in FACET full run 2026-10-17 (array 1040937), rechecked with the improved check (URL routes, regular expressions, PDF name objects, archive members, harness scripts, environment-variable defaults and punctuation no longer count) on the validated task versions",
        "source_binary_sha256": "f182f9223e543e5ae7b987bf4d0fa623bb6b248ff9a0f13992b001ad4d146042"
    },
    "task_007741": {
        "category": "backend_build_ownership",
        "image_key": "5c09de580b4e",
        "reason": "task_007741: fontconfig-config installation fails because its post-installation script changes ownership of /usr/local/share/fonts to a group unavailable in the single-UID Apptainer build namespace on Helma/CLAIX (no subordinate UID/GID mappings; chown: Invalid argument). The dependent font/graphics packages (LibreOffice, cairo, poppler) remain unconfigured and the image build fails. The Dockerfile builds under Docker; this is a runtime incompatibility of our build backend, not a task defect. Observed in FACET manual image retry 2026-10-16, CLAIX job 5074096 (/hnvme/workspace/y500bb12-optiagent/facet-manual-image-retry-20261016/rwth-results/5c09de580b4e.build.log). Archived with the user's authorization on 2026-10-16; original task payload retained.",
        "evidence": "FACET manual image retry 2026-10-16, CLAIX job 5074096 (/hnvme/workspace/y500bb12-optiagent/facet-manual-image-retry-20261016/rwth-results/5c09de580b4e.build.log)",
        "source_binary_sha256": "351cc06da1d4738a32af8dae2238cce9afe78a228777f98dd0b83b84747a8fc5"
    },
    "task_007834": {
        "category": "hidden-output-requirement",
        "image_key": "n/a",
        "reason": "task_007834: the tests require files the instruction doesn't name: slack_gateway.py. The solution creates them and the tests check them, but instruction.md and task.toml never mention them, so an agent cannot know they are required. Observed in stage-4 check-test-file-references.sh in FACET full run 2026-10-17 (array 1040937), rechecked with the improved check (URL routes, regular expressions, PDF name objects, archive members, harness scripts, environment-variable defaults and punctuation no longer count) on the validated task versions. Archived with the user's authorization on 2026-10-17; original task payload retained.",
        "evidence": "stage-4 check-test-file-references.sh in FACET full run 2026-10-17 (array 1040937), rechecked with the improved check (URL routes, regular expressions, PDF name objects, archive members, harness scripts, environment-variable defaults and punctuation no longer count) on the validated task versions",
        "source_binary_sha256": "52cce5d74eb8d642a19ecb1a714eba321a54731a51c66184458954c34b95b5b2"
    }
}


if __name__ == "__main__":
    from data.utils.cli import dispatch
    dispatch({'build-source': build_source_cli, 'prepare-pilot': prepare_pilot_cli})
    main()
