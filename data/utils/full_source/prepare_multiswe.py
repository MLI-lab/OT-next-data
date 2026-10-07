"""Adapt a reproducible 20-row Multi-SWE-RL-Verified sample to Harbor."""

import json
from pathlib import Path
import random
import shlex

from huggingface_hub import HfApi, hf_hub_download
import pyarrow.parquet as pq


REPO = "PrimeIntellect/Multi-SWE-RL-Verified"
SHARD = "data/train-00000-of-00003.parquet"
FIELDS = ("fixed_tests", "p2p_tests", "f2p_tests", "s2p_tests", "n2p_tests")


def restore(row):
    for field in FIELDS:
        value = row.get(field)
        if value and isinstance(value, dict) and "name" in value:
            row[field] = {
                name: {"fix": fix, "run": run, "test": test}
                for name, fix, run, test in zip(
                    value["name"], value["fix"], value["run"], value["test"]
                )
            }
    issues = row.get("resolved_issues")
    if issues and isinstance(issues, dict) and "title" in issues:
        row["resolved_issues"] = [
            {"title": title, "body": body, "number": number}
            for title, body, number in zip(issues["title"], issues["body"], issues["number"])
        ]
    return row


def statement(row):
    issues = row.get("resolved_issues") or []
    first = issues[0] if issues else {}
    parts = [row.get("problem_statement") or "", first.get("title") or row.get("title") or "",
             first.get("body") or row.get("body") or "", row.get("hints") or ""]
    return "\n\n".join(p.strip() for p in parts if p and p.strip()) + "\n"


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
                            cache_dir=str(output / "cache/huggingface"))
    rows = pq.read_table(shard).to_pylist()
    order = list(range(len(rows)))
    random.Random(args.seed).shuffle(order)
    selected = []
    used_repos = set()
    for index in order:
        row = rows[index]
        if not row.get("fix_patch") or not row.get("base", {}).get("sha"):
            continue
        if row["repo"] not in used_repos or len(used_repos) == 12:
            selected.append((index, restore(row)))
            used_repos.add(row["repo"])
        if len(selected) == 20:
            break
    if len(selected) != 20:
        raise ValueError("Unable to select 20 Multi-SWE rows")
    source = args.prime_source / "environments/swe/multiswe/multiswe"
    extract = (source / "extract_fix_patch.sh").read_bytes()
    records = []
    for index, row in selected:
        name = row["instance_id"].replace("/", "_")
        workdir = f'/home/{row["repo"]}'
        image = (row.get("docker_image") or f'mswebench/{row["org"]}_m_{row["repo"]}:pr-{row["number"]}').lower()
        target = output / "input/tasks" / name
        for folder in ("environment", "tests", "solution"):
            (target / folder).mkdir(parents=True, exist_ok=True)
        (target / "instruction.md").write_text(statement(row))
        (target / "task.toml").write_text(
            'version = "1.0"\n[agent]\ntimeout_sec = 1200\n'
            '[verifier]\ntimeout_sec = 1800\n'
            '[environment]\ncpus = 4\nmemory_mb = 4096\nallow_internet = true\n'
            '[metadata]\nsource = "PrimeIntellect/Multi-SWE-RL-Verified"\n'
        )
        (target / "environment/Dockerfile").write_text(
            f"FROM {image}\nWORKDIR {workdir}\n"
        )
        (target / "tests/row.json").write_text(json.dumps(row, ensure_ascii=False))
        (target / "tests/extract_fix_patch.sh").write_bytes(extract)
        (target / "tests/grade.py").write_bytes((Path(__file__).parent / "multiswe_grade.py").read_bytes())
        (target / "solution/gold.patch").write_text(row["fix_patch"])
        solve = target / "solution/solve.sh"
        solve.write_text(
            '#!/bin/bash\nset -e\n' + f'cd {shlex.quote(workdir)}\n'
            'git apply --whitespace=fix /solution/gold.patch || '
            'patch --fuzz=5 -p1 -i /solution/gold.patch\n'
        )
        solve.chmod(0o755)
        test = target / "tests/test.sh"
        test.write_text(
            '#!/bin/bash\nset -uo pipefail\nmkdir -p /logs/verifier\n'
            'export PATH=/root/.local/bin:/root/.cargo/bin:/go/bin:/usr/local/go/bin:/usr/local/cargo:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin\n'
            'python3 -m pip install --target /tmp/multiswe-grader --no-cache-dir '
            "'multi-swe-bench @ https://files.pythonhosted.org/packages/48/ad/6b7cda600a50392c790b14ee420b9a3bb318a982a298c05f2d1c066a434f/multi_swe_bench-1.1.2.tar.gz#sha256=44944bc6608d7d9b8d4390f3ce0a3b2c69122ea6be6e35766c6fde2328f50392' "
            '> /logs/verifier/grader_install.log 2>&1 || true\n'
            'export PYTHONPATH=/tmp/multiswe-grader:${PYTHONPATH:-}\n'
            f'cd {shlex.quote(workdir)} || exit 1\n'
            f'bash /tests/extract_fix_patch.sh . {shlex.quote(row["base"]["sha"])} /home/fix.patch || exit 1\n'
            'bash /home/fix-run.sh > /logs/verifier/test_output.txt 2>&1 || true\n'
            'python3 /tests/grade.py /tests/row.json /logs/verifier/test_output.txt /logs/verifier/reward.json\n'
        )
        test.chmod(0o755)
        records.append({"task_id": name, "row": index, "repo": row["repo"], "image": image})
    (output / "selection.json").write_text(json.dumps({
        "dataset": REPO, "revision": revision, "source_shard": SHARD,
        "prime_source_commit": "f2e0d70968f9d89421c8035091febee8e9ac3975",
        "seed": args.seed, "selection": "random order; one row per available repository first, then additional rows to twenty",
        "tasks": records,
    }, indent=2) + "\n")
    print(revision, len(records), output / "input/tasks")


if __name__ == "__main__":
    main()
