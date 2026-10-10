"""Resumable stage-3 pilots, repairs, final infrastructure retries, and publication."""

from __future__ import annotations

import argparse
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import time

from validation.data.materialize import parquet_files
from validation.data.selection import discover_tasks
from validation.patch_repair_loop.agents import ROLES, invoke, parse_json_answer
from validation.patch_repair_loop.reports import STAGES, summarize

ROOT = Path(__file__).resolve().parents[2]
VERSION = 2

class LoopBlocked(Exception):
    def __init__(self, kind, reason, evidence=None):
        self.kind, self.reason, self.evidence = kind, reason, evidence
        super().__init__(reason)



def check_blocker(payload, evidence):
    blocker = payload.get("blocked")
    if blocker:
        if not isinstance(blocker, dict) or blocker.get("kind") not in (
                "credentials", "infrastructure", "human_review") or not blocker.get("reason"):
            raise ValueError("blocked requires kind and reason")
        raise LoopBlocked(blocker["kind"], blocker["reason"], str(evidence))



def check_credentials(outcome):
    for finding in outcome.get("failures", []):
        detail = finding.get("detail", "")
        if re.search(r"(?:no|missing|unset|not configured)[^\n]{0,100}(?:API_KEY|credential)|"
                     r"(?:API_KEY|credential)[^\n]{0,100}(?:missing|not configured|not set)",
                     detail, re.IGNORECASE):
            raise LoopBlocked("credentials", detail, finding.get("evidence"))



def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)



def validation_policy(config):
    stages = config.get("required_stages", list(STAGES))
    exclusions = config.get("static_exclusions", {})
    if stages not in (list(STAGES), [1, 3, 5]):
        raise ValueError("required_stages must be [1,3,4,5] or [1,3,5]")
    if not isinstance(exclusions, dict) or any(not isinstance(reason, str) or not reason.strip()
                                              for reason in exclusions.values()):
        raise ValueError("static_exclusions must map checks to explicit reasons")
    if (stages != list(STAGES) or exclusions) and not config.get("validation_policy_reason"):
        raise ValueError("custom validation requires a human-approved validation_policy_reason")
    return {"required_stages": stages, "static_exclusions": exclusions,
            "oracle_validated": 4 in stages, "reason": config.get("validation_policy_reason")}



def load_config(path):
    config = json.loads(Path(path).read_text())
    required = ("dataset", "source_revision", "source", "patcher", "patch_command",
                "work_root", "workspace", "python")
    missing = [key for key in required if not config.get(key)]
    if missing:
        raise ValueError(f"configuration is missing {', '.join(missing)}")
    if not isinstance(config["patch_command"], list) or not all(
            isinstance(arg, str) for arg in config["patch_command"]):
        raise ValueError("patch_command must be an argv list, not a shell command")
    for key in ("source", "patcher", "work_root", "workspace"):
        config[key] = str(Path(config[key]).expanduser().resolve())
    # Keep the virtualenv entry point: resolve() follows its Python symlink and
    # silently discards that environment's installed packages.
    config["python"] = str(Path(config["python"]).expanduser().absolute())
    if not Path(config["patcher"]).is_file() or not Path(config["source"]).exists():
        raise ValueError("patcher and pinned source must already exist")
    if not Path(config["workspace"]).is_dir() or not Path(config["python"]).is_file():
        raise ValueError("Helma workspace and prep Python must already exist")
    work = Path(config["work_root"])
    shared = Path("/hnvme/workspace")
    if not work.is_relative_to(shared) or not Path(config["workspace"]).is_relative_to(shared):
        raise ValueError("Helma work_root and workspace must be under /hnvme/workspace")
    config.setdefault("agent_provider", "codex")
    validation_policy(config)
    config.setdefault("review_setup_runs", 5)
    if not isinstance(config["review_setup_runs"], int) or config["review_setup_runs"] < 5:
        raise ValueError("review_setup_runs must be at least five, as required by the protocol")
    config.setdefault("poll_seconds", 30)
    config.setdefault("seed", 42)
    config.setdefault("time", "10:00:00")
    config.setdefault("partition", "cpu")
    config.setdefault("cpus", 48)
    config.setdefault("memory", "128G")
    config.setdefault("concurrency", 8)
    config.setdefault("network_mode", "host")
    config.setdefault("usage_retry_seconds", 600)
    if config["poll_seconds"] < 5:
        raise ValueError("poll_seconds must be at least five seconds")
    if config["usage_retry_seconds"] < 1:
        raise ValueError("usage_retry_seconds must be positive")
    if config["agent_provider"] not in ("codex", "claude"):
        raise ValueError("agent_provider must be codex or claude")
    supervisor_provider = config.get("supervisor_provider", config["agent_provider"])
    if supervisor_provider not in ("codex", "claude"):
        raise ValueError("supervisor_provider must be codex or claude")
    supervisor_effort = config.get("supervisor_reasoning_effort")
    if supervisor_effort and (supervisor_provider != "codex" or supervisor_effort not in (
            "low", "medium", "high", "xhigh", "max")):
        raise ValueError("supervisor_reasoning_effort requires Codex and a supported effort")
    effort = config.get("agent_reasoning_effort")
    if effort and (config["agent_provider"] != "codex" or
                   effort not in ("low", "medium", "high", "xhigh", "max")):
        raise ValueError("agent_reasoning_effort requires Codex and a supported effort")
    if not isinstance(config.get("agent_models", {}), dict) or set(
            config.get("agent_models", {})) - ROLES:
        raise ValueError(f"agent_models must map only these roles: {sorted(ROLES)}")
    for role, model in config.get("agent_models", {}).items():
        if not isinstance(model, str) or not model:
            raise ValueError(f"agent_models[{role}] must be a nonempty model name")
    loop_settings(config)
    return config



