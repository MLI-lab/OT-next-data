"""Repair Terminal-Lego build inputs and share images.

Repairs absent empty build directories and archived bullseye security sources. Tasks that
only install unpinned apt/pip packages before their task-specific steps share one superset
image per base, ENV/ARG lines and WORKDIR, and replay those steps from setup_files/setup.sh
(see shared_plan); all other tasks share images only when their contexts are identical.
"""
import argparse
import copy
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import shlex
import sys
import tarfile
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import pyarrow as pa
import pyarrow.parquet as pq
from data.utils.task_archive import read_members
from data.utils.patch_reporting import write_patch_report
from harbor_patches.image_context import environment_dir_hash

SOURCE = {
    "dataset": "PrimeIntellect/Terminal-Lego-15k",
    "url": "https://huggingface.co/datasets/PrimeIntellect/Terminal-Lego-15k",
    "revision": "92e6b5f577610cec9b040250a94ce66cfce24839",
}
DOCKERFILE = "environment/Dockerfile"
CANARY = re.compile(rb"\A# Terminal-Bench Canary String, DO NOT REMOVE:\n"
                    rb"# terminal-bench-canary GUID [0-9a-fA-F]+(?:-[0-9a-fA-F]+){4}\n")


def executable_dockerfile(content):
    """Ignore precisely the two known leading comments, never shell text."""
    match = CANARY.match(content)
    return content[match.end():] if match else content


def members(blob):
    # Shared safety validation, plus normalized names and nonregular metadata.
    read_members(blob)
    result, seen = [], set()
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:*") as archive:
        for info in archive:
            name = PurePosixPath(info.name).as_posix()
            if name in seen:
                raise ValueError(f"duplicate normalized archive path: {name}")
            seen.add(name)
            if not (info.isfile() or info.isdir() or info.issym() or info.islnk()):
                raise ValueError(f"unsupported archive member: {name}")
            if info.issym() or info.islnk():
                link = PurePosixPath(info.linkname)
                if link.is_absolute() or ".." in link.parts:
                    raise ValueError(f"unsafe archive link: {name}")
            content = archive.extractfile(info).read() if info.isfile() else None
            result.append((info, content))
    return result


def context_signature(entries, normalize=False):
    result = []
    for info, content in entries:
        name = PurePosixPath(info.name).as_posix()
        if not name.startswith("environment/") and name != "environment":
            continue
        if normalize and name == DOCKERFILE:
            content = executable_dockerfile(content)
        # Include ownership, timestamps and PAX metadata conservatively.
        result.append((name, info.type.hex(), info.mode, info.uid, info.gid,
                       info.uname, info.gname, info.linkname, info.mtime,
                       info.devmajor, info.devminor, tuple(sorted(info.pax_headers.items())),
                       hashlib.sha256(content).hexdigest() if content is not None else None))
    return tuple(sorted(result))


def dockerfile(entries):
    return next(content for info, content in entries
                if PurePosixPath(info.name).as_posix() == DOCKERFILE and info.isfile())


EMPTY_COPY = b"COPY ./task_file /app/task_file"
EMPTY_MKDIR = b"RUN mkdir -p /app/task_file"
BUILD_EVIDENCE = (
    "generation-0000/stage3-10/results/submissions/fdf713c987dc/evidence.tar.gz: "
    "image-build-logs/{039afccbe705,2e4bf59ffccb,c3eea60e1047,f11c40c0430c}.build.log"
)


def repair_empty_copy(entries):
    """Materialize only the missing directory, never invent missing file contents.

    Match the complete standalone instruction, not shell strings or continuations.
    Existing directories, files, and links are always left untouched.
    """
    if any(PurePosixPath(info.name).as_posix() == "environment/task_file"
           or PurePosixPath(info.name).as_posix().startswith("environment/task_file/")
           for info, _ in entries):
        return entries, False
    original = dockerfile(entries)
    lines = original.splitlines(keepends=True)
    matches = [i for i, line in enumerate(lines)
               if line.rstrip(b"\r\n") == EMPTY_COPY
               and (i == 0 or not lines[i - 1].rstrip().endswith(b"\\"))]
    if len(matches) != 1:
        return entries, False
    i = matches[0]
    lines[i] = EMPTY_MKDIR + lines[i][len(EMPTY_COPY):]
    replacement = b"".join(lines)
    repaired = []
    for info, content in entries:
        if PurePosixPath(info.name).as_posix() == DOCKERFILE:
            info = copy.deepcopy(info)
            content = replacement
            info.size = len(content)
            if "size" in info.pax_headers:
                info.pax_headers["size"] = str(info.size)
        repaired.append((info, content))
    return repaired, True


