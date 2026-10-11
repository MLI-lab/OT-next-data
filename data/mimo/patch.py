"""Convert the pinned MiMo-V2.6-RL-oss Code rows to Harbor tasks and archive the Cyber rows.

    python data/mimo/patch.py --source SOURCE_DIR --output NEW_DIR

SOURCE_DIR holds the pinned code.parquet, cyber.parquet and image-mapping.jsonl
and is only read. NEW_DIR receives tasks.parquet (2,698 Code tasks),
tasks.archive.parquet (1,000 Cyber rows with their original payload),
tasks.manifest.json and the patcher snapshot.

Code semantics follow OpenSourceCodeEnvironment (opensource_code.py and
datasets/base.py, MiMo-Agent commit 467f0a19016f0ac4d63b8d17a1f0da9ba07f232c):
capture the base ref before the agent starts, reset every path the test patch
touches to that ref, git apply the test patch, run test_command in cwd, reward 1
if and only if it exits 0, then reset the touched paths again.
"""

# Support both direct execution and python -m data.<source>.patch.
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


import argparse
import hashlib
import io
import json
import re
import shlex
import tarfile

import pyarrow.parquet as pq

from data.utils.full_source.harbor_parquet import pack_task, write_tasks
from data.utils.patch_reporting import write_conversion_report


DATASET = "XiaomiMiMo/MiMo-V2.6-RL-oss"
REVISION = "639865fd3374018d6cb29b9fb82dd531406fcf5f"
MIMO_AGENT_REVISION = "467f0a19016f0ac4d63b8d17a1f0da9ba07f232c"
SUBSETS = {"code": ("code.parquet", 2698), "cyber": ("cyber.parquet", 1000)}
SHA256 = {
    "code.parquet": "e15733cf2451cfbc5492a4120f7f8cfddbad818aa9f0b324c79888dd1fece161",
    "cyber.parquet": "9e6e69034f4f3e6cfb5c901de642dddc7f4bacf35ab886b0e8eb53eb03084ba1",
    "image-mapping.jsonl": "704ad2716d746430a805b9b1ac7c6725aea2117fd8e4823ebe9d339d0313ad58",
}
REQUIRED_KEYS = {
    # _REQUIRED_FIELDS of opensource_code.py plus dataset_type; the rows have exactly these keys.
    "code": {"cwd", "dataset_type", "docker_image", "instance_id", "problem_statement",
             "test_command", "test_patch", "verifier_timeout_sec"},
    "cyber": {"cwd", "dataset_type", "description", "docker_image", "instance_id", "problem_statement"},
}
TASK_ID = re.compile(r"[A-Za-z0-9._-]+")
WORKDIR = re.compile(r"/[A-Za-z0-9._/-]+")

# agent_loop.py default trajectory_timeout at the pinned MiMo-Agent commit.
AGENT_TIMEOUT_SEC = 1800
# Kept outside the repository so the agent's own commits cannot move it.
BASE_REF_FILE = "/var/lib/mimo/base_ref"

CONVERSION_LABEL = "converted-to-harbor"
CONVERSION_REASON = (
    "The Code subset is published as rows of a training Parquet (problem statement, prebuilt "
    "image, hidden test patch and test command) and is graded by the MiMo-Agent environment "
    "OpenSourceCodeEnvironment, not as Harbor tasks. Each row becomes one Harbor task with its "
    "original instance ID. The instruction is the problem statement, unchanged. The environment is "
    "the published image, wrapped only with WORKDIR set to the row's cwd, so the repository and its "
    "dependencies are exactly the upstream ones and there is one image per task. The upstream reward "
    "steps are split between two verifier scripts: tests/setup.sh resets every path the test patch "
    "touches to the base commit and applies the test patch with git apply, and tests/test.sh runs "
    "the published test command in cwd, gives reward 1 if and only if it exits 0, and resets the "
    "touched paths again. A patch that cannot be reset or applied gives reward 0, as upstream. "
    "The verifier timeout is the row's verifier_timeout_sec (1800 s) and the agent timeout is the "
    "upstream default trajectory timeout (1800 s). The source has no resource or network fields, so "
    "4 CPUs, 4096 MiB and internet access are adapter defaults. Upstream records the base commit "
    "before the agent starts. Here an environment healthcheck does that before the agent runs. If "
    "that record is missing, the verifier uses the current HEAD, which differs from upstream only "
    "when an agent commits changes to files the test patch modifies. The rows contain no reference "
    "solution, so the tasks have none."
)