def loop_settings(config):
    validation_args(config)
    if type(config.setdefault('prebuild_all_images', False)) is not bool:
        raise ValueError('prebuild_all_images must be boolean')
    if type(config.setdefault('allow_exclusions', True)) is not bool:
        raise ValueError('allow_exclusions must be boolean')
    if not isinstance(config.setdefault('user_instructions', ''), str):
        raise ValueError('user_instructions must be a string')
    prefix = config.setdefault('pilot_prefix_ids', [])
    if (not isinstance(prefix, list) or any(not isinstance(task, str) or not task for task in prefix)
            or len(prefix) != len(set(prefix))):
        raise ValueError('pilot_prefix_ids must be a list of distinct nonempty task IDs')
    sizes = config.setdefault('pilot_sizes', [10, 50, 200])
    if (not isinstance(sizes, list) or not sizes or
            any(type(size) is not int or size < 1 for size in sizes) or
            sizes != sorted(set(sizes))):
        raise ValueError('pilot_sizes must be a strictly increasing list of positive integers')
    for key in ('max_repairs', 'max_infra_retries', 'max_recoveries', 'max_supervisor_reviews'):
        value = config.setdefault(key, 3 if key in ("max_recoveries", "max_supervisor_reviews") else None)
        if value is not None and (type(value) is not int or value < 0):
            raise ValueError(f'{key} must be a nonnegative integer or null for unlimited')
    if 'recovery_shared_infra' in config:
        raise ValueError('Replace recovery_shared_infra with supervisor_shared_infra and set supervisor_model; recovery agents cannot edit shared code')
    if type(config.setdefault('supervisor_shared_infra', True)) is not bool:
        raise ValueError('supervisor_shared_infra must be boolean')
    if config.get('supervisor_model') is not None and (not isinstance(config['supervisor_model'], str) or not config['supervisor_model'].strip()):
        raise ValueError('supervisor_model must be a nonempty model name or null')
    wait = config.setdefault('recovery_wait_seconds', 300)
    if type(wait) is not int or wait < 1:
        raise ValueError('recovery_wait_seconds must be a positive integer')
    if not isinstance(config.get('recovery_edit_paths', []), list) or any(
            not isinstance(path, str) or not Path(path).is_absolute()
            for path in config.get('recovery_edit_paths', [])):
        raise ValueError('recovery_edit_paths must be a list of absolute paths')
    for path in config.get('recovery_edit_paths', []):
        if Path(path).resolve().is_relative_to(ROOT):
            raise ValueError('recovery_edit_paths are for dedicated files outside the repository; use supervisor_shared_infra for shared code')
    return config


def validation_args(config):
    """Allow resource/timing settings, never workflow or success-policy overrides."""
    args = config.get('validation_args', [])
    if not isinstance(args, list) or any(not isinstance(arg, str) for arg in args):
        raise ValueError('validation_args must be an argv list')
    numeric = {
        '--preparation-median-target-seconds': float,
        '--preparation-mean-target-seconds': float,
        '--preparation-max-seconds': float,
        '--image-build-timeout-sec': int,
        '--image-build-memory-mb': int,
        '--image-build-cpus': int,
        '--image-build-concurrency': int,
    }
    seen = set()
    index = 0
    while index < len(args):
        flag = args[index]
        if flag in seen or flag not in {*numeric, '--pack-image-cache'}:
            raise ValueError(f'unsupported or repeated validation_args option: {flag}')
        seen.add(flag)
        index += 1
        if flag in numeric:
            try:
                value = numeric[flag](args[index])
                if not math.isfinite(value) or (value < 0 if flag == '--image-build-timeout-sec' else value <= 0):
                    raise ValueError()
            except (ValueError, IndexError):
                raise ValueError(f'{flag} requires a positive finite number (or zero for the image-build timeout)') from None
            index += 1
    return list(args)


def require_retained_tasks(config, removed, evidence=None):
    if not config.get('allow_exclusions', True) and removed:
        raise LoopBlocked('human_review',
                          f'Task exclusions are not authorized: {sorted(removed)}',
                          str(evidence) if evidence else None)


def check_limit(config, state, setting, counter):
    limit = config.get(setting)
    if limit is not None and state.get(counter, 0) >= limit:
        raise LoopBlocked('human_review', f'{setting}={limit} reached; increase it and resume to continue')


def task_ids(source):
    files = parquet_files(source)
    if files:
        import pyarrow.parquet as pq
        result = []
        for path in files:
            for batch in pq.ParquetFile(path).iter_batches(columns=["path"], batch_size=4096):
                result += batch.column(0).to_pylist()
    else:
        result = [path.name for path in discover_tasks(source)]
    if len(result) != len(set(result)) or not result:
        raise ValueError("materialized source has duplicate or no task IDs")
    return sorted(result)