# Debian moved bullseye-security to archive.debian.org: deb.debian.org still serves the
# index but every package file returns 404 (checked 2026-10-18; failed task_09628 and
# task_12604 in the generation-7 pilot). The main and updates suites are unaffected.
BULLSEYE_BASES = {b"mcr.microsoft.com/dotnet/sdk:6.0", b"debian:bullseye-slim",
                  b"debian:11-slim", b"ruby:2.7-slim-bullseye"}
BULLSEYE_SECURITY = (
    b"RUN if grep -qs '^VERSION_CODENAME=bullseye$' /etc/os-release; then "
    b"for f in $(grep -rlE 'https?://(deb|security)\\.debian\\.org/debian-security' "
    b"/etc/apt/sources.list /etc/apt/sources.list.d 2>/dev/null); do "
    b"sed -i -E 's#https?://(deb|security)\\.debian\\.org/debian-security#"
    b"http://archive.debian.org/debian-security#g' \"$f\"; done; fi\n")
BULLSEYE_EVIDENCE = (
    "generation-0007/stage3-200/array/shards/0000/evidence.tar.gz: "
    "image-build-logs/{273bb009cbdb,08665df4e9e4}.build.log"
)


def repair_bullseye_security(entries):
    """Point only the bullseye security source at the Debian archive, right after FROM.

    Applies to the known bullseye bases when the Dockerfile runs apt-get; the
    os-release guard leaves any other distribution unchanged at build time.
    """
    original = dockerfile(entries)
    lines = original.splitlines(keepends=True)
    froms = [i for i, line in enumerate(lines) if line.split()[:1] == [b"FROM"]]
    if (len(froms) != 1 or len(lines[froms[0]].split()) != 2
            or lines[froms[0]].split()[1] not in BULLSEYE_BASES or b"apt-get" not in original):
        return entries, False
    i = froms[0]
    replacement = b"".join(lines[:i + 1] + [BULLSEYE_SECURITY] + lines[i + 1:])
    repaired = []
    for info, content in entries:
        if PurePosixPath(info.name).as_posix() == DOCKERFILE:
            info = copy.deepcopy(info)
            content = replacement
            info.size = len(content)
            if "size" in info.pax_headers:
                info.pax_headers["size"] = str(info.size)
        repaired.append((info, content))
    return repaired, True


def pack(entries):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for info, content in entries:
            archive.addfile(copy.deepcopy(info), io.BytesIO(content) if content is not None else None)
    return buffer.getvalue()


def replace_dockerfile(entries, replacement):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for original, content in entries:
            info = copy.deepcopy(original)
            if PurePosixPath(info.name).as_posix() == DOCKERFILE:
                assert CANARY.match(content) and CANARY.match(replacement)
                assert executable_dockerfile(content) == executable_dockerfile(replacement)
                content = replacement
                info.size = len(content)
                if "size" in info.pax_headers:
                    info.pax_headers["size"] = str(info.size)
            archive.addfile(info, io.BytesIO(content) if content is not None else None)
    return buffer.getvalue()


def assert_preserved(before, after):
    assert len(before) == len(after)
    for (old, old_content), (new, new_content) in zip(before, after):
        old_metadata, new_metadata = old.get_info(), new.get_info()
        # Header checksum is derived from serialized bytes, not environment metadata.
        old_metadata.pop("chksum"); new_metadata.pop("chksum")
        old_pax, new_pax = dict(old.pax_headers), dict(new.pax_headers)
        if PurePosixPath(old.name).as_posix() == DOCKERFILE:
            assert executable_dockerfile(old_content) == executable_dockerfile(new_content)
            old_metadata.pop("size"); new_metadata.pop("size")
            old_pax.pop("size", None); new_pax.pop("size", None)
        else:
            assert old_content == new_content
        assert old_metadata == new_metadata, (old.name, old_metadata, new_metadata)
        assert old_pax == new_pax


