import json
import hashlib
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from validation.patch_repair_loop import orchestrator
from validation.patch_repair_loop import agents
from validation.patch_repair_loop import queue
from validation.patch_repair_loop.image_audit import audit as image_audit
from validation.patch_repair_loop.lessons import append_confirmed, targets
from validation.patch_repair_loop.orchestrator import choose_new, source_effect, source_hashes
from validation.patch_repair_loop.reports import before_after, summarize


def test_sample_is_disjoint_deterministic_and_uses_regression_set():
    ids = [f"task-{i:03d}" for i in range(20)]
    first = choose_new(ids, [], 4, 42, 0)
    second = choose_new(ids, first, 5, 42, 1)
    assert first == choose_new(ids, [], 4, 42, 0)
    assert len(set(first + second)) == 9


def test_small_source_uses_all_remaining_tasks_in_final_wave():
    ids = [f"task-{i:03d}" for i in range(100)]
    first = choose_new(ids, [], 10, 42, 0)
    second = choose_new(ids, first, 50, 42, 1)
    third = choose_new(ids, first + second, 200, 42, 2)
    assert len(first + second + third) == 100
    assert len(third) == 40


def test_image_audit_distinguishes_dockerfile_and_build_payload(tmp_path: Path):
    for name, fixture in (("a", "A"), ("b", "B")):
        task = tmp_path / name
        (task / "environment").mkdir(parents=True)
        (task / "task.toml").write_text('version = "1.0"\n')
        (task / "environment/Dockerfile").write_text("FROM example/base:1\nCOPY fixture /data\n")
        (task / "environment/fixture").write_text(fixture)
    (tmp_path / "a/setup_files").mkdir()
    (tmp_path / "a/setup_files/setup.sh").write_text("#!/bin/sh\n")
    result = image_audit(tmp_path)
    assert result["tasks"] == 2
    assert result["unique_dockerfiles"] == 1
    assert result["unique_build_payloads"] == 2
    assert result["unique_base_sequences"] == 1
    assert result["tasks_with_setup_files"] == 1


def test_agent_json_parser_accepts_one_fenced_object_after_prose():
    assert agents.parse_json_answer('Summary.\n```json\n{"groups": []}\n```') == {"groups": []}
    with pytest.raises(ValueError, match="exactly one"):
        agents.parse_json_answer('```json\n{}\n```\n```json\n{}\n```')


def test_skipped_oracle_is_not_a_pass_and_counts_are_comparable(tmp_path: Path):
    def report(root, stage, statuses):
        root.mkdir()
        for number in (1, 3, 4, 5):
            payload = {"stage": number, "complete": True, "contract_sha256": "abc",
                       "items": [{"task": task, "status": status if number == stage else "passed",
                                  "checks": [{"check": "example", "status": "passed"}]}
                                 for task, status in statuses.items()]}
            (root / f"stage-{number}-a.json").write_text(json.dumps(payload))

    before_dir = tmp_path / "before"
    after_dir = tmp_path / "after"
    report(before_dir, 4, {"a": "passed", "b": "skipped"})
    report(after_dir, 4, {"a": "passed", "b": "passed"})
    before = summarize(before_dir, ["a", "b"])
    after = summarize(after_dir, ["a", "b"])
    assert before["passed_all"] == 1
    assert after["passed_all"] == 2
    assert before_after(before, after)["passed_all_delta_on_retained"] == 1
    with pytest.raises(ValueError, match="incomplete"):
        summarize(before_dir, ["a", "b", "c"])


def test_skipped_static_check_is_not_a_pass(tmp_path: Path):
    for stage in (1, 3, 4, 5):
        (tmp_path / f"stage-{stage}-a.json").write_text(json.dumps({
            "stage": stage, "complete": True, "contract_sha256": "abc",
            "items": [{"task": "a", "status": "passed",
                       "checks": [{"check": "policy", "status": "skipped", "optional": True}]
                       if stage == 1 else []}]}))
    result = summarize(tmp_path, ["a"])
    assert result["passed_all"] == 0
    assert result["stage_counts"]["1"]["missing_or_skipped_check"] == 1


