"""Repair rules for the pinned full CalibForge Harbor source (revision fb1e75441a94b8bb0ced08acd6b59e711704d70a).

R1  FROM <repo>:<tag>@sha256:<64 hex>  ->  FROM <repo>@sha256:<64 hex>  (Apptainer rejects tag+digest;
    the digest already fixes the image content).
R2  shell-form RUN after a literal absolute WORKDIR  ->  RUN mkdir -p <wd> && cd <wd> && <cmd>
    (Harbor's Apptainer builder never changes directory in %post; Docker semantics are unchanged).
R3  instruction.md fenced block: after a line/segment `cd <abs>`, rewrite `./name` to `<abs>/name`.

Only environment/Dockerfile (R1, R2) and instruction.md (R3) change. Tests, task.toml, oracle and
verifier files are never touched. Heredoc RUN lines are left alone and listed in the manifest.
"""

# Support both direct execution and python -m data.<source>.patch.
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


import argparse
import hashlib
import io
import json
from pathlib import Path
import re
import shlex
import tarfile

import pyarrow as pa
import pyarrow.parquet as pq

REVISION = "fb1e75441a94b8bb0ced08acd6b59e711704d70a"

FROM_LINE = re.compile(r"^(?P<head>\s*FROM\s+(?:--\S+\s+)*)(?P<ref>\S+)(?P<tail>.*)$", re.I)
TAG_AND_DIGEST = re.compile(r"^(?P<repo>[^\s@]+?):(?P<tag>[^:@/\s]+)@(?P<digest>sha256:[0-9a-f]{64})$")
WORKDIR_LINE = re.compile(r"^\s*WORKDIR\s+(?P<path>\S+)\s*$", re.I)
RUN_LINE = re.compile(r"^(?P<head>\s*RUN[ \t]+)(?P<cmd>.*)$", re.I | re.S)
CD_SEGMENT = re.compile(r"^cd\s+(/[^\s'\"$`;&|]*)$")
DOT_SLASH_TOKEN = re.compile(r"(?<![\w./~$-])\./(?=\w)")


def sha256(data):
    return hashlib.sha256(data).hexdigest()


# ---- tar helpers -------------------------------------------------------------------------------

def read_task(binary):
    members = []
    with tarfile.open(fileobj=io.BytesIO(binary), mode="r:*") as archive:
        for info in archive.getmembers():
            members.append((info, archive.extractfile(info).read() if info.isfile() else None))
    return members


def write_task(members):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for info, data in members:
            if data is not None:
                info.size = len(data)
            archive.addfile(info, io.BytesIO(data) if data is not None else None)
    return buffer.getvalue()


# ---- Dockerfile rules (R1, R2) -----------------------------------------------------------------

def logical_instructions(lines):
    """Yield (first_index, last_index) of each instruction, joining backslash continuations."""
    i = 0
    while i < len(lines):
        j = i
        while j + 1 < len(lines) and lines[j].rstrip("\r").rstrip().endswith("\\"):
            j += 1
        yield i, j
        i = j + 1


def patch_dockerfile(task_id, text):
    """Return (new_text, info). info lists R1 rewrites, R2 rewrites and skipped heredoc RUNs."""
    lines = text.split("\n")
    info = {"R1": [], "R2": 0, "heredoc_run_skipped": 0}
    workdir = None
    for first, last in logical_instructions(lines):
        match = FROM_LINE.match(lines[first])
        if match:
            workdir = None  # a new build stage starts without the previous WORKDIR
            ref = match.group("ref")
            if "@" in ref:
                parts = TAG_AND_DIGEST.match(ref)
                if parts:
                    lines[first] = f"{match.group('head')}{parts['repo']}@{parts['digest']}{match.group('tail')}"
                    info["R1"].append(ref)
                elif not re.search(r"@sha256:[0-9a-f]{64}$", ref):
                    raise ValueError(f"{task_id}: FROM digest is not sha256 with 64 hex characters: {ref}")
            continue
        match = WORKDIR_LINE.match(lines[first])
        if match and first == last:
            path = match.group("path")
            workdir = path if path.startswith("/") and path != "/" and not re.search(r"[$\s]", path) else None
            continue
        if workdir is None:
            continue
        logical = "\n".join(lines[first:last + 1])
        match = RUN_LINE.match(logical)
        if not match:
            continue
        command = match.group("cmd")
        if command.startswith(("[", "--")):
            continue  # exec form and RUN --flag are excluded
        if "<<" in command:
            info["heredoc_run_skipped"] += 1
            continue
        quoted = shlex.quote(workdir)
        rewritten = f"{match.group('head')}mkdir -p {quoted} && cd {quoted} && {command}"
        lines[first:last + 1] = rewritten.split("\n")
        # Rewriting keeps the line count, so indices of later instructions stay valid.
        info["R2"] += 1
    return "\n".join(lines), info


