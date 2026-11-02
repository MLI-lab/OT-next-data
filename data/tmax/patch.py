"""Patcher for the pinned full TMAX Harbor source (PrimeIntellect-ai/prime-tasks, datasets/tmax).

    python data/tmax/patch.py --source tasks.parquet --output NEW_DIR

Writes NEW_DIR/tasks.parquet, the shared patch report (tasks.manifest.json,
tasks.archive.parquet) and tmax_patch_manifest.json with the image analysis.

Build compatibility (every task). The Apptainer builder differs from Docker in
two ways that stop every upstream TMAX Dockerfile before its first command:

* files copied to /tmp are hidden while RUN steps execute, so
  `RUN bash /tmp/post_install.sh` finds no script. The install scripts are
  copied to /opt/tmax-build instead, run from there, and that directory is
  removed by the last RUN step in place of the upstream `rm` of the script;
* ENV values are exported unexpanded, so `ENV PATH=...:$PATH` (v2 tasks) leaves
  no system directory on PATH. The value is written as Docker substitutes it
  for ubuntu:22.04.

The scripts themselves are unchanged and no script refers to its own path,
with one build-compatibility exception:

* ten v2 tasks install Ubuntu's nodejs/npm in base_install.sh and then
  NodeSource's nodejs in post_install.sh; dpkg refuses to overwrite
  /usr/include/node/common.gypi of libnode-dev, so those images kept Node.js 12
  instead of the requested version (task_000110 also left MongoDB unconfigured
  and failed the build). The Ubuntu Node.js packages are purged right before
  the NodeSource setup line.

repairs.py holds post-build task, verifier and review fixes, applied by default.
--no-repairs omits these changes and historical exclusions for baseline audits.
Build compatibility and offline dependency preparation remain enabled.
The repair for service wrappers whose background output keeps the verifier's
`pytest | tee` pipe open also lives in repairs.py.

Shared image. Upstream builds one image per task: environment/post_install.sh
first installs packages and then creates the task (files, small compiles, user,
permissions). The "shared image" transformation splits that script:

* the leading package installation stays in the image, written in a canonical
  form, so that all tasks with the same install commands share one image;
* the rest runs unchanged from setup_files/ when the container starts, after
  the v2 fixtures have been copied to the paths upstream copies them to.

The shared runner starts setup_files/setup.sh only for tasks it has a setup
trigger for (validation.stages.task_setup.detect). TMAX has no solution/, so
this patcher asks the runner whether a task with setup_files/setup.sh and no
solution/ gets a setup command. If not, no task is changed: files moved to
setup_files/ would never be executed and the tasks would start empty. The
analysis is still written to tmax_patch_manifest.json.

Git-history repair helpers remain in repairs.py for explicit later use. The
pinned published tasks predate those metadata flags, so default reproduction
and --no-repairs do not introduce them.

Prepared verifier runtimes (--verifier-inventory). Upstream grades with the
image's python3 and pytest after the agent had root access to them. Each task
gets a private writable runtime at the image's Python and package versions
(data/tmax/verifier_runtime.py, .agents/skills/prepare-task-runtimes):

    python hpc/offline_prep/image_python_inventory.py --images-manifest M --bundles B --output INV
    python data/tmax/patch.py --source S --output PREP --verifier-inventory INV
    python hpc/offline_prep/verifier_bundles.py --specs PREP/verifier-bundle-specs.json
    python data/tmax/patch.py --source S --output NEW --verifier-inventory INV \
        --verifier-runtime-cache "${OT_RUNTIME_CACHE:-$OT_WORKSPACE/runtimes}"

The first pass only writes the specs; tasks change when every runtime is ready.
That changes tests/test.sh (the pytest invocation), adds
tests/ot-verifier-runtime.json, replaces sys.executable in the test files that
use it and, for tests importing the task environment, adds tests/conftest.py.

Base conversion preserves instructions. Default repairs also replay the published
instruction suffix, source-guarded path repairs and task-file modes; --no-repairs
omits them. Each change is recorded separately from image build compatibility.
For published tasks, `prepare-runtime` adds the setup recipe and compact runtime
reference without replaying historical repairs. Run tests/setup.sh --prepare on
a trusted preparation host with this repository on PYTHONPATH and uv installed.
Normal verification uses the cache or installs privately through the host helper.
Task setup whose dependencies already live in the image needs no extra runtime;
its --prepare option exits before initializing task state. Python-wrapper service
startup runs before the agent, never from tests/test.sh.
The Dockerfile constants below are the upstream texts; build compatibility is
applied last, to whatever Dockerfile a task then has.
"""

# Support both direct execution and python -m data.<source>.patch.
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import argparse
from collections import Counter, defaultdict
import hashlib
import io
import json
import re
import shlex
import tarfile
import tempfile

from data.tmax import verifier_runtime
from data.tmax import repairs
# Compatibility exports for existing callers; all repair definitions live in repairs.py.
from data.tmax.repairs import (UNSHARED, SERVICE_SETUP, SERVICE_REASON, TASK_REPAIRS,
    ARCHIVED, VERIFIER_REPAIRS, REPAIR_LABEL, WRAPPER_LABEL, WRAPPER_REASON,
    GIT_HISTORY_LABEL, GIT_HISTORY_REASON, service_setup_compatible,
    wrapper_output_compatible, git_history_input, mark_git_history_input)

from harbor_patches.verifier_bundle import spec_id

DATASET = "PrimeIntellect-ai/prime-tasks/datasets/tmax"
REVISION = "27de1c1b1ed6279b607e105ff9cb598e177ad938"
SOURCE_URL = f"https://github.com/PrimeIntellect-ai/prime-tasks/tree/{REVISION}/datasets/tmax"
TASKS = 14600

LEGACY_DOCKERFILE = (
    "FROM ubuntu:22.04\n\n"
    "ENV DEBIAN_FRONTEND=noninteractive\n\n"
    "COPY post_install.sh /tmp/post_install.sh\n"
    "RUN bash /tmp/post_install.sh && rm /tmp/post_install.sh\n")
# The six upstream v2 Dockerfiles without their COPY _fixtures/... lines.
V2_DOCKERFILE = (
    "FROM ubuntu:22.04\n\n"
    "ENV DEBIAN_FRONTEND=noninteractive\n"
    "ENV LC_ALL=C.UTF-8\n"
    "ENV LANG=C.UTF-8\n"
    "ENV GOPATH=/home/user/go\n"
    "ENV PATH=/home/user/go/bin:/home/user/.cargo/bin:$PATH\n\n"
    "COPY base_install.sh /tmp/base_install.sh\n"
    "RUN bash /tmp/base_install.sh && rm /tmp/base_install.sh\n\n"
    "COPY post_install.sh /tmp/post_install.sh\n"
    "RUN bash /tmp/post_install.sh && rm /tmp/post_install.sh\n")
# base_install.sh of 5,134 of the 5,135 v2 tasks.
V2_BASE_INSTALL_SHA256 = "0e71b3e06fcf8cc6f9d95ba2e9664dfbba7786949a0e21b810ada563ae27a637"
FIXTURE_COPY = re.compile(r"^COPY (_fixtures(/\S+)) (\S+)\n", re.M)

# --- Apptainer build compatibility ---------------------------------------------------------

BUILD_DIR = "/opt/tmax-build"
BUILD_SCRIPT = re.compile(r"/tmp/((?:base|post)_install\.sh)\b")
LAST_RUN = f"RUN bash {BUILD_DIR}/post_install.sh && rm {BUILD_DIR}/post_install.sh\n"
# PATH from the image configuration of ubuntu:22.04 (linux/amd64).
UBUNTU_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
V2_PATH = "ENV PATH=/home/user/go/bin:/home/user/.cargo/bin:"
COMPATIBILITY_LABEL = "apptainer-build-compatibility"
COMPATIBILITY_REASON = {
    "legacy": (
        "The Apptainer builder hides files copied to /tmp while RUN steps execute, so the image build "
        "stopped at `bash /tmp/post_install.sh: No such file or directory`. The unchanged script is "
        f"copied to {BUILD_DIR}, run from there, and the directory is removed by the same RUN step."),
    "v2": (
        "The Apptainer builder hides files copied to /tmp while RUN steps execute and exports ENV values "
        "unexpanded, so the image build stopped at `bash: not found` (literal $PATH) and at the missing "
        f"/tmp install scripts. The unchanged scripts are copied to {BUILD_DIR}, run from there, and the "
        "directory is removed by the last RUN step. ENV PATH carries the value Docker substitutes for "
        "ubuntu:22.04."),
}


def build_compatible(dockerfile):
    """The Dockerfile with its install scripts outside /tmp and $PATH written out."""
    text = BUILD_SCRIPT.sub(BUILD_DIR + r"/\1", dockerfile)
    if text.count(LAST_RUN) != 1 or not text.endswith(LAST_RUN) or "/tmp/" in text:
        raise ValueError("unexpected Dockerfile: install script steps")
    text = text[:-len(LAST_RUN)] + f"RUN bash {BUILD_DIR}/post_install.sh && rm -rf {BUILD_DIR}\n"
    text = text.replace(V2_PATH + "$PATH\n", V2_PATH + UBUNTU_PATH + "\n")
    if "$" in text:
        raise ValueError("unexpected Dockerfile: variable reference")
    return text


NODESOURCE_SETUP = re.compile(r"^(\s*)curl -fsSL https://deb\.nodesource\.com/setup_\d+\.x \| (?:sudo -E )?bash -\s*$",
                              re.M)
DISTRO_NODE = re.compile(r"^\s*nodejs npm\b", re.M)
NODE_PURGE = "apt-get purge -y nodejs npm libnode-dev libnode72\n"
NODE_LABEL = "nodesource-after-distro-node"
NODE_REASON = (
    "base_install.sh installs Ubuntu's nodejs/npm and post_install.sh then NodeSource's nodejs, which dpkg "
    "refuses (`trying to overwrite '/usr/include/node/common.gypi', which is also in package libnode-dev`), "
    "so the image kept Node.js 12. The Ubuntu Node.js packages are purged before the NodeSource setup.")


