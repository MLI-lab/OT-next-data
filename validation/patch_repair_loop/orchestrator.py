"""Resumable 10/50/200 pilot repair loop, then a human-approved full run."""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import time

from validation.data.materialize import materialize, parquet_files
from validation.data.selection import discover_tasks
from validation.patch_repair_loop.agents import ROLES, invoke, parse_json_answer
from validation.patch_repair_loop.lessons import append_confirmed, targets as lesson_targets
from validation.patch_repair_loop.reports import STAGES, before_after, summarize


ROOT = Path(__file__).resolve().parents[2]
WAVES = (10, 50, 200)  # New tasks per wave; older tasks remain regression cases.


class ReviewRejected(Exception):
    def __init__(self, result, evidence):
        self.result, self.evidence = result, str(evidence)
        super().__init__(f"review rejected repair: {evidence}")


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
    # Old frozen configurations may contain this key; it no longer limits work.
    config.pop("max_repairs_per_wave", None)
    validation_policy(config)
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
    config["infrastructure_files"] = [str(Path(path).expanduser().resolve())
                                      for path in config.get("infrastructure_files", [])]
    return config


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
        completions = [round_dir / "agents/implement/completed.json",
                       *round_dir.glob("repair-attempts/*/agents/implement/completed.json")]
        repaired = any(path.exists() and json.loads(path.read_text()).get(
            "watched_after", {}).get(config["patcher"]) == current_hash for path in completions)
        if ((record.get("patcher_sha256") != current_hash and not repaired) or
                record.get("source_revision") != config["source_revision"]):
            raise RuntimeError(f"patcher or source changed after materialization: {round_dir}")
        return output
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
    hashes = source_hashes(output)
    ids = sorted(hashes)
    save(round_dir / "source-hashes.json", hashes)
    from validation.patch_repair_loop.image_audit import audit
    save(round_dir / "docker-image-audit.json", audit(output))
    save(marker, {"tasks": len(ids), "patcher_sha256": hashlib.sha256(
        Path(config["patcher"]).read_bytes()).hexdigest(), "source_revision": config["source_revision"]})
    return output


def source_effect(before_path, after_path):
    before = json.loads(Path(before_path).read_text())
    after = json.loads(Path(after_path).read_text())
    return {"before_count": len(before), "after_count": len(after),
            "changed_ids": sorted(task for task in before.keys() & after.keys()
                                  if before[task] != after[task]),
            "removed_ids": sorted(before.keys() - after.keys()),
            "added_ids": sorted(after.keys() - before.keys())}


def choose_new(all_ids, previous, count, seed, wave):
    remaining = set(all_ids) - set(previous)
    order = sorted(remaining, key=lambda task: hashlib.sha256(
        f"{seed}:{wave}:{task}".encode()).hexdigest())
    # Small published sources still get a final all-remaining regression wave.
    return order[:min(count, len(order))]


def pilot_input(source, selected, destination):
    if destination.exists():
        found = {path.name for path in discover_tasks(destination)}
        if found != set(selected):
            raise ValueError("existing pilot tree does not match frozen selection")
        return destination
    destination.mkdir(parents=True)
    if parquet_files(source):
        materialize(source, destination, selected_ids=set(selected))
    else:
        tasks = {path.name: path for path in discover_tasks(source)}
        for task in selected:
            shutil.copytree(tasks[task], destination / task)
    return destination


def submit(config, source, selected, round_dir, *, full=False):
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
    options = ["--stages", ",".join(map(str, policy["required_stages"])), "--submit", "helma",
               "--partition", config["partition"], "--cpus", str(config["cpus"]),
               "--memory", config["memory"], "--concurrency", str(config["concurrency"]),
               "--time", config["time"], "--network-mode", config["network_mode"],
               "--dataset-source", config["dataset"],
               "--dataset-revision", config["source_revision"], "--out", str(results)]
    for check, reason in policy["static_exclusions"].items():
        options += ["--exclude", f"{check}={reason}"]
    if full and config.get("publish_repo"):
        options += ["--publish-repo", config["publish_repo"], "--publish-require-complete"]
        if config.get("publish_readme", True):
            options.append("--publish-readme")
        for folder in config.get("publish_folder", []):
            options += ["--publish-folder", folder]
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