def test_full_source_effect_records_rule_scope(tmp_path: Path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    for root in (first, second):
        (root / "a").mkdir(parents=True)
        (root / "b").mkdir()
        for task in ("a", "b"):
            (root / task / "task.toml").write_text('version = "1.0"\n')
    (second / "b" / "task.toml").write_text('version = "1.0"\n# rule applied\n')
    before_path = tmp_path / "before.json"
    after_path = tmp_path / "after.json"
    before_path.write_text(json.dumps(source_hashes(first)))
    after_path.write_text(json.dumps(source_hashes(second)))
    effect = source_effect(before_path, after_path)
    assert effect["changed_ids"] == ["b"]
    assert effect["before_count"] == effect["after_count"] == 2


def test_controller_stops_for_human_review_before_full_run(tmp_path: Path, monkeypatch):
    config_path = tmp_path / "config.json"
    config_path.write_text("{}")
    work = tmp_path / "work"
    ids = [f"task-{i:04d}" for i in range(300)]
    config = {"work_root": str(work), "seed": 42, "poll_seconds": 5,
              "dataset": "example", "patcher": str(tmp_path / "patch.py"),
              "max_repairs_per_wave": 2}
    submitted = []
    monkeypatch.setattr(orchestrator, "load_config", lambda _: config)
    monkeypatch.setattr(orchestrator, "materialize_source", lambda _, __: tmp_path)
    monkeypatch.setattr(orchestrator, "task_ids", lambda _: ids)

    def submit(_, source, selected, round_dir, *, full):
        submitted.append((len(selected), full))
        return {"job_id": str(len(submitted)), "submission": str(round_dir),
                "selected_ids": selected}

    monkeypatch.setattr(orchestrator, "submit", submit)
    monkeypatch.setattr(orchestrator, "wait_for_job", lambda job, poll, hours: Path(job["submission"]))
    monkeypatch.setattr(orchestrator, "summarize", lambda report, selected: {
        "passed_all": len(selected), "stage_counts": {}, "check_counts": {},
        "per_task": {task: {"1": "passed", "3": "passed", "4": "passed", "5": "passed"}
                     for task in selected}})

    orchestrator.run(config_path)
    state = json.loads((work / "loop-state.json").read_text())
    assert state["status"] == "awaiting_human_review"
    assert submitted == [(10, False), (60, False), (260, False)]
    orchestrator.run(config_path, full_approved=True)
    state = json.loads((work / "loop-state.json").read_text())
    assert state["status"] == "complete"
    assert submitted[-1] == (300, True)


def test_rejected_repairs_feed_back_without_limit_and_resume_approval(tmp_path, monkeypatch):
    patcher = tmp_path / "patch.py"
    patcher.write_text("baseline")
    config = {"patcher": str(patcher), "max_repairs_per_wave": 1}
    round_dir = tmp_path / "round"
    round_dir.mkdir()
    monkeypatch.setattr(orchestrator, "diagnose", lambda *args: [{"category": "task"}])
    seen = []

    def repair_once(config, outcome, source, report, attempt, groups, feedback):
        seen.append(feedback)
        if len(seen) <= 12:
            raise orchestrator.ReviewRejected({"approved": False,
                "required_changes": [f"fix-{len(seen)}"]}, attempt)
        patcher.write_text("approved correction")
        (attempt / "patcher.diff").write_text("baseline -> approved correction")
        return {"rules": ["general-rule"], "discards": []}

    monkeypatch.setattr(orchestrator, "repair_once", repair_once)
    result = orchestrator.repair(config, {"failures": []}, tmp_path, tmp_path, round_dir)
    assert len(seen) == 13
    assert seen[1]["review"]["required_changes"] == ["fix-1"]
    assert seen[-1]["review"]["required_changes"] == ["fix-12"]
    assert len(list((round_dir / "repair-attempts").glob("*/rejection.json"))) == 12
    assert orchestrator.repair(config, {}, tmp_path, tmp_path, round_dir) == result
    assert len(seen) == 13


@pytest.mark.parametrize("structured", [True, False])
def test_unrecoverable_infrastructure_stops_and_preserves_candidate(tmp_path, monkeypatch, structured):
    patcher = tmp_path / "patch.py"
    patcher.write_text("baseline")
    round_dir = tmp_path / "round"
    round_dir.mkdir()
    monkeypatch.setattr(orchestrator, "diagnose", lambda *args: [{"category": "infrastructure"}])

    def blocked(*args):
        patcher.write_text("unreviewed")
        if structured:
            raise orchestrator.LoopBlocked("infrastructure", "unavailable external service", "log")
        raise RuntimeError("unavailable external service")

    monkeypatch.setattr(orchestrator, "repair_once", blocked)
    with pytest.raises((orchestrator.LoopBlocked, RuntimeError), match="unavailable external service"):
        orchestrator.repair({"patcher": str(patcher)}, {"failures": []}, tmp_path, tmp_path, round_dir)
    assert patcher.read_text() == "baseline"
    saved = json.loads((round_dir / "repair-attempts/attempt-0000/blocked-candidate-files.json").read_text())
    assert saved[str(patcher)] == "unreviewed"


def test_speculative_infrastructure_retry_stops_before_review_or_submission(tmp_path, monkeypatch):
    monkeypatch.setattr(orchestrator, "agent_call", lambda *args, **kwargs:
        json.dumps({"action": "retry_only", "reason": "service remains unavailable"}))
    monkeypatch.setattr(orchestrator, "review", lambda *args: pytest.fail("must not review a blind retry"))
    with pytest.raises(orchestrator.LoopBlocked, match="no verified recovery prerequisite"):
        orchestrator.repair_once({"dataset": "test"}, {}, tmp_path, tmp_path, tmp_path,
                                 [{"category": "infrastructure"}], None)


@pytest.mark.parametrize("without_oracle", [False, True])
def test_missing_gptzero_key_does_not_prevent_slurm_submission(tmp_path, monkeypatch, without_oracle):
    monkeypatch.delenv("GPTZERO_API_KEY", raising=False)
    calls = []
    def submit(command, **kwargs):
        calls.append(command)
        if "--prepare-contract" in command:
            destination = Path(command[command.index("--prepare-contract") + 1])
            destination.write_text(json.dumps({"success_criteria": {
                "static_checks": ["check_ai_detection.py"]}}))
        else:
            destination = tmp_path / "results/submissions/example"
            destination.mkdir(parents=True)
            (destination / "submission.json").write_text(json.dumps({
                "status": "submitted", "job_id": "123"}))
    monkeypatch.setattr(orchestrator.subprocess, "run", submit)
    cfg = {"python": "python", "partition": "cpu", "cpus": 1, "memory": "1G",
           "concurrency": 1, "time": "00:10:00", "network_mode": "host",
           "dataset": "example", "source_revision": "pinned", "workspace": str(tmp_path)}
    if without_oracle:
        cfg.update(required_stages=[1, 3, 5], validation_policy_reason="human approval",
                   static_exclusions={"check-test-file-references.sh": "no reference solution"})
    result = orchestrator.submit(cfg, tmp_path, ["task"], tmp_path, full=True)
    assert calls[0][calls[0].index("--stages") + 1] == ("1,3,5" if without_oracle else "1,3,4,5")
    if without_oracle:
        assert "check-test-file-references.sh=no reference solution" in calls[0]
    assert result["job_id"] == "123"
    assert len(calls) == 2 and "--contract" in calls[1]


@pytest.mark.parametrize("ai_status,optional,accepted", [
    ("skipped", True, True), ("skipped", False, False), ("failed", True, False)])
def test_only_optional_gptzero_skip_is_non_failing(tmp_path, ai_status, optional, accepted):
    for stage in (1, 3, 4, 5):
        checks = [{"check": "required", "status": "passed"},
                  {"check": "check_ai_detection.py", "status": ai_status,
                   "optional": optional, "reason": "no GPTZERO_API_KEY configured"}]
        (tmp_path / f"stage-{stage}-test.json").write_text(json.dumps({
            "stage": stage, "complete": True, "contract_sha256": "abc",
            "items": [{"task": "a", "status": "passed", "checks": checks if stage == 1 else []}]}))
    outcome = summarize(tmp_path, ["a"])
    assert outcome["passed_all"] == int(accepted)
    if accepted:
        assert outcome["failures"] == []
        assert outcome["skipped_optional_checks"][0]["status"] == "skipped"
        orchestrator.check_credentials(outcome)


def test_missing_credentials_save_blocked_state_without_repair(tmp_path, monkeypatch):
    config_path = tmp_path / "config.json"
    config_path.write_text("{}")
    work = tmp_path / "work"
    config = {"work_root": str(work), "seed": 42, "poll_seconds": 5,
              "dataset": "example", "patcher": str(tmp_path / "patch.py")}
    monkeypatch.setattr(orchestrator, "load_config", lambda _: config)
    monkeypatch.setattr(orchestrator, "materialize_source", lambda *args: tmp_path)
    monkeypatch.setattr(orchestrator, "task_ids", lambda _: ["task"])
    monkeypatch.setattr(orchestrator, "submit", lambda *args, **kwargs:
                        {"job_id": "1", "submission": str(tmp_path)})
    monkeypatch.setattr(orchestrator, "wait_for_job", lambda *args: tmp_path)
    monkeypatch.setattr(orchestrator, "summarize", lambda *args:
        {"passed_all": 0, "failures": [{"detail": "no REQUIRED_API_KEY configured", "evidence": "report"}]})
    monkeypatch.setattr(orchestrator, "repair", lambda *args, **kwargs: pytest.fail("must not repair"))
    orchestrator.run(config_path)
    state = json.loads((work / "loop-state.json").read_text())
    assert state["status"] == "blocked" and state["blocker"]["kind"] == "credentials"
    assert len(state["history"]) == 1
    orchestrator.run(config_path)  # A blocked run stays stopped, without a resubmission.
    assert len(json.loads((work / "loop-state.json").read_text())["history"]) == 1


def test_unlimited_pilot_rounds_and_rejected_outcome_cannot_advance_wave(tmp_path, monkeypatch):
    config_path = tmp_path / "config.json"
    config_path.write_text("{}")
    work = tmp_path / "work"
    config = {"work_root": str(work), "seed": 42, "poll_seconds": 5,
              "dataset": "example", "patcher": str(tmp_path / "patch.py"),
              "max_repairs_per_wave": 1}
    monkeypatch.setattr(orchestrator, "load_config", lambda _: config)
    monkeypatch.setattr(orchestrator, "materialize_source", lambda *args: tmp_path)
    monkeypatch.setattr(orchestrator, "task_ids", lambda _: [f"task-{n}" for n in range(300)])
    submitted, feedbacks = [], []

    def submit(config, source, selected, round_dir, **kwargs):
        submitted.append(len(selected))
        return {"job_id": str(len(submitted)), "submission": str(round_dir)}

    def summarize(report, selected):
        passed = len(submitted) > 10
        return {"passed_all": len(selected) if passed else 0,
                "stage_counts": {}, "check_counts": {}, "failures": [],
                "per_task": {task: {str(n): "passed" if passed or n != 1 else "failed"
                                   for n in (1, 3, 4, 5)} for task in selected}}

    rejected = []
    def review(config, context, round_dir, label):
        if context["before_after"]["after"]["passed_all"] and not rejected:
            rejected.append(True)
            raise orchestrator.ReviewRejected({"approved": False,
                "required_changes": ["verify outside-pilot scope"]}, round_dir)
        return {"approved": True, "lessons": []}

    def repair(*args, initial_feedback=None):
        feedbacks.append(initial_feedback)
        return {"rules": [], "discards": []}

    monkeypatch.setattr(orchestrator, "submit", submit)
    monkeypatch.setattr(orchestrator, "wait_for_job", lambda job, *args: Path(job["submission"]))
    monkeypatch.setattr(orchestrator, "summarize", summarize)
    monkeypatch.setattr(orchestrator, "review", review)
    monkeypatch.setattr(orchestrator, "repair", repair)
    monkeypatch.setattr(orchestrator, "source_effect", lambda *args:
                        {"changed_ids": [], "removed_ids": [], "added_ids": []})
    monkeypatch.setattr(orchestrator, "append_confirmed", lambda *args, **kwargs: None)
    orchestrator.run(config_path)
    assert submitted == [10] * 12 + [60, 260]
    assert len(feedbacks) == 11
    assert feedbacks[-1]["review"]["required_changes"] == ["verify outside-pilot scope"]
    assert json.loads((work / "loop-state.json").read_text())["status"] == "awaiting_human_review"


def test_confirmed_rule_goes_into_stage_skill_with_evidence(tmp_path: Path):
    root = tmp_path / "skills"
    (root / "stage-3").mkdir(parents=True)
    (root / "stage-3/SKILL.md").write_text("# Stage 3\n")
    comparison = {"same_retained_tasks": {
        "before": {"stage_counts": {str(n): {"passed": 0} for n in (1, 3, 4, 5)}},
        "after": {"stage_counts": {str(n): {"passed": 2 if n == 3 else 0}
                                   for n in (1, 3, 4, 5)}}},
        "full_source_effect": {"changed_ids": ["a", "b"], "removed_ids": [], "added_ids": []}}
    assert targets(comparison) == [3]
    entry = {"stage": 3, "failure_signature": "missing repo workdir",
             "cause": "bridge mounted over an absent path", "general_rule": "use a common root workdir",
             "match_predicate": "tasks referencing the affected base image",
             "limits": "other images need their own probe"}
    paths = append_confirmed(comparison, [entry], dataset="source", wave=0, iteration=1,
                             evidence=tmp_path / "comparison.json", repair_record=tmp_path / "repair",
                             skill_root=root)
    assert paths == [str(root / "stage-3/SKILL.md")]
    text = (root / "stage-3/SKILL.md").read_text()
    assert "missing repo workdir" in text
    assert "use a common root workdir" in text
    assert "0 → 2" in text
    assert "2 changed" in text
    append_confirmed(comparison, [entry], dataset="source", wave=0, iteration=1,
                     evidence=tmp_path / "comparison.json", repair_record=tmp_path / "repair",
                     skill_root=root)
    assert (root / "stage-3/SKILL.md").read_text().count("### source — wave 0, repair 1") == 1


def test_usage_limit_waits_then_retries_and_caches_answer(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(agents, "prompt_for", lambda role, context: "diagnose")
    waited = []
    monkeypatch.setattr(agents, "wait_until", lambda when: waited.append(when))
    calls = []

    def run(*args, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return SimpleNamespace(returncode=1, stdout="",
                                   stderr="5-hour limit reached - resets 3pm")
        return SimpleNamespace(returncode=0, stdout='{"groups": []}', stderr="")

    monkeypatch.setattr(agents.subprocess, "run", run)
    options = {"provider": "claude", "repo": tmp_path, "output_dir": tmp_path / "agent",
               "model": "sonnet", "usage_retry_seconds": 10}
    assert agents.invoke("diagnose", {}, **options) == '{"groups": []}'
    assert len(calls) == 2 and len(waited) == 1
    assert not (tmp_path / "agent/usage-wait.json").exists()
    assert agents.invoke("diagnose", {}, **options) == '{"groups": []}'
    assert len(calls) == 2


def test_usage_wait_survives_process_restart(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(agents, "prompt_for", lambda role, context: "diagnose")
    responses = iter([SimpleNamespace(returncode=1, stdout="", stderr="usage limit reached"),
                      SimpleNamespace(returncode=0, stdout='{"groups": []}', stderr="")])
    monkeypatch.setattr(agents.subprocess, "run", lambda *args, **kwargs: next(responses))
    options = {"provider": "claude", "repo": tmp_path, "output_dir": tmp_path / "agent",
               "usage_retry_seconds": 10}
    monkeypatch.setattr(agents, "wait_until", lambda when: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        agents.invoke("diagnose", {}, **options)
    assert (tmp_path / "agent/usage-wait.json").exists()
    monkeypatch.setattr(agents, "wait_until", lambda when: None)
    assert agents.invoke("diagnose", {}, **options) == '{"groups": []}'


def test_invalid_agent_answer_retries_and_only_caches_valid_json(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(agents, "prompt_for", lambda role, context: "diagnose")
    responses = iter([SimpleNamespace(returncode=0, stdout="unrelated notice", stderr=""),
                      SimpleNamespace(returncode=0, stdout='{"groups": []}', stderr="")])
    monkeypatch.setattr(agents.subprocess, "run", lambda *args, **kwargs: next(responses))
    output = tmp_path / "agent"
    answer = agents.invoke("diagnose", {}, provider="claude", repo=tmp_path, output_dir=output)
    assert answer == '{"groups": []}'
    assert json.loads((output / "completed.json").read_text())["attempt"] == 2
    assert "Return exactly one JSON object" in (output / "attempt-0002.prompt.txt").read_text()
    assert not (output / "protocol-error.txt").exists()


def test_usage_limit_after_partial_edit_stops_for_inspection(tmp_path: Path, monkeypatch):
    patcher = tmp_path / "patch.py"
    patcher.write_text("before")
    monkeypatch.setattr(agents, "prompt_for", lambda role, context: "implement")

    def run(*args, **kwargs):
        patcher.write_text("partial")
        return SimpleNamespace(returncode=1, stdout="", stderr="usage limit reached")

    monkeypatch.setattr(agents.subprocess, "run", run)
    with pytest.raises(RuntimeError, match="partial edit"):
        agents.invoke("implement", {}, provider="claude", repo=tmp_path,
                      output_dir=tmp_path / "agent", watched_paths=[patcher])
    assert (tmp_path / "agent/partial-edit.json").exists()


def test_role_specific_model_overrides_default(tmp_path: Path, monkeypatch):
    chosen = []
    monkeypatch.setattr(orchestrator, "invoke", lambda *args, **kwargs: chosen.append(kwargs["model"]))
    config = {"agent_provider": "claude", "agent_model": "sonnet",
              "agent_models": {"propose_rule": "opus", "review": "opus"}}
    for role in ("diagnose", "propose_rule", "implement", "review"):
        orchestrator.agent_call(config, role, {}, tmp_path, role)
    assert chosen == ["sonnet", "opus", "sonnet", "opus"]


def test_codex_agent_uses_explicit_model_and_reasoning_effort(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(agents, "prompt_for", lambda role, context: "diagnose")
    seen = []

    def run(command, **kwargs):
        seen.append(command)
        return SimpleNamespace(returncode=0, stdout='{"groups": []}', stderr="")

    monkeypatch.setattr(agents.subprocess, "run", run)
    output = tmp_path / "agent"
    (output).mkdir()
    # Codex writes the last message to this file itself.
    def run_with_answer(command, **kwargs):
        Path(command[command.index("--output-last-message") + 1]).write_text('{"groups": []}')
        return run(command, **kwargs)

    monkeypatch.setattr(agents.subprocess, "run", run_with_answer)
    agents.invoke("diagnose", {}, provider="codex", repo=tmp_path, output_dir=output,
                  model="gpt-6-astra", reasoning_effort="xhigh")
    assert ["--model", "gpt-6-astra"] == seen[0][seen[0].index("--model"):seen[0].index("--model") + 2]
    assert 'model_reasoning_effort="xhigh"' in seen[0]
    agents.invoke("implement", {}, provider="codex", repo=tmp_path,
                  output_dir=tmp_path / "implement", model="gpt-6-astra",
                  reasoning_effort="medium")
    assert "--approve-for-me" in seen[1] and "--sandbox" not in seen[1]


def test_namespace_failure_is_not_cached_as_completed_analysis(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(agents, "prompt_for", lambda role, context: "diagnose")
    monkeypatch.setattr(agents.subprocess, "run", lambda *args, **kwargs:
        SimpleNamespace(returncode=0, stdout='{"groups": []}',
                        stderr="bwrap: Creating new namespace failed: ENOSPC\n"))
    output = tmp_path / "agent"
    with pytest.raises(RuntimeError, match="shell inspection was blocked"):
        agents.invoke("diagnose", {}, provider="codex", repo=tmp_path, output_dir=output)
    assert (output / "runtime-blocker.json").is_file()
    assert not (output / "completed.json").exists()


def test_completed_implementation_can_resume_old_round_but_new_edits_cannot(tmp_path: Path):
    patcher = tmp_path / "patch.py"
    patcher.write_text("original")
    old_hash = hashlib.sha256(patcher.read_bytes()).hexdigest()
    round_dir = tmp_path / "round"
    (round_dir / "source").mkdir(parents=True)
    (round_dir / "materialized.json").write_text(json.dumps({
        "patcher_sha256": old_hash, "source_revision": "pinned"}))
    patcher.write_text("agent repair")
    new_hash = hashlib.sha256(patcher.read_bytes()).hexdigest()
    completed = round_dir / "agents/implement/completed.json"
    completed.parent.mkdir(parents=True)
    completed.write_text(json.dumps({"watched_after": {str(patcher): new_hash}}))
    config = {"patcher": str(patcher), "source_revision": "pinned"}
    assert orchestrator.materialize_source(config, round_dir) == round_dir / "source"
    patcher.write_text("unrecorded edit")
    with pytest.raises(RuntimeError, match="patcher or source changed"):
        orchestrator.materialize_source(config, round_dir)


def test_materialization_recovers_empty_output_and_imports_repo_package(tmp_path: Path):
    patcher = tmp_path / "patch.py"
    patcher.write_text('''import argparse
from data.scaleswe import patch
p = argparse.ArgumentParser()
p.add_argument("--source")
p.add_argument("--output")
a = p.parse_args()
from pathlib import Path
t = Path(a.output) / "test-task"
t.mkdir()
(t / "task.toml").write_text('version = "1.0"\\n')
''')
    round_dir = tmp_path / "round"
    (round_dir / "source").mkdir(parents=True)
    config = {"source": str(tmp_path / "input"), "patcher": str(patcher),
              "python": sys.executable, "source_revision": "pinned",
              "patch_command": ["{python}", "{patcher}", "--source", "{source}",
                                "--output", "{output}"]}
    assert orchestrator.materialize_source(config, round_dir) == round_dir / "source"
    assert (round_dir / "materialized.json").is_file()


@pytest.mark.parametrize("previously_stopped", [False, True])
def test_queue_tracks_external_loop_and_continues_next_dataset(tmp_path: Path, monkeypatch, previously_stopped):
    manifest = tmp_path / "queue.json"
    manifest.write_text(json.dumps({"work_root": str(tmp_path / "work"),
                                    "entries": [{"id": "facet", "wait_for_existing": True,
                                                 "tmux_session": "facet"}, {"id": "next"}]}))
    if previously_stopped:
        orchestrator.save(tmp_path / "work/queue-state.json", {
            "entries": {"facet": {"status": "needs_human"}}})
    monkeypatch.setattr(queue, "create_config", lambda entry, settings: (tmp_path / "config", None))
    monkeypatch.setattr(queue, "loop_state", lambda config: {"status": "running"})
    called = []

    def run(command, **kwargs):
        called.append(command)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(queue.subprocess, "run", run)
    queue.run_queue(manifest, once=True)
    saved = json.loads((tmp_path / "work/queue-state.json").read_text())
    assert saved["status"] == "waiting_for_external"
    assert saved["entries"]["facet"]["status"] == "running_external"
    assert saved["entries"]["next"]["status"] == "needs_human"
    assert called[0] == ["tmux", "has-session", "-t", "facet"]
    assert "validation.patch_repair_loop.orchestrator" in called[1]


def test_queue_continues_after_review_gate_and_records_missing_adapters(tmp_path: Path, monkeypatch):
    manifest = tmp_path / "queue.json"
    manifest.write_text(json.dumps({"work_root": str(tmp_path / "work"),
        "entries": [{"id": "facet"}, {"id": "missing"}, {"id": "next"}]}))
    monkeypatch.setattr(queue, "create_config", lambda entry, settings:
                        (None, "full source missing") if entry["id"] == "missing"
                        else (tmp_path / entry["id"], None))
    monkeypatch.setattr(queue, "loop_state", lambda config: {"status": "awaiting_human_review"}
                        if Path(config).name == "facet" else None)
    monkeypatch.setattr(queue.subprocess, "run", lambda command, **kwargs:
                        SimpleNamespace(returncode=1))
    queue.run_queue(manifest, once=True)
    saved = json.loads((tmp_path / "work/queue-state.json").read_text())
    assert saved["entries"]["facet"]["status"] == "awaiting_human_review"
    assert saved["entries"]["missing"]["status"] == "needs_preparation"
    assert saved["entries"]["next"]["status"] == "needs_human"


def test_oracle_omission_is_explicit_and_nop_still_required(tmp_path):
    for stage in (1, 3, 5):
        (tmp_path / f'stage-{stage}-test.json').write_text(json.dumps({
            'stage': stage, 'complete': True, 'contract_sha256': 'abc',
            'items': [{'task': 'a', 'status': 'passed',
                       'checks': [{'check': 'required', 'status': 'passed'}] if stage == 1 else []}]}))
    with pytest.raises(ValueError, match='stage 4'):
        summarize(tmp_path, ['a'])
    outcome = summarize(tmp_path, ['a'], required_stages=[1, 3, 5])
    assert outcome['passed_all'] == 1 and not outcome['oracle_validated']
    assert outcome['stage_counts']['4'] == {'not_evaluated': 1}
    assert outcome['per_task']['a']['4'] == 'not_evaluated'
    (tmp_path / 'stage-5-test.json').unlink()
    with pytest.raises(ValueError, match='stage 5'):
        summarize(tmp_path, ['a'], required_stages=[1, 3, 5])


def test_policy_change_does_not_claim_oracle_repair_or_write_lessons():
    before = {'per_task': {'a': {'1': 'passed', '3': 'passed', '4': 'skipped', '5': 'passed'}},
              'stage_counts': {'1': {'passed': 1}, '3': {'passed': 1},
                               '4': {'skipped': 1}, '5': {'passed': 1}},
              'check_counts': {}, 'passed_all': 0, 'required_stages': [1, 3, 4, 5]}
    after = {**before, 'required_stages': [1, 3, 5], 'passed_all': 1,
             'per_task': {'a': {**before['per_task']['a'], '4': 'not_evaluated'}},
             'stage_counts': {**before['stage_counts'], '4': {'not_evaluated': 1}}}
    comparison = before_after(before, after)
    assert comparison['policy_changed']
    assert comparison['passed_all_delta_on_retained'] == 0
    assert targets(comparison) == []


def test_custom_validation_policy_requires_recorded_human_reason():
    with pytest.raises(ValueError, match='validation_policy_reason'):
        orchestrator.validation_policy({'required_stages': [1, 3, 5]})
    with pytest.raises(ValueError, match='required_stages'):
        orchestrator.validation_policy({'required_stages': [3]})
    assert orchestrator.validation_policy({})['oracle_validated']


def test_submit_restart_preserves_old_contract_tree_and_recovers_existing_job(tmp_path, monkeypatch):
    old_tree = tmp_path / 'contract.stage1-tasks'
    old_tree.mkdir()
    (old_tree / 'evidence').write_text('preserved')
    (tmp_path / 'contract.json').write_text('old frozen contract')
    calls = []
    def submit(command, **kwargs):
        calls.append(command)
        if '--prepare-contract' in command:
            destination = Path(command[command.index('--prepare-contract') + 1])
            assert destination != tmp_path / 'contract.json'
            destination.write_text('{}')
        else:
            destination = tmp_path / 'results/submissions/example'
            destination.mkdir(parents=True)
            (destination / 'submission.json').write_text(json.dumps({
                'status': 'submitted', 'job_id': '123'}))
    monkeypatch.setattr(orchestrator.subprocess, 'run', submit)
    cfg = {'python': 'python', 'partition': 'cpu', 'cpus': 1, 'memory': '1G',
           'concurrency': 1, 'time': '00:10:00', 'network_mode': 'host',
           'dataset': 'example', 'source_revision': 'pinned', 'workspace': str(tmp_path)}
    first = orchestrator.submit(cfg, tmp_path, ['task'], tmp_path, full=True)
    assert (old_tree / 'evidence').read_text() == 'preserved'
    assert (tmp_path / 'contract.json').read_text() == 'old frozen contract'
    second = orchestrator.submit(cfg, tmp_path, ['task'], tmp_path, full=True)
    assert first == second and len(calls) == 2


def test_restart_reuses_prepared_contract_after_submission_failure(tmp_path, monkeypatch):
    import subprocess
    calls = []
    def failed(command, **kwargs):
        calls.append(command)
        if '--prepare-contract' in command:
            Path(command[command.index('--prepare-contract') + 1]).write_text('{}')
        else:
            raise subprocess.CalledProcessError(1, command)
    monkeypatch.setattr(orchestrator.subprocess, 'run', failed)
    cfg = {'python': 'python', 'partition': 'cpu', 'cpus': 1, 'memory': '1G',
           'concurrency': 1, 'time': '00:10:00', 'network_mode': 'host',
           'dataset': 'example', 'source_revision': 'pinned', 'workspace': str(tmp_path)}
    for _ in range(2):
        with pytest.raises(subprocess.CalledProcessError):
            orchestrator.submit(cfg, tmp_path, ['task'], tmp_path, full=True)
    assert sum('--prepare-contract' in command for command in calls) == 1


def test_claude_session_limit_is_recognized_for_existing_usage_retry():
    assert agents.USAGE_LIMIT.search("You've hit your session limit · resets 5:20pm (Europe/Berlin)")


def test_implementation_disagreement_returns_to_generalization_before_review(tmp_path, monkeypatch):
    patcher = tmp_path / 'patch.py'
    patcher.write_text('original')
    def call(config, role, *args, **kwargs):
        if role == 'propose_rule':
            return json.dumps({'rules': [], 'discards': [], 'notes': {}})
        assert role == 'implement'
        return json.dumps({'proposal_feedback': {'reason': 'scope differs',
            'evidence': ['full-source-scan.json'], 'suggested_revision': 'correct predicate'}})
    monkeypatch.setattr(orchestrator, 'agent_call', call)
    monkeypatch.setattr(orchestrator, 'review', lambda *args: pytest.fail('disagreement must return first'))
    with pytest.raises(orchestrator.ReviewRejected) as rejection:
        orchestrator.repair_once({'dataset': 'test', 'patcher': str(patcher)},
            {'stage_counts': {}}, tmp_path, tmp_path, tmp_path, [{'category': 'task'}], None)
    assert rejection.value.result['implementation_feedback']['reason'] == 'scope differs'
    assert patcher.read_text() == 'original'


def test_preparation_repair_preserves_reviewer_feedback_and_waits_for_approval(tmp_path, monkeypatch):
    prior = tmp_path / 'prior'
    prior.mkdir()
    (prior / 'source').mkdir()
    (prior / 'job.json').write_text(json.dumps({'submission': str(tmp_path / 'submission')}))
    (prior / 'outcome.json').write_text(json.dumps({'passed_all': 0, 'failures': []}))
    (prior / 'patcher-before.txt').write_text('reviewed baseline')
    state = {'history': [{'outcome': str(prior / 'outcome.json')}], 'iteration': 1,
             'pending_repair_feedback': {'review': {'required_changes': ['fix unsafe predicate']}}}
    feedback = state['pending_repair_feedback']
    def repair(config, outcome, source, report, directory, initial_feedback):
        assert state['iteration'] == 1
        assert initial_feedback == feedback
        assert source == prior / 'source'
        assert (directory / 'patcher-before.txt').read_text() == 'reviewed baseline'
        return {'rules': ['corrected'], 'discards': []}
    monkeypatch.setattr(orchestrator, 'repair', repair)
    monkeypatch.setattr(orchestrator, 'submit', lambda *args: pytest.fail('no job before approval'))
    orchestrator.repair_preparation_failure({}, state, tmp_path / 'state.json', tmp_path / 'new', feedback)
    assert state['iteration'] == 2 and 'pending_repair_feedback' not in state
    assert state['last_proposal']['rules'] == ['corrected']


def test_patch_script_failure_reaches_agents_instead_of_becoming_cluster_blocker(tmp_path, monkeypatch):
    import subprocess
    config_path = tmp_path / 'config.json'
    config_path.write_text('{}')
    work = tmp_path / 'work'
    work.mkdir()
    state = {'config_sha256': hashlib.sha256(config_path.read_bytes()).hexdigest(),
             'dataset': 'test', 'wave': 0, 'iteration': 1, 'status': 'running',
             'history': [{'outcome': str(tmp_path / 'previous/outcome.json')}],
             'discards': [], 'selected_ids': ['a']}
    orchestrator.save(work / 'loop-state.json', state)
    monkeypatch.setattr(orchestrator, 'load_config', lambda _: {'work_root': str(work)})
    monkeypatch.setattr(orchestrator, 'materialize_source', lambda *args:
                        (_ for _ in ()).throw(subprocess.CalledProcessError(1, ['patch.py'])))
    seen = []
    def repair(config, state, state_path, round_dir, feedback):
        seen.append(feedback)
        assert feedback['kind'] == 'patch_preparation_failure'
        state['status'] = 'needs_human'
    monkeypatch.setattr(orchestrator, 'repair_preparation_failure', repair)
    monkeypatch.setattr(orchestrator, 'submit', lambda *args: pytest.fail('no job before repair'))
    orchestrator.run_inner(config_path)
    assert len(seen) == 1 and 'patch.log' in seen[0]['patch_log']
