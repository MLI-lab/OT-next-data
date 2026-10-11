import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from validation.patch_repair_loop import controller
from validation.patch_repair_loop import agents
from validation.patch_repair_loop.image_audit import audit as image_audit
from validation.patch_repair_loop.reports import summarize


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
    monkeypatch.setattr(controller.subprocess, "run", submit)
    cfg = {"python": "python", "partition": "cpu", "cpus": 1, "memory": "1G",
           "concurrency": 1, "time": "00:10:00", "network_mode": "host",
           "dataset": "example", "source_revision": "pinned", "workspace": str(tmp_path)}
    if without_oracle:
        cfg.update(required_stages=[1, 3, 5], validation_policy_reason="human approval",
                   static_exclusions={"check-test-file-references.sh": "no reference solution"})
    result = controller.submit(cfg, tmp_path, ["task"], tmp_path, full=True)
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
        controller.check_credentials(outcome)



def test_usage_limit_waits_then_retries_and_caches_answer(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(agents, "prompt_for", lambda role, context: "final_failure_reviewer")
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
    assert agents.invoke("final_failure_reviewer", {}, **options) == '{"groups": []}'
    assert len(calls) == 2 and len(waited) == 1
    assert not (tmp_path / "agent/usage-wait.json").exists()
    assert agents.invoke("final_failure_reviewer", {}, **options) == '{"groups": []}'
    assert len(calls) == 2



def test_usage_wait_survives_process_restart(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(agents, "prompt_for", lambda role, context: "final_failure_reviewer")
    responses = iter([SimpleNamespace(returncode=1, stdout="", stderr="usage limit reached"),
                      SimpleNamespace(returncode=0, stdout='{"groups": []}', stderr="")])
    monkeypatch.setattr(agents.subprocess, "run", lambda *args, **kwargs: next(responses))
    options = {"provider": "claude", "repo": tmp_path, "output_dir": tmp_path / "agent",
               "usage_retry_seconds": 10}
    monkeypatch.setattr(agents, "wait_until", lambda when: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        agents.invoke("final_failure_reviewer", {}, **options)
    assert (tmp_path / "agent/usage-wait.json").exists()
    monkeypatch.setattr(agents, "wait_until", lambda when: None)
    assert agents.invoke("final_failure_reviewer", {}, **options) == '{"groups": []}'



def test_invalid_agent_answer_retries_and_only_caches_valid_json(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(agents, "prompt_for", lambda role, context: "final_failure_reviewer")
    responses = iter([SimpleNamespace(returncode=0, stdout="unrelated notice", stderr=""),
                      SimpleNamespace(returncode=0, stdout='{"groups": []}', stderr="")])
    monkeypatch.setattr(agents.subprocess, "run", lambda *args, **kwargs: next(responses))
    output = tmp_path / "agent"
    answer = agents.invoke("final_failure_reviewer", {}, provider="claude", repo=tmp_path, output_dir=output)
    assert answer == '{"groups": []}'
    assert json.loads((output / "completed.json").read_text())["attempt"] == 2
    assert "Return exactly one JSON object" in (output / "attempt-0002.prompt.txt").read_text()
    assert not (output / "protocol-error.txt").exists()



def test_usage_limit_after_partial_edit_stops_for_inspection(tmp_path: Path, monkeypatch):
    patcher = tmp_path / "patch.py"
    patcher.write_text("before")
    monkeypatch.setattr(agents, "prompt_for", lambda role, context: "implementer")

    def run(*args, **kwargs):
        patcher.write_text("partial")
        return SimpleNamespace(returncode=1, stdout="", stderr="usage limit reached")

    monkeypatch.setattr(agents.subprocess, "run", run)
    with pytest.raises(RuntimeError, match="partial edit"):
        agents.invoke("implementer", {}, provider="claude", repo=tmp_path,
                      output_dir=tmp_path / "agent", watched_paths=[patcher])
    assert (tmp_path / "agent/partial-edit.json").exists()



def test_role_specific_model_overrides_default(tmp_path: Path, monkeypatch):
    chosen = []
    monkeypatch.setattr(controller, "invoke", lambda *args, **kwargs: chosen.append(kwargs["model"]))
    config = {"agent_provider": "claude", "agent_model": "sonnet",
              "agent_models": {"proposer": "opus", "final_failure_reviewer": "opus"}}
    for role in ("fixer", "proposer", "implementer", "final_failure_reviewer"):
        controller.agent_call(config, role, {}, tmp_path, role)
    assert chosen == ["sonnet", "opus", "sonnet", "opus"]



def test_codex_agent_uses_explicit_model_and_reasoning_effort(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(agents, "prompt_for", lambda role, context: "final_failure_reviewer")
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
    agents.invoke("final_failure_reviewer", {}, provider="codex", repo=tmp_path, output_dir=output,
                  model="gpt-6-astra", reasoning_effort="xhigh")
    assert ["--model", "gpt-6-astra"] == seen[0][seen[0].index("--model"):seen[0].index("--model") + 2]
    assert 'model_reasoning_effort="xhigh"' in seen[0]
    agents.invoke("implementer", {}, provider="codex", repo=tmp_path,
                  output_dir=tmp_path / "implementer", model="gpt-6-astra",
                  reasoning_effort="medium")
    assert all("--dangerously-bypass-approvals-and-sandbox" in command for command in seen)
    assert all("--approve-for-me" not in command for command in seen)



def test_namespace_failure_is_not_cached_as_completed_analysis(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(agents, "prompt_for", lambda role, context: "final_failure_reviewer")
    monkeypatch.setattr(agents.subprocess, "run", lambda *args, **kwargs:
        SimpleNamespace(returncode=0, stdout='{"groups": []}',
                        stderr="bwrap: Creating new namespace failed: ENOSPC\n"))
    output = tmp_path / "agent"
    with pytest.raises(RuntimeError, match="shell inspection was blocked"):
        agents.invoke("final_failure_reviewer", {}, provider="codex", repo=tmp_path, output_dir=output)
    assert (output / "runtime-blocker.json").is_file()
    assert not (output / "completed.json").exists()



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
    assert controller.materialize_source(config, round_dir) == round_dir / "source"
    assert (round_dir / "materialized.json").is_file()



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



def test_custom_validation_policy_requires_recorded_human_reason():
    with pytest.raises(ValueError, match='validation_policy_reason'):
        controller.validation_policy({'required_stages': [1, 3, 5]})
    with pytest.raises(ValueError, match='required_stages'):
        controller.validation_policy({'required_stages': [3]})
    assert controller.validation_policy({})['oracle_validated']



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
    monkeypatch.setattr(controller.subprocess, 'run', submit)
    cfg = {'python': 'python', 'partition': 'cpu', 'cpus': 1, 'memory': '1G',
           'concurrency': 1, 'time': '00:10:00', 'network_mode': 'host',
           'dataset': 'example', 'source_revision': 'pinned', 'workspace': str(tmp_path)}
    first = controller.submit(cfg, tmp_path, ['task'], tmp_path, full=True)
    assert (old_tree / 'evidence').read_text() == 'preserved'
    assert (tmp_path / 'contract.json').read_text() == 'old frozen contract'
    second = controller.submit(cfg, tmp_path, ['task'], tmp_path, full=True)
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
    monkeypatch.setattr(controller.subprocess, 'run', failed)
    cfg = {'python': 'python', 'partition': 'cpu', 'cpus': 1, 'memory': '1G',
           'concurrency': 1, 'time': '00:10:00', 'network_mode': 'host',
           'dataset': 'example', 'source_revision': 'pinned', 'workspace': str(tmp_path)}
    for _ in range(2):
        with pytest.raises(subprocess.CalledProcessError):
            controller.submit(cfg, tmp_path, ['task'], tmp_path, full=True)
    assert sum('--prepare-contract' in command for command in calls) == 1



def test_claude_session_limit_is_recognized_for_existing_usage_retry():
    assert agents.USAGE_LIMIT.search("You've hit your session limit · resets 5:20pm (Europe/Berlin)")


def test_usage_metadata_is_separate_from_agent_answer():
    payload = {'type': 'result', 'result': '{"summary":"done"}',
               'usage': {'input_tokens': 100, 'cache_read_input_tokens': 40, 'output_tokens': 10},
               'total_cost_usd': 0.012, 'modelUsage': {'claude-opus-5-5': {'costUSD': 0.012}}}
    metadata = agents.usage_from_output('claude', json.dumps(payload))
    assert metadata['estimated_cost_usd'] == 0.012
    assert metadata['usage']['output_tokens'] == 10
    assert agents.parse_json_answer(metadata['answer']) == {'summary': 'done'}
    assert agents.usage_from_output('claude', '{"total_cost_usd":999}')['estimated_cost_usd'] is None
    events = '\n'.join(json.dumps(event) for event in [
        {'type': 'turn.completed', 'usage': {'input_tokens': 100, 'cached_input_tokens': 80, 'output_tokens': 10}},
        {'type': 'item.completed', 'usage': {'input_tokens': 999}},
        {'type': 'turn.completed', 'usage': {'input_tokens': 40, 'cached_input_tokens': 20, 'output_tokens': 5}}])
    assert agents.usage_from_output('codex', events)['usage'] == {
        'input_tokens': 140, 'cached_input_tokens': 100, 'output_tokens': 15}
    assert agents.usage_from_output('codex', 'incomplete output')['usage'] is None


@pytest.mark.parametrize("role", sorted(agents.ROLES))
@pytest.mark.parametrize("provider", ["claude", "codex"])
def test_all_agent_roles_run_with_full_permissions(tmp_path, monkeypatch, role, provider):
    monkeypatch.setattr(agents, "prompt_for", lambda *args: "role instructions")
    seen = []
    def run(command, **kwargs):
        seen.append(command)
        if provider == "codex":
            Path(command[command.index("--output-last-message") + 1]).write_text('{}')
        return SimpleNamespace(returncode=0, stdout='{}', stderr='')
    monkeypatch.setattr(agents.subprocess, "run", run)
    agents.invoke(role, {}, provider=provider, repo=tmp_path, output_dir=tmp_path / "agent")
    command = seen[0]
    assert ("--dangerously-skip-permissions" if provider == "claude" else
            "--dangerously-bypass-approvals-and-sandbox") in command
    assert not {"--permission-mode", "--permission-prompts", "--approve-for-me", "--sandbox"} & set(command)