def nodesource_compatible(files):
    """post_install.sh with the distro Node.js purged before NodeSource's, or None if not needed."""
    base = files.get("environment/base_install.sh")
    post = files.get("environment/post_install.sh")
    if not base or not post or not DISTRO_NODE.search(base[0].decode()):
        return None
    text = post[0].decode()
    if len(NODESOURCE_SETUP.findall(text)) != 1:
        return None
    return NODESOURCE_SETUP.sub(lambda m: m.group(1) + NODE_PURGE + m.group(0), text).encode()


SETUP_SH = """#!/bin/bash
# Replays the task-specific part of upstream post_install.sh at container start.
marker=/var/lib/tmax-setup.done
[ -e "$marker" ] && exit 0
cd /
export HOME=/root DEBIAN_FRONTEND=noninteractive
if [ -d /setup_files/fixtures ]; then cp -R --preserve=mode,timestamps /setup_files/fixtures/. /; fi
bash /setup_files/task_install.sh
status=$?
rm -rf /setup_files/task_install.sh /setup_files/fixtures /setup_files/downloads
[ "$status" -eq 0 ] && mkdir -p /var/lib && touch "$marker"
exit "$status"
"""
SETUP_COMMAND = "bash /setup_files/setup.sh"

LABELS = ["shared-image", "task-setup-at-runtime"]
SUPERSET_LABEL = "superset-image"
SUPERSET_REASON = (
    "Package installation from post_install.sh is replaced by the {family} superset image, which "
    "installs the union of the packages of all {family} tasks without version pins, so this task's "
    "image also contains packages it did not ask for ({size} tasks share it). The task-specific part "
    "of post_install.sh (initial files, fixtures, users, permissions) runs unchanged from setup_files "
    "at container start instead of at image build.")
REASON = (
    "Package installation from post_install.sh is kept in an image shared by all tasks with the "
    "same install commands (group {group}, {size} tasks). The task-specific part of post_install.sh "
    "(initial files, fixtures, users, permissions) runs unchanged from setup_files at container "
    "start instead of at image build.")


# --- post_install.sh: logical lines -------------------------------------------------------

HEREDOC = re.compile(r"(?<!<)<<(?!<)(-?)\s*(?:'([^']+)'|\"([^\"]+)\"|\\?([A-Za-z_][A-Za-z0-9_]*))")
FILE_HEREDOC = re.compile(r"\s*(?:cat\b[^|]*(?<![<>&])>|tee\b[^|]*$)")


def logical_lines(text):
    """Yield (command, heredoc bodies, raw text) per shell line.

    Backslash continuations are joined and here-document bodies are attached to
    the line that opens them, so a body is never read as a command. Joining the
    raw texts gives back the input.
    """
    lines = re.findall(r"[^\n]*\n|[^\n]+", text)
    i = 0
    while i < len(lines):
        start = i
        command = lines[i]
        i += 1
        while command.rstrip("\n").endswith("\\") and i < len(lines):
            command = command.rstrip("\n")[:-1] + " " + lines[i]
            i += 1
        bodies = []
        for dash, single, double, bare in HEREDOC.findall(command):
            end, body = single or double or bare, []
            while i < len(lines):
                line = lines[i]
                i += 1
                probe = line.rstrip("\n")
                if (probe.lstrip("\t") if dash else probe) == end:
                    break
                body.append(line)
            bodies.append("".join(body))
        yield command, bodies, "".join(lines[start:i])


# --- post_install.sh: install prefix ------------------------------------------------------

APT_FLAGS = {"-y", "-q", "-qq", "-yq", "-qy", "-yqq", "-qqy", "--yes", "--assume-yes",
             "--no-install-recommends", "--fix-missing"}
APT_PACKAGE = re.compile(r"[a-z0-9][a-z0-9+.:=~-]*")
PIP_FLAGS = {"--no-cache-dir", "--break-system-packages", "-q", "--quiet", "-U", "--upgrade",
             "--no-input", "--prefer-binary", "--ignore-installed", "--force-reinstall",
             "--disable-pip-version-check", "--root-user-action=ignore"}
PIP_TIMEOUT = re.compile(r"--default-timeout=\d+")
PIP_INDEX_FLAGS = {"--index-url", "-i", "--extra-index-url"}
PIP_INDEXES = {"https://download.pytorch.org/whl/cpu"}
_SPEC = r"(?:==|>=|<=|~=|!=|<|>)[A-Za-z0-9.*+!-]+"
PIP_REQUIREMENT = re.compile(
    rf"[A-Za-z0-9][A-Za-z0-9._-]*(?:\[[A-Za-z0-9,._-]+\])?(?:{_SPEC}(?:,{_SPEC})*)?")


def _canonical(head, flags, packages):
    return " ".join([*head, *sorted(set(flags)), *(shlex.quote(p) for p in sorted(set(packages)))])


def _install_command(tokens):
    """Canonical text of one allowed install command, or None."""
    head = []
    if tokens[:1] == ["DEBIAN_FRONTEND=noninteractive"]:
        head, tokens = tokens[:1], tokens[1:]
    if not tokens:
        return None
    if not head and tokens == ["export", "DEBIAN_FRONTEND=noninteractive"]:
        return " ".join(tokens)
    if not head and tokens == ["rm", "-rf", "/var/lib/apt/lists/*"]:
        return " ".join(tokens)
    if tokens[0] in ("apt-get", "apt"):
        rest = tokens[1:]
        action = next((t for t in rest if not t.startswith("-")), None)
        if action not in ("update", "install", "clean"):
            return None
        rest.remove(action)
        flags = [t for t in rest if t.startswith("-")]
        packages = [t for t in rest if not t.startswith("-")]
        if set(flags) - APT_FLAGS or any(not APT_PACKAGE.fullmatch(p) for p in packages):
            return None
        if (action == "install") != bool(packages):
            return None
        return _canonical([*head, tokens[0], action], flags, packages)
    if tokens[:3] in (["python3", "-m", "pip"], ["python", "-m", "pip"]):
        tool, rest = tokens[:3], tokens[3:]
    elif tokens[0] in ("pip", "pip3"):
        tool, rest = tokens[:1], tokens[1:]
    else:
        return None
    if head or rest[:1] != ["install"]:
        return None
    flags, packages, rest = [], [], rest[1:]
    while rest:
        token = rest.pop(0)
        name, _, value = token.partition("=")
        if name in PIP_INDEX_FLAGS and (value or rest):
            value = value or rest.pop(0)
            if value not in PIP_INDEXES:
                return None
            flags.append(f"{name} {value}")
        elif token.startswith("-"):
            if token not in PIP_FLAGS and not PIP_TIMEOUT.fullmatch(token):
                return None
            flags.append(token)
        elif PIP_REQUIREMENT.fullmatch(token):
            packages.append(token)
        else:
            return None
    if not packages:
        return None
    return _canonical([*tool, "install"], flags, packages)