ARCHIVE_CATEGORY = "unsupported-user-isolation"
ARCHIVE_REASON = (
    "Archived for compatibility with the current Helma Apptainer runtime: upstream Cyber grading "
    "requires separate agent, verify and root identities. The tested Helma account configuration "
    "has no subordinate UID/GID mappings, and switching to the required users fails. The current "
    "runtime cannot preserve the upstream grading permissions. This is a runtime compatibility "
    "exclusion, not a task-quality or reward-hacking finding; the original payload is preserved "
    "for future re-enablement. No claim is made that cluster policy prohibits these mappings."
)

# _setup_dataset_specific: safe.directory (result ignored, as upstream), _ensure_work_tree,
# then _capture_base_ref (without overwriting an earlier capture). A base ref file that
# cannot be written is not fatal; the verifier then resolves HEAD itself.
CAPTURE_BASE_REF = (
    "git config --global --add safe.directory {cwd}; cd {cwd} && "
    "{{ git rev-parse --git-dir >/dev/null 2>&1 || "
    "{{ git init -q && git add -A && git commit -q -m baseline --allow-empty; }}; }} && "
    "ref=$(git rev-parse --verify HEAD) && [ ${{#ref}} -eq 40 ] && "
    "{{ [ -s {file} ] || {{ mkdir -p {directory} && printf '%s\\n' \"$ref\" > {file}; }} || true; }}"
)

RESET_SH = r"""#!/bin/bash
# Upstream reset_test_files_to_base: give every path the test patch touches a
# clean starting point at the base commit. Sourced by setup.sh and test.sh.
mimo_base_ref() {
  local ref=""
  if [ -s __BASE_REF_FILE__ ]; then
    ref=$(head -n 1 __BASE_REF_FILE__)
    if [ ${#ref} -eq 40 ] && git cat-file -e "$ref^{commit}" 2>/dev/null; then
      echo "base ref $ref (recorded before the agent started)" >&2
      printf '%s\n' "$ref"
      return 0
    fi
  fi
  # No usable record (the healthcheck did not run): same steps as upstream setup.
  if ! git rev-parse --git-dir >/dev/null 2>&1; then
    git init -q && git add -A && git commit -q -m baseline --allow-empty || return 1
  fi
  ref=$(git rev-parse --verify HEAD) || return 1
  [ ${#ref} -eq 40 ] || return 1
  echo "base ref $ref (current HEAD, no usable record at __BASE_REF_FILE__)" >&2
  printf '%s\n' "$ref"
}

mimo_reset_test_files() {
  local base tf
  cd __CWD__ || return 1
  base=$(mimo_base_ref) || return 1
  while IFS= read -r tf; do
    [ -z "$tf" ] && continue
    if git cat-file -e "$base":"$tf" 2>/dev/null; then
      git checkout "$base" -- "$tf" 2>/dev/null || true
    else
      git rm -f --cached "$tf" >/dev/null 2>&1 || true
      rm -f "$tf"
    fi
  done < /tests/touched_paths.txt
}
"""

SETUP_SH = r"""#!/bin/bash
# Verifier preparation, upstream reward steps 1 and 2: reset the paths the test
# patch touches to the base commit, then apply the test patch.
fail() {
  echo "$1" >&2
  # Upstream scores a testbed that cannot be reset or patched as reward 0.
  mkdir -p /logs/verifier
  rm -f /logs/verifier/reward.json
  echo 0 > /logs/verifier/reward.txt
  exit 1
}
git config --global --add safe.directory __CWD__
. /tests/reset_test_files.sh
mimo_reset_test_files || fail "reset_tests_failed"
cd __CWD__ || fail "reset_tests_failed"
git apply --verbose /tests/test.patch || fail "apply_test_patch_failed"
"""