def agent_call(config, role, context, round_dir, label, *, watched_paths=()):
    context = {**context, "approved_validation_policy": validation_policy(config)}
    if config.get("prior_repair_evidence"):
        context = {**context, "prior_repair_evidence": config["prior_repair_evidence"]}
    model = config.get("agent_models", {}).get(role, config.get("agent_model"))
    return invoke(role, context, provider=config["agent_provider"], repo=ROOT,
                  model=model, output_dir=round_dir / "agents" / label,
                  reasoning_effort=config.get("agent_reasoning_effort"),
                  usage_retry_seconds=config.get("usage_retry_seconds", 600),
                  watched_paths=watched_paths)


def diagnose(config, report, outcome, source, round_dir):
    groups = []
    for stage in STAGES:
        failures = [item for item in outcome["failures"] if item["stage"] == stage]
        if not failures:
            continue
        context = {"dataset": config["dataset"], "stage": stage,
                   "source": str(source), "report": str(report),
                   "slurm_log": str(next(report.parent.glob("slurm-*.out"), "")),
                   "counts": outcome["stage_counts"].get(str(stage)),
                   "failures": failures[:80], "failure_total": len(failures)}
        image_audit = round_dir / "docker-image-audit.json"
        if stage == 3 and image_audit.is_file():
            context["docker_image_audit"] = str(image_audit)
            context["image_reuse_guidance"] = (
                "Identify shared base images and task-specific Dockerfile or build-context layers. "
                "Look for safe rules that move task-specific runtime setup or files into "
                "mounted setup_files. Setup files are not automatically executed: preserve "
                "the setup command, its timing, agent-visible state, oracle behavior, and NOP behavior. "
                "Do not replace genuinely different base images or build-time dependencies "
                "with a shared image without runtime evidence.")
        answer = agent_call(config, "diagnose", context, round_dir, f"diagnose-stage-{stage}")
        diagnosis = parse_json_answer(answer)
        check_blocker(diagnosis, round_dir / "agents" / f"diagnose-stage-{stage}")
        if not isinstance(diagnosis.get("groups"), list):
            raise ValueError(f"diagnosis for stage {stage} lacks groups")
        groups += diagnosis["groups"]
    save(round_dir / "diagnoses.json", groups)
    return groups


def review(config, context, round_dir, label):
    answer = agent_call(config, "review", context, round_dir, label)
    result = parse_json_answer(answer)
    check_blocker(result, round_dir / "agents" / label)
    if result.get("approved") is not True:
        raise ReviewRejected(result, round_dir / "agents" / label)
    return result