def source_hashes(source):
    """Hash every generated task to show full-source effects of a patch rule."""
    files = parquet_files(source)
    if files:
        import pyarrow.parquet as pq
        result = {}
        for path in files:
            for batch in pq.ParquetFile(path).iter_batches(columns=["path", "task_binary"], batch_size=1):
                for row in batch.to_pylist():
                    if row["path"] in result:
                        raise ValueError(f"duplicate task: {row['path']}")
                    result[row["path"]] = hashlib.sha256(row["task_binary"]).hexdigest()
        return result
    result = {}
    for task in discover_tasks(source):
        digest = hashlib.sha256()
        for path in sorted(p for p in task.rglob("*") if p.is_file()):
            digest.update(path.relative_to(task).as_posix().encode() + b"\0")
            digest.update(hashlib.sha256(path.read_bytes()).digest())
        result[task.name] = digest.hexdigest()
    return result



def materialize_source(config, round_dir):
    output = round_dir / "source"
    marker = round_dir / "materialized.json"
    if marker.exists():
        record = json.loads(marker.read_text())
        current_hash = hashlib.sha256(Path(config["patcher"]).read_bytes()).hexdigest()
        if (record.get("patcher_sha256") != current_hash or
                record.get("source_revision") != config["source_revision"]):
            raise RuntimeError(f"patcher or source changed after materialization: {round_dir}")
        return Path(record.get("generated_source", output))
    if output.exists():
        if any(output.iterdir()):
            raise RuntimeError(f"incomplete source materialization needs inspection: {output}")
    else:
        output.mkdir(parents=True)
    replacements = {"output": str(output), "source": config["source"],
                    "patcher": config["patcher"], "python": config["python"]}
    command = [arg.format_map(replacements) for arg in config["patch_command"]]
    save(round_dir / "patch_command.json", command)
    patch_env = {**os.environ, "PYTHONPATH": str(ROOT) + os.pathsep + os.environ.get("PYTHONPATH", "")}
    with (round_dir / "patch.log").open("w") as log:
        subprocess.run(command, cwd=ROOT, env=patch_env,
                       stdout=log, stderr=subprocess.STDOUT, check=True)
    generated = output / "tasks.parquet" if (output / "tasks.parquet").is_file() else output
    hashes = source_hashes(generated)
    ids = sorted(hashes)
    save(round_dir / "source-hashes.json", hashes)
    from validation.patch_repair_loop.image_audit import audit
    save(round_dir / "docker-image-audit.json", audit(generated))
    save(marker, {"tasks": len(ids), "patcher_sha256": hashlib.sha256(
        Path(config["patcher"]).read_bytes()).hexdigest(), "source_revision": config["source_revision"], "generated_source": str(generated)})
    return generated



def pilot_input(source, selected, destination):
    files = parquet_files(source)
    if files:
        # Keep task archives packed on shared storage. Validation materializes
        # them only in node-local scratch, including for the full stage-3 run.
        import pyarrow as pa
        import pyarrow.compute as pc
        import pyarrow.parquet as pq
        destination.mkdir(parents=True, exist_ok=True)
        output = destination / 'tasks.parquet'
        marker = destination / 'selected-ids.json'
        expected = set(selected)
        if len(expected) != len(selected) or not expected:
            raise ValueError('pilot selection must contain distinct task IDs')
        if output.exists():
            if (not marker.exists() or set(json.loads(marker.read_text())) != expected
                    or set(task_ids(output)) != expected):
                raise ValueError('existing pilot Parquet does not match frozen selection')
            return output
        temporary = destination / 'tasks.parquet.tmp'
        schema = pq.read_schema(files[0])
        seen = set()
        try:
            with pq.ParquetWriter(temporary, schema) as writer:
                for path in files:
                    if not pq.read_schema(path).equals(schema, check_metadata=True):
                        raise ValueError('pilot Parquet shards have incompatible schemas or metadata')
                    for batch in pq.ParquetFile(path).iter_batches(batch_size=16):
                        subset = pa.Table.from_batches([batch]).filter(pc.is_in(
                            batch.column(batch.schema.get_field_index('path')),
                            value_set=pa.array(selected)))
                        ids = subset.column('path').to_pylist()
                        if seen.intersection(ids) or len(ids) != len(set(ids)):
                            raise ValueError('duplicate selected task in pilot source')
                        seen.update(ids)
                        if ids:
                            writer.write_table(subset)
            if seen != expected:
                raise ValueError('pilot source did not contain all selected task IDs')
            save(marker, sorted(expected))
            temporary.replace(output)
        finally:
            temporary.unlink(missing_ok=True)
        return output
    if destination.exists():
        found = {path.name for path in discover_tasks(destination)}
        if found != set(selected):
            raise ValueError("existing pilot tree does not match frozen selection")
        return destination
    destination.mkdir(parents=True)
    tasks = {path.name: path for path in discover_tasks(source)}
    for task in selected:
        shutil.copytree(tasks[task], destination / task)
    return destination



