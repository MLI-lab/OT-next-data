"""Invoke fresh Claude Code or Codex sessions with durable prompt/output logs."""

import json
import math
import hashlib
import os
from pathlib import Path
import re
import subprocess
import time
from datetime import datetime, timezone


HERE = Path(__file__).resolve().parent
MUTATING = {"proposer", "implementer", "fixer", "recovery", "supervisor"}
ROLES = {*MUTATING, "final_failure_reviewer"}
USAGE_LIMIT = re.compile(
    r"(?:5.hour limit reached|usage limit (?:reached|exceeded)|"
    r"rate limit (?:reached|exceeded)|limit reached[^\n]{0,100}resets|"
    r"quota (?:exceeded|reached)|too many requests|"
    r"you(?:'ve| have) (?:hit|reached) (?:your )?(?:codex )?(?:usage|rate|session) limit)",
    re.IGNORECASE)


def wait_until(when):
    """Sleep in short chunks so the controller remains interruptible."""
    while remaining := when - time.time():
        if remaining <= 0:
            break
        time.sleep(min(remaining, 60))


def fingerprints(paths):
    result = {}
    for value in paths:
        path = Path(value)
        if path.is_dir():
            digest = hashlib.sha256()
            for child in sorted(path.rglob("*")):
                relative = child.relative_to(path)
                if child.is_file() and not any(part.startswith(".") or part in (
                        "__pycache__", "pilot-results") for part in relative.parts):
                    digest.update(relative.as_posix().encode() + b"\0")
                    digest.update(hashlib.sha256(child.read_bytes()).digest())
            result[str(value)] = digest.hexdigest()
        else:
            result[str(value)] = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "missing"
    return result