def install_line(command):
    """Canonical form of a line that only installs packages; "" for blank/comment; else None."""
    text = command.strip()
    if not text or text.startswith("#"):
        return ""
    try:
        lexer = shlex.shlex(text, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        return None
    parts, current = [], []
    for token in tokens:
        if token in ("&&", ";"):
            parts += [current, token]
            current = []
        elif token and not set(token) - set("();<>|&"):
            return None
        else:
            current.append(token)
    if current:
        parts.append(current)
    result = []
    for part in parts:
        if isinstance(part, str):
            result.append(" &&" if part == "&&" else ";")
            continue
        canonical = _install_command(part) if part else None
        if canonical is None:
            return None
        result.append((" " if result else "") + canonical)
    return "".join(result).rstrip(";") if result and result[-1] != " &&" else None


def split_post_install(text):
    """Return (canonical install script, task suffix byte for byte)."""
    prefix, suffix, installs = [], [], False
    for command, bodies, raw in logical_lines(text):
        if not suffix and not bodies:
            canonical = install_line(command)
            if canonical is not None:
                if canonical:
                    prefix.append(canonical)
                    installs = installs or " install " in canonical
                continue
        suffix.append(raw)
    return ("\n".join(prefix) + "\n" if installs else ""), "".join(suffix)


# --- post_install.sh: what the task suffix may contain ------------------------------------

# Commands that install packages or reach the network. Such steps stay in the image.
NETWORK = [
    ("package-manager", re.compile(
        r"\b(?:apt|apt-get|apt-key|aptitude|dpkg|pip3?|pipx|npm|npx|yarn|pnpm|gem|bundle|conda|snap|"
        r"add-apt-repository|rustup)\b|-m\s+pip\b")),
    ("download", re.compile(r"\b(?:curl|wget|urlopen|urlretrieve)\b|requests\.(?:get|post)\b|"
                            r"\bgit\s+(?:clone|fetch|pull|submodule)\b")),
    ("cargo", re.compile(r"\bcargo\s+(?:install|build|fetch|run|test|update|add|check)\b")),
    # Compiling local code with the image's Go needs no network; fetching modules does.
    ("go", re.compile(r"\bgo\s+(?:get|install|mod\s+(?:tidy|download|vendor|graph|verify|why)|work\s+sync)\b")),
]
_AT_COMMAND = r"(?:^|&&|\|\||;|\(|\bsudo\s|\bexec\s|\bthen\s|\bdo\s)\s*"
# Only a docker call counts; docker-compose.yml written as a file or a docker group does not.
NETWORK.append(("docker", re.compile(_AT_COMMAND + r"docker(?:d|-compose)?\b", re.M)))
# Processes that would keep running in the task container. At image build they end with the build.
SERVICES = [
    ("service", re.compile(
        r"\b(?:systemctl|pg_ctl|pg_ctlcluster|pg_createcluster|initdb|mysqld_safe|mysql_install_db|"
        r"supervisord|nohup|setsid|disown)\b|/etc/init\.d/|"
        + _AT_COMMAND + r"(?:service|redis-server|nginx|postgres|mongod|mysqld|mariadbd|sshd|"
        r"cron|crond|memcached|dockerd)\b(?!\s*=)", re.M)),
    ("background-process", re.compile(r"(?<![&><|\\])&(?![&>\d])")),
    # May start a login session or user processes; left to the upstream build.
    ("runs-as-user", re.compile(r"\bsu\s+(?:-l?\s+)?\w+\s+-c\b|\bsudo\s+-u\b|\brunuser\b")),
]
# The suffix must not depend on being a part of /tmp/post_install.sh.
SELF_REFERENCE = re.compile(r"post_install|base_install|\$0\b|BASH_SOURCE")


def writes_text_only(command):
    """True for `echo|printf TEXT > FILE` (or >>): the text is data, nothing in it runs now."""
    if "$(" in command or "`" in command:
        return False
    try:
        lexer = shlex.shlex(command.strip(), posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        return False
    operators = [i for i, token in enumerate(tokens) if token and not set(token) - set("();<>|&")]
    return (len(tokens) >= 3 and tokens[0] in ("echo", "printf") and len(operators) == 1
            and operators[0] == len(tokens) - 2 and tokens[-2] in (">", ">>"))


def suffix_findings(suffix):
    """Reasons why a task suffix has to stay in the image build (empty: it may run as setup)."""
    scanned = []
    for command, bodies, _ in logical_lines(suffix):
        # A here-document written to a file is data. One fed to an interpreter is code.
        if FILE_HEREDOC.match(command):
            # The header names the target file; scan it only when it also runs something.
            if re.search(r"&&|\|\||;|\|", command.split("<<", 1)[0] + command.rsplit(">", 1)[-1]):
                scanned.append(command)
            continue
        if writes_text_only(command):
            continue
        scanned.append(command)
        scanned.extend(bodies)
    text = "".join(scanned)
    found = [name for name, pattern in (*NETWORK, *SERVICES) if pattern.search(text)]
    # A go.mod with dependencies makes a later go build fetch modules, even when the file is
    # written by a here-document.
    if "go" not in found and "go.mod" in suffix and re.search(r"^\s*require\b", suffix, re.M):
        found.append("go")
    if SELF_REFERENCE.search(text):
        found.append("self-reference")
    if not suffix.strip():
        found.append("no-task-part")
    return found


# --- tasks --------------------------------------------------------------------------------
# Setup creates or changes a git repository / the instruction refers to git.
def read_task(blob):
    """{name: (content, mode)}, or None when the archive holds more than regular files."""
    files = {}
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:*") as archive:
        for member in archive:
            if member.isdir():
                continue
            if not member.isfile() or member.name in files:
                return None
            files[member.name] = (archive.extractfile(member).read(), member.mode)
    return files


def classify(files):
    """Describe how one task could share an image: family, findings, canonical prefix, suffix."""
    info = {"family": None, "findings": []}
    if files is None:
        return {**info, "findings": ["unsupported-archive"]}
    dockerfile = files["environment/Dockerfile"][0].decode()
    environment = {name for name in files if name.startswith("environment/")}
    fixtures = {}
    if dockerfile == LEGACY_DOCKERFILE:
        info["family"] = "legacy"
        expected = {"environment/Dockerfile", "environment/post_install.sh"}
    else:
        info["family"] = "v2"
        copies = FIXTURE_COPY.findall(dockerfile)
        fixtures = {f"environment/{source}": target for source, target, destination in copies
                    if target == destination}
        if (FIXTURE_COPY.sub("", dockerfile).replace("\n\n\n", "\n\n") != V2_DOCKERFILE
                or len(fixtures) != len(copies)):
            return {**info, "findings": ["unknown-dockerfile"]}
        if hashlib.sha256(files["environment/base_install.sh"][0]).hexdigest() != V2_BASE_INSTALL_SHA256:
            info["findings"].append("base-install-variant")
        expected = {"environment/Dockerfile", "environment/post_install.sh",
                    "environment/base_install.sh", *fixtures}
    if environment != expected:
        return {**info, "findings": ["unexpected-environment-files"]}
    if any(name.startswith(("setup_files/", "solution/")) or name == "tests/setup.sh" for name in files):
        return {**info, "findings": ["has-setup-or-solution"]}
    try:
        script = files["environment/post_install.sh"][0].decode()
    except UnicodeDecodeError:
        return {**info, "findings": ["undecodable-script"]}
    prefix, suffix = split_post_install(script)
    if not prefix:
        info["findings"].append("no-install-prefix")
    info["findings"] += suffix_findings(suffix)
    downloads, rust = {}, False
    if info["findings"] and set(info["findings"]) <= {"download", "package-manager"}:
        rewritten = offline_suffix(suffix)
        if rewritten and not suffix_findings(rewritten[0]):
            suffix, downloads, rust = rewritten
            info["findings"] = []
    return {**info, "prefix": prefix, "suffix": suffix, "fixtures": fixtures, "downloads": downloads,
            "rust": rust}


def shared_files(files, info):
    """The task with its package installation in a shared image and the rest in setup_files/."""
    result = {name: value for name, value in files.items() if name not in info["fixtures"]}
    if info["family"] == "v2":
        result["environment/Dockerfile"] = (V2_DOCKERFILE.encode(), files["environment/Dockerfile"][1])
    result["environment/post_install.sh"] = (info["prefix"].encode(), files["environment/post_install.sh"][1])
    for name, target in info["fixtures"].items():
        result["setup_files/fixtures" + target] = files[name]
    result["setup_files/task_install.sh"] = (info["suffix"].encode(), 0o644)
    for name, content in info.get("downloads", {}).items():
        result[f"setup_files/downloads/{name}"] = (content, 0o644)
    result["setup_files/setup.sh"] = (SETUP_SH.encode(), 0o755)
    return result


# --- downloads and Rust: rewritten so that setup needs no network ---------------------------
#
# A plain file download (`wget URL -O FILE`, `curl -L URL -o FILE`) is fetched once by
# `fetch-downloads` into a content-addressed cache, shipped in setup_files/downloads/ and
# copied to the same destination at setup. The standard rustup installer line is dropped
# from setup because the superset image installs the same stable toolchain for root.

DOWNLOAD_DIR = "/setup_files/downloads"
DOWNLOAD_MAX_BYTES = 20 * 1024 * 1024
DOWNLOAD_CACHE = None  # set by main(): Path with index.json {url: {sha256, size}} and <sha256> files
WGET_FLAGS = {"-q", "--quiet", "-nv", "--no-verbose", "-c", "--continue", "-N", "--no-check-certificate"}
WGET_VALUE_FLAGS = re.compile(r"--(?:tries|timeout|progress|retry-connrefused)(?:=\S+)?")
CURL_FLAG_LETTERS = set("sSLfk")
CURL_FLAGS = {"--silent", "--show-error", "--location", "--fail", "--insecure", "--create-dirs",
              "--tlsv1.2", "--proto=https", "--compressed"}
RUSTUP_INSTALL = re.compile(
    r"\s*curl --proto '?=https'? --tlsv1\.2 -sSf https://sh\.rustup\.rs \| sh -s -- -y"
    r"(?: --no-modify-path| --profile (?:minimal|default))*\s*")
RUST_IN_IMAGE = ": # The stable Rust toolchain for root comes from the shared image\n"
# apt's rustc is part of the union; rustup installs beside it, as the task lines did.
RUSTUP_IMAGE_INSTALL = ("export HOME=/root RUSTUP_INIT_SKIP_PATH_CHECK=yes && curl --proto '=https' --tlsv1.2 "
                        "-sSf https://sh.rustup.rs | sh -s -- -y\n")


def download_command(command):
    """(url, destination, create_dirs) of a plain file download; destination is relative to the
    working directory exactly as the tool resolves it. None for anything else."""
    text = command.strip()
    if re.search(r"[|;&<>`]|\$\(", text):
        return None
    try:
        tokens = shlex.split(text)
    except ValueError:
        return None
    if not tokens or tokens[0] not in ("wget", "curl"):
        return None
    tool, rest, url, out, directory, remote = tokens[0], tokens[1:], None, None, None, False
    while rest:
        token = rest.pop(0)
        if token.startswith(("http://", "https://")):
            if url:
                return None
            url = token
        elif tool == "wget" and (token in ("-O", "--output-document") or re.fullmatch(r"-[qnc]*O", token)):
            out = rest.pop(0) if rest else None
        elif tool == "wget" and token.startswith("--output-document="):
            out = token.split("=", 1)[1]
        elif tool == "wget" and token in ("-P", "--directory-prefix"):
            directory = rest.pop(0) if rest else None
        elif tool == "wget" and (token in WGET_FLAGS or WGET_VALUE_FLAGS.fullmatch(token)):
            pass
        elif tool == "curl" and re.fullmatch(r"-[sSLfk]*o", token) or token == "--output":
            out = rest.pop(0) if rest else None
        elif tool == "curl" and (re.fullmatch(r"-[sSLfk]*O", token) or token == "--remote-name"):
            remote = True
        elif tool == "curl" and (token in CURL_FLAGS or re.fullmatch(r"-[sSLfk]+", token)):
            pass
        elif tool == "curl" and token == "--retry" and rest and rest[0].isdigit():
            rest.pop(0)
        else:
            return None
    if not url or re.search(r"[?#]", url) or out == "-":
        return None
    name = url.rstrip("/").rsplit("/", 1)[-1]
    if out is None:
        if tool == "curl" and not remote or not name or url.endswith("/"):
            return None
        out = f"{directory}/{name}" if directory else name
    return url, out, tool == "curl" and "--create-dirs" in tokens


def cached_download(url):
    """The cached bytes of url, or None when absent, oversized or failing its checksum."""
    if DOWNLOAD_CACHE is None:
        return None
    record = json.loads((DOWNLOAD_CACHE / "index.json").read_text()).get(url) if (
        DOWNLOAD_CACHE / "index.json").exists() else None
    if not record or record["size"] > DOWNLOAD_MAX_BYTES:
        return None
    content = (DOWNLOAD_CACHE / record["sha256"]).read_bytes()
    if hashlib.sha256(content).hexdigest() != record["sha256"]:
        raise ValueError(f"download cache corrupt for {url}")
    return content


def offline_suffix(suffix):
    """(suffix, {name: bytes}, rust) with downloads and the rustup installer rewritten, or None."""
    parts, downloads, rust, changed = [], {}, False, False
    # The image installs Rust in root's default locations only.
    default_rust = not re.search(r"\b(?:RUSTUP_HOME|CARGO_HOME)\s*=", suffix)
    for command, bodies, raw in logical_lines(suffix):
        if default_rust and not bodies and RUSTUP_INSTALL.fullmatch(command.rstrip("\n")):
            parts.append(RUST_IN_IMAGE)
            rust = changed = True
            continue
        download = None if bodies else download_command(command)
        content = cached_download(download[0]) if download else None
        if content is None:
            parts.append(raw)
            continue
        url, destination, create_dirs = download
        name = hashlib.sha256(content).hexdigest()[:16] + "-" + re.sub(r"[^A-Za-z0-9._-]", "_",
                                                                         url.rsplit("/", 1)[-1])
        downloads[name] = content
        target = shlex.quote(destination)
        mkdir = f'mkdir -p "$(dirname {target})" && ' if create_dirs else ""
        parts.append(f"{mkdir}cp {DOWNLOAD_DIR}/{name} {target}\n")
        changed = True
    return ("".join(parts), downloads, rust) if changed else None


def fetch_downloads_main():
    """Fill the download cache with every plain download the patcher could ship."""
    import urllib.request
    import pyarrow.parquet as pq
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    args = parser.parse_args()
    args.cache.mkdir(parents=True, exist_ok=True)
    index_path = args.cache / "index.json"
    index = json.loads(index_path.read_text()) if index_path.exists() else {}
    urls = set()
    for batch in pq.ParquetFile(args.source).iter_batches(batch_size=64):
        for row in batch.to_pylist():
            files = read_task(row["task_binary"])
            if files is None or "environment/post_install.sh" not in files:
                continue
            _, suffix = split_post_install(files["environment/post_install.sh"][0].decode(errors="replace"))
            for command, bodies, _ in logical_lines(suffix):
                download = None if bodies else download_command(command)
                if download:
                    urls.add(download[0])
    failures = {}
    for url in sorted(urls - set(index)):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "Wget/1.21.2"})
            with urllib.request.urlopen(request, timeout=60) as response:
                content = response.read(DOWNLOAD_MAX_BYTES + 1)
        except Exception as exc:
            failures[url] = repr(exc)
            continue
        digest = hashlib.sha256(content).hexdigest()
        if len(content) <= DOWNLOAD_MAX_BYTES:
            (args.cache / digest).write_bytes(content)
        index[url] = {"sha256": digest, "size": len(content)}
        index_path.write_text(json.dumps(index, indent=1, sort_keys=True) + "\n")
    (args.cache / "failures.json").write_text(json.dumps(failures, indent=1, sort_keys=True) + "\n")
    print(json.dumps({"urls": len(urls), "cached": len(index), "failed": len(failures)}))