def submit(config, source, selected, round_dir, *, full=False, stages=None, review_setup=False, reuse_stage3=None, prebuild_only=False):
    pilot = source if full else pilot_input(source, selected, round_dir / "pilot/tasks")
    contract = round_dir / "contract.json"
    results = round_dir / "results"
    existing_submissions = list((results / "submissions").glob("*/submission.json"))
    if existing_submissions:
        if len(existing_submissions) != 1:
            raise RuntimeError("multiple submission records require inspection before retry")
        existing = json.loads(existing_submissions[0].read_text())
        if existing.get("status") != "submitted" or not existing.get("job_id"):
            raise RuntimeError("incomplete submission record requires inspection before retry")
        prepared = round_dir / "prepared-contract.json"
        if prepared.exists():
            contract = Path(json.loads(prepared.read_text())["path"])
        return {"job_id": str(existing["job_id"]), "submission": str(existing_submissions[0].parent),
                "contract": str(contract), "selected_ids": selected}
    base = [config["python"], str(ROOT / "validation/run.py")]
    policy = validation_policy(config)
    save(round_dir / "validation-policy.json", policy)
    options = ["--stages", ",".join(map(str, stages or policy["required_stages"])), "--submit", "helma",
               "--partition", config["partition"], "--cpus", str(config["cpus"]),
               "--memory", config["memory"], "--concurrency", str(config["concurrency"]),
               "--time", config["time"], "--network-mode", config["network_mode"],
               "--dataset-source", config["dataset"],
               "--dataset-revision", config["source_revision"], "--out", str(results)]
    if reuse_stage3:
        options += ["--reuse-stage3", str(reuse_stage3)]
    if review_setup:
        options += ["--review-setup", str(config.get("review_setup_runs", 5))]
    if prebuild_only:
        options += ['--prebuild-only']
    options += validation_args(config)
    for check, reason in policy["static_exclusions"].items():
        options += ["--exclude", f"{check}={reason}"]
    env = {**os.environ, "OT_WORKSPACE": config["workspace"], "PYTHONPATH": str(ROOT)}
    prepared = round_dir / "prepared-contract.json"
    if prepared.exists():
        contract = Path(json.loads(prepared.read_text())["path"])
        if not contract.is_file():
            raise RuntimeError(f"saved prepared contract is missing: {contract}")
    else:
        # An interrupted older controller may have left a contract or normalized
        # task tree. Preserve it and prepare in a new location, never overwrite
        # an immutable contract or collide with its stage-1 copy.
        if any(round_dir.glob("contract*")):
            number = 0
            while (round_dir / "contract-preparations" / f"preparation-{number:04d}").exists():
                number += 1
            directory = round_dir / "contract-preparations" / f"preparation-{number:04d}"
            directory.mkdir(parents=True)
            contract = directory / "contract.json"
        with (round_dir / "prepare.log").open("w") as log:
            subprocess.run(base + [str(pilot), *options, "--prepare-contract", str(contract)],
                           cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, check=True)
        save(prepared, {"path": str(contract), "policy": policy})
    with (round_dir / "submit.log").open("w") as log:
        subprocess.run(base + ["--contract", str(contract)], cwd=ROOT, env=env,
                       stdout=log, stderr=subprocess.STDOUT, check=True)
    submissions = list((results / "submissions").glob("*/submission.json"))
    if len(submissions) != 1:
        raise ValueError(f"expected one submission, found {len(submissions)}")
    record = json.loads(submissions[0].read_text())
    if record.get("status") != "submitted" or not record.get("job_id"):
        raise RuntimeError(f"Slurm submission failed: {record}")
    return {"job_id": str(record["job_id"]), "submission": str(submissions[0].parent),
            "contract": str(contract), "selected_ids": selected}



def wait_for_job(job, poll_seconds, max_wait_hours=24):
    submission = Path(job["submission"])
    deadline = time.monotonic() + max_wait_hours * 3600
    while True:
        if time.monotonic() > deadline:
            raise TimeoutError(f"job {job['job_id']} did not produce a complete report in {max_wait_hours}h")
        report = submission / "report/summary.json"
        if report.is_file():
            summary = json.loads(report.read_text())
            if summary.get("complete"):
                return submission / "report"
        result = subprocess.run(["sacct", "-X", "-n", "-P", "-j", job["job_id"],
                                 "--format", "JobIDRaw,State"], capture_output=True,
                                text=True, check=True)
        states = [line.split("|")[1] for line in result.stdout.splitlines()
                  if line.split("|")[0] == job["job_id"]]
        if states and states[0] in ("FAILED", "CANCELLED", "TIMEOUT", "OUT_OF_MEMORY", "COMPLETED"):
            if report.is_file() and json.loads(report.read_text()).get("complete"):
                return submission / "report"
            raise RuntimeError(f"job {job['job_id']} ended {states[0]} without a complete report: {submission}")
        time.sleep(poll_seconds)



def wait_for_prebuild(job, poll_seconds, max_wait_hours=24):
    submission = Path(job['submission'])
    deadline = time.monotonic() + max_wait_hours * 3600
    while True:
        result = submission / 'image-prebuild.json'
        execution = submission / 'execution.json'
        if result.is_file() and execution.is_file():
            status = json.loads(execution.read_text())
            if status.get('status') in ('completed', 'findings') and status.get('evidence'):
                return json.loads(result.read_text())
        if time.monotonic() > deadline:
            raise TimeoutError(f'prebuild job {job["job_id"]} exceeded {max_wait_hours}h')
        result = subprocess.run(['sacct', '-X', '-n', '-P', '-j', job['job_id'],
                                 '--format', 'JobIDRaw,State'], capture_output=True, text=True, check=True)
        states = [line.split('|')[1] for line in result.stdout.splitlines()
                  if line.split('|')[0] == job['job_id']]
        if states and states[0] in ('FAILED', 'CANCELLED', 'TIMEOUT', 'OUT_OF_MEMORY', 'COMPLETED'):
            raise RuntimeError(f'prebuild job {job["job_id"]} ended {states[0]} without archived results')
        time.sleep(poll_seconds)