def cache_key(entries, scratch):
    """Use the same full-context helper as the configured image prebuilder."""
    with tempfile.TemporaryDirectory(dir=scratch) as temporary:
        root, links = Path(temporary), []
        for info, content in entries:
            path = PurePosixPath(info.name)
            if not path.is_relative_to("environment") or path == PurePosixPath("environment"):
                continue
            target = root / path.relative_to("environment")
            target.parent.mkdir(parents=True, exist_ok=True)
            if info.isfile():
                target.write_bytes(content)
                target.chmod(info.mode)
            elif info.isdir():
                target.mkdir(exist_ok=True)
            else:
                links.append((target, info))
        for target, info in links:
            if info.issym():
                target.symlink_to(info.linkname)
            else:
                link = PurePosixPath(info.linkname)
                if not link.is_relative_to("environment"):
                    raise ValueError("environment hardlink outside build context")
                target.hardlink_to(root / link.relative_to("environment"))
        return environment_dir_hash(root)


# --- Shared superset images -----------------------------------------------------------
# Most Dockerfiles only install packages (apt-get/pip, no pins) and then create the task
# (copy task_file, write files, git init, ...). The installs of all tasks with the same
# base, ENV/ARG lines and final WORKDIR go into one superset image with the union of
# their packages; the task-specific steps run unchanged, in order, from
# setup_files/setup.sh when the container starts. Everything else keeps its own image.

from data.tmax.patch import install_line  # noqa: E402  (canonical apt/pip install lines)

EMPTY_APT_INSTALL = re.compile(r"(?:DEBIAN_FRONTEND=noninteractive\s+)?apt(?:-get)?\s+install(?:\s+-\S+)*\s*(?=&&|;|$)")
SETUP_ROOT = "setup_files/context"
SETUP_SCRIPT = "setup_files/setup.sh"
SUPERSET_HEADER = "# Terminal-Lego shared superset image (data/terminallego/patch.py)\n"
PIP_KEEP_FLAGS = {"--break-system-packages"}
META = {"EXPOSE", "CMD", "ENTRYPOINT", "LABEL", "MAINTAINER", "STOPSIGNAL", "HEALTHCHECK", "VOLUME"}


# Task steps that download, install or build stay in an image: in setup they would repeat
# at every container start, depend on the network and risk the start budget.
_AT = r"(?:^|&&|\|\||;|\||\(|\bthen\s|\bdo\s|\bsudo\s|\bexec\s)\s*(?:[A-Z_]+=\S*\s+)*"
HEAVY_STEP = re.compile(_AT + r"(?:\S*/)?(?:"
    r"apt-get\s|apt\s|apk\s|yum\s|dnf\s|curl\s|wget\s|git\s+clone\b|"
    r"(?:pip3?|python3?\s+-m\s+pip)\s+install\b|uv\s+(?:pip|sync|add|tool)\b|"
    r"(?:npm|pnpm|yarn)\s+(?:install|ci|i|add)\b|npx\s|conda\s+(?:install|create|env|update)\b|mamba\s|"
    r"gem\s+install\b|bundle\s+install\b|composer\s+(?:install|require|update)\b|"
    r"dotnet\s+(?:restore|new|add|tool|build|publish)\b|cargo\s|rustup\s|go\s+(?:mod|get|build|install)\b|"
    r"mvn\s|gradle\s|make\b|cmake\s|docker-php-ext-\S+|pecl\s|cpanm?\s|luarocks\s|swift\s+build\b)")


HEREDOC = re.compile(r"(?<!<)<<(-?)\s*(?:'([^']+)'|\"([^\"]+)\"|\\?([A-Za-z_][A-Za-z0-9_]*))")


def instructions(text):
    """Dockerfile instructions with continuations joined and heredoc bodies attached.

    A RUN heredoc keeps its body and terminator lines, so the command is complete
    shell text (Harbor's own translator reads body lines as instructions).
    """
    joined, pending, raw = [], "", text.splitlines()
    i = 0
    while i < len(raw):
        line = raw[i].strip()
        i += 1
        if not pending and (not line or line.startswith("#")):
            continue
        if pending and line.startswith("#"):
            continue
        line = (pending + " " + line).strip() if pending else line
        if line.endswith("\\"):
            pending = line[:-1].rstrip()
            continue
        pending = ""
        markers = HEREDOC.findall(line) if line.upper().startswith("RUN ") else []
        if markers:
            parts = [line]
            for dash, single, double, bare in markers:
                end = single or double or bare
                while i < len(raw):
                    body = raw[i]
                    i += 1
                    parts.append(body)
                    if (body.lstrip("\t") if dash else body) == end:
                        break
                else:
                    return None
            line = "\n".join(parts)
        joined.append(line)
    if pending:
        joined.append(pending)
    return joined