def repair_once(config, outcome, source, report, round_dir, groups, feedback):
    infrastructure = [group for group in groups if group.get("category") == "infrastructure"]
    task_groups = [group for group in groups if group.get("category") != "infrastructure"]
    if infrastructure:
        files = [Path(path) for path in config.get("infrastructure_files", [])]
        before_path = round_dir / "infrastructure-before.json"
        if before_path.exists():
            original = json.loads(before_path.read_text())
        else:
            original = {str(path): path.read_text() for path in files}
            save(before_path, original)
        context = {"dataset": config["dataset"], "groups": infrastructure,
                   "report": str(report), "source": str(source),
                   "reviewer_feedback": feedback,
                   "allowed_infrastructure_files": config.get("infrastructure_files", [])}
        decision = parse_json_answer(agent_call(config, "infrastructure", context, round_dir,
                                                "infrastructure", watched_paths=files))
        check_blocker(decision, round_dir / "agents/infrastructure")
        if decision.get("action") == "blocked":
            raise LoopBlocked("infrastructure", decision.get("reason", "unrecoverable infrastructure"),
                              str(round_dir / "agents/infrastructure"))
        if decision.get("action") not in ("retry_only", "patched"):
            raise ValueError("infrastructure agent must choose retry_only, patched or blocked")
        if decision["action"] == "retry_only" and (
                decision.get("retry_ready") is not True or not decision.get("retry_evidence")):
            raise LoopBlocked("infrastructure", "retry has no verified recovery prerequisite: " +
                              decision.get("reason", "no recovery evidence"),
                              str(round_dir / "agents/infrastructure"))
        save(round_dir / "infrastructure.json", decision)
        if decision["action"] == "patched" and not files:
            raise RuntimeError("infrastructure patch needs configured infrastructure_files")
        infra_diff = "".join("".join(difflib.unified_diff(
            original[str(path)].splitlines(True), path.read_text().splitlines(True),
            fromfile=f"{path}-before", tofile=f"{path}-after")) for path in files)
        (round_dir / "infrastructure.diff").write_text(infra_diff)
        if bool(infra_diff) != (decision["action"] == "patched"):
            raise RuntimeError("infrastructure action does not match configured-file changes")
    if task_groups:
        proposal_context = {"dataset": config["dataset"], "groups": task_groups,
            "source": str(source), "patcher": config["patcher"],
            "reviewer_feedback": feedback,
            "pilot_counts": outcome["stage_counts"]}
        image_audit = round_dir / "docker-image-audit.json"
        if image_audit.is_file():
            proposal_context["docker_image_audit"] = str(image_audit)
            proposal_context["image_reuse_guidance"] = (
                "For build/runtime failures, count unique Dockerfiles, build payloads, and bases. "
                "Propose reuse via mounted setup_files only for a proven equivalent task class. "
                "Document the setup invocation and compare stage 3, oracle, and post-setup NOP results. "
                "Keep an image difference when the task truly requires it.")
        proposal = parse_json_answer(agent_call(config, "propose_rule",
            proposal_context, round_dir, "propose-rule"))
        save(round_dir / "proposal.json", proposal)
        check_blocker(proposal, round_dir / "proposal.json")
        before_path = round_dir / "patcher-before.txt"
        if before_path.exists():
            before = before_path.read_text()
        else:
            before = Path(config["patcher"]).read_text()
            before_path.write_text(before)
        answer = agent_call(config, "implement", {"proposal": proposal,
            "reviewer_feedback": feedback,
            "patcher": config["patcher"], "source": str(source)}, round_dir, "implement",
            watched_paths=[config["patcher"]])
        try:
            implementation = parse_json_answer(answer)
        except ValueError:
            implementation = {}
        check_blocker(implementation, round_dir / "agents/implement")
        disagreement = implementation.get("proposal_feedback")
        if disagreement:
            if not isinstance(disagreement, dict) or not disagreement.get("reason") or not disagreement.get("evidence"):
                raise ValueError("proposal_feedback requires reason and evidence")
            raise ReviewRejected({"approved": False, "implementation_feedback": disagreement,
                "required_changes": ["Reconcile the implementation findings with the proposed rule using the supplied evidence."]},
                round_dir / "agents/implement")
        after = Path(config["patcher"]).read_text()
        diff = "".join(difflib.unified_diff(before.splitlines(True), after.splitlines(True),
                                             fromfile="patcher-before", tofile="patcher-after"))
        (round_dir / "patcher.diff").write_text(diff)
        evidence_only = feedback and outcome.get("task_count", 0) > 0 and (
            outcome.get("passed_all") == outcome["task_count"])
        if not diff and not infrastructure and not evidence_only:
            raise ReviewRejected({"approved": False, "required_changes": [
                "Implement a supported repair or explicit evidence-backed discard; "
                "otherwise report a structured blocker requiring human review."]}, round_dir)
        review_context = {"proposal": proposal, "patcher_diff": diff,
                        "infrastructure_diff": (round_dir / "infrastructure.diff").read_text()
                        if (round_dir / "infrastructure.diff").exists() else "",
                        "implementation_answer": answer, "pilot_before": outcome["stage_counts"]}
        if image_audit.is_file():
            review_context["docker_image_audit"] = str(image_audit)
            review_context["image_reuse_guidance"] = (
                "Review whether the rule preserves needed build steps and setup timing. "
                "Prefer shared images with mounted setup_files where equivalence is evidenced; "
                "setup_files is not an automatic execution hook.")
        review(config, review_context, round_dir, "review-code")
        return proposal
    if not infrastructure:
        raise RuntimeError("diagnosis produced no actionable task or infrastructure group")
    review(config, {"infrastructure": infrastructure, "report": str(report),
                    "decision": json.loads((round_dir / "infrastructure.json").read_text()),
                    "infrastructure_diff": (round_dir / "infrastructure.diff").read_text()},
           round_dir, "review-infrastructure")
    return {"rules": [], "discards": []}