# ---- instruction rule (R3) ---------------------------------------------------------------------

def patch_instruction(text):
    """Rewrite ./name after a `cd <abs>` inside one fenced block. Returns (new_text, rewrites)."""
    lines = text.split("\n")
    in_block, cwd, count = False, None, 0
    for index, line in enumerate(lines):
        if line.lstrip().startswith("```"):
            in_block, cwd = not in_block, None
            continue
        if not in_block:
            continue
        pieces = re.split(r"(\s*(?:&&|;)\s*)", line)
        for k in range(0, len(pieces), 2):
            segment = pieces[k].strip()
            cd = CD_SEGMENT.match(segment)
            if cd:
                cwd = cd.group(1).rstrip("/") or "/"
            elif re.match(r"cd(\s|$)", segment):
                cwd = None
            elif cwd and cwd != "/":
                pieces[k], n = DOT_SLASH_TOKEN.subn(cwd + "/", pieces[k])
                count += n
        lines[index] = "".join(pieces)
    return "\n".join(lines), count


# ---- driver ------------------------------------------------------------------------------------

def patch_task(task_id, binary):
    members = read_task(binary)
    changes = {}
    for k, (info, data) in enumerate(members):
        if info.name == "environment/Dockerfile":
            text = data.decode("utf-8")
            new, details = patch_dockerfile(task_id, text)
            if details["heredoc_run_skipped"]:
                changes["heredoc_run_skipped"] = details["heredoc_run_skipped"]
            if new != text:
                members[k] = (info, new.encode("utf-8"))
                if details["R1"]:
                    changes["R1"] = details["R1"]
                if details["R2"]:
                    changes["R2"] = details["R2"]
        elif info.name == "instruction.md":
            text = data.decode("utf-8")
            new, count = patch_instruction(text)
            if new != text:
                members[k] = (info, new.encode("utf-8"))
                changes["R3"] = count
    modified = {"R1", "R2", "R3"} & changes.keys()
    return (write_task(members) if modified else binary), changes, bool(modified)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    source = args.source.resolve()
    files = [source] if source.is_file() else sorted(source.glob("*.parquet"))
    if not files or any(f.suffix != ".parquet" for f in files):
        raise ValueError(f"no pinned CalibForge Parquet files in {source}")
    args.output.mkdir(parents=True, exist_ok=True)
    manifest = {"source_revision": REVISION, "tasks": [],
                "counts": {"R1": 0, "R2": 0, "R3": 0, "changed": 0, "total": 0}}
    for path in files:
        target = args.output / path.name
        temporary = target.with_suffix(".parquet.tmp")
        reader = pq.ParquetFile(path)
        with pq.ParquetWriter(temporary, reader.schema_arrow, compression="zstd") as writer:
            for batch in reader.iter_batches(batch_size=64):
                table = batch.to_pydict()
                for i, task_id in enumerate(table["path"]):
                    binary, changes, modified = patch_task(task_id, table["task_binary"][i])
                    manifest["counts"]["total"] += 1
                    if changes:
                        manifest["tasks"].append({"task": task_id, "changes": changes})
                    if modified:
                        table["task_binary"][i] = binary
                        manifest["counts"]["changed"] += 1
                        for rule in ("R1", "R2", "R3"):
                            manifest["counts"][rule] += rule in changes
                writer.write_table(pa.table(table, schema=reader.schema_arrow))
        temporary.replace(target)
        from data.utils.patch_reporting import write_patch_report
        write_patch_report(path, target,
            source={"dataset": "AweAI-Team/CalibForge", "url": "https://huggingface.co/datasets/AweAI-Team/CalibForge", "revision": REVISION},
            dropped={}, patcher=__file__,
            file_labels={"environment/": "apptainer-build-compatibility", "instruction.md": "explicit-command-paths"},
            change_reasons={item["task"]: " ".join(message for rule, message in (
                ("R1", "Remove redundant image tags while preserving pinned digests for Apptainer."),
                ("R2", "Preserve Docker WORKDIR semantics during Apptainer image builds."),
                ("R3", "Make command paths explicit after directory changes.")) if rule in item["changes"])
                for item in manifest["tasks"]})
    (args.output / "calibforge_repair_manifest.json").write_text(json.dumps(manifest, indent=1) + "\n")


