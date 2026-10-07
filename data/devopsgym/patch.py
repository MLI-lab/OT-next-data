"""Repair rules for the pinned convertible DevOps-Gym Harbor tasks."""

# Support both direct execution and python -m data.<source>.patch.
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from data.utils.full_source.copy_source import copy_source


# Build full
"""Adapt the single-container DevOps-Gym categories from a pinned Git tree."""

import argparse
import json
from pathlib import Path, PurePosixPath
import subprocess
import tarfile

import yaml

from data.utils.full_source.harbor_parquet import write_tasks


CATEGORIES = ("build", "issue_resolving", "monitor", "test_generation")


def task_files(category, name, source):
    required = {"Dockerfile", "run-tests.sh", "solution.sh", "task.yaml"}
    if not required <= source.keys():
        if (required - source.keys() == {"Dockerfile"} and
                {"Dockerfile.client", "Dockerfile.server", "docker-compose.yaml"} <= source.keys()):
            return None  # multi-container source; recorded explicitly by main
        raise ValueError(f"missing DevOps files in {category}/{name}: {sorted(required - source.keys())}")
    try:
        metadata = yaml.safe_load(source["task.yaml"][0].decode("utf-8").expandtabs(2))
    except yaml.YAMLError as exc:
        raise ValueError(f"invalid task.yaml in {category}/{name}: {exc}") from exc
    files = {
        "instruction.md": metadata["instruction"].rstrip() + "\n",
        "task.toml": ('version = "1.0"\n'
            f'[agent]\ntimeout_sec = {int(metadata.get("max_agent_timeout_sec") or 1200)}\n'
            f'[verifier]\ntimeout_sec = {int(metadata.get("max_test_timeout_sec") or 1200)}\n'
            '[environment]\ncpus = 2\nmemory_mb = 4096\nallow_internet = true\n'
            f'[metadata]\nsource = "ucsb-mlsec/DevOps-Gym {category}"\n'),
        "environment/Dockerfile": source["Dockerfile"],
        "tests/run-tests.sh": source["run-tests.sh"],
        "solution/solve.sh": (source["solution.sh"][0], 0o755),
        "tests/test.sh": ('#!/bin/bash\n'
            'mkdir -p /logs/verifier\n'
            'bash /tests/run-tests.sh\n'
            'status=$?\n'
            'if [ "$status" -eq 0 ]; then\n'
            "  printf '{\"reward\": 1.0}\\n' > /logs/verifier/reward.json\n"
            'else\n'
            "  printf '{\"reward\": 0.0}\\n' > /logs/verifier/reward.json\n"
            'fi\n'
            'exit 0\n', 0o755),
    }
    for path, value in source.items():
        if path.startswith("tests/"):
            files[path] = value
        elif path not in required and path != "docker-compose.yaml":
            files["environment/" + path] = value
    return files


def records(repo, revision, category, excluded):
    command = ["git", "-C", str(repo), "archive", "--format=tar", revision,
               f"tasks/{category}"]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    current = None
    source = {}
    try:
        with tarfile.open(fileobj=process.stdout, mode="r|") as archive:
            for member in archive:
                if not member.isfile():
                    continue
                parts = PurePosixPath(member.name).parts
                if parts[:2] != ("tasks", category) or len(parts) < 4:
                    raise ValueError(f"unexpected DevOps archive member: {member.name}")
                name = parts[2]
                if name != current:
                    if current is not None:
                        files = task_files(category, current, source)
                        if files is None:
                            excluded.append(f"tasks/{category}/{current}")
                        else:
                            yield f"{category}-{current}", files
                    current, source = name, {}
                relative = "/".join(parts[3:])
                source[relative] = (archive.extractfile(member).read(), member.mode & 0o777)
            if current is not None:
                files = task_files(category, current, source)
                if files is None:
                    excluded.append(f"tasks/{category}/{current}")
                else:
                    yield f"{category}-{current}", files
        error = process.stderr.read().decode("utf-8", "replace")
        if process.wait() != 0:
            raise RuntimeError(error)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()