def repair(config, outcome, source, report, round_dir, initial_feedback=None):
    """Persist unlimited review attempts; submit only after agent approval."""
    check_credentials(outcome)
    approved = round_dir / "repair-approved.json"
    if approved.exists():
        saved = json.loads(approved.read_text())
        for filename, expected in saved["file_hashes"].items():
            if hashlib.sha256(Path(filename).read_bytes()).hexdigest() != expected:
                raise LoopBlocked("human_review", "files changed after repair approval", str(approved))
        return saved["proposal"]
    groups = diagnose(config, report, outcome, source, round_dir)
    if initial_feedback:
        groups.append({"category": "task", "failure_signature": "rejected outcome review",
                       "reviewer_feedback": initial_feedback})
    originals_path = round_dir / "repair-originals.json"
    if originals_path.exists():
        originals = json.loads(originals_path.read_text())
    else:
        paths = [config["patcher"], *config.get("infrastructure_files", [])]
        originals = {path: Path(path).read_text() for path in paths}
        old_patcher = round_dir / "patcher-before.txt"
        if old_patcher.exists():
            originals[config["patcher"]] = old_patcher.read_text()
        old_infra = round_dir / "infrastructure-before.json"
        if old_infra.exists():
            originals.update(json.loads(old_infra.read_text()))
        save(originals_path, originals)
    progress_path = round_dir / "repair-progress.json"
    progress = json.loads(progress_path.read_text()) if progress_path.exists() else {
        "attempt": 0, "feedback": initial_feedback, "rejections": []}
    while True:
        attempt = round_dir / "repair-attempts" / f"attempt-{progress['attempt']:04d}"
        attempt.mkdir(parents=True, exist_ok=True)
        before = attempt / "patcher-before.txt"
        if not before.exists():
            before.write_text(originals[config["patcher"]])
        infra = attempt / "infrastructure-before.json"
        if not infra.exists():
            save(infra, {path: text for path, text in originals.items() if path != config["patcher"]})
        audit = round_dir / "docker-image-audit.json"
        if audit.exists() and not (attempt / audit.name).exists():
            shutil.copy2(audit, attempt / audit.name)
        try:
            proposal = repair_once(config, outcome, source, report, attempt, groups, progress["feedback"])
        except ReviewRejected as exc:
            record = {"review": exc.result, "evidence": exc.evidence,
                      "attempt": progress["attempt"]}
            save(attempt / "rejection.json", record)
            progress["rejections"].append(record)
            progress["feedback"] = record
            progress["attempt"] += 1
            save(progress_path, progress)
            continue
        except (LoopBlocked, RuntimeError, ValueError, OSError, subprocess.SubprocessError, TimeoutError):
            # Keep candidate changes reviewable, then restore the input baseline.
            save(attempt / "blocked-candidate-files.json",
                 {path: Path(path).read_text() if Path(path).exists() else None for path in originals})
            for path, text in originals.items():
                Path(path).write_text(text)
            raise
        for filename in ("patcher.diff", "infrastructure.diff", "infrastructure.json", "proposal.json"):
            if (attempt / filename).exists():
                shutil.copy2(attempt / filename, round_dir / filename)
        save(approved, {"attempt": str(attempt), "proposal": proposal,
                       "file_hashes": {path: hashlib.sha256(Path(path).read_bytes()).hexdigest()
                                       for path in originals}})
        return proposal


def run(config_path, *, full_approved=False):
    try:
        return run_inner(config_path, full_approved=full_approved)
    except (LoopBlocked, RuntimeError, ValueError, OSError, subprocess.SubprocessError, TimeoutError) as exc:
        config = load_config(config_path)
        state_path = Path(config["work_root"]) / "loop-state.json"
        if not state_path.exists():
            raise
        state = json.loads(state_path.read_text())
        blocker = {"kind": getattr(exc, "kind", "infrastructure"),
                   "reason": str(exc), "evidence": getattr(exc, "evidence", None)}
        state["status"] = "needs_human" if blocker["kind"] == "human_review" else "blocked"
        state["blocker"] = blocker
        save(Path(config["work_root"]) / "blocked.json", blocker)
        save(state_path, state)
        print(f"Loop stopped ({blocker['kind']}): {blocker['reason']}")


