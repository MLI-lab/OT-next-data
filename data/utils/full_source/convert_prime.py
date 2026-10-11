"""Pack all pinned Prime SWE rows using the frozen 20-task Harbor adapters."""

import argparse
import json
from pathlib import Path
import shlex

import pyarrow.parquet as pq

from data.utils.full_source.harbor_parquet import write_tasks
from data.utils.full_source.prepare_scale import ids
from data.utils.full_source.prepare_multiswe import restore, statement


REPOS = {
    "scaleswe": "PrimeIntellect/Scale-SWE-Verified",
    "multiswe": "PrimeIntellect/Multi-SWE-RL-Verified",
    "swelego": "PrimeIntellect/SWE-Lego-Real-Data-Verified",
}


def support_files(kind, pilot):
    task = sorted((pilot / "input/tasks").iterdir())[0]
    if kind == "scaleswe":
        test = (task / "tests/test.sh").read_text()
        start = test.index("\n", test.index("\nbase=") + 1) + 1
        end = test.index("if [ -s /tests/f2p.patch ]; then", start)
        return {"score": (task / "tests/score.py").read_bytes(),
                "restore": test[start:end]}
    if kind == "multiswe":
        return {"extract": (task / "tests/extract_fix_patch.sh").read_bytes(),
                "grade": (Path(__file__).with_name("multiswe_grade.py")).read_bytes()}
    return {"grade": (Path(__file__).with_name("swelego_grade.py")).read_bytes()}


def scale_files(row, support):
    workdir = row["workdir"]
    base = row["parent_commit"]
    health = "cd " + shlex.quote(workdir) + " && " + row["pre_commands"].strip().removesuffix("\\n")
    files = {
        "instruction.md": row["problem_statement"].rstrip() + "\n",
        "task.toml": ('version = "1.0"\n'
            '[agent]\ntimeout_sec = 1200\n'
            '[verifier]\ntimeout_sec = 1800\n'
            '[environment]\ncpus = 4\nmemory_mb = 4096\nallow_internet = true\n'
            'workdir = "/"\n'
            '[environment.healthcheck]\n'
            f'command = {json.dumps(health)}\n'
            'timeout_sec = 300\nretries = 1\n'
            '[metadata]\nsource = "PrimeIntellect/Scale-SWE-Verified"\n'),
        "environment/Dockerfile": f'FROM {row["image_url"]}\nWORKDIR /\n',
        "environment/setup.sh": '#!/bin/bash\nset -e\n' + row["pre_commands"].strip().removesuffix("\\n") + "\n",
        "tests/score.py": support["score"],
        "tests/test_ids.json": json.dumps(ids(row["FAIL_TO_PASS"]) + ids(row["PASS_TO_PASS"])),
        "tests/f2p.patch": row.get("f2p_patch") or "",
        "tests/f2p_script.py": row.get("f2p_script") or "",
        "solution/gold.patch": row["patch"],
        "solution/solve.sh": ('#!/bin/bash\nset -e\n'
            f'cd {shlex.quote(workdir)}\n'
            'git apply /solution/gold.patch || '
            'git apply --ignore-space-change --ignore-whitespace /solution/gold.patch || '
            'patch --batch --fuzz=5 -p1 -i /solution/gold.patch\n', 0o755),
        "tests/test.sh": ('#!/bin/bash\nmkdir -p /logs/verifier\n'
            'export PATH=/opt/miniconda3/envs/testbed/bin:/opt/miniconda3/bin:'
            '/opt/conda/envs/testbed/bin:/opt/conda/bin:$PATH\n'
            f'cd {shlex.quote(workdir)} || exit 1\n'
            f'base={shlex.quote(base)}\n'
            + support["restore"] +
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
            'fi\n', 0o755),
    }
    return files


def multiswe_files(raw, support):
    row = restore(raw)
    workdir = f'/home/{row["repo"]}'
    image = (row.get("docker_image") or
             f'mswebench/{row["org"]}_m_{row["repo"]}:pr-{row["number"]}').lower()
    return {
        "instruction.md": statement(row),
        "task.toml": ('version = "1.0"\n[agent]\ntimeout_sec = 1200\n'
            '[verifier]\ntimeout_sec = 1800\n'
            '[environment]\ncpus = 4\nmemory_mb = 4096\nallow_internet = true\n'
            '[metadata]\nsource = "PrimeIntellect/Multi-SWE-RL-Verified"\n'),
        "environment/Dockerfile": f"FROM {image}\nWORKDIR {workdir}\n",
        "tests/row.json": json.dumps(row, ensure_ascii=False),
        "tests/extract_fix_patch.sh": support["extract"],
        "tests/grade.py": support["grade"],
        "solution/gold.patch": row["fix_patch"],
        "solution/solve.sh": ('#!/bin/bash\nset -e\n' + f'cd {shlex.quote(workdir)}\n'
            'git apply --whitespace=fix /solution/gold.patch || '
            'patch --fuzz=5 -p1 -i /solution/gold.patch\n', 0o755),
        "tests/test.sh": ('#!/bin/bash\nset -uo pipefail\nmkdir -p /logs/verifier\n'
            'export PATH=/root/.local/bin:/root/.cargo/bin:/go/bin:/usr/local/go/bin:/usr/local/cargo:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin\n'
            'python3 -m pip install --target /tmp/multiswe-grader --no-cache-dir '
            "'multi-swe-bench @ https://files.pythonhosted.org/packages/48/ad/6b7cda600a50392c790b14ee420b9a3bb318a982a298c05f2d1c066a434f/multi_swe_bench-1.1.2.tar.gz#sha256=44944bc6608d7d9b8d4390f3ce0a3b2c69122ea6be6e35766c6fde2328f50392' "
            '> /logs/verifier/grader_install.log 2>&1 || true\n'
            'export PYTHONPATH=/tmp/multiswe-grader:${PYTHONPATH:-}\n'
            f'cd {shlex.quote(workdir)} || exit 1\n'
            f'bash /tests/extract_fix_patch.sh . {shlex.quote(row["base"]["sha"])} /home/fix.patch || exit 1\n'
            'bash /home/fix-run.sh > /logs/verifier/test_output.txt 2>&1 || true\n'
            'python3 /tests/grade.py /tests/row.json /logs/verifier/test_output.txt /logs/verifier/reward.json\n', 0o755),
    }


