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
X   A listed task whose Dockerfile must run a server under an unprivileged account while the image is
    built is excluded and archived; the build backend has a single user ID.
A   Tasks whose environments differ only in top-level Dockerfile comments, blank lines and trailing
    whitespace receive the Dockerfile of the smallest task ID in the group, so they share one image.
B1  The installation commands that tests/test.sh runs before its checks, and one warm-up of its exact
    `uvx ... pytest` options, are appended to the Dockerfile as a final RUN (no additional images).
B2  A leading block of installation commands moves unchanged from tests/test.sh to tests/setup.sh, the
    preparation script the shared runner executes and times before test.sh.

Instructions change only through R3. task.toml, test_outputs.py, the pytest command, its arguments and the
reward handling are never touched. Heredoc RUN lines are left alone and listed in the manifest, as are
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


def patch_dockerfile(task_id, text):
    """Return (new_text, info). info lists R1 rewrites, R2 rewrites and skipped heredoc RUNs."""
    lines = text.split("\n")
    info = {"R1": [], "R2": 0, "heredoc_run_skipped": 0}
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
        if workdir is None:
            continue
        logical = "\n".join(lines[first:last + 1])
        match = RUN_LINE.match(logical)
        if not match:
            continue
        command = match.group("cmd")
        if command.startswith(("[", "--")):
            continue  # exec form and RUN --flag are excluded
        if "<<" in command:
            info["heredoc_run_skipped"] += 1
            continue
        quoted = shlex.quote(workdir)
        rewritten = f"{match.group('head')}mkdir -p {quoted} && cd {quoted} && {command}"
        lines[first:last + 1] = rewritten.split("\n")
        # Rewriting keeps the line count, so indices of later instructions stay valid.
        info["R2"] += 1
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
            steps += f'; . "$HOME/.local/bin/env" && {warm[0]}'
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
    plan["layer"] = ("# Verifier preparation from tests/test.sh, installed at build time.\n"
                     f"RUN {steps}\n")
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


# ---- driver ------------------------------------------------------------------------------------

LABELS = (
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
    ("A", "shared-image", "Use the comment-free equivalent Dockerfile of task {A} so identical environments "
                          "share one cached image."),
    ("B1", "verifier-dependencies-in-image", "Install the verifier's test runner and tools at image build time "
                                             "instead of at each verification."),
    ("B2", "verifier-setup-separation", "Move verifier installation commands from tests/test.sh to tests/setup.sh "
                                        "unchanged so setup is timed separately; checks and reward logic are unchanged."),
)
RULES = [rule for rule, _, _ in LABELS]


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
            if task_id in EMPTY_DIRECTORY_COPIES:
                new, changes["R6"] = restore_empty_directory(task_id, new, context)
            elif task_id in UPSTREAM_IMAGE_TASKS:
                new, changes["R7"] = upstream_image_dockerfile(task_id, new, context)
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
            new, staged, reason = stage_tmp_copies(new, directories, other_texts)
            if staged:
                changes["R4"] = staged
            elif reason:
                changes["tmp_copy_unresolved"] = reason
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
            if plan and plan["layer"]:
                if new.rstrip().endswith("\\"):
                    raise ValueError(f"{task_id}: Dockerfile ends inside a continued instruction")
                new = new.rstrip("\n") + "\n\n" + plan["layer"]
                changes["B1"] = plan["kind"]
            members[k] = (info, new.encode("utf-8"))
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
    for key in ("copy_source_missing", "copy_glob_unresolved", "copy_order_unresolved", "env_reference_unresolved", "tmp_copy_unresolved", "heredoc_run_skipped", "unpinned_uvx_requirements", "verifier_packages_in_image",
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


if __name__ == "__main__":
    from data.utils.cli import dispatch
    dispatch({'prepare-pilot': prepare_pilot_cli})
    main()
