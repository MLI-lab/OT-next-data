"""Repair rules for the pinned full TermiGen Harbor Parquet (ucsb-mlsec/terminal-bench-env @ 03bddac).

R1  restore COPY sources that upstream .gitignore files dropped from environments_harbor/,
    taking bytes from the pinned termigen_env.zip (never overwriting existing files).
R2  pin flagged pip tokens from a pin in the same Dockerfile, or bare numpy to the base-image version.
R3  pin the remaining flagged pip tokens from data/termigen/pip_lock.json (harvested from builds).

Dockerfile edits keep every other byte. Tests, instruction, oracle and verifier files are never touched.
R5 (single-file COPY replication) is shared runtime code and is not handled here.
"""

# Support both direct execution and python -m data.<source>.patch.
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import tarfile
import zipfile

import pyarrow as pa
import pyarrow.parquet as pq

REVISION = "03bddac74ada2fa344df0e93b4a3ace2034294d1"
UPSTREAM_ZIP_SHA256 = "1d6cfab428b496d489922426f9800fca502200a04a0deff351b4b1e786e185b8"
DEFAULT_UPSTREAM_ZIP = "/hnvme/workspace/y500bb12-optiagent/termigen-20-pilot/upstream/termigen_env.zip"
# pip3 freeze of three images built 2026-10-02 from the TermiGen base image and base pip line
# (shap, yara, perceptual_loss) all show numpy==2.5.3, pulled in by pandas/scipy.
NUMPY_PIN = "2.5.3"
CHECK = Path(__file__).resolve().parents[2] / "validation/upstream/terminal_bench/scripts/checks/check-pip-pinning.sh"
LOCK = Path(__file__).with_name("pip_lock.json")
BARE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
PINNED = re.compile(r"([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[^\]]*\])?==([^\s;,\\&|]+)")
REQUIREMENT = re.compile(r"([A-Za-z0-9][A-Za-z0-9._-]*)(\[[^\]]*\])?(.*)")
PIP_INSTALL = re.compile(r"\b(?:pip3?|uv\s+pip)\s+install\b")


def load_check():
    """Reuse the copied check's own functions so the patch and the check agree."""
    source = CHECK.read_text()
    code = source.split("<<'PYEOF'\n", 1)[1].split("failed = []", 1)[0]
    scope = {}
    exec(compile(code, str(CHECK), "exec"), scope)
    return scope


CHECKER = load_check()


