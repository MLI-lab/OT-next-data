"""Adapt 20 resolved SWE-Lego rows to Harbor with Prime's scoring semantics."""

import json
from pathlib import Path
import random
import shlex
import shutil

from huggingface_hub import HfApi, hf_hub_download
import pyarrow.parquet as pq


REPO = "PrimeIntellect/SWE-Lego-Real-Data-Verified"
SHARD = "data/resolved-00000-of-00003.parquet"


def main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    revision = HfApi().dataset_info(REPO).sha
    shard = hf_hub_download(REPO, SHARD, repo_type="dataset", revision=revision,
                            cache_dir=str(output / "cache"))
    rows = pq.read_table(shard).to_pylist()
    order = list(range(len(rows)))
    random.Random(args.seed).shuffle(order)
    selected, used_repos = [], set()
    for index in order:
        row = rows[index]
        if row["repo"] in used_repos or not (row["patch"] and row["test_cmd"] and row["image_name"]):
            continue
        selected.append((index, row))
        used_repos.add(row["repo"])
        if len(selected) == 20:
            break
    if len(selected) != 20:
        raise ValueError("Unable to select 20 distinct repositories")
    records = []
    for index, row in selected:
        name = row["instance_id"].replace("/", "_")
        target = output / "input/tasks" / name
        for folder in ("environment", "tests", "solution"):
            (target / folder).mkdir(parents=True, exist_ok=True)
        (target / "instruction.md").write_text(row["problem_statement"].rstrip() + "\n")
        (target / "task.toml").write_text(
            'version = "1.0"\n'
            '[agent]\ntimeout_sec = 1200\n'
            '[verifier]\ntimeout_sec = 1800\n'
            '[environment]\ncpus = 4\nmemory_mb = 4096\nallow_internet = true\n'
            '[metadata]\nsource = "PrimeIntellect/SWE-Lego-Real-Data-Verified"\n'
        )
        (target / "environment/Dockerfile").write_text(
            f'FROM {row["image_name"]}\nWORKDIR /testbed\n'
        )
        (target / "tests/test.patch").write_text(row.get("test_patch") or "")
        (target / "tests/required.json").write_text(json.dumps(
            list(row.get("FAIL_TO_PASS") or []) + list(row.get("PASS_TO_PASS") or [])))
        shutil.copy2(Path(__file__).with_name("swelego_grade.py"), target / "tests/grade.py")
        (target / "tests/eval.sh").write_text(
            '#!/bin/bash\nset -uo pipefail\ncd /testbed\n'
            'set +u\n'
            'if [ -f /opt/miniconda3/bin/activate ]; then source /opt/miniconda3/bin/activate; '
            'elif [ -f /opt/conda/bin/activate ]; then source /opt/conda/bin/activate; fi\n'
            'conda activate testbed 2>/dev/null || true\nset -u\nset +e\n'
            + row["test_cmd"] + '\nstatus=$?\necho "SWELEGO_PYTEST_EXIT=$status"\nexit 0\n'
        )
        (target / "solution/gold.patch").write_text(row["patch"])
        solution = target / "solution/solve.sh"
        solution.write_text('#!/bin/bash\nset -e\ncd /testbed\n'
                            'git apply --whitespace=fix /solution/gold.patch || '
                            'patch --batch --fuzz=5 -p1 -i /solution/gold.patch\n')
        solution.chmod(0o755)
        test = target / "tests/test.sh"
        test.write_text(
            '#!/bin/bash\nmkdir -p /logs/verifier\n'
            'export PATH=/opt/miniconda3/envs/testbed/bin:/opt/miniconda3/bin:'
            '/opt/conda/envs/testbed/bin:/opt/conda/bin:$PATH\n'
            'cd /testbed || exit 1\n'
            f'base={shlex.quote(row["base_commit"])}\n'
            'patch=/tests/test.patch\n'
            'if [ -s "$patch" ]; then\n'
            '  git apply --numstat "$patch" | cut -f3- | while IFS= read -r path; do\n'
            '    path=${path#\\"}; path=${path%\\"}\n'
            '    if git cat-file -e "$base:$path" 2>/dev/null; then\n'
            '      git checkout "$base" -- "$path" 2>/dev/null || true\n'
            '    else rm -f -- "$path"; fi\n'
            '  done\n'
            '  if ! (git apply --whitespace=fix "$patch" || patch --batch --fuzz=5 -p1 -i "$patch"); then\n'
            "    printf '{\"reward\": 0.0}\\n' > /logs/verifier/reward.json\n"
            '    exit 0\n'
            '  fi\n'
            'fi\n'
            'bash /tests/eval.sh | tee /logs/verifier/test-output.txt\n'
            'python /tests/grade.py /logs/verifier/test-output.txt /tests/required.json\n'
        )
        test.chmod(0o755)
        records.append({"task_id": name, "row": index, "repo": row["repo"], "image": row["image_name"]})
    (output / "selection.json").write_text(json.dumps({
        "dataset": REPO, "revision": revision, "source_shard": SHARD,
        "prime_source_commit": "f2e0d70968f9d89421c8035091febee8e9ac3975",
        "verifiers_parser_commit": "185b67da2496cdc724cd7e9686fc4ee965ba83c0",
        "seed": args.seed, "selection": "uniform random order, first row from each of 20 distinct repositories",
        "tasks": records,
    }, indent=2) + "\n")
    print(revision, len(records), output / "input/tasks")


if __name__ == "__main__":
    main()