# --- superset images ----------------------------------------------------------------------
#
# Grouping by identical install commands still leaves thousands of images. Instead, all
# regular tasks of a family share one image with the union of the family's packages. Extra
# packages a task did not ask for are accepted. Tasks pinning a pip version keep exact grouping,
# since one image cannot hold two versions. Instructions that mention installing a package the
# superset already contains are recorded in the manifest for the no-op review.

PIP_INDEX = "https://download.pytorch.org/whl/cpu"
PIP_VERSION = re.compile(r"[=<>~!]")
# Words that are package names but appear in instructions as ordinary words or tools.
COMMON_WORDS = set(
    "file bash build time zip make sed grep tables parallel rename tar patch expect pip perl less tree "
    "jq bc vim nano curl wget git python3 python3-pip pytest ca-certificates coreutils procps gcc g++ "
    "unzip gzip findutils util-linux".split())


def prefix_packages(prefix):
    """(apt packages, {normalized pip name: requirement}) of a canonical install prefix."""
    apt, pip = set(), {}
    for line in prefix.splitlines():
        for part in re.split(r" && |; ", line):
            tokens = shlex.split(part)
            if tokens[:1] == ["DEBIAN_FRONTEND=noninteractive"]:
                tokens = tokens[1:]
            if tokens[:2] in (["apt-get", "install"], ["apt", "install"]):
                apt.update(token for token in tokens[2:] if not token.startswith("-"))
            elif "install" in tokens and (tokens[0] in ("pip", "pip3") or tokens[:3] in (
                    ["python3", "-m", "pip"], ["python", "-m", "pip"])):
                rest = tokens[tokens.index("install") + 1:]
                for i, token in enumerate(rest):
                    if token.startswith("-") or (i and rest[i - 1] in PIP_INDEX_FLAGS):
                        continue
                    name = re.split(r"[=<>~!\[]", token)[0].lower().replace("_", "-")
                    pip[name] = token if PIP_VERSION.search(token) or "[" in token else name
    return apt, pip