def install_packages(command):
    """(apt packages, pip packages, pip flags) of an install-only RUN, '' for no-ops, else None."""
    canonical = install_line(command)
    if canonical is None:
        stripped = EMPTY_APT_INSTALL.sub("", command)
        stripped = re.sub(r"^\s*(&&|;)\s*|\s*(&&|;)\s*(?=(&&|;|$))", "", stripped).strip()
        stripped = re.sub(r"(&&|;)\s*(&&|;)", r"\1", stripped).strip()
        if stripped == command.strip():
            return None
        canonical = install_line(stripped) if stripped else ""
        if canonical is None:
            return None
    apt, pip, flags = set(), set(), set()
    for part in re.split(r"\s*(?:&&|;)\s*", canonical):
        words = part.split()
        if "install" not in words:
            continue
        tool = words[0] if words[0] != "DEBIAN_FRONTEND=noninteractive" else words[1]
        values = [w for w in words[words.index("install") + 1:]]
        if tool in ("apt-get", "apt"):
            apt |= {w for w in values if not w.startswith("-")}
        else:
            if any(w.startswith(("--index-url", "-i", "--extra-index-url")) for w in values):
                return None
            packages = {w for w in values if not w.startswith("-")}
            if any(re.search(r"[=<>~!]", w) for w in packages):
                return None
            pip |= packages
            flags |= {w for w in values if w in PIP_KEEP_FLAGS}
    return apt, pip, flags


def copy_step(rest, context):
    """Setup shell for a plain COPY with Docker semantics, or None if unsupported."""
    if rest.startswith(("[", "--")):
        return None
    words = shlex.split(rest)
    if len(words) < 2:
        return None
    *sources, dest = words
    commands = []
    for source in sources:
        name = PurePosixPath(source).as_posix().strip("/")
        name = name[2:] if name.startswith("./") else name
        if not name or name == "." or any(c in name for c in "*?[") or ".." in name.split("/"):
            return None
        kind = context.get(name)
        if kind is None:
            return None
        origin = shlex.quote(f"/{SETUP_ROOT}/{name}")
        target = shlex.quote(dest)
        if kind == "dir":
            commands.append(f"mkdir -p {target} && cp -a {origin}/. {target}/")
        elif dest.endswith("/") or len(sources) > 1:
            commands.append(f"mkdir -p {target} && cp -a {origin} {target}")
        else:
            commands.append(f'mkdir -p "$(dirname {target})" && cp -a {origin} {target}')
    return " && ".join(commands)


