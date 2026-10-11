"""Reproduce the pinned MiMo code subset before general dataset repairs."""

# Support both direct execution and python -m data.<source>.patch.
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


import argparse
from pathlib import Path
import shutil


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.source.is_file() or args.source.suffix != ".parquet":
        raise ValueError(f"missing pinned MiMo Parquet: {args.source}")
    args.output.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(args.source, args.output / "tasks.parquet")


# Build full
"""Pack every pinned MiMo code row with the same Harbor adapter as the pilot."""

import argparse
import json
from pathlib import Path
import shlex

import pyarrow.parquet as pq

from data.utils.full_source.harbor_parquet import write_tasks


def converted(source, mapping):
    for batch in pq.ParquetFile(source).iter_batches(batch_size=32):
        for row in batch.to_pylist():
            instance = json.loads(row["extra_info"]["instance_json"])
            task_id = instance["instance_id"]
            image = mapping[instance["docker_image"]]
            workdir = instance["cwd"]
            test = (
                '#!/bin/bash\n'
                'mkdir -p /logs/verifier\n'
                f'cd {shlex.quote(workdir)} || exit 1\n'
                'if git apply /tests/test.patch; then\n'
                f'  bash -lc {shlex.quote(instance["test_command"])}\n'
                '  status=$?\n'
                'else\n'
                '  status=1\n'
                'fi\n'
                'if [ "$status" -eq 0 ]; then\n'
                "  printf '{\"reward\": 1.0}\\n' > /logs/verifier/reward.json\n"
                'else\n'
                "  printf '{\"reward\": 0.0}\\n' > /logs/verifier/reward.json\n"
                'fi\n'
                'exit 0\n'
            )
            files = {
                "instruction.md": instance["problem_statement"].rstrip() + "\n",
                "environment/Dockerfile": f"FROM {image}\nWORKDIR {workdir}\n",
                "tests/test.patch": instance["test_patch"],
                "task.toml": ('version = "1.0"\n'
                    '[agent]\ntimeout_sec = 1200\n'
                    f'[verifier]\ntimeout_sec = {int(instance["verifier_timeout_sec"])}\n'
                    '[environment]\ncpus = 4\nmemory_mb = 4096\nallow_internet = true\n'
                    '[metadata]\nsource = "XiaomiMiMo/MiMo-V2.6-RL-oss code"\n'),
                "tests/test.sh": (test, 0o755),
            }
            yield task_id, files


def build_full_main():
    parser = argparse.ArgumentParser(description='Pack every pinned MiMo code row with the same Harbor adapter as the pilot.')
    parser.add_argument("source", type=Path)
    parser.add_argument("image_mapping", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    mapping = {entry["dataset_image"]: entry["dockerhub_image"]
               for entry in map(json.loads, args.image_mapping.read_text().splitlines())}
    expected = pq.ParquetFile(args.source).metadata.num_rows
    count = write_tasks(converted(args.source, mapping), args.output, expected_count=expected)
    print(f"packed {count} MiMo code tasks into {args.output}")


def build_full_cli():
    build_full_main()


# Prepare pilot
"""Adapt twenty MiMo code rows with their published images and test commands."""

import json
from pathlib import Path
import random
import shlex

from huggingface_hub import HfApi, hf_hub_download
import pyarrow.parquet as pq


def prepare_pilot_main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    repo = "XiaomiMiMo/MiMo-V2.6-RL-oss"
    revision = HfApi().dataset_info(repo).sha
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    def download(name):
        return hf_hub_download(repo_id=repo, repo_type="dataset", filename=name,
                               revision=revision, cache_dir=str(output / "cache"))
    table = pq.read_table(download("code.parquet"))
    mapping = {entry["dataset_image"]: entry["dockerhub_image"]
               for entry in map(json.loads, Path(download("image-mapping.jsonl")).read_text().splitlines())}
    indices = sorted(random.Random(args.seed).sample(range(table.num_rows), 20))
    records = []
    for index in indices:
        row = table.slice(index, 1).to_pylist()[0]
        instance = json.loads(row["extra_info"]["instance_json"])
        task_id = instance["instance_id"]
        image = mapping[instance["docker_image"]]
        workdir = instance["cwd"]
        target = output / "input/tasks" / task_id
        for folder in ("environment", "tests"):
            (target / folder).mkdir(parents=True, exist_ok=True)
        (target / "instruction.md").write_text(instance["problem_statement"].rstrip() + "\n")
        (target / "environment/Dockerfile").write_text(
            f"FROM {image}\nWORKDIR {workdir}\n"
        )
        (target / "tests/test.patch").write_text(instance["test_patch"])
        (target / "task.toml").write_text(
            'version = "1.0"\n'
            '[agent]\ntimeout_sec = 1200\n'
            f'[verifier]\ntimeout_sec = {int(instance["verifier_timeout_sec"])}\n'
            '[environment]\ncpus = 4\nmemory_mb = 4096\nallow_internet = true\n'
            '[metadata]\nsource = "XiaomiMiMo/MiMo-V2.6-RL-oss code"\n'
        )
        test = target / "tests/test.sh"
        test.write_text(
            '#!/bin/bash\n'
            'mkdir -p /logs/verifier\n'
            f'cd {shlex.quote(workdir)} || exit 1\n'
            'if git apply /tests/test.patch; then\n'
            f'  bash -lc {shlex.quote(instance["test_command"])}\n'
            '  status=$?\n'
            'else\n'
            '  status=1\n'
            'fi\n'
            'if [ "$status" -eq 0 ]; then\n'
            "  printf '{\"reward\": 1.0}\\n' > /logs/verifier/reward.json\n"
            'else\n'
            "  printf '{\"reward\": 0.0}\\n' > /logs/verifier/reward.json\n"
            'fi\n'
            'exit 0\n'
        )
        test.chmod(0o755)
        records.append({"task_id": task_id, "row": index, "dataset_image": instance["docker_image"],
                        "dockerhub_image": image})
    (output / "selection.json").write_text(json.dumps({
        "dataset": repo, "revision": revision, "seed": args.seed,
        "selection": "uniform random over code.parquet rows",
        "adaptation": "apply released test_patch after agent, run released test_command, score exit 0 as reward 1",
        "tasks": records,
    }, indent=2) + "\n")
    print(revision, len(records), output / "input/tasks")


def prepare_pilot_cli():
    prepare_pilot_main()


if __name__ == "__main__":
    from data.utils.cli import dispatch
    dispatch({'build-full': build_full_cli, 'prepare-pilot': prepare_pilot_cli})
    main()