def norm(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def sha256(data):
    return hashlib.sha256(data).hexdigest()


# ---- tar helpers -------------------------------------------------------------------------------

def read_task(binary):
    members = []
    with tarfile.open(fileobj=io.BytesIO(binary)) as archive:
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


def new_member(name, data):
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mode = 0o644
    info.mtime = 0
    return info, data


# ---- R1: restore missing COPY sources ----------------------------------------------------------

def copy_sources(dockerfile):
    """Literal (non-glob, non-URL, non-stage) COPY/ADD source paths relative to the build context."""
    for line in CHECKER["join_continuations"](dockerfile):
        stripped = line.strip()
        if not re.match(r"(?i)(COPY|ADD)\s", stripped) or "<<" in stripped:
            continue
        try:
            parts = shlex.split(stripped)[1:]
        except ValueError:
            continue
        if any(p.startswith("--from") for p in parts if p.startswith("--")):
            continue
        parts = [p for p in parts if not p.startswith("--")]
        for src in parts[:-1]:
            if re.search(r"[*?\[$]", src) or re.match(r"\w+://", src) or src in (".", "./"):
                continue
            yield src.rstrip("/")


def restore_sources(task_id, members, upstream):
    names = {info.name for info, _ in members if info.isfile()}
    dockerfile = next(data for info, data in members if info.name == "environment/Dockerfile").decode()
    added = {}
    for src in copy_sources(dockerfile):
        rel = str(PurePosixPath("environment") / src)
        if rel in names or any(n.startswith(rel + "/") for n in names):
            continue
        # Predicate: literal COPY/ADD source is neither a file nor a directory under environment/.
        key = f"{task_id}/{src}"
        found = [n for n in upstream if n == key or n.startswith(key + "/")]
        if not found:
            raise ValueError(f"{task_id}: COPY source {src} missing and not in the pinned upstream zip")
        for name in found:
            if name.endswith("/"):
                continue
            added[str(PurePosixPath("environment") / name[len(task_id) + 1:])] = upstream[name]
    return added


# ---- R2/R3: pip pinning ------------------------------------------------------------------------

def logical_groups(text):
    """Yield (raw_text, logical_line) per Dockerfile logical line, mirroring join_continuations."""
    raw, groups = text.split("\n"), []
    buf = []
    for line in raw:
        buf.append(line)
        if not line.rstrip().endswith("\\"):
            groups.append(buf)
            buf = []
    if buf:
        groups.append(buf)
    for group in groups:
        joined = "\n".join(group)
        yield joined, CHECKER["join_continuations"](joined)[0] if group else ""


def flagged(text):
    """All tokens flagged by the check, plus any flagged by uvx/uv tool (which these rules never fix)."""
    pip, other = [], []
    for _, logical in logical_groups(text):
        pip.extend(CHECKER["check_pip_install"](logical))
        other.extend(CHECKER["check_uvx_with"](logical))
        other.extend(CHECKER["check_uv_tool_install"](logical))
    return pip, other


def same_dockerfile_pins(text):
    pins = {}
    for _, logical in logical_groups(text):
        m = PIP_INSTALL.search(logical)
        if not m:
            continue
        for tok in CHECKER["truncate_at_separator"](logical[m.end():]).split():
            p = PINNED.fullmatch(tok.rstrip("\\;&|"))
            if p:
                name, version = norm(p.group(1)), p.group(2)
                if pins.setdefault(name, version) != version:
                    raise ValueError(f"two different pins for {name}: {pins[name]} vs {version}")
    return pins


def rewrite(text, resolve):
    """Replace each flagged token with resolve(token); everything else is byte-preserved."""
    out = []
    for raw, logical in logical_groups(text):
        bad = CHECKER["check_pip_install"](logical)
        if bad:
            aligned = re.sub(r"\\[ \t\r]*\n", lambda m: " " * len(m.group(0)), raw)
            m = PIP_INSTALL.search(aligned)
            tail = aligned[m.end():]
            sep = CHECKER["_SEPARATOR_RE"].search(tail)
            end = m.end() + (sep.start() if sep else len(tail))
            edits, pending = [], list(bad)
            for t in re.finditer(r"\S+", aligned[m.end():end]):
                clean = t.group(0).rstrip("\\;&|")
                if pending and clean == pending[0]:
                    pending.pop(0)
                    edits.append((m.end() + t.start(), m.end() + t.start() + len(clean), resolve(clean)))
            if pending:
                raise ValueError(f"could not locate flagged tokens {pending} in {logical!r}")
            for start, stop, new in reversed(edits):
                raw = raw[:start] + new + raw[stop:]
        out.append(raw)
    return "\n".join(out)


def r2_resolver(text):
    """Return resolve(token) for the R2 predicate, or None if any flagged token is not A or B."""
    pip, other = flagged(text)
    if other or not pip:
        return None
    pins = same_dockerfile_pins(text)

    def resolve(token):
        if not BARE_NAME.fullmatch(token):
            return None
        if norm(token) in pins:  # A: pinned elsewhere in the same Dockerfile
            return f"{token}=={pins[norm(token)]}"
        if norm(token) == "numpy":  # B: bare numpy
            return f"numpy=={NUMPY_PIN}"
        return None

    return resolve if all(resolve(t) for t in pip) else None


def r3_resolver(original, lock):
    """Return resolve(token) when the lock has a version for every flagged name, else None."""
    entry = lock.get(sha256(original))
    pip, other = flagged(original.decode())
    if entry is None or other or not pip:
        return None
    versions = {norm(k): v for k, v in entry["versions"].items()}

    def resolve(token):
        m = REQUIREMENT.fullmatch(token)
        if not m or norm(m.group(1)) not in versions:
            return None
        version, extras, spec = versions[norm(m.group(1))], m.group(2) or "", m.group(3)
        if spec:
            from packaging.specifiers import SpecifierSet
            if version not in SpecifierSet(spec):
                raise ValueError(f"locked {m.group(1)}=={version} violates {spec}")
        return f"{m.group(1)}{extras}=={version}"

    return resolve if all(resolve(t) for t in pip) else None


def pin_dockerfile(original, lock):
    """Return (new bytes, rule, replacements) or None when no rule applies."""
    text = original.decode()
    for rule, resolve in (("R2", r2_resolver(text)), ("R3", r3_resolver(original, lock))):
        if resolve is None:
            continue
        pip, _ = flagged(text)
        new = rewrite(text, resolve)
        if flagged(new)[0]:
            raise ValueError(f"{rule} left flagged tokens: {flagged(new)[0]}")
        return new.encode(), rule, {t: resolve(t) for t in pip}
    return None


# ---- driver ------------------------------------------------------------------------------------

def load_upstream(path):
    path = Path(path)
    if not path.is_file() or sha256(path.read_bytes()) != UPSTREAM_ZIP_SHA256:
        raise ValueError(f"upstream zip missing or sha256 != {UPSTREAM_ZIP_SHA256}: {path}")
    with zipfile.ZipFile(path) as archive:
        return {i.filename: archive.read(i) for i in archive.infolist() if not i.is_dir()}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--upstream-zip", type=Path,
                        default=Path(os.environ.get("TERMIGEN_UPSTREAM_ZIP", DEFAULT_UPSTREAM_ZIP)))
    args = parser.parse_args()
    source = args.source.resolve()
    if not source.is_file() or source.suffix != ".parquet":
        raise ValueError(f"missing pinned TermiGen Parquet: {source}")
    upstream = load_upstream(args.upstream_zip)
    lock = json.loads(LOCK.read_text())["entries"]
    args.output.mkdir(parents=True, exist_ok=True)
    target = args.output / "tasks.parquet"
    temporary = target.with_suffix(".parquet.tmp")
    manifest = {"source_revision": REVISION, "upstream_zip_sha256": UPSTREAM_ZIP_SHA256,
                "R1_restored_files": [], "pip_pins": []}
    reader = pq.ParquetFile(source)
    with pq.ParquetWriter(temporary, reader.schema_arrow, compression="zstd") as writer:
        for batch in reader.iter_batches(batch_size=64):
            table = batch.to_pydict()
            for i, task_id in enumerate(table["path"]):
                members = read_task(table["task_binary"][i])
                changed = False
                for rel, data in sorted(restore_sources(task_id, members, upstream).items()):
                    members.append(new_member(rel, data))
                    manifest["R1_restored_files"].append(
                        {"task": task_id, "path": rel, "sha256": sha256(data)})
                    changed = True
                for k, (info, data) in enumerate(members):
                    if info.name == "environment/Dockerfile":
                        result = pin_dockerfile(data, lock)
                        if result:
                            members[k] = (info, result[0])
                            manifest["pip_pins"].append({
                                "task": task_id, "rule": result[1], "replacements": result[2],
                                "dockerfile_sha256_before": sha256(data),
                                "dockerfile_sha256_after": sha256(result[0])})
                            changed = True
                if changed:
                    table["task_binary"][i] = write_task(members)
            writer.write_table(pa.table(table, schema=reader.schema_arrow))
    temporary.replace(target)
    (args.output / "termigen_repair_manifest.json").write_text(json.dumps(manifest, indent=1) + "\n")