def save_json(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.replace(path)


def record_event(path, event):
    with path.open("a") as stream:
        stream.write(json.dumps(event, sort_keys=True) + "\n")


def prompt_for(role, context):
    if role in ("recovery", "supervisor"):
        return (HERE / f"prompts/{role}.txt").read_text() + "\n\n" + json.dumps(context, indent=2)
    if role == "final_failure_reviewer":
        prompt = (HERE / "prompts/final_failure_reviewer.txt").read_text()
        return prompt.replace("{evidence_dir}", str(context["evidence_dir"])) + "\n\n" + json.dumps(context, indent=2)
    if role in {"proposer", "implementer", "fixer"}:
        name = "image_reduction_proposer" if role == "proposer" else role
        common = (HERE / "prompts/system_prompt.txt").read_text()
        specific = (HERE / f"prompts/{name}.txt").read_text()
        for key in ("dataset", "integration_plan", "evidence_dir"):
            specific = specific.replace("{" + key + "}", str(context.get(key, "")))
        specific = specific.replace("data/<dataset>/", f"data/{context['dataset']}/")
        protocol = (
            "Controller contract (takes precedence over conflicting prose above): "
            "Only edit project files beneath dataset_dir; verification output may be written under evidence_dir. Proposer may ONLY write integration_plan. "
            "Keep upstream source immutable. Do not edit shared files, submit jobs, commit, "
            "publish, or touch another datasource. Follow .agents/AGENTS.md: generated datasource "
            "READMEs describe task content; put validation, exclusion and patch explanations in "
            "patch reporting inputs and your answer. Return exactly one JSON object. "
            "Proposer: {changes_needed: boolean, reason: string}; when true write the plan. "
            "Implementer/fixer: {summary: string, exclusions: [{task_id: string, category: string, "
            "reason: string, evidence: string}], requires_review_setup: boolean}. "
            "Set requires_review_setup=true when changing image sharing or runtime preparation. "
            "For credentials or infrastructure failures return {blocked: {kind: credentials or "
            "infrastructure, reason: string}}. Never exclude temporary infrastructure failures. "
            "The evidence archive preserves detailed logs; paths inside reports can refer to "
            "expired node-local scratch. Locate those logs by archive member path."
        )
        return "\n\n".join((common, specific, protocol, json.dumps(context, indent=2)))
    raise ValueError(f"Unknown agent role: {role}")


def usage_from_output(provider, output):
    """Read CLI metadata, never cost or usage claimed in the agent's answer."""
    records = []
    try:
        parsed = json.loads(output)
        records = parsed if isinstance(parsed, list) else [parsed]
    except json.JSONDecodeError:
        for line in output.splitlines():
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    records = [row for row in records if isinstance(row, dict)]
    if provider == 'claude':
        finals = [row for row in records if row.get('type') == 'result']
        final = finals[-1] if finals else {}
        usage = final.get('usage')
        cost = final.get('total_cost_usd')
        if type(cost) not in (int, float) or not math.isfinite(cost) or cost < 0:
            cost = None
        return {'usage': usage if isinstance(usage, dict) else None,
                'estimated_cost_usd': cost, 'model_usage': final.get('modelUsage'),
                'answer': final.get('result'), 'is_error': bool(final.get('is_error'))}
    totals = {}
    for row in records:
        if row.get('type') == 'turn.completed' and isinstance(row.get('usage'), dict):
            for key, value in row['usage'].items():
                if type(value) is int and value >= 0:
                    totals[key] = totals.get(key, 0) + value
    return {'usage': totals or None, 'estimated_cost_usd': None}


def invoke(role, context, *, provider, repo, output_dir, model=None, timeout=3600,
           usage_retry_seconds=600, watched_paths=(), reasoning_effort=None):
    if role not in ROLES:
        raise ValueError(f"unknown agent role: {role}")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    prompt = prompt_for(role, context)
    prompt_hash = hashlib.sha256(prompt.encode()).hexdigest()
    if provider == "codex":
        command = ["codex", "exec", "--json", "--ephemeral", "-C", str(repo),
                   "--dangerously-bypass-approvals-and-sandbox",
                   "--output-last-message", str(output_dir / "answer.txt")]
        if model:
            command += ["--model", model]
        if reasoning_effort:
            command += ["--config", f'model_reasoning_effort="{reasoning_effort}"']
        command.append("-")
    elif provider == "claude":
        command = ["claude", "--print", "--no-session-persistence",
                   "--dangerously-skip-permissions", "--output-format", "json"]
        if model:
            command += ["--model", model]
    else:
        raise ValueError("provider must be codex or claude")
    identity = {"role": role, "provider": provider, "model": model,
                "prompt_sha256": prompt_hash, "command": command}
    completed = output_dir / "completed.json"
    if completed.exists():
        saved = json.loads(completed.read_text())
        if any(saved.get(key) != value for key, value in identity.items()):
            raise RuntimeError(f"completed agent call has different input: {output_dir}")
        if saved.get("watched_after") != fingerprints(watched_paths):
            raise RuntimeError(f"files changed after completed agent call: {output_dir}")
        return (output_dir / "answer.txt").read_text().strip()
    if (output_dir / "partial-edit.json").exists():
        raise RuntimeError(f"agent left a partial edit; inspect {output_dir / 'partial-edit.json'}")
    (output_dir / "prompt.txt").write_text(prompt)
    (output_dir / "command.json").write_text(json.dumps(command, indent=2) + "\n")
    pending = output_dir / "usage-wait.json"
    if pending.exists():
        saved = json.loads(pending.read_text())
        if any(saved.get(key) != value for key, value in identity.items()):
            raise RuntimeError(f"pending agent call has different input: {output_dir}")
        wait_until(saved["next_retry_epoch"])
    attempt = len(list(output_dir.glob("attempt-*.stdout.txt")))
    while True:
        attempt += 1
        before = fingerprints(watched_paths)
        # A previous failed Codex call must not supply a stale answer.
        (output_dir / "answer.txt").unlink(missing_ok=True)
        trial_prompt = prompt
        if attempt > 1 and (output_dir / "protocol-error.txt").exists():
            trial_prompt += ("\n\nYour previous response could not be parsed. "
                             "Return exactly one JSON object with the requested keys, "
                             "and no other text or code blocks.\n")
        (output_dir / f"attempt-{attempt:04d}.prompt.txt").write_text(trial_prompt)
        started = time.monotonic()
        try:
            result = subprocess.run(command, input=trial_prompt, text=True, cwd=repo,
                                    capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            (output_dir / "error.txt").write_text(f"Agent timeout after {timeout}s: {exc}\n")
            partial = exc.stdout or ''
            partial = partial.decode(errors='replace') if isinstance(partial, bytes) else partial
            (output_dir / f"attempt-{attempt:04d}.stdout.txt").write_text(partial)
            metadata = usage_from_output(provider, partial)
            record_event(output_dir / "attempts.jsonl", {
                "attempt": attempt, "exit_code": None, "timed_out": True,
                "at": datetime.now(timezone.utc).isoformat(), "duration_seconds": time.monotonic() - started,
                "provider": provider, "model": model, "usage": metadata['usage'],
                "estimated_cost_usd": metadata['estimated_cost_usd'], "usage_complete": False})
            raise
        prefix = output_dir / f"attempt-{attempt:04d}"
        prefix.with_suffix(".stdout.txt").write_text(result.stdout)
        prefix.with_suffix(".stderr.txt").write_text(result.stderr)
        metadata = usage_from_output(provider, result.stdout)
        answer_text = metadata.get('answer') if isinstance(metadata.get('answer'), str) else result.stdout
        combined = answer_text + "\n" + result.stderr
        plain_error = not answer_text.lstrip().startswith(("{", "```"))
        limited = bool(USAGE_LIMIT.search(combined)) and (result.returncode != 0 or metadata.get("is_error") or
                    (provider == "claude" and plain_error and len(answer_text) < 1000))
        event = {"attempt": attempt, "exit_code": result.returncode,
                 "usage_limited": limited, "at": datetime.now(timezone.utc).isoformat(),
                 "duration_seconds": time.monotonic() - started, "provider": provider, "model": model,
                 "usage": metadata['usage'], "estimated_cost_usd": metadata['estimated_cost_usd'],
                 "model_usage": metadata.get('model_usage'),
                 "usage_complete": metadata['usage'] is not None and result.returncode == 0 and not metadata.get('is_error')}
        record_event(output_dir / "attempts.jsonl", event)
        if limited:
            after = fingerprints(watched_paths)
            if after != before:
                save_json(output_dir / "partial-edit.json",
                          {"before": before, "after": after, "attempt": attempt})
                raise RuntimeError(f"usage limit followed a partial edit; inspect {output_dir}")
            retry_at = time.time() + usage_retry_seconds
            save_json(pending, {**identity, "attempt": attempt,
                "next_retry_epoch": retry_at, "next_retry_utc": datetime.fromtimestamp(
                    retry_at, timezone.utc).isoformat()})
            wait_until(retry_at)
            continue
        pending.unlink(missing_ok=True)
        (output_dir / "stdout.txt").write_text(result.stdout)
        (output_dir / "stderr.txt").write_text(result.stderr)
        if result.returncode or metadata.get("is_error"):
            raise RuntimeError(f"{role} agent failed with exit {result.returncode}; see {output_dir}")
        if provider == "codex" and re.search(
                r"(?m)^bwrap: Creating new namespace failed:", result.stderr):
            save_json(output_dir / "runtime-blocker.json", {
                "reason": "Codex shell sandbox could not create a namespace",
                "attempt": attempt, "watched_before": before,
                "watched_after": fingerprints(watched_paths)})
            raise RuntimeError(f"agent shell inspection was blocked; see {output_dir}")
        answer = ((output_dir / "answer.txt").read_text() if provider == "codex"
                  else answer_text).strip()
        (output_dir / "answer.txt").write_text(answer + "\n")
        try:
            parse_json_answer(answer)
        except (ValueError, json.JSONDecodeError) as exc:
            (output_dir / "protocol-error.txt").write_text(f"Attempt {attempt}: {exc}\n")
            after = fingerprints(watched_paths)
            if after != before:
                save_json(output_dir / "partial-edit.json",
                          {"before": before, "after": after, "attempt": attempt})
                raise RuntimeError(f"invalid agent response after a partial edit: {output_dir}") from exc
            if role == "final_failure_reviewer" and attempt < 3:
                continue
            raise RuntimeError(f"{role} agent did not return a JSON object: {output_dir}") from exc
        (output_dir / "protocol-error.txt").unlink(missing_ok=True)
        save_json(completed, {**identity, "watched_after": fingerprints(watched_paths),
                              "attempt": attempt})
        return answer


def parse_json_answer(answer):
    """Accept plain JSON or one fenced JSON object, including brief surrounding prose."""
    content = answer.strip()
    if not content.startswith("{"):
        matches = re.findall(r"```(?:json)?\s*([\s\S]*?)\s*```", content)
        if len(matches) != 1:
            raise ValueError("agent answer must contain exactly one JSON code block")
        content = matches[0].strip()
    value = json.loads(content)
    if not isinstance(value, dict):
        raise ValueError("agent answer must be a JSON object")
    return value