def repair_preparation_failure(config, state, state_path, round_dir, feedback):
    """Return patch-generation failures to agents using the last valid pilot."""
    if not state.get("history"):
        raise LoopBlocked("human_review", "patch preparation failed before any valid pilot evidence", str(round_dir))
    prior = Path(state["history"][-1]["outcome"]).parent
    job = json.loads((prior / "job.json").read_text())
    source = prior / "source"
    if not source.exists():
        raise LoopBlocked("infrastructure", "previous pilot source is missing for preparation repair", str(prior))
    directory = round_dir / "preparation-repair"
    directory.mkdir(parents=True, exist_ok=True)
    for filename in ("patcher-before.txt", "infrastructure-before.json", "docker-image-audit.json"):
        original = prior / filename
        if original.exists() and not (directory / filename).exists():
            shutil.copy2(original, directory / filename)
    save(directory / "preparation-feedback.json", feedback)
    outcome = json.loads((prior / "outcome.json").read_text())
    proposal = repair(config, outcome, source, Path(job["submission"]) / "report",
                      directory, initial_feedback=feedback)
    state["last_proposal"] = proposal
    state["pending_discards"] = [item["task_id"] if isinstance(item, dict) else item
                                 for item in proposal.get("discards", [])]
    state["last_diff"] = "\n".join(path.read_text() for path in
        (directory / "patcher.diff", directory / "infrastructure.diff") if path.exists())
    state["last_repair_types"] = (["task"] if (directory / "patcher.diff").exists() else []) + (
        ["infrastructure"] if (directory / "infrastructure.json").exists() else [])
    state.pop("pending_repair_feedback", None)
    state["iteration"] += 1
    save(state_path, state)