def prebuild_gate(config, source, available, generation):
    """Cache the whole generation before any pilot or validation submission."""
    directory = generation / 'image-prebuild'
    directory.mkdir(exist_ok=True)
    job_path = directory / 'job.json'
    job = json.loads(job_path.read_text()) if job_path.exists() else submit(
        config, source, available, directory, full=True, stages=[3], prebuild_only=True)
    save(job_path, job)
    evidence(directory, job)
    result = wait_for_prebuild(job, config['poll_seconds'], config.get('max_wait_hours', 24))
    save(directory / 'outcome.json', result)
    return directory, job, result


def agent_call(config, role, context, round_dir, label, *, watched_paths=()):
    context = {**context, "approved_validation_policy": validation_policy(config),
               "user_instructions": config.get('user_instructions', ''),
               "allow_exclusions": config.get('allow_exclusions', True),
               "validation_args": validation_args(config)}
    if config.get("prior_repair_evidence"):
        context = {**context, "prior_repair_evidence": config["prior_repair_evidence"]}
    model = config.get("agent_models", {}).get(role, config.get("agent_model"))
    if role == "recovery":
        model = config.get("recovery_model") or model
    if role == "supervisor":
        model = config.get("supervisor_model") or config.get("agent_models", {}).get("supervisor")
        if not model:
            raise ValueError("Set supervisor_model explicitly; shared infrastructure review never uses the ordinary agent model by default")
    provider = config.get("supervisor_provider", config["agent_provider"]) if role == "supervisor" else config["agent_provider"]
    effort = config.get("supervisor_reasoning_effort", config.get("agent_reasoning_effort")
                        if provider == config["agent_provider"] else None) if role == "supervisor" else config.get("agent_reasoning_effort")
    return invoke(role, context, provider=provider, repo=ROOT,
                  model=model, output_dir=round_dir / "agents" / label,
                  reasoning_effort=effort,
                  usage_retry_seconds=config.get("usage_retry_seconds", 600),
                  watched_paths=watched_paths)



@contextmanager
def lock(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError(f'Another controller holds {path}') from exc
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def selection(ids, size, seed, prefix=()):
    """One stable ordering gives nested 10/50/200 pilots, including after repairs."""
    if set(prefix) - set(ids):
        raise LoopBlocked('human_review', 'pilot_prefix_ids contains unavailable tasks')
    ordered = sorted(ids, key=lambda task: hashlib.sha256(f'{seed}:{task}'.encode()).hexdigest())
    ordered = list(prefix) + [task for task in ordered if task not in set(prefix)]
    return ordered if size is None else ordered[:size]


def dataset_hashes(config):
    root = Path(config['patcher']).parent
    return {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob('*')) if p.is_file()
            and not any(part.startswith('.') or part in ('__pycache__', 'pilot-results')
                        for part in p.relative_to(root).parts)}


def fixer_idle(config, state):
    """A fixer phase without partial dataset edits may be replaced by a fresh stage 3.

    The fixer snapshot is written before its agent runs. When the dataset still
    matches it (the fixer never started, or finished without a dataset change
    such as a shared-infrastructure blocker), no in-progress edit needs to be
    finished and the stale evidence can be superseded by a new generation.
    """
    if state.get('phase') != 'fixer':
        return False
    evidence_dir = (state.get('pending_evidence') or {}).get('evidence_dir')
    before = Path(evidence_dir) / 'dataset-before.json' if evidence_dir else None
    return before is None or not before.exists() or json.loads(before.read_text()) == dataset_hashes(config)


def restart_stage3(state):
    """Open a new generation from the first pilot with setup review enabled."""
    state.update(phase='build', generation=state.get('generation', 0) + 1, pilot=0, review_setup=True)
    for key in ('validation_finished_at', 'recovery_requires_stage3', 'pending_evidence'):
        state.pop(key, None)


def context(config, state):
    directory = Path(config['patcher']).parent
    return {'dataset': directory.name, 'dataset_source': config['dataset'],
            'dataset_dir': str(directory), 'patcher': config['patcher'],
            'source': config['source'], 'source_revision': config['source_revision'],
            'integration_plan': str(directory / 'INTEGRATION_PLAN.md'),
            'evidence_dir': config['work_root'],
            'previous_attempts': state['history'],
            'recovery_instructions': state.get('recovery_handoff'),
            'protocol': str(ROOT / 'validation/PROTOCOL.md'),
            'image_audit_tool': 'python -m validation.patch_repair_loop.image_audit SOURCE'}


def call(config, state, role, directory, extra=None):
    answer = agent_call(config, role, {**context(config, state), **(extra or {})},
                             directory / f'agent-attempt-{state.get("agent_attempt", 0):04d}', role,
                             watched_paths=[Path(config['patcher']).parent])
    result = parse_json_answer(answer)
    check_blocker(result, directory / f'agent-attempt-{state.get("agent_attempt", 0):04d}' / 'agents' / role)
    require_retained_tasks(config, [item['task_id'] for item in exclusions(result)], directory)
    return result


def exclusions(result, config=None):
    items = result.get('exclusions', [])
    if not isinstance(items, list) or any(not isinstance(item, dict) or
            any(not isinstance(item.get(key), str) or not item[key].strip()
                for key in ('task_id', 'category', 'reason', 'evidence')) for item in items):
        raise ValueError('exclusions require task_id, category, reason, and evidence')
    require_retained_tasks(config or {}, [item['task_id'] for item in items])
    return items


