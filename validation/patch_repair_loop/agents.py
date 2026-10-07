"""Invoke fresh Claude Code or Codex sessions with durable prompt/output logs."""

import json
import hashlib
import getpass
import os
from pathlib import Path
import re
import subprocess
import time
from datetime import datetime, timezone


HERE = Path(__file__).resolve().parent
MUTATING = {"implement", "infrastructure"}
ROLES = {"diagnose", "propose_rule", "implement", "infrastructure", "review"}
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
    return {str(path): (hashlib.sha256(Path(path).read_bytes()).hexdigest()
                        if Path(path).is_file() else "missing") for path in paths}


def save_json(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.replace(path)


def record_event(path, event):
    with path.open("a") as stream:
        stream.write(json.dumps(event, sort_keys=True) + "\n")


def prompt_for(role, context):
    common = (HERE / "prompts/common.txt").read_text()
    specific = (HERE / f"prompts/{role}.txt").read_text()
    stage = context.get("stage")
    if stage in (1, 3, 4, 5):
        skills = [HERE / f"skills/stage-{stage}/SKILL.md"]
    elif role == "infrastructure":
        skills = [HERE / "skills/infrastructure/SKILL.md"]
    else:
        skills = sorted((HERE / "skills").glob("*/SKILL.md"))
    skill_text = "\n\n".join(path.read_text() for path in skills)
    return "\n\n".join((common, specific, "Confirmed cross-dataset repair skills:\n" + skill_text,
                         "Current dataset evidence:\n" + json.dumps(context, indent=2)))


def invoke(role, context, *, provider, repo, output_dir, model=None, timeout=3600,
           usage_retry_seconds=600, watched_paths=(), reasoning_effort=None):
    if role not in ROLES:
        raise ValueError(f"unknown agent role: {role}")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    prompt = prompt_for(role, context)
    prompt_hash = hashlib.sha256(prompt.encode()).hexdigest()
    if provider == "codex":
        private_temp = Path("/scratch/.privtmp") / getpass.getuser() / f"codex-daemon-{os.getuid()}"
        private_temp.mkdir(parents=True, exist_ok=True, mode=0o700)
        if private_temp.is_symlink() or private_temp.stat().st_uid != os.getuid():
            raise RuntimeError(f"unsafe Codex temporary directory: {private_temp}")
        command = ["codex", "exec", "--ephemeral", "-C", str(repo),
                   "--output-last-message", str(output_dir / "answer.txt")]
        if role in MUTATING:
            command.append("--approve-for-me")
            command += ["--config", "sandbox_workspace_write.network_access=true"]
            command += ["--config", "sandbox_workspace_write.writable_roots=" +
                        json.dumps([str(private_temp.parent)])]
        else:
            # Helma's shell daemon uses this private scratch directory even
            # when TMPDIR is unset. Create it before entering the read-only
            # mount namespace and permit temporary runtime files there only.
            # Helma disables network namespace creation (max_net_namespaces=0).
            # Keep filesystem reads restricted, but use the permitted host
            # network rather than asking Bubblewrap for a new namespace.
            command += ["--config", 'default_permissions="audit-read-net"',
                        "--config", 'permissions.audit-read-net.extends=":read-only"',
                        "--config", 'permissions.audit-read-net.filesystem={":slash_tmp"="write", '
                                    '":tmpdir"="write", ' + json.dumps(str(private_temp.parent)) + '="write"}',
                        "--config", "permissions.audit-read-net.network.enabled=true"]
        if model:
            command += ["--model", model]
        if reasoning_effort:
            command += ["--config", f'model_reasoning_effort="{reasoning_effort}"']
        command.append("-")
    elif provider == "claude":
        command = ["claude", "--print", "--no-session-persistence",
                   "--permission-mode", "acceptEdits" if role in MUTATING else "plan",
                   "--permission-prompts", "none", "--output-format", "text"]
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
        try:
            result = subprocess.run(command, input=trial_prompt, text=True, cwd=repo,
                                    capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            (output_dir / "error.txt").write_text(f"Agent timeout after {timeout}s: {exc}\n")
            raise
        prefix = output_dir / f"attempt-{attempt:04d}"
        prefix.with_suffix(".stdout.txt").write_text(result.stdout)
        prefix.with_suffix(".stderr.txt").write_text(result.stderr)
        combined = result.stdout + "\n" + result.stderr
        plain_error = not result.stdout.lstrip().startswith(("{", "```"))
        limited = bool(USAGE_LIMIT.search(combined)) and (result.returncode != 0 or
                    (provider == "claude" and plain_error and len(result.stdout) < 1000))
        event = {"attempt": attempt, "exit_code": result.returncode,
                 "usage_limited": limited, "at": datetime.now(timezone.utc).isoformat()}
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
        if result.returncode:
            raise RuntimeError(f"{role} agent failed with exit {result.returncode}; see {output_dir}")
        if provider == "codex" and re.search(
                r"(?m)^bwrap: Creating new namespace failed:", result.stderr):
            save_json(output_dir / "runtime-blocker.json", {
                "reason": "Codex shell sandbox could not create a namespace",
                "attempt": attempt, "watched_before": before,
                "watched_after": fingerprints(watched_paths)})
            raise RuntimeError(f"agent shell inspection was blocked; see {output_dir}")
        answer = ((output_dir / "answer.txt").read_text() if provider == "codex"
                  else result.stdout).strip()
        (output_dir / "answer.txt").write_text(answer + "\n")
        if role != "implement":
            try:
                parse_json_answer(answer)
            except (ValueError, json.JSONDecodeError) as exc:
                (output_dir / "protocol-error.txt").write_text(f"Attempt {attempt}: {exc}\n")
                after = fingerprints(watched_paths)
                if after != before:
                    save_json(output_dir / "partial-edit.json",
                              {"before": before, "after": after, "attempt": attempt})
                    raise RuntimeError(f"invalid agent response after a partial edit: {output_dir}") from exc
                if role in {"diagnose", "propose_rule", "review"} and attempt < 3:
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