# Packages that cannot be installed together with the rest of the union on ubuntu:22.04
# (apt-get install -s): gcc-multilib conflicts with the aarch64 cross compilers.
APT_CONFLICTS = {"gcc-multilib"}
# pip resolves the whole union by backtracking to ancient, unbuildable releases, so the
# superset installs embedded locks instead. Regenerate whenever the union changes,
# then copy the exact output into SUPERSET_PIP_LOCKS:
#   uv pip compile REQUIREMENTS --python-version 3.10 --python-platform x86_64-manylinux_2_35 \
#     --extra-index-url https://download.pytorch.org/whl/cpu --index-strategy unsafe-best-match \
#     --no-header --no-annotate -o /tmp/tmax-superset-FAMILY.lock.txt
SUPERSET_PIP_LOCKS = {
    "legacy": """agate==1.14.2
agate-dbf==0.2.4
agate-excel==0.4.2
agate-sql==0.7.3
aiohappyeyeballs==2.7.1
aiohttp==3.14.5
aiosignal==1.4.0
aiosmtpd==1.4.6
alembic==1.20.0
annotated-doc==0.0.5
annotated-types==0.8.0
anyio==4.15.1
argon2-cffi==25.1.0
argon2-cffi-bindings==26.1.0
arrow==1.4.0
arviz==0.23.4
astropy==6.1.7
astropy-iers-data==0.2026.10.12.1.3.27
asttokens==3.0.2
async-lru==2.4.0
async-timeout==5.0.1
asyncio==4.0.0
atpublic==8.0.1
attrs==26.1.0
babel==2.18.0
backports-asyncio-runner==1.2.0
bandit==1.9.4
bcrypt==5.0.0
beautifulsoup4==4.15.0
biopython==1.88
bleach==6.4.0
blinker==1.9.0
blosc2==4.3.3
build==1.6.1
cachetools==7.2.2
capstone==5.0.9
certifi==2026.7.22
cffi==2.1.1
cftime==1.6.5
chardet==7.6.0
charset-normalizer==3.5.2
click==8.5.0
cloudpickle==3.1.2
colored-traceback==0.4.2
comm==0.2.3
cons==0.4.7
contourpy==1.3.2
cramjam==2.11.0
cryptography==50.0.2
csvkit==2.2.0
cycler==0.12.1
cython==3.3.0
dask==2026.8.0
databricks-sdk==0.153.0
dateparser==1.4.3
dbfread==2.0.7
debugpy==1.8.22
decorator==5.3.1
defusedxml==0.7.1
deprecated==1.3.1
docker==7.2.0
dpkt==1.9.8
duckdb==1.5.6
emcee==3.1.6
emoji==2.16.0
entrypoints==0.4.2
et-xmlfile==2.0.0
etuples==0.3.10
exceptiongroup==1.3.1
executing==2.3.0
faiss-cpu==1.15.1
fastapi==0.143.3
fastjsonschema==2.22.2
fastparquet==2026.9.0
filelock==4.1.0
flask==3.1.3
flask-cors==6.0.5
flask-limiter==4.1.1
fonttools==4.65.0
formulaic==1.2.2
fqdn==1.6.0
frozenlist==1.8.0
fsspec==2026.9.0
gitdb==4.0.12
gitpython==3.2.1
google-auth==2.62.0
graphene==3.4.3
graphql-core==3.2.13
graphql-relay==3.2.0
greenlet==3.5.6
grpcio==1.84.0
grpcio-reflection==1.84.0
grpcio-tools==1.84.0
gunicorn==26.2.0
h11==0.16.0
h5netcdf==1.8.1
h5py==3.16.0
hf-xet==1.7.0
httpcore==1.0.9
httpx==0.28.1
huey==3.4.0
huggingface-hub==1.33.0
hypothesis==6.168.5
idna==3.20
importlib-metadata==9.0.1
iniconfig==2.3.1
interface-meta==2.0.1
intervaltree==3.2.1
invoke==3.0.3
ipykernel==7.3.0
ipython==8.39.0
ipywidgets==8.1.9
isodate==0.7.2
isoduration==20.11.0
itsdangerous==2.2.0
jedi==0.20.1
jinja2==3.1.6
joblib==1.6.0
json5==0.17.3
jsonpointer==3.2.1
jsonschema==4.26.0
jsonschema-specifications==2025.9.1
jupyter==1.1.1
jupyter-builder==1.2.3
jupyter-client==8.10.0
jupyter-console==6.6.3
jupyter-core==5.9.1
jupyter-events==0.12.1
jupyter-lsp==2.3.1
jupyter-server==2.21.1
jupyter-server-terminals==0.5.4
jupyterlab==4.6.4
jupyterlab-pygments==0.3.0
jupyterlab-server==2.28.1
jupyterlab-widgets==3.0.17
kiwisolver==1.5.1
kuzu==0.11.3
lark==1.3.1
leather==0.4.1
levenshtein==0.27.4
limits==5.8.0
locket==1.0.0
logical-unification==0.4.7
lxml==6.1.3
mako==1.4.3
markdown-it-py==4.2.0
markupsafe==3.0.4
matplotlib==3.10.9
matplotlib-inline==0.2.2
maturin==1.15.0
mdurl==0.1.2
memory-profiler==0.61.0
minikanren==1.0.5
mistune==3.3.4
mlflow==3.17.1
mlflow-skinny==3.17.1
mlflow-tracing==3.17.1
mongomock==4.3.0
mpi4py==4.1.2
mpmath==1.3.0
msgpack==1.2.3
multidict==7.1.0
multipledispatch==1.0.0
narwhals==2.27.1
nbclient==0.11.0
nbconvert==7.17.2
nbformat==5.11.1
ndindex==1.10.1
nest-asyncio2==1.7.4
netcdf4==1.7.4
networkx==3.4.2
notebook==7.6.3
notebook-shim==0.2.4
numexpr==2.14.1
numpy==2.2.6
olefile==0.47
openpyxl==3.1.5
opentelemetry-api==1.45.1
opentelemetry-proto==1.45.1
opentelemetry-sdk==1.45.1
opentelemetry-semantic-conventions==0.66b1
ordered-set==4.1.0
overrides==7.7.0
packaging==26.3
pandas==2.3.3
pandocfilters==1.5.1
papermill==2.7.0
paramiko==5.0.0
parsedatetime==2.6
parso==0.8.7
partd==1.4.2
passlib==1.7.4
patsy==1.0.3
pexpect==4.9.0
pillow==12.3.0
pip==26.2.1
platformdirs==4.12.4
pluggy==1.7.0
plumbum==2.0.2
prettytable==3.18.0
prometheus-client==0.26.0
prompt-toolkit==3.0.53
propcache==0.5.4
protobuf==7.36.2
psutil==7.2.2
ptyprocess==0.7.0
pure-eval==0.2.4
pwntools==4.15.0
py-cpuinfo==9.0.0
pyarrow==25.0.1
pyasn1==0.6.4
pyasn1-modules==0.4.2
pycparser==3.11
pycryptodome==4.0.0
pydantic==2.14.1
pydantic-core==2.50.1
pyelftools==0.33
pyerfa==2.0.1.5
pygments==2.21.0
pyjwt==2.15.1
pymc==5.25.1
pynacl==1.6.2
pyopenssl==26.4.0
pyparsing==3.3.3
pyproject-hooks==1.3.3
pyserial==3.5
pysocks==1.7.1
pytensor==2.31.7
pytest==9.1.1
pytest-asyncio==1.4.0
pytest-json-report==1.5.0
pytest-metadata==3.1.1
pytest-mock==3.16.0
pytest-timeout==2.4.0
python-dateutil==2.9.0.post0
python-dotenv==1.2.4
python-json-logger==4.2.0
python-levenshtein==0.27.4
python-slugify==9.1.3
pytimeparse==1.1.8
pytz==2026.5
pyyaml==6.0.3
pyzmq==27.2.0
rapidfuzz==3.14.5
rdflib==7.6.0
referencing==0.37.0
regex==2026.9.29
requests==2.34.2
rfc3339-validator==0.1.4
rfc3986-validator==0.1.1
rfc3987-syntax==1.1.0
rich==15.0.0
ropgadget==7.7
rpds-py==0.30.0
rpyc==6.0.2
safetensors==0.8.0
scapy==2.8.0
scikit-learn==1.7.2
scipy==1.15.3
seaborn==0.13.2
semantic-version==2.10.0
semver==3.1.0
send2trash==2.1.0
sentence-transformers==6.1.0
sentinels==1.1.1
setuptools==84.0.0
setuptools-rust==1.13.0
shellingham==1.5.4
six==1.17.0
skops==0.16.0
smmap==5.0.3
sortedcontainers==2.4.0
soupsieve==2.10
sqlalchemy==2.0.55
sqlparse==0.6.0
stack-data==0.6.3
starlette==1.7.0
statsmodels==0.15.0
stevedore==5.8.0
supervisor==4.3.0
sympy==1.14.0
tables==3.10.1
tenacity==9.2.1
terminado==0.18.1
text-unidecode==1.3
threadpoolctl==3.7.0
tinycss2==1.5.1
tokenizers==0.23.3
tomli==2.5.0
toolz==1.2.0
torch==2.14.1+cpu
tornado==6.5.10
tqdm==4.70.1
traitlets==5.16.1
transformers==5.20.1
typer==0.27.3
typing-extensions==4.16.0
typing-inspection==0.4.4
tzdata==2026.5
tzlocal==5.4.4
unicorn==2.1.2
unix-ar==0.2.1
uri-template==1.3.0
urllib3==2.8.0
uvicorn==0.54.0
watchdog==6.0.0
wcwidth==0.9.2
webcolors==25.10.0
webencodings==0.6.1
websocket-client==1.9.2
websockets==16.1.1
werkzeug==3.1.9
wheel==0.48.0
widgetsnbextension==4.0.16
wrapt==2.5.1
xarray==2025.6.1
xarray-einstats==0.8.0
xlrd==2.0.2
xmltodict==1.0.4
yarl==1.25.1
z3-solver==5.3.1.0
zipp==4.1.1
zstandard==0.25.0
""",
    "v2": """aiohappyeyeballs==2.7.1
aiohttp==3.14.5
aiosignal==1.4.0
aiosmtpd==1.4.6
alembic==1.20.0
altgraph==0.17.5
amqp==5.4.1
annotated-doc==0.0.5
annotated-types==0.8.0
anyio==4.15.1
argcomplete==3.7.2
argon2-cffi==25.1.0
argon2-cffi-bindings==26.1.0
arrow==1.4.0
asttokens==3.0.2
async-lru==2.4.0
async-timeout==5.0.1
atpublic==8.0.1
attrs==26.1.0
audioread==3.1.0
av==17.1.0
babel==2.18.0
bcrypt==5.0.0
beautifulsoup4==4.15.0
billiard==4.3.1
biopython==1.88
bleach==6.4.0
blinker==1.9.0
boto3==1.43.114
botocore==1.43.114
cachetools==7.2.2
capstone==5.0.9
category-encoders==2.8.1
celery==5.6.3
certifi==2026.7.22
cffi==2.1.1
cftime==1.6.5
chardet==7.6.0
charset-normalizer==3.5.2
click==8.1.8
click-default-group==1.2.4
click-didyoumean==0.3.1
click-plugins==1.1.1.2
click-repl==0.4.1
cloudpickle==3.1.2
coloredlogs==15.0.1
comm==0.2.3
contourpy==1.3.2
cramjam==2.11.0
croniter==6.2.4
cryptography==50.0.2
ctranslate2==4.8.3
cycler==0.12.1
cython==3.3.0
databricks-sdk==0.153.0
debugpy==1.8.22
decorator==5.3.1
defusedxml==0.7.1
deprecated==1.3.1
dnspython==2.8.0
docker==7.2.0
dpkt==1.9.8
duckdb==1.5.6
edge-tts==7.3.1
emcee==3.1.6
emoji==2.16.0
entrypoints==0.4.2
exceptiongroup==1.3.1
executing==2.3.0
faiss-cpu==1.15.1
fastapi==0.143.3
faster-whisper==1.2.1
fastjsonschema==2.22.2
fastparquet==2026.9.0
fbm==0.3.0
ffmpeg-python==0.2.0
filelock==4.1.0
flask==3.1.3
flask-cors==6.0.5
flask-limiter==4.1.1
flatbuffers==25.12.19
fonttools==4.65.0
formulaic==1.2.2
fqdn==1.6.0
frozenlist==1.8.0
fsspec==2026.9.0
future==1.0.0
gitdb==4.0.12
gitpython==3.2.1
google-auth==2.62.0
graphene==3.4.3
graphql-core==3.2.13
graphql-relay==3.2.0
greenlet==3.5.6
grpcio==1.84.0
grpcio-tools==1.84.0
gtts==2.5.4
gunicorn==26.2.0
h11==0.16.0
h5py==3.16.0
hf-xet==1.7.0
httpcore==1.0.9
httpx==0.28.1
huey==3.4.0
huggingface-hub==1.16.1
humanfriendly==10.0
hypothesis==6.168.5
idna==3.20
imageio==2.38.0
imageio-ffmpeg==0.6.0
importlib-metadata==9.0.1
iniconfig==2.3.1
interface-meta==2.0.1
invoke==3.0.3
ipykernel==7.3.0
ipython==8.39.0
ipywidgets==8.1.9
isodate==0.7.2
isoduration==20.11.0
itsdangerous==2.2.0
jedi==0.20.1
jinja2==3.1.6
jiwer==4.0.0
jmespath==1.1.0
joblib==1.6.0
json5==0.17.3
jsonpatch==1.35
jsonpointer==3.2.1
jsonschema==4.26.0
jsonschema-specifications==2025.9.1
jupyter==1.1.1
jupyter-builder==1.2.3
jupyter-client==8.10.0
jupyter-console==6.6.3
jupyter-core==5.9.1
jupyter-events==0.12.1
jupyter-lsp==2.3.1
jupyter-server==2.21.1
jupyter-server-terminals==0.5.4
jupyterlab==4.6.4
jupyterlab-pygments==0.3.0
jupyterlab-server==2.28.1
jupyterlab-widgets==3.0.17
kiwisolver==1.5.1
kombu==5.6.2
kuzu==0.11.3
lark==1.3.1
lazy-loader==0.6
levenshtein==0.27.4
librosa==0.11.0
lief==1.0.0
limits==5.8.0
llvmlite==0.50.0
mako==1.4.3
markdown==3.10.3
markdown-it-py==4.2.0
markupsafe==3.0.4
matplotlib==3.10.9
matplotlib-inline==0.2.2
maturin==1.15.0
mdurl==0.1.2
minio==7.2.20
mistune==3.3.4
mlflow==3.17.1
mlflow-skinny==3.17.1
mlflow-tracing==3.17.1
more-itertools==11.1.0
mpi4py==4.1.2
mpmath==1.3.0
msgpack==1.2.3
multidict==7.1.0
narwhals==2.27.1
nbclient==0.11.0
nbconvert==7.17.2
nbformat==5.11.1
neo4j==6.4.1
nest-asyncio2==1.7.4
netcdf4==1.7.4
networkx==3.4.2
notebook==7.6.3
notebook-shim==0.2.4
numba==0.68.0
numpy==2.2.6
onnxruntime==1.23.2
openai-whisper==20250625
opencv-python==5.0.0.93
opencv-python-headless==5.0.0.93
opentelemetry-api==1.45.1
opentelemetry-proto==1.45.1
opentelemetry-sdk==1.45.1
opentelemetry-semantic-conventions==0.66b1
ordered-set==4.1.0
overrides==7.7.0
packaging==26.3
pandas==2.3.3
pandocfilters==1.5.1
papermill==2.7.0
paramiko==5.0.0
parso==0.8.7
patsy==1.0.3
pexpect==4.9.0
pillow==12.3.0
pip==26.2.1
platformdirs==4.12.4
pluggy==1.7.0
pocketsphinx==5.1.1
polars==2.0.0
polars-runtime-32==2.0.0
pooch==1.9.0
prettytable==3.18.0
prometheus-client==0.26.0
prompt-toolkit==3.0.53
propcache==0.5.4
protobuf==7.36.2
psutil==7.2.2
psycopg2-binary==2.9.13
ptyprocess==0.7.0
pure-eval==0.2.4
pyarrow==25.0.1
pyasn1==0.6.4
pyasn1-modules==0.4.2
pybind11==3.1.0
pycparser==3.11
pycryptodome==4.0.0
pydantic==2.14.1
pydantic-core==2.50.1
pydub==0.25.1
pyelftools==0.33
pyftpdlib==2.2.0
pygments==2.21.0
pyinstaller==6.22.3
pyinstaller-hooks-contrib==2026.8
pyjwt==2.15.1
pymongo==4.18.3
pynacl==1.6.2
pyopenssl==26.4.0
pyparsing==3.3.3
pytesseract==0.3.13
pytest==9.1.1
python-barcode==0.16.1
python-dateutil==2.9.0.post0
python-dotenv==1.2.4
python-json-logger==4.2.0
python-levenshtein==0.27.4
python-multipart==0.0.32
pytz==2026.5
pyyaml==6.0.3
pyzbar==0.1.9
pyzmq==27.2.0
qrcode==8.2
rapidfuzz==3.14.5
rdflib==7.6.0
redis==8.1.0
referencing==0.37.0
regex==2026.9.29
requests==2.34.2
rfc3339-validator==0.1.4
rfc3986-validator==0.1.1
rfc3987-syntax==1.1.0
rich==15.0.0
rpds-py==0.30.0
rq==2.12.0
ruamel-yaml==0.19.1
s3transfer==0.19.2
safetensors==0.8.0
scapy==2.8.0
scikit-image==0.25.2
scikit-learn==1.7.2
scipy==1.15.3
seaborn==0.13.2
semantic-version==2.10.0
semver==3.1.0
send2trash==2.1.0
sentence-transformers==6.1.0
setuptools==84.0.0
setuptools-rust==1.13.0
setuptools-scm==10.3.4
shellingham==1.5.4
simplejson==4.2.0
six==1.17.0
skops==0.16.0
smmap==5.0.3
sortedcontainers==2.4.0
sounddevice==0.5.6
soundfile==0.14.0
soupsieve==2.10
soxr==1.1.0
speechrecognition==3.17.0
sqlalchemy==2.0.55
sqlglot==30.23.0
sqlite-fts4==1.0.3
sqlparse==0.6.0
stack-data==0.6.3
starlette==1.7.0
statsmodels==0.15.0
supervisor==4.3.0
sympy==1.14.0
tabulate==0.10.0
tenacity==9.2.1
terminado==0.18.1
thefuzz==0.22.1
threadpoolctl==3.7.0
tifffile==2025.5.10
tiktoken==0.14.0
tinycss2==1.5.1
tokenizers==0.23.3
tomli==2.5.0
tomlkit==0.15.2
torch==2.14.1+cpu
torchaudio==2.11.0+cpu
torchvision==0.29.1+cpu
tornado==6.5.10
tqdm==4.70.1
traitlets==5.16.1
transformers==5.17.0
triton==3.8.0
typer==0.27.3
typing-extensions==4.16.0
typing-inspection==0.4.4
tzdata==2026.5
tzlocal==5.4.4
uri-template==1.3.0
urllib3==2.8.0
uvicorn==0.54.0
vcs-versioning==2.6.0
vine==5.1.0
waitress==3.0.2
watchdog==6.0.0
wcwidth==0.9.2
webcolors==25.10.0
webencodings==0.6.1
websocket-client==1.9.2
websockets==16.1.1
werkzeug==3.1.9
wheel==0.48.0
whisper-ctranslate2==0.5.8
widgetsnbextension==4.0.16
wrapt==2.5.1
xmltodict==1.0.4
xxhash==4.0.1
yarl==1.25.1
yq==4.4.5
zipp==4.1.1
""",
}