TEST_SH = r"""#!/bin/bash
# Upstream reward step 3: reward 1 if and only if the published test command exits 0.
mkdir -p /logs/verifier
if cd __CWD__; then
  bash -lc __TEST_COMMAND__
  status=$?
else
  status=1
fi
if [ "$status" -eq 0 ]; then
  printf '{"reward": 1.0}\n' > /logs/verifier/reward.json
else
  printf '{"reward": 0.0}\n' > /logs/verifier/reward.json
fi
# Upstream leaves the test files as they were found.
. /tests/reset_test_files.sh
mimo_reset_test_files
exit 0
"""


def get_patch_touched_files(patch):
    """Upstream DatasetEnvironment.get_patch_touched_files, unchanged."""
    files = []
    for line in patch.split("\n"):
        m = re.match(r"^diff --git a/(.+?) b/(.+)$", line)
        if m:
            for fp in (m.group(1), m.group(2)):
                if fp not in files:
                    files.append(fp)
    return files


def sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def check_source(source):
    """Pinned hashes are constants; the loop's input manifest must agree with them."""
    manifest = source.parent / "input-manifest.json"
    recorded = json.loads(manifest.read_text()) if manifest.is_file() else None
    if recorded is not None:
        if recorded.get("dataset") != DATASET or recorded.get("revision") != REVISION:
            raise ValueError(f"{manifest}: not {DATASET} at {REVISION}")
        if recorded.get("domains") != {name: count for name, (_, count) in SUBSETS.items()}:
            raise ValueError(f"{manifest}: unexpected subset counts {recorded.get('domains')}")
    for name, expected in SHA256.items():
        path = source / name
        if not path.is_file():
            raise FileNotFoundError(f"missing pinned MiMo source file: {path}")
        actual = sha256(path)
        if actual != expected:
            raise ValueError(f"{path}: sha256 {actual} differs from the pinned {expected}")
        if recorded is not None and recorded.get("sha256", {}).get(name) != expected:
            raise ValueError(f"{manifest}: sha256 of {name} differs from the pinned {expected}")


def read_mapping(path):
    mapping = {}
    for line in path.read_text().splitlines():
        entry = json.loads(line)
        if mapping.setdefault(entry["dataset_image"], entry["dockerhub_image"]) != entry["dockerhub_image"]:
            raise ValueError(f"conflicting image mapping: {entry['dataset_image']}")
    return mapping


def read_subset(source, subset, mapping):
    """Yield (row index, source row, decoded instance, published image) for every row."""
    filename, expected = SUBSETS[subset]
    rows = pq.read_table(source / filename).to_pylist()
    if len(rows) != expected:
        raise ValueError(f"{filename}: expected {expected} rows, found {len(rows)}")
    for index, row in enumerate(rows):
        instance = json.loads(row["extra_info"]["instance_json"])
        where = f"{filename} row {index}"
        if set(instance) != REQUIRED_KEYS[subset]:
            raise ValueError(f"{where}: unexpected instance keys {sorted(instance)}")
        empty = [key for key in sorted(instance) if not instance[key] or not isinstance(
            instance[key], (str, int) if key == "verifier_timeout_sec" else str)]
        if empty:
            raise ValueError(f"{where}: empty or mistyped field(s) {empty}")
        task_id = instance["instance_id"]
        if row["extra_info"]["instance_id"] != task_id:
            raise ValueError(f"{where}: instance ID mismatch {row['extra_info']['instance_id']!r} != {task_id!r}")
        if not TASK_ID.fullmatch(task_id):
            raise ValueError(f"{where}: unsafe instance ID {task_id!r}")
        if instance["docker_image"] not in mapping:
            raise ValueError(f"{where}: unmapped image {instance['docker_image']!r}")
        yield index, row, instance, mapping[instance["docker_image"]]


