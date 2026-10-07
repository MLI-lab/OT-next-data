"""Adapt 20 Scale-SWE-Verified rows to Harbor using Prime's scoring code."""

import ast
import json
from pathlib import Path
import random
import shlex

from huggingface_hub import HfApi, hf_hub_download
import pyarrow.parquet as pq


REPO = "PrimeIntellect/Scale-SWE-Verified"
SHARD = "data/train-00000-of-00002.parquet"


def ids(value):
    if isinstance(value, list):
        return value
    if not value:
        return []
    parsed = json.loads(value)
    return parsed if isinstance(parsed, list) else [parsed]


def prime_restore(source):
    tree = ast.parse(source.read_text())
    return next(ast.literal_eval(node.value) for node in tree.body
                if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "RESTORE"
                                                     for target in node.targets))


def main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--prime-source", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    revision = HfApi().dataset_info(REPO).sha
    shard = hf_hub_download(REPO, SHARD, repo_type="dataset", revision=revision,
                            cache_dir=str(output / "cache"))
    table = pq.read_table(shard)
    rows = table.to_pylist()
    order = list(range(len(rows)))
    random.Random(args.seed).shuffle(order)
    selected = []
    used_repos = set()
    for index in order:
        row = rows[index]
        if row["repo"] in used_repos:
            continue
        if not (row.get("patch") and row.get("pre_commands") and row.get("image_url")):
            continue
        used_repos.add(row["repo"])
        selected.append((index, row))
        if len(selected) == 20:
            break
    if len(selected) != 20:
        raise ValueError("Unable to select 20 distinct repositories")
    source_root = args.prime_source / "environments/swe/scaleswe/scaleswe"
    restore = prime_restore(source_root / "taskset.py")
    score = (source_root / "score.py").read_bytes()
    records = []
    for index, row in selected:
        name = row["instance_id"].replace("/", "_")
        workdir = row["workdir"]
        base = row["parent_commit"]
        target = output / "input/tasks" / name
        for folder in ("environment", "tests", "solution"):
            (target / folder).mkdir(parents=True, exist_ok=True)
        (target / "instruction.md").write_text(row["problem_statement"].rstrip() + "\n")
        (target / "task.toml").write_text(
            'version = "1.0"\n'
            '[agent]\ntimeout_sec = 1200\n'
            '[verifier]\ntimeout_sec = 1800\n'
            '[environment]\ncpus = 4\nmemory_mb = 4096\nallow_internet = true\n'
            'workdir = "/"\n'
            '[environment.healthcheck]\n'
            f'command = {json.dumps("cd " + shlex.quote(workdir) + " && " + row["pre_commands"].strip().removesuffix(chr(92) + "n"))}\n'
            'timeout_sec = 300\nretries = 1\n'
            '[metadata]\nsource = "PrimeIntellect/Scale-SWE-Verified"\n'
        )
        (target / "environment/Dockerfile").write_text(
            f'FROM {row["image_url"]}\nWORKDIR /\n'
        )
        (target / "environment/setup.sh").write_text(
            '#!/bin/bash\nset -e\n' + row["pre_commands"].strip().removesuffix("\\n") + "\n"
        )
        (target / "tests/score.py").write_bytes(score)
        (target / "tests/test_ids.json").write_text(json.dumps(ids(row["FAIL_TO_PASS"]) + ids(row["PASS_TO_PASS"])))
        (target / "tests/f2p.patch").write_text(row.get("f2p_patch") or "")
        (target / "tests/f2p_script.py").write_text(row.get("f2p_script") or "")
        (target / "solution/gold.patch").write_text(row["patch"])
        solution = target / "solution/solve.sh"
        solution.write_text(
            '#!/bin/bash\nset -e\n'
            f'cd {shlex.quote(workdir)}\n'
            'git apply /solution/gold.patch || '
            'git apply --ignore-space-change --ignore-whitespace /solution/gold.patch || '
            'patch --batch --fuzz=5 -p1 -i /solution/gold.patch\n'
        )
        solution.chmod(0o755)
        test = target / "tests/test.sh"
        test.write_text(
            '#!/bin/bash\nmkdir -p /logs/verifier\n'
            'export PATH=/opt/miniconda3/envs/testbed/bin:/opt/miniconda3/bin:'
            '/opt/conda/envs/testbed/bin:/opt/conda/bin:$PATH\n'
            f'cd {shlex.quote(workdir)} || exit 1\n'
            f'base={shlex.quote(base)}\n'
            + restore + "\n"
            'if [ -s /tests/f2p.patch ]; then\n'
            '  git apply /tests/f2p.patch || '
            'git apply --ignore-space-change --ignore-whitespace /tests/f2p.patch || '
            'patch --batch --fuzz=5 -p1 -i /tests/f2p.patch || true\n'
            'fi\n'
            'if [ -s /tests/f2p_script.py ]; then cp /tests/f2p_script.py test_fail_to_pass.py; fi\n'
            'python /tests/score.py /tests/test_ids.json | tee /logs/verifier/score.txt\n'
            'if grep -q "<score>1.0</score>" /logs/verifier/score.txt; then\n'
            "  printf '{\"reward\": 1.0}\\n' > /logs/verifier/reward.json\n"
            'else\n'
            "  printf '{\"reward\": 0.0}\\n' > /logs/verifier/reward.json\n"
            'fi\n'
        )
        test.chmod(0o755)
        records.append({"task_id": name, "row": index, "repo": row["repo"], "image": row["image_url"]})
    (output / "selection.json").write_text(json.dumps({
        "dataset": REPO, "revision": revision, "source_shard": SHARD,
        "prime_source_commit": "f2e0d70968f9d89421c8035091febee8e9ac3975",
        "seed": args.seed, "selection": "uniform random order, first row from each of 20 distinct repositories",
        "tasks": records,
    }, indent=2) + "\n")
    print(revision, len(records), output / "input/tasks")


if __name__ == "__main__":
    main()