def superset_prefix(family, apt, pip):
    """Install script of a superset image. Unlike the upstream scripts it stops at the first
    failing command, so a broken superset cannot pass as a built image."""
    lock = SUPERSET_PIP_LOCKS[family]
    normalized = lambda name: re.sub(r"[-_.]+", "-", re.split(r"[=<>~!\[ ;]", name)[0].lower())
    locked = {normalized(line) for line in lock.splitlines() if line.strip() and not line.startswith("#")}
    missing = {normalized(name) for name in pip} - locked
    if missing:
        raise ValueError(f"{family} superset lock lacks {sorted(missing)}; regenerate it (see SUPERSET_PIP_LOCKS)")
    return ("set -e -o pipefail\n"
            f"apt-get update && apt-get install -y {' '.join(sorted(apt))}\n"
            "python3 -m pip install --no-cache-dir -U pip\n"
            f"cat > {BUILD_DIR}/superset-requirements.txt <<'EOF'\n{lock.rstrip()}\nEOF\n"
            # apt's python3-* packages (sympy, pexpect) cannot be uninstalled by pip; the lock
            # goes to /usr/local, which precedes /usr/lib/python3 on sys.path.
            f"python3 -m pip install --no-cache-dir --ignore-installed --extra-index-url {PIP_INDEX} "
            f"-r {BUILD_DIR}/superset-requirements.txt\n")


def install_mentions(instruction, extras):
    """Extra packages the instruction mentions near the word install."""
    text = instruction.lower()
    return sorted(name for name in extras if len(name) > 2 and name not in COMMON_WORDS and re.search(
        r"install\w*[^.\n]{0,80}(?<![a-z0-9_.-])" + re.escape(name) + r"(?![a-z0-9_-])", text))


def runtime_setup_trigger():
    """True when the shared runner runs setup_files/setup.sh for a task without solution/."""
    from validation.stages.task_setup import detect
    with tempfile.TemporaryDirectory() as directory:
        task = Path(directory) / "task"
        (task / "setup_files").mkdir(parents=True)
        (task / "setup_files/setup.sh").write_text(SETUP_SH)
        try:
            return detect(task) == SETUP_COMMAND
        except Exception:
            return False


def payload_hash(files):
    digest = hashlib.sha256()
    for name in sorted(name for name in files if name.startswith("environment/")):
        digest.update(name.removeprefix("environment/").encode() + b"\0" + hashlib.sha256(files[name][0]).digest())
    return digest.hexdigest()


def environment_hash(files):
    """The image identity of the task's environment/ (as the image cache computes it)."""
    from harbor_patches.image_context import environment_dir_hash
    with tempfile.TemporaryDirectory() as directory:
        for name, (content, mode) in files.items():
            if name.startswith("environment/"):
                path = Path(directory) / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
                path.chmod(mode & 0o777 or 0o644)
        return environment_dir_hash(Path(directory) / "environment")


def runtime_catalog(cache, spec):
    """{id: manifest} for one spec from the verifier runtime cache."""
    path = Path(cache) / (spec_id(spec) + ".json")
    if not path.is_file():
        raise FileNotFoundError(f"verifier runtime {spec_id(spec)} is not prepared in {cache}")
    manifest = json.loads(path.read_text())
    # Every installed package must be one of the image's pins; only the interpreter's own pip
    # and setuptools may be added (a resolver-chosen version would not be the image's).
    pinned = {re.sub(r"[-_.]+", "-", r.split("==")[0]).lower() for r in spec["requirements"]} | {"pip", "setuptools"}
    extra = [r for r in manifest["resolved_requirements"]
             if re.sub(r"[-_.]+", "-", r.split("==")[0]).lower() not in pinned]
    if extra or set(spec["requirements"]) - set(manifest["resolved_requirements"]):
        raise ValueError(f"verifier runtime {spec_id(spec)} differs from its image pins: {extra}")
    return {spec_id(spec): manifest}


# Populated only from separately recorded post-audit online retries.
ONLINE_TASKS = {}