def code_files(row, instance, image):
    task_id, cwd = instance["instance_id"], instance["cwd"]
    if not WORKDIR.fullmatch(cwd):
        raise ValueError(f"{task_id}: unsupported cwd {cwd!r}")
    if [message["content"] for message in row["prompt"]] != [instance["problem_statement"]]:
        raise ValueError(f"{task_id}: prompt differs from problem_statement")
    touched = get_patch_touched_files(instance["test_patch"])
    if not touched or any(not path or path != path.strip() or "\r" in path for path in touched):
        raise ValueError(f"{task_id}: test patch paths cannot be listed one per line: {touched!r}")
    quoted = shlex.quote(cwd)
    health = CAPTURE_BASE_REF.format(cwd=quoted, file=BASE_REF_FILE, directory=str(Path(BASE_REF_FILE).parent))
    fill = lambda text: text.replace("__CWD__", quoted).replace("__BASE_REF_FILE__", BASE_REF_FILE)
    task_toml = (
        'version = "1.0"\n'
        f'[agent]\ntimeout_sec = {AGENT_TIMEOUT_SEC}\n'
        f'[verifier]\ntimeout_sec = {int(instance["verifier_timeout_sec"])}\n'
        '[environment]\ncpus = 4\nmemory_mb = 4096\nallow_internet = true\n'
        '[environment.healthcheck]\n'
        f'command = {json.dumps(health)}\n'
        'timeout_sec = 300\nretries = 1\n'
        '[metadata]\n'
        f'source = {json.dumps(DATASET)}\n'
        'subset = "code"\n'
        f'source_revision = {json.dumps(REVISION)}\n'
        f'dataset_image = {json.dumps(instance["docker_image"])}\n'
        f'dockerhub_image = {json.dumps(image)}\n'
    )
    return {
        "instruction.md": instance["problem_statement"].encode(),
        "environment/Dockerfile": f"FROM {image}\nWORKDIR {cwd}\n",
        "task.toml": task_toml,
        # Bytes as published: CRLF content and Git binary hunks must survive.
        "tests/test.patch": instance["test_patch"].encode(),
        "tests/touched_paths.txt": "".join(path + "\n" for path in touched),
        "tests/reset_test_files.sh": fill(RESET_SH),
        "tests/setup.sh": (fill(SETUP_SH), 0o755),
        "tests/test.sh": (fill(TEST_SH).replace("__TEST_COMMAND__", shlex.quote(instance["test_command"])), 0o755),
    }


def original_payload(filename, index, row, instance, image):
    """The complete source row, sufficient to rebuild it without the upstream Parquet."""
    dump = lambda value: json.dumps(value, sort_keys=True, ensure_ascii=False, indent=1) + "\n"
    return {
        "original/row.json": dump(row),
        "original/instance.json": dump(instance),
        "original/image.json": dump({
            "dataset": DATASET, "revision": REVISION, "source_file": filename, "row_index": index,
            "dataset_image": instance["docker_image"], "dockerhub_image": image}),
    }


def convert(source, output):
    check_source(source)
    mapping = read_mapping(source / "image-mapping.jsonl")
    records, code, archived = {}, {}, []

    known = set()
    for subset in SUBSETS:
        for index, row, instance, image in read_subset(source, subset, mapping):
            task_id = instance["instance_id"]
            if task_id.lower() in known:
                raise ValueError(f"duplicate instance ID: {task_id}")
            known.add(task_id.lower())
            records[task_id] = {"project": subset, "bug_id": task_id,
                                "dataset_image": instance["docker_image"], "dockerhub_image": image}
            if subset == "code":
                code[task_id] = code_files(row, instance, image)
            else:
                archived.append({
                    "path": task_id,
                    "task_binary": pack_task(original_payload(SUBSETS[subset][0], index, row, instance, image)),
                    "archive_category": ARCHIVE_CATEGORY,
                    "archive_reason": f"{ARCHIVE_CATEGORY}: Cyber task {task_id} (published image {image}). "
                                      + ARCHIVE_REASON})
    archived.sort(key=lambda entry: entry["path"])
    output.mkdir(parents=True, exist_ok=True)
    write_tasks(sorted(code.items()), output / "tasks.parquet", expected_count=SUBSETS["code"][1])
    rows = [{"path": task_id, "task_binary": pack_task(files)} for task_id, files in sorted(code.items())]
    write_conversion_report(
        output, rows, archived, patcher=__file__, records=records,
        source={"dataset": DATASET, "url": f"https://huggingface.co/datasets/{DATASET}/tree/{REVISION}",
                "revision": REVISION, "task_count": len(records),
                "subsets": {name: count for name, (_, count) in SUBSETS.items()},
                "mimo_agent_revision": MIMO_AGENT_REVISION},
        # One label covers the whole adapter; the explanation appears once per PR table.
        change_labels={row["path"]: [CONVERSION_LABEL] for row in rows},
        change_reasons={row["path"]: CONVERSION_REASON for row in rows})


