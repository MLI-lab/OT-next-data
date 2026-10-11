"""Give FACET task packages unique Harbor-compatible task names."""

# Support both direct execution and python -m data.<source>.patch.
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


import argparse
import io
import json
from pathlib import Path
import re
import tarfile
import tomllib

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


def patch_binary(blob, task_id):
    source = io.BytesIO(blob)
    members, contents = [], {}
    with tarfile.open(fileobj=source, mode="r:*") as reader:
        for member in reader:
            if not member.isfile():
                raise ValueError(f"unexpected tar member for {task_id}: {member.name}")
            members.append(member)
            contents[member.name] = reader.extractfile(member).read()
    if "task.toml" not in contents:
        raise ValueError(f"task.toml missing: {task_id}")
    contents["task.toml"] = patched_toml(contents["task.toml"], task_id)
    contents = relocate_tmp_staging(contents, task_id)
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as writer:
        for member in members:
            content = contents[member.name]
            member.size = len(content)
            writer.addfile(member, io.BytesIO(content))
    return output.getvalue()


def patch_parquet(source, output):
    source = Path(source)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    target = output / "tasks.parquet"
    if target.exists():
        raise FileExistsError(target)
    temporary = target.with_suffix(".parquet.tmp")
    try:
        with pq.ParquetWriter(temporary, SCHEMA, compression="zstd") as writer:
            for batch in pq.ParquetFile(source).iter_batches(batch_size=16):
                rows = batch.to_pylist()
                writer.write_table(pa.table({
                    "path": [row["path"] for row in rows],
                    "task_binary": [patch_binary(row["task_binary"], row["path"])
                                    for row in rows]}, schema=SCHEMA))
        temporary.replace(target)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("tasks", type=Path, nargs="?")
    parser.add_argument("--source-parquet", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.source_parquet or args.output:
        if args.tasks or not args.source_parquet or not args.output:
            parser.error("--source-parquet and --output are required together")
        patch_parquet(args.source_parquet, args.output)
        return
    if not args.tasks:
        parser.error("tasks directory is required")
    for task in sorted(args.tasks.iterdir()):
        if not task.is_dir():
            continue
        config = task / "task.toml"
        raw = config.read_bytes()
        patched = patched_toml(raw, task.name)
        if patched != raw:
            config.write_bytes(patched)
            print(task.name, "FACET-Terminal", f"facet/{task.name}")
        files = {path.relative_to(task).as_posix(): path.read_bytes()
                 for path in task.rglob("*") if path.is_file()}
        relocated = relocate_tmp_staging(files, task.name)
        for name, content in relocated.items():
            if content != files[name]:
                (task / name).write_bytes(content)
                print(task.name, "relocated /tmp staging in", name)


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