def set_network_policy(files, task):
    """Apply the user's offline-first policy, including explicit upstream online tasks."""
    import tomllib
    data, mode = files['task.toml']
    text = data.decode()
    tomllib.loads(text)  # Reject malformed source configuration rather than guessing.
    online = task in ONLINE_TASKS
    value = 'true' if online else 'false'
    match = re.search(r'(?ms)^\[environment\][ \t]*\n(.*?)(?=^\[|\Z)', text)
    if match:
        body = match.group(1)
        field = re.compile(r'(?m)^[ \t]*allow_internet[ \t]*=.*$')
        if field.search(body):
            body, count = field.subn('allow_internet = ' + value, body)
            if count != 1:
                raise ValueError('task.toml has duplicate allow_internet fields')
        else:
            body = 'allow_internet = ' + value + '\n' + body
        text = text[:match.start(1)] + body + text[match.end(1):]
    else:
        text = text.rstrip() + '\n\n[environment]\nallow_internet = ' + value + '\n'
    assert tomllib.loads(text)['environment']['allow_internet'] is online
    files['task.toml'] = (text.encode(), mode)
    return online


def main():
    import pyarrow as pa
    import pyarrow.parquet as pq
    from data.utils.harbor_parquet import pack_task
    from data.utils.patch_reporting import write_patch_report

    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--download-cache", type=Path,
                        help="cache filled by fetch-downloads; without it downloads stay in per-task images")
    parser.add_argument("--runtime-setup", choices=["auto", "off", "force"], default="auto",
                        help="auto: share images only if the runner has a setup trigger for tasks "
                             "without solution/. force: for static inspection only; without the "
                             "trigger such tasks start without their initial files")
    parser.add_argument("--verifier-inventory", type=Path,
                        help="hpc/offline_prep/image_python_inventory.py output; plans a prepared verifier runtime per task")
    parser.add_argument("--verifier-runtime-cache", type=Path,
                        help="cache with the ready runtimes (hpc/offline_prep/verifier_bundles.py); without it only "
                             "verifier-bundle-specs.json is written and tasks keep upstream grading")
    parser.add_argument('--published-input', action='store_true',
                        help='migrate only verifier runtime files in an existing published subset')
    parser.add_argument('--published-revision')
    parser.add_argument('--published-subset')
    parser.add_argument("--no-repairs", action="store_true",
                        help="omit post-build repairs; baseline must use the pinned upstream source")
    args = parser.parse_args()
    archived = {} if args.no_repairs else ARCHIVED
    unshared = {} if args.no_repairs else UNSHARED
    service_setup = {} if args.no_repairs else SERVICE_SETUP
    if args.verifier_runtime_cache and not args.verifier_inventory:
        parser.error("--verifier-runtime-cache needs --verifier-inventory")
    if args.published_input:
        if args.no_repairs:
            parser.error("--no-repairs requires pinned upstream input, not --published-input")
        if not all((args.verifier_inventory, args.verifier_runtime_cache, args.published_revision, args.published_subset)):
            parser.error('--published-input requires inventory, runtime cache, revision and subset')
        verifier_runtime.migrate_published(args.source, args.output, args.verifier_inventory,
                                          args.verifier_runtime_cache, args.published_revision, args.published_subset)
        return
    inventory = verifier_runtime.load_inventory(args.verifier_inventory) if args.verifier_inventory else None
    runtime_specs, runtime_plans, runtime_labels = {}, {}, {}
    historical_reproduction = {}
    global DOWNLOAD_CACHE
    DOWNLOAD_CACHE = args.download_cache
    source = args.source.resolve()
    files = [source] if source.is_file() else sorted(source.glob("*.parquet"))
    if len(files) != 1 or files[0].suffix != ".parquet":
        raise ValueError(f"expected the one pinned TMAX Parquet file in {source}")
    source = files[0]
    args.output.mkdir(parents=True, exist_ok=True)
    target = args.output / "tasks.parquet"
    if target.exists():
        raise FileExistsError(target)

    # Pass 1: classify every task and form the image groups.
    tasks, groups, payloads = {}, defaultdict(list), {}
    reader = pq.ParquetFile(source)
    for batch in reader.iter_batches(batch_size=64):
        for row in batch.to_pylist():
            if row["path"] in tasks:
                raise ValueError(f"duplicate task ID: {row['path']}")
            content = read_task(row["task_binary"])
            info = classify(content)
            if row["path"] in unshared:
                info["findings"].append("listed-unshared")
            payloads[row["path"]] = payload_hash(content) if content else row["path"]
            tasks[row["path"]] = {key: info.get(key) for key in ("family", "findings", "group", "prefix", "rust")}
            tasks[row["path"]]["download_bytes"] = sum(map(len, info.get("downloads", {}).values()))
            if not info["findings"]:
                apt, pip = prefix_packages(info["prefix"])
                tasks[row["path"]].update(apt=apt, pip=pip, instruction=content["instruction.md"][0].decode(
                    errors="replace") if "instruction.md" in content else "")
    if len(tasks) != TASKS:
        raise ValueError(f"expected {TASKS} tasks in the pinned source, found {len(tasks)}")
    if set(unshared) - set(tasks):
        raise ValueError("unshared names tasks absent from the source")

    # Superset images: the union of packages of the family's tasks without version pins.
    supersets, exact_reasons = {}, Counter()
    for family in ("legacy", "v2"):
        members = [info for info in tasks.values() if info["family"] == family and not info["findings"]]
        unpinned = [info for info in members if all(value == name for name, value in info["pip"].items())]
        for info in unpinned:
            if info["apt"] & APT_CONFLICTS:
                info["exact"] = "apt-package-conflicts-with-superset"
        unpinned = [info for info in unpinned if not info.get("exact")]
        apt = set().union(*(info["apt"] for info in unpinned))
        pip = set().union(*(info["pip"] for info in unpinned))
        supersets[family] = superset_prefix(family, apt, pip)
        if any(info["rust"] for info in unpinned):
            supersets[family] += RUSTUP_IMAGE_INSTALL
        for info in members:
            if info not in unpinned and info["rust"]:
                info["findings"].append("rustup-without-superset")
            elif info not in unpinned and not info.get("exact"):
                info["exact"] = "pinned-pip-version"
            elif mentioned := install_mentions(info["instruction"], (apt - info["apt"]) | (pip - set(info["pip"]))):
                # Mostly "you may install X if needed" or installs into a fresh venv. Kept on the
                # superset; a task the extra packages solve fails the no-op stage and is then
                # listed in unshared with its measured reason.
                info["mentioned"] = mentioned
    for task, info in tasks.items():
        if info["findings"]:
            continue
        if info.get("exact"):
            exact_reasons[info["exact"]] += 1
        else:
            info["superset"] = True
            info["prefix"] = supersets[info["family"]]
        info["group"] = hashlib.sha256((info["family"] + "\0" + info["prefix"]).encode()).hexdigest()[:12]
        groups[info["group"]].append(task)
    shared = {task: group for group, members in groups.items() if len(members) > 1 for task in members}

    trigger = runtime_setup_trigger()
    apply = args.runtime_setup == "force" or (args.runtime_setup == "auto" and trigger)

    # Pass 2: write the tasks.
    before = set(payloads.values())
    if_applied = {payloads[task] for task in tasks if task not in shared} | set(shared.values())
    after = set()
    compatible, node, wrapper, repaired, git_history = {}, {}, {}, {}, {}
    temporary = target.with_suffix(".parquet.tmp")
    with pq.ParquetWriter(temporary, reader.schema_arrow, compression="zstd") as writer:
        for batch in reader.iter_batches(batch_size=64):
            table = batch.to_pydict()
            for i, task in enumerate(table["path"]):
                content = read_task(table["task_binary"][i])
                if apply and task in shared:
                    content = shared_files(content, {**classify(content), "prefix": tasks[task]["prefix"]})
                elif content and (post := nodesource_compatible(content)):
                    content["environment/post_install.sh"] = (post, content["environment/post_install.sh"][1])
                    node[task] = True
                if content and not args.no_repairs:
                    changes = repairs.apply(content, task, shared=apply and task in shared)
                    if changes['task']:
                        repaired[task] = True
                    if changes['wrapper']:
                        wrapper[task] = True
                    if changes['git_history']:
                        git_history[task] = True
                    if any(changes.values()):
                        table["task_binary"][i] = pack_task(content)
                if content and tasks[task]["family"] and "unknown-dockerfile" not in tasks[task]["findings"]:
                    dockerfile, mode = content["environment/Dockerfile"]
                    content["environment/Dockerfile"] = (build_compatible(dockerfile.decode()).encode(), mode)
                    compatible[task] = tasks[task]["family"]
                    table["task_binary"][i] = pack_task(content)
                if content and not args.no_repairs:
                    repairs.finalize(content, task)
                    table["task_binary"][i] = pack_task(content)
                if content and inventory is not None and task not in archived:
                    planned = verifier_runtime.plan(content, inventory.get(environment_hash(content)),
                                                    with_repairs=not args.no_repairs)
                    runtime_specs[spec_id(planned["spec"])] = planned["spec"]
                    runtime_plans[task] = planned
                    if args.verifier_runtime_cache:
                        catalog = runtime_catalog(args.verifier_runtime_cache, planned["spec"])
                        catalog.update(verifier_runtime.model_assets.catalog(
                            args.verifier_runtime_cache, planned.get('model_assets')))
                        runtime_labels[task] = verifier_runtime.apply(content, planned, catalog)
                        table["task_binary"][i] = pack_task(content)
                if content and not args.no_repairs and not args.verifier_runtime_cache and task not in archived:
                    reproduced = repairs.pack_published(content, task)
                    historical_reproduction[task] = hashlib.sha256(reproduced).hexdigest()
                if content:
                    set_network_policy(content, task)
                    table["task_binary"][i] = pack_task(content)
                after.add(payload_hash(content) if content else task)
            kept = [i for i, task in enumerate(table["path"]) if task not in archived]
            writer.write_table(pa.table({key: [values[i] for i in kept] for key, values in table.items()}, schema=reader.schema_arrow))
    temporary.replace(target)
    if pq.ParquetFile(target).metadata.num_rows != TASKS - len(archived):
        raise ValueError("task count changed")

    changed = shared if apply else {}
    labels = {task: [COMPATIBILITY_LABEL] for task in compatible}
    reasons = {task: [COMPATIBILITY_REASON[family]] for task, family in compatible.items()}
    for task, reason in unshared.items():
        labels.setdefault(task, []).append("compile-during-image-build")
        reasons.setdefault(task, []).append(reason)
    for task in service_setup:
        if task in changed:
            labels.setdefault(task, []).append("service-start-once")
            reasons.setdefault(task, []).append(SERVICE_REASON)
    for task in node:
        labels.setdefault(task, []).append(NODE_LABEL)
        reasons.setdefault(task, []).append(NODE_REASON)
    for task in wrapper:
        labels.setdefault(task, []).append(WRAPPER_LABEL)
        reasons.setdefault(task, []).append(WRAPPER_REASON)
    for task in git_history:
        labels.setdefault(task, []).append(GIT_HISTORY_LABEL)
        reasons.setdefault(task, []).append(GIT_HISTORY_REASON)
    for task, extra in runtime_labels.items():
        labels.setdefault(task, []).extend(extra)
        reasons.setdefault(task, []).append(verifier_runtime.reason(runtime_plans[task]))
    for task in repaired:
        labels.setdefault(task, []).append(REPAIR_LABEL)
        reasons.setdefault(task, []).append(VERIFIER_REPAIRS[task]["reason"] if task in VERIFIER_REPAIRS else TASK_REPAIRS[task][4])
    for task, group in changed.items():
        labels.setdefault(task, []).extend(LABELS)
        if tasks[task].get("superset"):
            labels[task] = [label if label != "shared-image" else SUPERSET_LABEL for label in labels[task]]
            reasons.setdefault(task, []).append(SUPERSET_REASON.format(
                family=tasks[task]["family"], size=len(groups[group])))
        else:
            reasons.setdefault(task, []).append(REASON.format(group=group, size=len(groups[group])))
    for task in tasks:
        if task not in archived:
            labels.setdefault(task, []).append('network-offline-first' if task not in ONLINE_TASKS else 'network-required-online')
            reasons.setdefault(task, []).append(
                'User-requested offline-first execution, including explicit upstream online tasks.'
                if task not in ONLINE_TASKS else ONLINE_TASKS[task])
    write_patch_report(
        source, target, source={"dataset": DATASET, "url": SOURCE_URL, "revision": REVISION},
        dropped=archived, patcher=__file__,
        change_labels={task: value for task, value in labels.items() if task not in archived},
        change_reasons={task: " ".join(parts) for task, parts in reasons.items() if task not in archived})

    manifest_path = args.output / 'tasks.manifest.json'
    patch_manifest = json.loads(manifest_path.read_text())
    patch_manifest['repairs_applied'] = not args.no_repairs
    patch_manifest['network_policy'] = dict(default='offline', online_exceptions=ONLINE_TASKS)
    if historical_reproduction:
        (args.output / 'published-reproduction.json').write_text(json.dumps(
            dict(scope='exact historical archive reproduction before the user-requested network policy',
                 published_revision='578f94175bf89c2bed9f376da8045b3a9beee376',
                 tasks=historical_reproduction, intentional_post_reproduction_change='offline-first network policy'), indent=2) + '\n')
    patch_manifest['patcher_dependencies'] = []
    for name in ('repairs.py', 'instruction_repairs.json', 'archive_layouts.json.gz'):
        data = Path(__file__).with_name(name).read_bytes()
        (args.output / name).write_bytes(data)
        patch_manifest['patcher_dependencies'].append(
            dict(path=name, sha256=hashlib.sha256(data).hexdigest()))
    manifest_path.write_text(json.dumps(patch_manifest, indent=2) + '\n')

    def family_counts(selected):
        return dict(sorted(Counter(tasks[task]["family"] or "unknown" for task in selected).items()))
    regular = [task for task, info in tasks.items() if not info["findings"]]
    shared_groups = {group: members for group, members in groups.items() if len(members) > 1}
    manifest = {
        "dataset": DATASET, "source_revision": REVISION, "repairs_applied": not args.no_repairs,
        "runtime_setup": {
            "mode": args.runtime_setup, "runner_has_setup_trigger": trigger, "applied": apply,
            "note": None if apply else (
                "No task was moved to a shared image: validation.stages.task_setup.detect returns no setup command "
                "for a task with setup_files/setup.sh and no solution/solve.sh, so files moved to "
                "setup_files/ would not be executed. The counts below describe what the shared-image "
                "change would do once the runner provides that trigger.")},
        "counts": {
            "tasks": len(tasks) - len(archived), "archived": len(archived), "changed": len(set(changed) - set(archived)), "build_compatible": len(compatible), "nodesource_purge": len(node), "python3_wrapper_output": len(wrapper), "task_repairs": len(repaired), "git_history_task_input": len(git_history),
            "tasks_by_family": family_counts(tasks),
            "regular": len(regular), "regular_by_family": family_counts(regular),
            "irregular": len(tasks) - len(regular),
            "irregular_by_finding": dict(Counter(
                finding for info in tasks.values() for finding in info["findings"]).most_common()),
            "regular_in_single_task_group": len(regular) - len(shared),
            "shareable": len(shared), "shareable_by_family": family_counts(shared),
            "shared_groups": len(shared_groups),
            "superset_tasks": dict(sorted(Counter(info["family"] for info in tasks.values()
                                                  if info.get("superset")).items())),
            "exact_group_reasons": dict(exact_reasons.most_common()),
            "offline_downloads": {"tasks": sum(1 for info in tasks.values() if info["download_bytes"] and not info["findings"]),
                                  "bytes": sum(info["download_bytes"] for info in tasks.values() if not info["findings"])},
            "rust_from_image": sum(1 for info in tasks.values() if info["rust"] and not info["findings"]),
            "unique_images_before": len(before), "unique_images_after": len(after),
            "unique_images_if_applied": len(if_applied),
        },
        "groups": [{"group": group, "family": tasks[members[0]]["family"], "tasks": len(members),
                    "install_script": tasks[members[0]]["prefix"]}
                   for group, members in sorted(shared_groups.items(), key=lambda item: (-len(item[1]), item[0]))],
        "tasks": {task: {"family": info["family"],
                         "image": ("superset" if info.get("superset") and task in shared else
                                   "shared" if task in shared else "per-task"),
                         **({"exact_reason": info["exact"]} if info.get("exact") else {}),
                         **({"mentioned": info["mentioned"]} if info.get("mentioned") else {}),
                         **({"group": info["group"]} if info["group"] else {}),
                         **({"findings": info["findings"]} if info["findings"] else {})}
                  for task, info in sorted(tasks.items())},
    }
    if inventory is not None:
        (args.output / "verifier-bundle-specs.json").write_text(json.dumps(runtime_specs, indent=1, sort_keys=True) + "\n")
        per_spec = Counter(spec_id(planned["spec"]) for planned in runtime_plans.values())
        manifest["verifier_runtime"] = {
            "applied": bool(args.verifier_runtime_cache), "tasks": len(runtime_plans),
            "runtimes": len(runtime_specs), "tasks_per_runtime": dict(per_spec.most_common()),
            "task_environment": sum(p["task_environment"] for p in runtime_plans.values()),
            "sys_executable": sum(p["executable"] for p in runtime_plans.values()),
            "python3_wrapper": sum(p["wrapper"] for p in runtime_plans.values()),
            "python_versions": dict(Counter(p["spec"]["python"] for p in runtime_plans.values())),
            "exceptions": {task: {"reasons": p["reasons"], "excluded": p["excluded"], "unresolved": p["unresolved"]}
                           for task, p in sorted(runtime_plans.items()) if p["task_environment"]}}
    (args.output / "tmax_patch_manifest.json").write_text(json.dumps(manifest, indent=1) + "\n")
    print(json.dumps({"runtime_setup": manifest["runtime_setup"], "counts": manifest["counts"]}, indent=1))


