"""Pack a pinned Git tree of native Harbor tasks without checking out its files."""

import argparse
import hashlib
import io
import os
from pathlib import Path, PurePosixPath
import subprocess
import tarfile
import time

from huggingface_hub import get_token, hf_hub_url
import requests

import pyarrow as pa
import pyarrow.parquet as pq


SCHEMA = pa.schema([("path", pa.string()), ("task_binary", pa.binary())])


def pack(repo, revision, prefix, output, *, exclude_roots=(), expected_count=None,
         include_root_prefix="", task_id_prefix="", hf_repo=None, lfs_cache=None):
    repo = Path(repo)
    output = Path(output)
    if output.exists():
        raise FileExistsError(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    actual = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"],
                                     text=True).strip()
    if actual != revision:
        raise ValueError(f"pinned commit mismatch: {actual} != {revision}")
    command = ["git", "-C", str(repo), "archive", "--format=tar", revision]
    if prefix != ".":
        command.append(prefix)
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               env={**os.environ, "GIT_LFS_SKIP_SMUDGE": "1"})
    temporary = output.with_suffix(output.suffix + ".tmp")
    count = 0
    current = None
    buffer = None
    writer_tar = None
    has_config = False
    http = requests.Session()
    token = get_token() if hf_repo else None

    def resolve_lfs(path, pointer):
        if not pointer.startswith(b"version https://git-lfs.github.com/spec/v1\n"):
            return pointer
        if not hf_repo:
            raise ValueError(f"LFS pointer needs --hf-repo: {path}")
        fields = dict(line.split(" ", 1) for line in pointer.decode().splitlines()[1:])
        digest = fields["oid"].removeprefix("sha256:")
        size = int(fields["size"])
        cache = Path(lfs_cache) / digest if lfs_cache else None
        if cache and cache.exists():
            data = cache.read_bytes()
        else:
            url = hf_hub_url(hf_repo, path, repo_type="dataset", revision=revision)
            for attempt in range(5):
                try:
                    response = http.get(url, headers={"Authorization": f"Bearer {token}"} if token else {},
                                        timeout=(30, 300))
                    response.raise_for_status()
                    data = response.content
                    break
                except requests.RequestException:
                    if attempt == 4:
                        raise
                    time.sleep(2 ** attempt)
            if cache:
                cache.parent.mkdir(parents=True, exist_ok=True)
                cache.write_bytes(data)
        if len(data) != size or hashlib.sha256(data).hexdigest() != digest:
            raise ValueError(f"LFS digest or size mismatch: {path}")
        return data

    def flush(writer):
        nonlocal count, buffer, writer_tar, has_config
        if current is None:
            return
        writer_tar.close()
        if not has_config:
            raise ValueError(f"task.toml missing: {current}")
        writer.write_table(pa.table({"path": [current],
                                     "task_binary": [buffer.getvalue()]}, schema=SCHEMA))
        count += 1

    try:
        with pq.ParquetWriter(temporary, SCHEMA, compression="zstd") as writer:
            with tarfile.open(fileobj=process.stdout, mode="r|") as source:
                for member in source:
                    if member.isdir():
                        continue
                    if not member.isfile():
                        raise ValueError(f"nonregular Git archive member: {member.name}")
                    parts = PurePosixPath(member.name).parts
                    root = () if prefix == "." else PurePosixPath(prefix).parts
                    if len(parts) <= len(root) + 1:
                        continue  # repository metadata alongside task directories
                    if tuple(parts[:len(root)]) != tuple(root):
                        raise ValueError(f"unexpected task path: {member.name}")
                    source_task_id = parts[len(root)]
                    if source_task_id in exclude_roots or not source_task_id.startswith(include_root_prefix):
                        continue
                    task_id = task_id_prefix + source_task_id
                    relative = "/".join(parts[len(root) + 1:])
                    if task_id != current:
                        flush(writer)
                        current = task_id
                        buffer = io.BytesIO()
                        writer_tar = tarfile.open(fileobj=buffer, mode="w")
                        has_config = False
                    content = resolve_lfs(member.name, source.extractfile(member).read())
                    info = tarfile.TarInfo(relative)
                    info.size = len(content)
                    info.mode = member.mode & 0o777
                    info.mtime = 0
                    writer_tar.addfile(info, io.BytesIO(content))
                    if relative == "task.toml":
                        has_config = True
                flush(writer)
        stderr = process.stderr.read().decode("utf-8", "replace")
        if process.wait() != 0:
            raise RuntimeError(f"git archive failed: {stderr}")
        if count == 0:
            raise ValueError("no Harbor tasks in Git tree")
        if expected_count is not None and count != expected_count:
            raise ValueError(f"expected {expected_count} tasks, packed {count}")
        temporary.replace(output)
    except BaseException:
        process.kill()
        process.wait()
        temporary.unlink(missing_ok=True)
        raise
    return count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repo", type=Path)
    parser.add_argument("revision")
    parser.add_argument("prefix")
    parser.add_argument("output", type=Path)
    parser.add_argument("--exclude-root", action="append", default=[])
    parser.add_argument("--expected-count", type=int)
    parser.add_argument("--include-root-prefix", default="")
    parser.add_argument("--task-id-prefix", default="")
    parser.add_argument("--hf-repo")
    parser.add_argument("--lfs-cache", type=Path)
    args = parser.parse_args()
    count = pack(args.repo, args.revision, args.prefix, args.output,
                 exclude_roots=set(args.exclude_root), expected_count=args.expected_count,
                 include_root_prefix=args.include_root_prefix,
                 task_id_prefix=args.task_id_prefix, hf_repo=args.hf_repo,
                 lfs_cache=args.lfs_cache)
    print(f"packed {count} tasks into {args.output}; excluded roots: {args.exclude_root}")


if __name__ == "__main__":
    main()