def shared_plan(entries):
    """Superset key, package unions and setup script of an eligible task, or None."""
    names = {PurePosixPath(info.name).as_posix(): info for info, _ in entries}
    if any(name.startswith("setup_files/") for name in names) or "environment/.dockerignore" in names:
        return None
    context = {}
    for name, info in names.items():
        if name.startswith("environment/") and name != DOCKERFILE:
            relative = name[len("environment/"):]
            context[relative] = "dir" if info.isdir() else "file"
            parts = relative.split("/")
            for i in range(1, len(parts)):
                context.setdefault("/".join(parts[:i]), "dir")
    lines = instructions(executable_dockerfile(dockerfile(entries)).decode())
    if lines is None:
        return None
    base, before, after, image_steps, steps = None, [], [], [], []
    apt, pip, flags = set(), set(), set()
    workdir, task_started, args = "/", False, []
    for line in lines:
        keyword, _, rest = line.partition(" ")
        keyword, rest = keyword.upper(), rest.strip()
        if keyword == "FROM":
            if base is not None or len(rest.split()) != 1:
                return None
            base = rest
        elif keyword in ("ENV", "ARG"):
            (after if task_started else before).append(line)
            if keyword == "ARG":
                args.append(rest)
        elif keyword == "WORKDIR":
            workdir = rest if rest.startswith("/") else str(PurePosixPath(workdir) / rest)
            if task_started:
                steps.append(f"mkdir -p {shlex.quote(workdir)} && cd {shlex.quote(workdir)}")
        elif keyword in META:
            continue
        elif keyword == "COPY":
            command = copy_step(rest, context)
            if command is None:
                return None
            task_started = True
            steps.append(f"mkdir -p {shlex.quote(workdir)} && cd {shlex.quote(workdir)}")
            steps.append(command)
        elif keyword == "RUN":
            if (line + "\n").encode() == BULLSEYE_SECURITY and not task_started:
                image_steps.append(line)
                continue
            packages = install_packages(rest)
            if packages is None:
                if HEAVY_STEP.search(rest):
                    return None
                task_started = True
                steps.append(f"mkdir -p {shlex.quote(workdir)} && cd {shlex.quote(workdir)}")
                steps.append(f"/bin/sh -c {shlex.quote(rest)}")
            elif task_started:
                return None
            else:
                apt |= packages[0]; pip |= packages[1]; flags |= packages[2]
        else:
            return None
    if base is None:
        return None
    key = (base, tuple(before), tuple(image_steps), tuple(after), workdir)
    return {"key": key, "apt": apt, "pip": pip, "pip_flags": flags, "steps": steps,
            "args": args, "workdir": workdir}


def superset_dockerfile(key, apt, pip, flags):
    base, before, image_steps, after, workdir = key
    lines = [SUPERSET_HEADER, f"FROM {base}\n", *(f"{line}\n" for line in before),
             *(f"{line}\n" for line in image_steps)]
    if apt:
        lines.append("RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y "
                     + " ".join(sorted(apt)) + " && rm -rf /var/lib/apt/lists/*\n")
    if pip:
        # pip cannot uninstall Debian-installed Python packages it would upgrade (seen in TMax).
        extra = {"--ignore-installed"} if any(p.startswith("python3-") for p in apt) else set()
        lines.append("RUN pip install --no-cache-dir " + " ".join(sorted(flags | extra) + sorted(pip)) + "\n")
    lines += [*(f"{line}\n" for line in after), f"WORKDIR {workdir}\n"]
    return "".join(lines).encode()


def setup_script(plan):
    exports = []
    for arg in plan["args"]:
        name, eq, value = arg.partition("=")
        if eq:
            exports.append(f"export {name}={value}")
    body = "\n".join([*exports, *(f"{step}" for step in plan["steps"])])
    return ("#!/bin/bash\n"
            "# Replays the task-specific Dockerfile steps in their original order at container start.\n"
            "marker=/var/lib/terminal-lego-setup.done\n"
            '[ -e "$marker" ] && exit 0\n'
            "set -e\n"
            "export DEBIAN_FRONTEND=noninteractive\n"
            f"{body}\n"
            f"rm -rf /{SETUP_ROOT}\n"
            'mkdir -p /var/lib && touch "$marker"\n').encode()


def apply_shared(entries, superset, plan):
    """Superset Dockerfile, task files under setup_files/context, generated setup.sh."""
    result = []
    for info, content in entries:
        name = PurePosixPath(info.name).as_posix()
        info = copy.deepcopy(info)
        if name == DOCKERFILE:
            content = superset
            info.size = len(content)
            info.pax_headers.pop("size", None)
        elif name.startswith("environment/"):
            info.name = SETUP_ROOT + "/" + name[len("environment/"):]
            info.pax_headers.pop("path", None)
        elif name == "environment":
            continue
        result.append((info, content))
    script = setup_script(plan)
    info = tarfile.TarInfo(SETUP_SCRIPT)
    info.mode, info.size = 0o755, len(script)
    result.append((info, script))
    return result


def assert_moved(before, after):
    """Non-environment members are byte- and metadata-identical; environment files moved intact."""
    old = {PurePosixPath(i.name).as_posix(): (i, c) for i, c in before}
    new = {PurePosixPath(i.name).as_posix(): (i, c) for i, c in after}
    for name, (info, content) in old.items():
        if name in (DOCKERFILE, "environment"):
            continue
        target = SETUP_ROOT + name[len("environment"):] if name.startswith("environment/") else name
        moved_info, moved_content = new[target]
        assert moved_content == content and moved_info.mode == info.mode and moved_info.type == info.type, name
    assert set(new) - {DOCKERFILE, SETUP_SCRIPT} == {
        SETUP_ROOT + n[len("environment"):] if n.startswith("environment/") else n
        for n in old if n not in (DOCKERFILE, "environment")}