# Prepare pilot
"""Prepare a deterministic ten-task CalibForge sample for the shared validation pipeline."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import random
import shutil

prepare_pilot_REVISION = 'fb1e75441a94b8bb0ced08acd6b59e711704d70a'
SOURCE = 'AweAI-Team/CalibForge'
CATEGORIES = ['software-engineering', 'system-administration', 'scientific-computing',
              'security', 'data-science', 'file-operations', 'debugging',
              'data-processing', 'mathematics', 'data-querying']


def prepare_pilot_main():
    ap = argparse.ArgumentParser(description='Prepare a deterministic ten-task CalibForge sample for the shared validation pipeline.')
    ap.add_argument('--base', type=Path, required=True)
    ap.add_argument('--source', type=Path, help='original task tree; defaults to upstream/repo')
    ap.add_argument('--input', type=Path, help='explicit patched ten-task directory for reruns')
    options = ap.parse_args()
    base = options.base.resolve()
    upstream = base / 'upstream'
    source = (options.source or upstream / 'repo').resolve()
    repository = json.loads((upstream / 'repository.json').read_text())
    if repository['sha'] != prepare_pilot_REVISION:
        raise ValueError('Unexpected source revision')
    rows = [json.loads(s) for s in (upstream / 'metadata.jsonl').read_text().splitlines()]
    rng = random.Random(42)
    selected = []
    for i, category in enumerate(CATEGORIES):
        subset = 'multi_solver' if i % 2 else 'contrastive_solver'
        candidates = sorted((r for r in rows if r['category'] == category and r['subset'] == subset),
                            key=lambda r: r['task_id'])
        selected.append(rng.choice(candidates))
    run = base / 'runs' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    run.mkdir(parents=True)
    tasks = run / 'input/tasks'
    tasks.mkdir(parents=True)
    records = []
    for row in selected:
        src = options.input / row['task_id'] if options.input else source / row['task_path']
        dest = tasks / row['task_id']
        shutil.copytree(src, dest)
        hashes = {}
        for file in sorted(dest.rglob('*')):
            if not file.is_file():
                continue
            content = file.read_bytes()
            if content.startswith(b'version https://git-lfs.github.com/spec/v1\n'):
                raise ValueError(f'Unfetched LFS object: {file}')
            hashes[str(file.relative_to(dest))] = hashlib.sha256(content).hexdigest()
        records.append({**row, 'files_sha256': hashes,
                        'has_oracle': (dest / 'solution/solve.sh').is_file()})
    manifest = {'source': SOURCE, 'revision': prepare_pilot_REVISION, 'seed': 42,
                'method': 'one random task per listed CPU-oriented category; alternating subsets; no outcome filtering',
                'categories': CATEGORIES, 'tasks': records, 'source_tree': str(source),
                'patched_input': str(options.input) if options.input else None}
    (run / 'selection.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(f'Prepared tasks: {tasks}')


def prepare_pilot_cli():
    prepare_pilot_main()


if __name__ == "__main__":
    from data.utils.cli import dispatch
    dispatch({'prepare-pilot': prepare_pilot_cli})
    main()
