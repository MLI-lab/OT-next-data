"""Inspect Harbor task environments for opportunities to share container images.

Count distinct Dockerfile contents, combinations of filenames and contents under
each task's environment/ directory (the build inputs), and FROM sequences.
For example, identical Dockerfiles with different requirements.txt files count
as one Dockerfile but two sets of build inputs.

Use these counts as additional information when an agent evaluates how to
reduce the number of unique container images.
"""

import hashlib
import io
import re
import tarfile
from collections import defaultdict

from validation.data.materialize import parquet_files
from validation.data.selection import discover_tasks


FROM = re.compile(rb"^\s*FROM\s+([^\r\n#]+)", re.IGNORECASE | re.MULTILINE)
RUN = re.compile(rb"^\s*RUN\s+", re.IGNORECASE | re.MULTILINE)


def base_sequence(dockerfile):
    result = []
    for line in FROM.findall(dockerfile):
        tokens = line.split()
        if tokens and tokens[0].lower() == b"--platform":
            tokens = tokens[2:]
        elif tokens and tokens[0].lower().startswith(b"--platform="):
            tokens = tokens[1:]
        if tokens:
            result.append(tokens[0].decode("utf-8", "replace"))
    return tuple(result)


def source_tasks(source):
    files = parquet_files(source)
    if files:
        import pyarrow.parquet as pq
        for file in files:
            for batch in pq.ParquetFile(file).iter_batches(batch_size=1):
                for row in batch.to_pylist():
                    with tarfile.open(fileobj=io.BytesIO(row["task_binary"]), mode="r:*") as tar:
                        environment, setup = {}, False
                        for member in tar:
                            if not member.isfile():
                                continue
                            if member.name.startswith("environment/"):
                                environment[member.name.removeprefix("environment/")] = (
                                    tar.extractfile(member).read())
                            elif member.name.startswith("setup_files/"):
                                setup = True
                        yield row["path"], environment, setup
    else:
        for task in discover_tasks(source):
            env = task / "environment"
            environment = {file.relative_to(env).as_posix(): file.read_bytes()
                           for file in env.rglob("*") if file.is_file()} if env.exists() else {}
            setup = any(file.is_file() for file in (task / "setup_files").rglob("*"))
            yield task.name, environment, setup


def audit(source):
    dockerfiles, payloads, bases = set(), set(), set()
    per_base = defaultdict(lambda: {"tasks": 0, "dockerfiles": set(), "payloads": set()})
    missing = []
    with_setup_files = with_environment_setup = with_run = tasks = 0
    for task_id, environment, setup in source_tasks(source):
        tasks += 1
        with_setup_files += setup
        with_environment_setup += "setup.sh" in environment
        dockerfile = environment.get("Dockerfile")
        if dockerfile is None:
            missing.append(task_id)
            continue
        with_run += bool(RUN.search(dockerfile))
        docker_hash = hashlib.sha256(dockerfile).hexdigest()
        h = hashlib.sha256()
        for name, content in sorted(environment.items()):
            h.update(name.encode() + b"\0" + hashlib.sha256(content).digest())
        payload_hash = h.hexdigest()
        base = base_sequence(dockerfile)
        dockerfiles.add(docker_hash)
        payloads.add(payload_hash)
        bases.add(base)
        group = per_base[" | ".join(base) or "<no FROM>"]
        group["tasks"] += 1
        group["dockerfiles"].add(docker_hash)
        group["payloads"].add(payload_hash)
    groups = sorted(per_base.items(), key=lambda item: (-item[1]["tasks"], item[0]))
    return {"tasks": tasks, "unique_dockerfiles": len(dockerfiles),
            "unique_build_payloads": len(payloads), "unique_base_sequences": len(bases),
            "tasks_with_dockerfile_run": with_run,
            "tasks_with_setup_files": with_setup_files,
            "tasks_with_environment_setup_sh": with_environment_setup,
            "tasks_missing_dockerfile": missing,
            "top_bases": [{"base": key, "tasks": item["tasks"],
                           "unique_dockerfiles": len(item["dockerfiles"]),
                           "unique_build_payloads": len(item["payloads"])}
                          for key, item in groups[:30]]}


def main():
    import argparse
    import json
    from pathlib import Path

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path, help='Harbor tasks or a task Parquet')
    args = parser.parse_args()
    source = args.source
    if source.is_dir() and (source / 'tasks.parquet').is_file():
        source = source / 'tasks.parquet'
    print(json.dumps(audit(source), indent=2))


if __name__ == '__main__':
    main()