def swelego_files(row, support):
    test = ('#!/bin/bash\nmkdir -p /logs/verifier\n'
        'export PATH=/opt/miniconda3/envs/testbed/bin:/opt/miniconda3/bin:'
        '/opt/conda/envs/testbed/bin:/opt/conda/bin:$PATH\n'
        'cd /testbed || exit 1\n'
        f'base={shlex.quote(row["base_commit"])}\n'
        'patch=/tests/test.patch\n'
        'if [ -s "$patch" ]; then\n'
        '  git apply --numstat "$patch" | cut -f3- | while IFS= read -r path; do\n'
        '    path=${path#\\\"}; path=${path%\\\"}\n'
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
        'python /tests/grade.py /logs/verifier/test-output.txt /tests/required.json\n')
    return {
        "instruction.md": row["problem_statement"].rstrip() + "\n",
        "task.toml": ('version = "1.0"\n'
            '[agent]\ntimeout_sec = 1200\n'
            '[verifier]\ntimeout_sec = 1800\n'
            '[environment]\ncpus = 4\nmemory_mb = 4096\nallow_internet = true\n'
            '[metadata]\nsource = "PrimeIntellect/SWE-Lego-Real-Data-Verified"\n'),
        "environment/Dockerfile": f'FROM {row["image_name"]}\nWORKDIR /testbed\n',
        "tests/test.patch": row.get("test_patch") or "",
        "tests/required.json": json.dumps(list(row.get("FAIL_TO_PASS") or []) +
                                          list(row.get("PASS_TO_PASS") or [])),
        "tests/grade.py": support["grade"],
        "tests/eval.sh": ('#!/bin/bash\nset -uo pipefail\ncd /testbed\n'
            'set +u\n'
            'if [ -f /opt/miniconda3/bin/activate ]; then source /opt/miniconda3/bin/activate; '
            'elif [ -f /opt/conda/bin/activate ]; then source /opt/conda/bin/activate; fi\n'
            'conda activate testbed 2>/dev/null || true\nset -u\nset +e\n'
            + row["test_cmd"] + '\nstatus=$?\necho "SWELEGO_PYTEST_EXIT=$status"\nexit 0\n'),
        "solution/gold.patch": row["patch"],
        "solution/solve.sh": ('#!/bin/bash\nset -e\ncd /testbed\n'
            'git apply --whitespace=fix /solution/gold.patch || '
            'patch --batch --fuzz=5 -p1 -i /solution/gold.patch\n', 0o755),
        "tests/test.sh": (test, 0o755),
    }


def rows(kind, shard_manifest, support):
    for shard in shard_manifest["shards"]:
        for batch in pq.ParquetFile(shard["path"]).iter_batches(batch_size=32):
            for row in batch.to_pylist():
                if kind == "scaleswe":
                    if not (row.get("patch") and row.get("pre_commands") and row.get("image_url")):
                        raise ValueError(f"unconvertible Scale-SWE row: {row.get('instance_id')}")
                    files = scale_files(row, support)
                elif kind == "multiswe":
                    if not row.get("fix_patch") or not row.get("base", {}).get("sha"):
                        raise ValueError(f"unconvertible Multi-SWE row: {row.get('instance_id')}")
                    files = multiswe_files(row, support)
                else:
                    if not (row.get("patch") and row.get("test_cmd") and row.get("image_name")):
                        raise ValueError(f"unconvertible SWE-Lego row: {row.get('instance_id')}")
                    files = swelego_files(row, support)
                yield row["instance_id"].replace("/", "_"), files


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=sorted(REPOS))
    parser.add_argument("shards", type=Path, help="pinned shards.json from fetch_prime")
    parser.add_argument("pilot", type=Path, help="frozen 20-task pilot with scorer support files")
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    manifest = json.loads(args.shards.read_text())
    if manifest["dataset"] != REPOS[args.kind]:
        raise ValueError("shard manifest does not match converter kind")
    expected = sum(pq.ParquetFile(shard["path"]).metadata.num_rows
                   for shard in manifest["shards"])
    count = write_tasks(rows(args.kind, manifest, support_files(args.kind, args.pilot)),
                        args.output, expected_count=expected)
    print(f"packed {count} {args.kind} tasks into {args.output}")


if __name__ == "__main__":
    main()