def build_full_main():
    parser = argparse.ArgumentParser(description='Adapt the single-container DevOps-Gym categories from a pinned Git tree.')
    parser.add_argument("repo", type=Path)
    parser.add_argument("revision")
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    actual = subprocess.check_output(["git", "-C", str(args.repo), "rev-parse", "HEAD"],
                                     text=True).strip()
    if actual != args.revision:
        raise ValueError(f"pinned Git revision differs: {actual}")
    excluded = []
    expected = 66 + 308 + 26 + 308
    count = write_tasks((record for category in CATEGORIES
                         for record in records(args.repo, args.revision, category, excluded)),
                        args.output, expected_count=expected)
    end_to_end = subprocess.check_output(["git", "-C", str(args.repo), "ls-tree", "-d",
        "--name-only", f"{args.revision}:tasks/end_to_end"], text=True).splitlines()
    excluded += [f"tasks/end_to_end/{name}" for name in end_to_end]
    if len(excluded) != 25:
        raise ValueError(f"expected 25 multi-container exclusions, found {len(excluded)}")
    args.output.with_suffix(".excluded.json").write_text(json.dumps({
        "revision": args.revision, "reason": "multi-container Compose environment; no supported single-container Harbor conversion",
        "source_tasks": sorted(excluded)}, indent=2) + "\n")
    print(f"packed {count} single-container DevOps tasks; {len(excluded)} multi-container tasks require review")


def build_full_cli():
    build_full_main()


# Prepare pilot
"""Adapt twenty pinned DevOps-Gym build tasks to Harbor task directories."""

import json
from pathlib import Path
import random
import shutil
import subprocess

import yaml


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


def prepare_pilot_main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("repo", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    revision = git(args.repo, "rev-parse", "HEAD")
    candidates = git(args.repo, "ls-tree", "-d", "--name-only", "HEAD:tasks/build").splitlines()
    selected = sorted(random.Random(args.seed).sample(candidates, 20))
    git(args.repo, "sparse-checkout", "init", "--cone")
    git(args.repo, "sparse-checkout", "set", *(f"tasks/build/{name}" for name in selected))
    git(args.repo, "checkout", "HEAD")
    tasks_dir = args.output / "input/tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)
    records = []
    for name in selected:
        source = args.repo / "tasks/build" / name
        target = tasks_dir / name
        metadata = yaml.safe_load((source / "task.yaml").read_text())
        for folder in ("environment", "tests", "solution"):
            (target / folder).mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / "Dockerfile", target / "environment/Dockerfile")
        shutil.copy2(source / "run-tests.sh", target / "tests/run-tests.sh")
        shutil.copy2(source / "solution.sh", target / "solution/solve.sh")
        (target / "instruction.md").write_text(metadata["instruction"].rstrip() + "\n")
        (target / "task.toml").write_text(
            'version = "1.0"\n'
            f'[agent]\ntimeout_sec = {int(metadata.get("max_agent_timeout_sec") or 1200)}\n'
            f'[verifier]\ntimeout_sec = {int(metadata.get("max_test_timeout_sec") or 1200)}\n'
            '[environment]\ncpus = 2\nmemory_mb = 4096\nallow_internet = true\n'
            '[metadata]\nsource = "ucsb-mlsec/DevOps-Gym build"\n'
        )
        test = target / "tests/test.sh"
        test.write_text(
            '#!/bin/bash\n'
            'mkdir -p /logs/verifier\n'
            'bash /tests/run-tests.sh\n'
            'status=$?\n'
            'if [ "$status" -eq 0 ]; then\n'
            "  printf '{\"reward\": 1.0}\\n' > /logs/verifier/reward.json\n"
            'else\n'
            "  printf '{\"reward\": 0.0}\\n' > /logs/verifier/reward.json\n"
            'fi\n'
            'exit 0\n'
        )
        test.chmod(0o755)
        (target / "solution/solve.sh").chmod(0o755)
        records.append({"task_id": name, "source_path": f"tasks/build/{name}",
                        "source_docker_image": metadata.get("docker_image")})
    (args.output / "selection.json").write_text(json.dumps({
        "dataset": "ucsb-mlsec/DevOps-Gym", "revision": revision,
        "seed": args.seed, "selection": "uniform random from build category; 20 of 66",
        "adaptation": "single-client Compose task to Harbor Dockerfile; run-tests.sh exit code to reward.json",
        "tasks": records,
    }, indent=2) + "\n")
    print(revision, len(records), tasks_dir)


def prepare_pilot_cli():
    prepare_pilot_main()


if __name__ == "__main__":
    from data.utils.cli import dispatch
    dispatch({'build-full': build_full_cli, 'prepare-pilot': prepare_pilot_cli})
    copy_source()