def run_inner(config_path, *, full_approved=False):
    config = load_config(config_path)
    work = Path(config["work_root"])
    work.mkdir(parents=True, exist_ok=True)
    state_path = work / "loop-state.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {
        "config_sha256": hashlib.sha256(Path(config_path).read_bytes()).hexdigest(),
        "dataset": config["dataset"], "wave": 0, "iteration": 0,
        "selected_ids": [], "status": "running", "history": [], "discards": []}
    if state["config_sha256"] != hashlib.sha256(Path(config_path).read_bytes()).hexdigest():
        raise ValueError("configuration changed after loop start; use a new work_root")
    if state["status"] == "awaiting_human_review" and not full_approved:
        print(f"Awaiting patcher/discard review: {work / 'human-review.json'}")
        return
    if full_approved:
        if state["status"] != "awaiting_human_review":
            raise ValueError("full run can start only after the 200-task pilot passes")
        state["human_approved"] = True
        state["status"] = "running"
        save(state_path, state)
    while state["status"] == "running":
        wave = state["wave"]
        full = wave == len(WAVES)
        if full and not state.get("human_approved"):
            state["status"] = "awaiting_human_review"
            save(work / "human-review.json", {"patcher": config["patcher"],
                "validation_policy": validation_policy(config),
                "history": state["history"], "discards": state["discards"],
                "retained": len(state["selected_ids"])})
            save(state_path, state)
            print(f"Awaiting human review: {work / 'human-review.json'}")
            return
        round_dir = work / ("full" if full else f"wave-{WAVES[wave]}") / f"iteration-{state['iteration']:02d}"
        round_dir.mkdir(parents=True, exist_ok=True)
        if state.get("pending_repair_feedback"):
            repair_preparation_failure(config, state, state_path, round_dir, state["pending_repair_feedback"])
            continue
        try:
            source = materialize_source(config, round_dir)
        except subprocess.CalledProcessError as exc:
            if full or not state.get("history"):
                raise
            feedback = {"kind": "patch_preparation_failure", "reason": str(exc),
                        "patch_log": str(round_dir / "patch.log"),
                        "required_changes": ["Inspect the failed patcher command and reconcile the rule, scope and assertions. Do not change expected counts without evidence. Classify missing credentials or unrecoverable infrastructure as blockers."]}
            state["pending_repair_feedback"] = feedback
            save(state_path, state)
            repair_preparation_failure(config, state, state_path, round_dir, feedback)
            continue
        available = task_ids(source)
        if not full and not state.get("wave_selected"):
            new = choose_new(available, state["selected_ids"], WAVES[wave], config["seed"], wave)
            state["selected_ids"] += new
            state["wave_selected"] = True
            save(state_path, state)
        selected = available if full else state["selected_ids"]
        missing = set(selected) - set(available)
        if missing:
            declared = set(state.get("pending_discards", []))
            if not missing <= declared:
                raise RuntimeError(f"patcher unexpectedly dropped tasks: {sorted(missing)}")
            state["discards"] += [{"task_id": task, "wave": wave,
                                   "iteration": state["iteration"]} for task in sorted(missing)]
            selected = [task for task in selected if task not in missing]
            state["selected_ids"] = selected
            state["pending_discards"] = []
            save(state_path, state)
        if not selected:
            raise RuntimeError("no retained tasks to validate")
        job_path = round_dir / "job.json"
        if job_path.exists():
            job = json.loads(job_path.read_text())
        else:
            job = submit(config, source, selected, round_dir, full=full)
            save(job_path, job)
        report = wait_for_job(job, config["poll_seconds"], config.get("max_wait_hours", 24))
        policy = validation_policy(config)
        outcome = (summarize(report, selected) if policy["required_stages"] == list(STAGES)
                   else summarize(report, selected, required_stages=policy["required_stages"]))
        outcome["validation_policy"] = policy
        save(round_dir / "outcome.json", outcome)
        if not any(entry["wave"] == wave and entry["iteration"] == state["iteration"]
                   for entry in state["history"]):
            state["history"].append({"wave": wave, "iteration": state["iteration"],
                "job_id": job["job_id"], "outcome": str(round_dir / "outcome.json"),
                "passed_all": outcome["passed_all"], "tasks": len(selected)})
            save(state_path, state)
        check_credentials(outcome)
        outcome_feedback = None
        previous_entry = next((entry for entry in reversed(state["history"])
                               if entry["wave"] == wave and entry["iteration"] < state["iteration"]), None)
        if previous_entry:
            previous = Path(previous_entry["outcome"])
            comparison = before_after(json.loads(previous.read_text()), outcome)
            comparison["full_source_effect"] = source_effect(
                previous.parent / "source-hashes.json", round_dir / "source-hashes.json")
            prior_audit = previous.parent / "docker-image-audit.json"
            current_audit = round_dir / "docker-image-audit.json"
            if prior_audit.is_file() and current_audit.is_file():
                old, new = json.loads(prior_audit.read_text()), json.loads(current_audit.read_text())
                comparison["image_reuse"] = {key: {"before": old[key], "after": new[key]}
                    for key in ("tasks", "unique_dockerfiles", "unique_build_payloads",
                                "unique_base_sequences", "tasks_with_setup_files")}
            save(round_dir / "before-after.json", comparison)
            effect = comparison["full_source_effect"]
            review_effect = {**effect, "changed_count": len(effect["changed_ids"]),
                             "changed_ids": effect["changed_ids"][:100]}
            review_context = {
                "before_after": {**comparison, "full_source_effect": review_effect},
                "full_comparison": str(round_dir / "before-after.json"),
                "lesson_targets": lesson_targets(comparison, state.get("last_repair_types", [])),
                "proposal": state.get("last_proposal"),
                "patcher_diff": state.get("last_diff", ""),
                "current_report": str(report)}
            if "image_reuse" in comparison:
                review_context["image_reuse_guidance"] = (
                    "Assess changes in unique Dockerfiles, build payloads, and bases together "
                    "with stage 3, oracle, and post-setup NOP results. Reject setup moves that "
                    "change task behavior or weaken checks; a higher count requires an explanation.")
            try:
                review_result = review(config, review_context, round_dir, "review-outcome")
                if comparison["passed_all_delta_on_retained"] < 0:
                    raise ReviewRejected({"approved": False, "required_changes": [
                        "Repair the regression on the retained pilot tasks."]}, round_dir / "before-after.json")
            except ReviewRejected as exc:
                outcome_feedback = {"review": exc.result, "evidence": exc.evidence}
                save(round_dir / "outcome-review-rejection.json", outcome_feedback)
            else:
                append_confirmed(comparison, review_result.get("lessons", []),
                                 dataset=config["dataset"], wave=wave, iteration=state["iteration"],
                                 evidence=round_dir / "before-after.json", repair_record=previous.parent,
                                 repair_types=state.get("last_repair_types", []))
        current_entry = next((entry for entry in state["history"]
                              if entry["wave"] == wave and entry["iteration"] == state["iteration"]), None)
        if current_entry:
            if current_entry["outcome"] != str(round_dir / "outcome.json"):
                raise RuntimeError("saved history does not match current round")
        else:
            state["history"].append({"wave": wave, "iteration": state["iteration"],
                                     "job_id": job["job_id"], "outcome": str(round_dir / "outcome.json"),
                                     "passed_all": outcome["passed_all"], "tasks": len(selected)})
            save(state_path, state)
        if outcome["passed_all"] == len(selected) and not outcome_feedback:
            if full:
                state["status"] = "complete"
                save(state_path, state)
                print(f"Full run complete: {report}")
                return
            state.update(wave=wave + 1, iteration=0, wave_selected=False,
                         pending_discards=[], last_proposal=None, last_diff="", last_repair_types=[])
            save(state_path, state)
            continue
        if full:
            state["status"] = "full_run_findings"
            save(state_path, state)
            print(f"Full run has findings: {report}")
            return
        proposal = repair(config, outcome, source, report, round_dir, initial_feedback=outcome_feedback)
        state["pending_discards"] = [item["task_id"] if isinstance(item, dict) else item
                                     for item in proposal.get("discards", [])]
        state["last_proposal"] = proposal
        state["last_diff"] = "\n".join(path.read_text() for path in
            (round_dir / "patcher.diff", round_dir / "infrastructure.diff") if path.exists())
        state["last_repair_types"] = (["task"] if (round_dir / "patcher.diff").exists() else []) + (
            ["infrastructure"] if (round_dir / "infrastructure.json").exists() else [])
        state["iteration"] += 1
        save(state_path, state)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("run", "status", "resume", "approve-full"))
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    config = load_config(args.config)
    state_path = Path(config["work_root"]) / "loop-state.json"
    if args.command == "status":
        if not state_path.exists():
            print("No run started")
            return
        state = json.loads(state_path.read_text())
        waits = sorted(Path(config["work_root"]).glob("**/agents/*/usage-wait.json"))
        if waits:
            state["usage_waits"] = [{"path": str(path), **json.loads(path.read_text())}
                                    for path in waits]
        jobs = sorted(Path(config["work_root"]).glob("**/job.json"), key=lambda path: path.stat().st_mtime_ns)
        if jobs and state.get("status") == "running":
            job = json.loads(jobs[-1].read_text())
            result = subprocess.run(["sacct", "-X", "-n", "-P", "-j", job["job_id"],
                                     "--format", "JobIDRaw,State,Elapsed"], capture_output=True, text=True)
            state["latest_job"] = {"job_id": job["job_id"], "slurm": result.stdout.strip(),
                                   "submission": job["submission"]}
        print(json.dumps(state, indent=2))
    elif args.command == "resume":
        state = json.loads(state_path.read_text())
        if state["status"] not in ("blocked", "needs_human", "repair_limit"):
            raise ValueError("resume requires a stopped loop after resolving its blocker")
        state.setdefault("resumptions", []).append({"prior_status": state["status"],
                                                   "blocker": state.get("blocker")})
        state.update(status="running", iteration=state["iteration"] + 1,
                     pending_discards=[], last_proposal=None, last_diff="", last_repair_types=[])
        state.pop("blocker", None)
        save(state_path, state)
        run(args.config)
    else:
        run(args.config, full_approved=args.command == "approve-full")


if __name__ == "__main__":
    main()