# Prepare pilot
"""Select ten medium and ten hard TermiGen Harbor tasks from a pinned clone."""

import json
from pathlib import Path
import random
import shutil
import subprocess


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


def prepare_pilot_main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("repo", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    commit = git(args.repo, "rev-parse", "HEAD")
    names = git(args.repo, "ls-tree", "-d", "--name-only", "HEAD:environments_harbor").splitlines()
    rng = random.Random(args.seed)
    chosen = sorted(rng.sample([name for name in names if name.endswith("_medium")], 10)
                    + rng.sample([name for name in names if name.endswith("_hard")], 10))
    git(args.repo, "sparse-checkout", "init", "--cone")
    git(args.repo, "sparse-checkout", "set", *(f"environments_harbor/{name}" for name in chosen))
    git(args.repo, "checkout", "HEAD")
    tasks = args.output / "input/tasks"
    tasks.mkdir(parents=True, exist_ok=True)
    for name in chosen:
        shutil.copytree(args.repo / "environments_harbor" / name, tasks / name)
    (args.output / "selection.json").write_text(json.dumps({
        "dataset": "ucsb-mlsec/terminal-bench-env",
        "revision": commit,
        "seed": args.seed,
        "selection": "ten uniformly sampled medium and ten uniformly sampled hard tasks",
        "tasks": [{"task_id": name, "source_path": f"environments_harbor/{name}"} for name in chosen],
    }, indent=2) + "\n")
    print(commit, len(chosen), tasks)


def prepare_pilot_cli():
    prepare_pilot_main()



def release_nop_failure(item):
    """Require an executed, passing no-op check with reward zero."""
    if item.get('status') == 'passed' and item.get('rewards') and all(r == 0 for r in item['rewards']):
        return None
    findings = '; '.join(str(f) for f in item.get('findings', []))
    if 'executed no tests' in findings:
        return 'no-op-no-tests', 'The no-op verifier executed no tests; reward zero does not establish a valid check.'
    if 'timeout' in findings.lower() or 'timed out' in findings.lower():
        return 'no-op-timeout', 'The no-op verifier timed out before a valid result was recorded.'
    raise ValueError(f'Unreviewed no-op outcome: {item}')


def prepare_release(root):
    """Select only confirmed build/no-op passes from the pinned published PR."""
    from collections import Counter
    root = Path(root)
    source = json.loads((root / 'input/source.json').read_text())
    revision = source['revision']
    if not re.fullmatch(r'[a-f0-9]{40}', revision):
        raise ValueError('An immutable HF revision is required')
    published = root / 'input/published'
    evidence = published / 'evidence/termigen-phases-1-3-5-6-7-preview-20261014'
    manifest = json.loads((evidence / 'patch-manifest.json').read_text())
    tasks = {r['task_id']: r for r in manifest['tasks']}
    assert len(tasks) == 3566
    reports = {n: {r['task']: r for r in json.loads((evidence / f'stage-{n}.json').read_text())['items']} for n in (3, 5)}
    output = root / 'prepared'
    output.mkdir(exist_ok=False)
    old_folder = published / 'TermiGen_phase-1-3-5-6-7'
    source_table = pq.ParquetFile(old_folder / 'tasks.parquet')
    schema = source_table.schema_arrow
    archive_rows = pq.read_table(old_folder / 'archive.parquet').to_pylist()
    original_archive = {r['path']: sha256(r['task_binary']) for r in archive_rows}
    assert len(archive_rows) == 197
    archive_ids = set(original_archive)
    retained_ids, new_archive = set(), []
    changes, labels, new_reasons = Counter(), Counter(), Counter()
    images = set()
    run_url = f'https://huggingface.co/datasets/FWeindel/validated-tasks/resolve/{revision}/runs/termigen-phases-1-3-5-6-7-preview-20261014.json'
    with pq.ParquetWriter(output / 'tasks.parquet', schema, compression='zstd') as writer:
        for batch in source_table.read().to_batches(max_chunksize=64):
            rows = []
            for row in batch.to_pylist():
                task = row['path']
                assert task not in retained_ids and task not in archive_ids
                assert sha256(row['task_binary']) == tasks[task]['output_binary_sha256']
                assert reports[3][task]['status'] == 'passed'
                failure = release_nop_failure(reports[5][task])
                row['run'] = run_url
                if failure:
                    label, reason = failure
                    row['archive_stage'] = 5
                    row['archive_reason'] = reason + ' Recorded findings: ' + '; '.join(reports[5][task].get('findings', []))
                    row['archive_labels'] = [label]
                    row['stages_passed'] = ','.join(s for s in row['stages_passed'].split(',') if s != '5')
                    archive_rows.append(row)
                    archive_ids.add(task)
                    new_reasons[label] += 1
                    new_archive.append(dict(task=task, label=label, reason=reason))
                    continue
                assert '3' in row['stages_passed'].split(',') and '5' in row['stages_passed'].split(',')
                retained_ids.add(task)
                labels.update(tasks[task]['labels'])
                changes['environment_patched'] += tasks[task]['action'] == 'changed'
                changes['instruction_patched'] += 'instruction.md' in tasks[task].get('publication_preparation_changed_files', [])
                members = read_task(row['task_binary'])
                dockerfiles = [data for info, data in members if info.name == 'environment/Dockerfile']
                assert len(dockerfiles) == 1
                images.add(sha256(dockerfiles[0]))
                rows.append(row)
            if rows:
                writer.write_table(pa.Table.from_pylist(rows, schema=schema))
    assert len(retained_ids) == 3336 and len(archive_rows) == 230
    assert new_reasons == {'no-op-no-tests': 28, 'no-op-timeout': 5}
    assert retained_ids.isdisjoint(archive_ids) and retained_ids | archive_ids == set(tasks)
    for row in archive_rows:
        if row['path'] in original_archive:
            assert sha256(row['task_binary']) == original_archive[row['path']]
        row['run'] = run_url
    pq.write_table(pa.Table.from_pylist(archive_rows, schema=schema), output / 'archive.parquet', compression='zstd')
    summary = dict(original=3566, kept=3336, archived=230, newly_archived=33,
                   changes=dict(changes), retained_repair_labels=dict(labels),
                   total_environment_patched=sum(r['action'] == 'changed' for r in tasks.values()),
                   retained_distinct_dockerfiles=len(images), original_task_image_definitions=3566,
                   cached_images_published=0, archive_labels=dict(Counter(label for row in archive_rows for label in row['archive_labels'])),
                   new_archive=new_archive, source_revision=revision, native_revision=REVISION,
                   reference_checks='not available: no reference solutions',
                   task_payloads_preserved=True, existing_instruction_changes_preserved=True,
                   validation='Historical stages 3 and 5; no new runtime run', run_url=run_url)
    (output / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    print(json.dumps({k:v for k,v in summary.items() if k != 'new_archive'}, indent=2))


def prepare_release_cli():
    parser = argparse.ArgumentParser(description='Prepare the existing TermiGen build/no-op passing subset')
    parser.add_argument('root', type=Path)
    prepare_release(parser.parse_args().root)


if __name__ == "__main__":
    from data.utils.cli import dispatch
    dispatch({'prepare-release': prepare_release_cli, 'prepare-pilot': prepare_pilot_cli})
    main()