# Prepare pilot
"""Select a reproducible twenty-task TMAX Harbor registry subset."""

import random


def prepare_pilot_main():
    parser = argparse.ArgumentParser()
    parser.add_argument("registry", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--size", type=int, default=20)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    datasets = json.loads(args.registry.read_text())
    source = next(dataset for dataset in datasets if dataset["name"] == "tmax")
    selected = sorted(random.Random(args.seed).sample(source["tasks"], args.size), key=lambda task: task["name"])
    subset = {**source, "name": "tmax-pilot", "tasks": selected}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps([subset], indent=2) + "\n")
    manifest = {
        "source": "PrimeIntellect-ai/prime-envs registry tmax@2026-07-01",
        "seed": args.seed,
        "task_count": len(selected),
        "tasks": selected,
    }
    args.output.with_name("selection.json").write_text(json.dumps(manifest, indent=2) + "\n")


def prepare_pilot_cli():
    prepare_pilot_main()


def runtime_cli():
    parser = argparse.ArgumentParser(description='Migrate published TMAX tasks to clean verifier runtimes')
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--revision', required=True)
    parser.add_argument('--subset', default='tmax_v1')
    parser.add_argument('--verifier-inventory', type=Path, required=True)
    parser.add_argument('--verifier-runtime-cache', type=Path, required=True)
    args = parser.parse_args()
    verifier_runtime.migrate_published(args.source, args.output, args.verifier_inventory,
                                      args.verifier_runtime_cache, args.revision, args.subset)


if __name__ == "__main__":
    from data.utils.cli import dispatch
    dispatch({'prepare-pilot': prepare_pilot_cli, 'fetch-downloads': fetch_downloads_main,
              'prepare-runtime': runtime_cli})
    main()