def self_check(source, output):
    """Re-read the written files and account for every source row."""
    mapping = read_mapping(source / "image-mapping.jsonl")
    expected = {subset: {instance["instance_id"]: (row, instance)
                         for _, row, instance, _ in read_subset(source, subset, mapping)}
                for subset in SUBSETS}
    kept = pq.read_table(output / "tasks.parquet", columns=["path"]).column("path").to_pylist()
    archive = pq.read_table(output / "tasks.archive.parquet").to_pylist()
    dropped = [row["path"] for row in archive]
    problems = []
    if sorted(kept) != sorted(expected["code"]) or len(set(kept)) != len(kept):
        problems.append("kept tasks differ from the Code source IDs")
    if sorted(dropped) != sorted(expected["cyber"]) or len(set(dropped)) != len(dropped):
        problems.append("archived tasks differ from the Cyber source IDs")
    if set(kept) & set(dropped):
        problems.append("kept and archived IDs overlap")
    for row in archive:
        task_id = row["path"]
        prefix = f"{ARCHIVE_CATEGORY}: Cyber task {task_id} "
        if row["archive_category"] != ARCHIVE_CATEGORY or not row["archive_reason"].startswith(prefix) \
                or not row["archive_reason"].endswith(ARCHIVE_REASON):
            problems.append(f"{task_id}: unexpected archive category or reason")
        with tarfile.open(fileobj=io.BytesIO(row["task_binary"])) as payload:
            original = {name: json.load(payload.extractfile(name)) for name in payload.getnames()}
        if task_id in expected["cyber"] and (
                original.get("original/row.json"), original.get("original/instance.json")) != expected["cyber"][task_id]:
            problems.append(f"{task_id}: archived payload differs from the source row")
    manifest = json.loads((output / "tasks.manifest.json").read_text())
    actions = {}
    for task in manifest["tasks"]:
        actions[task["action"]] = actions.get(task["action"], 0) + 1
    if manifest.get("complete") is not True or actions != {"changed": len(expected["code"]), "dropped": len(expected["cyber"])}:
        problems.append(f"manifest is incomplete or has unexpected actions: {actions}")
    if any(not task.get("reason") for task in manifest["tasks"]):
        problems.append("manifest task without a reason")
    if problems:
        raise SystemExit("MiMo conversion self-check failed:\n" + "\n".join(problems[:20]))
    total = len(expected["code"]) + len(expected["cyber"])
    print(f"source rows read: {total} (code {len(expected['code'])}, cyber {len(expected['cyber'])})")
    print(f"tasks.parquet: {len(kept)} converted Code tasks, label {CONVERSION_LABEL}")
    print(f"tasks.archive.parquet: {len(dropped)} archived Cyber rows, category {ARCHIVE_CATEGORY}, "
          "original payloads equal the source rows")
    print(f"ID accounting: {len(set(kept) | set(dropped))} of {total} source IDs, no overlap")
    print(f"tasks.manifest.json: complete, {actions['changed']} changed, {actions['dropped']} dropped")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--source", type=Path, required=True,
                        help="directory with the pinned code.parquet, cyber.parquet and image-mapping.jsonl")
    parser.add_argument("--output", type=Path, required=True, help="new output directory")
    args = parser.parse_args()
    if not args.source.is_dir():
        raise ValueError(f"missing pinned MiMo source directory: {args.source}")
    convert(args.source, args.output)
    self_check(args.source, args.output)


if __name__ == "__main__":
    main()