def evidence(directory, job=None, outcome=None, error=None):
    submission = Path(job['submission']) if job else None
    generation = directory.parent
    marker = generation / 'materialized.json'
    generated = json.loads(marker.read_text()).get('generated_source') if marker.exists() else None
    payload = {'evidence_dir': str(directory),
               'patched_source': generated,
               'image_audit': str(generation / 'docker-image-audit.json'),
               'patch_manifest': str(generation / 'source/tasks.manifest.json'),
               'patch_log': str(directory.parent / 'patch.log'),
               'prepare_log': str(directory / 'prepare.log'),
               'submit_log': str(directory / 'submit.log'),
               'job': job, 'error': error, 'outcome': outcome,
               'report_dir': str(submission / 'report') if submission else None,
               'evidence_archive': str(submission / 'evidence.tar.gz') if submission else None,
               'slurm_logs': [str(p) for p in submission.glob('slurm-*.out')] if submission else [],
               'archive_note': 'Detailed execution logs and timing samples are in evidence.tar.gz. '
                               'Report paths may name expired node-local scratch; find matching archive members.'}
    save(directory / 'evidence.json', payload)
    return payload


def drive(config, config_path, state_path, resume=False):
    loop_settings(config)
    work = Path(config['work_root'])
    frozen_config = {key: value for key, value in json.loads(Path(config_path).read_text()).items()
                     if not key.startswith(('publish', 'recovery_', 'supervisor_')) and key not in ('max_repairs', 'max_infra_retries', 'max_recoveries', 'max_supervisor_reviews')}
    digest = hashlib.sha256(json.dumps(frozen_config, sort_keys=True).encode()).hexdigest()
    state = json.loads(state_path.read_text()) if state_path.exists() else {
        'version': VERSION, 'config_sha256': digest, 'dataset': config['dataset'],
        'status': 'running', 'phase': 'proposer', 'generation': 0, 'pilot': 0,
        'started_at': datetime.now(timezone.utc).isoformat(),
        'review_setup': False, 'history': [], 'exclusions': [], 'known_ids': None}
    if state.get('version') != VERSION:
        raise ValueError('Legacy loop state: use a new work_root for the stage-3-first workflow')
    if state['config_sha256'] != digest:
        raise ValueError('Configuration changed; use a new work_root')
    if resume:
        if state['status'] != 'blocked':
            raise ValueError('resume requires a blocked controller')
        state.update(status='running', agent_attempt=state.get('agent_attempt', 0) + 1)
        if state['phase'] in ('build', 'validate') or (
                state['phase'] in ('final_review', 'final_retry') and
                (version := work / f'generation-{state["generation"]:04d}' / 'dataset-version.json').exists() and
                json.loads(version.read_text()) != dataset_hashes(config)):
            state.update(phase='build', generation=state['generation'] + 1, pilot=0)
        elif state.get('recovery_requires_stage3') and fixer_idle(config, state):
            # Accepted repairs (shared code or recovery edits) invalidate the
            # evidence the idle fixer was given; validate again instead.
            restart_stage3(state)
        state.pop('blocker', None)
    save(state_path, state)
    try:
        from validation.patch_repair_loop.recovery import infrastructure_revision, jobs
        revision = infrastructure_revision()
        if state.get('status') != 'complete' and state.get('shared_infrastructure_revision', revision) != revision and (
                state['phase'] not in ('proposer', 'implementer', 'fixer') or fixer_idle(config, state)
                ) and not (work / 'publication/job.json').exists():
            if any(not job['finished'] for job in jobs(work)):
                raise LoopBlocked('infrastructure', 'Shared infrastructure changed; wait for existing jobs before restarting stage 3')
            restart_stage3(state)
        state['shared_infrastructure_revision'] = revision
        save(state_path, state)
        while state['status'] == 'running':
            phase = state['phase']
            generation = work / f'generation-{state["generation"]:04d}'
            generation.mkdir(parents=True, exist_ok=True)
            if phase == 'proposer':
                result = call(config, state, 'proposer', work / 'proposal')
                if not isinstance(result.get('changes_needed'), bool) or not result.get('reason'):
                    raise ValueError('Proposer requires changes_needed boolean and reason')
                if result['changes_needed'] and not Path(context(config, state)['integration_plan']).is_file():
                    raise ValueError('Proposer did not write INTEGRATION_PLAN.md')
                save(work / 'proposal.json', result)
                state.update(review_setup=result['changes_needed'],
                             phase='implementer' if result['changes_needed'] else 'build')
            elif phase == 'implementer':
                result = call(config, state, 'implementer', work / 'implementation')
                state['exclusions'] += exclusions(result, config)
                save(work / 'implementation.json', result)
                state['phase'] = 'build'
            elif phase == 'final_review':
                from validation.patch_repair_loop.retries import decisions
                bundle = state['pending_evidence']
                directory = generation / f'final-review-{state["final_attempt"]:04d}'
                result = call(config, state, 'final_failure_reviewer', directory, bundle)
                save(directory / 'decision.json', result)
                groups = decisions(result, bundle['outcome'])
                require_retained_tasks(config, groups['archive'], directory / 'decision.json')
                if groups['retry']:
                    save(work / 'infrastructure-findings' / f'generation-{state["generation"]:04d}-review-{state["final_attempt"]:04d}.json', {
                        'dataset': config['dataset'], 'decision': str(directory / 'decision.json'),
                        'evidence_dir': bundle['evidence_dir'],
                        'findings': [item for item in result['findings'] if item['action'] == 'retry']})
                if groups['retry']:
                    state.update(phase='final_retry', retry_ids=groups['retry'],
                                 retry_decision=str(directory / 'decision.json'))
                elif groups['repair']:
                    state['pending_evidence']['reviewer_decision'] = result
                    state['phase'] = 'fixer'
                else:
                    state['phase'] = 'publish'
            elif phase == 'final_retry':
                check_limit(config, state, 'max_infra_retries', 'infra_retry_count')
                from validation.contract import read
                from validation.patch_repair_loop.retries import collect, merge
                version_path = generation / 'dataset-version.json'
                if json.loads(version_path.read_text()) != dataset_hashes(config):
                    raise ValueError('Dataset changed during infrastructure retries; restart pilots')
                selected = state['retry_ids']
                directory = generation / f'final-retry-{state["final_attempt"] + 1:04d}'
                directory.mkdir(exist_ok=True)
                job_path = directory / 'job.json'
                original = read(state['final_job']['contract'])
                job = json.loads(job_path.read_text()) if job_path.exists() else submit(
                    config, Path(original['arguments']['tasks']),
                    selected, directory, stages=original['stages'], review_setup=state['review_setup'],
                    reuse_stage3=work / 'stage3-passing-subset.json')
                job['reviewer_decision'] = state['retry_decision']
                save(job_path, job)
                evidence(directory, job)
                retry_report, _ = collect(config, job, selected, original['stages'], directory)
                report = directory / 'effective-report'
                outcome = merge(state['final_job'], state['final_report'], job, selected, report, retry_report)
                bundle = evidence(directory, job, outcome)
                bundle.update(report_dir=str(report), effective_report=str(report),
                              original_final_job=state['final_job'])
                save(directory / 'evidence.json', bundle)
                entry = {'phase': phase, 'job': job, 'retry_ids': selected,
                         'outcome': str(report / 'outcome.json'), 'evidence': str(directory / 'evidence.json')}
                if entry not in state['history']:
                    state['history'].append(entry)
                state.update(final_report=str(report), final_attempt=state['final_attempt'] + 1,
                             infra_retry_count=state.get('infra_retry_count', 0) + 1,
                             pending_evidence=bundle,
                             phase='final_review' if outcome['failures'] else 'publish')
            elif phase == 'publish':
                state.setdefault('validation_finished_at', datetime.now(timezone.utc).isoformat())
                save(state_path, state)
                from validation.patch_repair_loop.publication import loop_summary
                save(work / 'loop-summary.json', loop_summary(state, work))
                if config.get('publish', True):
                    from validation.patch_repair_loop.publication import publish
                    result = publish(config, state, work / 'publication')
                    state['pull_request'] = result['pull_request']
                state['status'] = 'complete'
            elif phase == 'fixer':
                check_limit(config, state, 'max_repairs', 'repair_count')
                directory = Path(state['pending_evidence']['evidence_dir'])
                before_path = directory / 'dataset-before.json'
                if not before_path.exists():
                    save(before_path, dataset_hashes(config))
                result = call(config, state, 'fixer', directory, state['pending_evidence'])
                save(directory / 'repair.json', result)
                state['exclusions'] += exclusions(result, config)
                if json.loads(before_path.read_text()) == dataset_hashes(config):
                    raise LoopBlocked('human_review', 'Fixer made no dataset changes', str(directory))
                state['review_setup'] |= result.get('requires_review_setup', False) is True
                state.update(phase='build', generation=state['generation'] + 1, pilot=0,
                             repair_count=state.get('repair_count', 0) + 1)
                state.pop('pending_evidence', None)
            else:
                version_path = generation / 'dataset-version.json'
                if version_path.exists() and json.loads(version_path.read_text()) != dataset_hashes(config):
                    raise ValueError('Dataset files changed during validation; resume to rebuild and restart pilots')
                try:
                    source = materialize_source(config, generation)
                except subprocess.CalledProcessError as exc:
                    directory = generation / 'preparation-failure'
                    directory.mkdir(exist_ok=True)
                    state.update(phase='fixer', pending_evidence=evidence(directory, error=str(exc)))
                    save(state_path, state)
                    continue
                if not version_path.exists():
                    save(version_path, dataset_hashes(config))
                    state.pop('recovery_requires_stage3', None)
                available = task_ids(source)
                if not config.get('allow_exclusions', True):
                    require_retained_tasks(config, set(task_ids(Path(config['source']))) - set(available),
                                           generation / 'materialized.json')
                if state['known_ids'] is not None:
                    missing = set(state['known_ids']) - set(available)
                    declared = {item['task_id'] for item in state['exclusions']}
                    if not missing <= declared:
                        raise ValueError(f'Undeclared task exclusions: {sorted(missing - declared)}')
                    if set(available) - set(state['known_ids']):
                        raise ValueError('A repair unexpectedly added task IDs')
                state['known_ids'] = available
                save(state_path, state)
                if config.get('prebuild_all_images', False):
                    build_directory, build_job, build_result = prebuild_gate(config, source, available, generation)
                    if not build_result['passed']:
                        bundle = evidence(build_directory, build_job, build_result)
                        state.update(phase='fixer', pending_evidence=bundle)
                        save(state_path, state)
                        continue
                final = phase == 'validate'
                size = None if final or state['pilot'] == len(config['pilot_sizes']) else config['pilot_sizes'][state['pilot']]
                selected = state['stage3_passed_ids'] if final else selection(
                    available, size, config['seed'], config.get('pilot_prefix_ids', []))
                if not selected or not set(selected) <= set(available):
                    raise ValueError('No valid retained tasks to validate')
                directory = generation / ('validation' if final else f'stage3-{size or "full"}')
                directory.mkdir(exist_ok=True)
                stages = validation_policy(config)['required_stages'] if final else [3]
                job_path = directory / 'job.json'
                job = json.loads(job_path.read_text()) if job_path.exists() else submit(
                    config, source, selected, directory, stages=stages,
                    full=final, review_setup=state['review_setup'],
                    reuse_stage3=work / 'stage3-passing-subset.json' if final else None)
                save(job_path, job)
                # Persist paths before waiting, so running and failed jobs are inspectable too.
                evidence(directory, job)
                if final:
                    from validation.patch_repair_loop.retries import collect
                    report, outcome = collect(config, job, selected, stages, directory)
                else:
                    report = wait_for_job(job, config['poll_seconds'], config.get('max_wait_hours', 24))
                    outcome = summarize(report, selected, required_stages=stages)
                save(directory / 'outcome.json', outcome)
                bundle = evidence(directory, job, outcome)
                bundle['report_dir'] = str(report)
                save(directory / 'evidence.json', bundle)
                entry = {'generation': state['generation'], 'phase': phase, 'size': size,
                         'job_id': job['job_id'], 'outcome': str(directory / 'outcome.json'),
                         'evidence': str(directory / 'evidence.json'),
                         'passed': outcome['passed_all'], 'tasks': len(selected)}
                if entry not in state['history']:
                    state['history'].append(entry)
                check_credentials(outcome)
                if final:
                    state.update(phase='final_review' if outcome['failures'] else 'publish',
                                 final_report=str(report), final_attempt=0,
                                 final_job=job, final_source=str(source), pending_evidence=bundle)
                elif outcome['passed_all'] != len(selected):
                    state.update(phase='fixer', pending_evidence=bundle)
                elif size is None:
                    state.update(phase='validate', stage3_passed_ids=outcome['passed_task_ids'])
                    save(work / 'stage3-passing-subset.json', {
                        'task_ids': outcome['passed_task_ids'], 'source': str(source),
                        'report': str(report), 'contract': job['contract'],
                        'contract_sha256': outcome['contract_sha256']})
                else:
                    state['pilot'] += 1
            save(state_path, state)
    except (Exception, KeyboardInterrupt) as exc:
        state.update(status='blocked', blocker={'kind': getattr(exc, 'kind', 'infrastructure'),
                     'reason': str(exc), 'evidence': getattr(exc, 'evidence', None)})
        from validation.patch_repair_loop.recovery import record_failure
        state['last_failure'] = str(record_failure(work, state, exc))
        save(state_path, state)
        raise
    print(json.dumps({'status': state['status'], 'state': str(state_path),
                      'final_report': state.get('final_report'), 'pull_request': state.get('pull_request')}, indent=2))
    return state