def rows(source):
    for batch in pq.ParquetFile(source).iter_batches(batch_size=32):
        yield from batch.to_pylist()


def patch(source, output):
    source, output = Path(source), Path(output)
    if source.is_dir():
        sources = sorted(source.glob("*.parquet"))
        if len(sources) != 1:
            raise ValueError("expected exactly one pinned source Parquet")
        source = sources[0]
    output.mkdir(parents=True, exist_ok=True)
    destination = output / source.name
    if destination.resolve() == source.resolve() or destination.exists():
        raise ValueError("use a new output directory distinct from upstream")
    groups, seen, before_contexts, supersets = {}, set(), set(), {}
    for row in rows(source):
        task_id = row["path"]
        if task_id in seen:
            raise ValueError(f"duplicate task ID: {task_id}")
        seen.add(task_id)
        entries = members(row["task_binary"])
        before_contexts.add(context_signature(entries))
        entries, _ = repair_empty_copy(entries)
        entries, _ = repair_bullseye_security(entries)
        plan = shared_plan(entries)
        if plan is not None:
            superset = supersets.setdefault(plan["key"], {"ids": [], "apt": set(), "pip": set(), "flags": set()})
            superset["ids"].append(task_id)
            superset["apt"] |= plan["apt"]; superset["pip"] |= plan["pip"]; superset["flags"] |= plan["pip_flags"]
        signature = context_signature(entries, normalize=True)
        group = groups.setdefault(signature, {"ids": [], "representative": task_id,
                                              "dockerfile": dockerfile(entries)})
        group["ids"].append(task_id)
        if task_id < group["representative"]:
            group.update(representative=task_id, dockerfile=dockerfile(entries))

    for key, superset in supersets.items():
        superset["dockerfile"] = superset_dockerfile(key, superset["apt"], superset["pip"], superset["flags"])
    labels, reasons, after_contexts = {}, {}, set()
    empty_repairs = bullseye_repairs = shared_tasks = 0
    temporary = destination.with_suffix(".parquet.tmp")
    schema = pq.ParquetFile(source).schema_arrow
    try:
        with pq.ParquetWriter(temporary, schema, compression="zstd") as writer:
            batch = []
            for row in rows(source):
                original_entries = members(row["task_binary"])
                entries, empty_repaired = repair_empty_copy(original_entries)
                entries, bullseye_repaired = repair_bullseye_security(entries)
                if empty_repaired or bullseye_repaired:
                    row["task_binary"] = pack(entries)
                    assert_preserved(entries, members(row["task_binary"]))
                empty_repairs += empty_repaired
                bullseye_repairs += bullseye_repaired
                plan = shared_plan(entries)
                if plan is not None:
                    superset = supersets[plan["key"]]
                    shared = apply_shared(entries, superset["dockerfile"], plan)
                    row["task_binary"] = pack(shared)
                    assert_moved(entries, members(row["task_binary"]))
                    shared_tasks += 1
                    labels.setdefault(row["path"], []).extend(["superset-image", "task-setup-at-runtime"])
                    reasons[row["path"]] = (
                        f"Package installation is replaced by a superset image shared by {len(superset['ids'])} "
                        f"tasks with base {plan['key'][0]}, the same ENV/ARG lines and WORKDIR {plan['workdir']}. "
                        "It installs the union of their apt and pip packages without version pins, so this "
                        "task's image also contains packages it did not ask for. The task-specific Dockerfile "
                        "steps (copying environment files, which move to setup_files/context, and all other "
                        "RUN/WORKDIR steps) run unchanged and in order from setup_files/setup.sh at container "
                        "start. Instructions, tests and solution are unchanged.")
                    entries = members(row["task_binary"])
                signature = context_signature(entries, normalize=True)
                group = groups.get(signature) if plan is None else None
                changed = group is not None and dockerfile(entries) != group["dockerfile"]
                if changed:
                    row["task_binary"] = replace_dockerfile(entries, group["dockerfile"])
                    patched = members(row["task_binary"])
                    assert_preserved(entries, patched)
                    assert context_signature(patched, normalize=True) == signature
                    entries = patched
                if group is not None and len(group["ids"]) > 1:
                    fingerprint = cache_key(entries, output)
                    if "cache_key" in group:
                        assert fingerprint == group["cache_key"]
                    group["cache_key"] = fingerprint
                if changed:
                    labels.setdefault(row["path"], []).append("shared-image")
                    reasons[row["path"]] = (
                        f"Share the unchanged environment of {group['representative']}; only the "
                        "Dockerfile canary header is canonicalized. All initial files, remaining build commands "
                        f"and verification files are preserved. Full context fingerprint: {fingerprint}."
                    )
                if empty_repaired:
                    labels.setdefault(row["path"], []).append("empty-build-directory")
                    explanation = (
                        "The source archive contains no environment/task_file entry. Replace only "
                        "the standalone COPY ./task_file /app/task_file with RUN mkdir -p "
                        "/app/task_file, preserving all later initialization commands and all "
                        "original task, solution and verifier bytes. No input files are invented. "
                        "The same absent-directory root cause failed task_07560, task_10403, "
                        f"task_11182 and task_11486 before setup; evidence: {BUILD_EVIDENCE}."
                    )
                    reasons[row["path"]] = explanation + " " + reasons.get(row["path"], "")
                if bullseye_repaired:
                    labels.setdefault(row["path"], []).append("bullseye-security-archive")
                    reasons[row["path"]] = (
                        "Debian moved bullseye-security to archive.debian.org; deb.debian.org still "
                        "serves its index but every package file returns 404, so apt-get fails during "
                        "the build. Directly after FROM, rewrite only the debian-security source URL to "
                        "http://archive.debian.org/debian-security when /etc/os-release is bullseye; the "
                        "main and updates suites and all task, solution and verifier bytes are unchanged. "
                        f"Same failure in task_09628 and task_12604; evidence: {BULLSEYE_EVIDENCE}. "
                        + reasons.get(row["path"], "")).strip()
                after_contexts.add(context_signature(entries))
                batch.append(row)
                if len(batch) == 32:
                    writer.write_table(pa.Table.from_pylist(batch, schema=schema))
                    batch = []
            if batch:
                writer.write_table(pa.Table.from_pylist(batch, schema=schema))
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    report = write_patch_report(
        source, destination, source=SOURCE, dropped={}, change_labels=labels,
        change_reasons=reasons, patcher=__file__, patches=[{
            "label": "shared-image", "reason": "Canonicalize only leading canary comments "
            "among identical complete environments, retaining all executable inputs.",
        }, {
            "label": "superset-image",
            "reason": "Install the unpinned apt/pip packages of all tasks with the same base, ENV/ARG "
            "lines and WORKDIR in one shared image (union of packages).",
        }, {
            "label": "task-setup-at-runtime",
            "reason": "Run the task-specific Dockerfile steps unchanged and in order from "
            "setup_files/setup.sh at container start; environment files move to setup_files/context.",
        }, {
            "label": "bullseye-security-archive",
            "reason": "Rewrite only the archived bullseye-security apt source to archive.debian.org "
            "directly after FROM, guarded by /etc/os-release; nothing else changes.",
        }, {
            "label": "empty-build-directory",
            "reason": "Create the absent empty task_file directory at the original COPY position; "
            "retain all supplied files and later build instructions.",
        }],
    )
    assert len(report["tasks"]) == len(seen)
    assert sum(t["action"] == "changed" for t in report["tasks"]) == len(labels)
    summary = {"tasks": len(seen), "changed_tasks": len(labels),
               "empty_build_directory_repairs": empty_repairs,
               "bullseye_security_repairs": bullseye_repairs,
               "superset_tasks": shared_tasks, "superset_images": len(supersets),
               "shared_groups": sum(len(g["ids"]) > 1 for g in groups.values()),
               "tasks_in_shared_groups": sum(len(g["ids"]) for g in groups.values() if len(g["ids"]) > 1),
               "unique_contexts_before": len(before_contexts),
               "unique_contexts_after": len(after_contexts), "exclusions": 0}
    print(json.dumps(summary, sort_keys=True))
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    patch(arguments.source, arguments.output)