def run(config_path, *, resume=False):
    config = load_config(config_path)
    work = Path(config['work_root'])
    # Both locks are held for the controller lifetime, including subprocesses and waits.
    # A second process must never rewrite the first controller's state on lock failure.
    with ExitStack() as stack:
        from validation.patch_repair_loop.recovery import infrastructure_lock
        stack.enter_context(infrastructure_lock())
        stack.enter_context(lock(Path(config['patcher']).parent / '.patch-repair.lock'))
        stack.enter_context(lock(work / '.controller.lock'))
        return drive(config, config_path, work / 'loop-state.json', resume=resume)


def main():
    parser = argparse.ArgumentParser(prog="python -m validation.patch_repair_loop", description=__doc__)
    parser.add_argument("command", choices=("run", "status", "resume", "supervise"))
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    config = load_config(args.config)
    state_path = Path(config["work_root"]) / "loop-state.json"
    if args.command == "supervise":
        from validation.patch_repair_loop.recovery import supervise
        supervise(args.config)
    elif args.command == "status":
        if not state_path.exists():
            print("No run started")
            return
        state = json.loads(state_path.read_text())
        waits = sorted(Path(config["work_root"]).glob("**/agents/*/usage-wait.json"))
        if waits:
            state["usage_waits"] = [{"path": str(path), **json.loads(path.read_text())}
                                    for path in waits]
        jobs = sorted(Path(config["work_root"]).glob("**/job.json"), key=lambda path: path.stat().st_mtime_ns)
        if jobs and state.get("status") == "running" and json.loads(jobs[-1].read_text()).get("job_id"):
            job = json.loads(jobs[-1].read_text())
            result = subprocess.run(["sacct", "-X", "-n", "-P", "-j", job["job_id"],
                                     "--format", "JobIDRaw,State,Elapsed"], capture_output=True, text=True)
            state["latest_job"] = {"job_id": job["job_id"], "slurm": result.stdout.strip(),
                                   "submission": job.get("submission", str(jobs[-1].parent))}
        print(json.dumps(state, indent=2))
    else:
        run(args.config, resume=args.command == "resume")



if __name__ == "__main__":
    # Use the canonical module instance shared by the retry/publication helpers.
    from validation.patch_repair_loop.controller import main as entry_main
    entry_main()
