"""Durable session results and Claude subscription reset handling."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


@dataclass(frozen=True)
class UsageLimit:
    reset_at: float | None
    reason: str = "Claude usage limit"


def reset_time(text: str, now: float) -> float | None:
    """Parse Claude's clock/date reset message only when its timezone is explicit."""
    match = re.search(r"resets?\s+(?:(\w{3,9}\s+\d{1,2}),?\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)\s*\(([^)]+)\)", text, re.I)
    if not match:
        return None
    date, hour, minute, period, zone = match.groups()
    try:
        tz = ZoneInfo(zone)
        local = datetime.fromtimestamp(now, tz)
        hour = int(hour)
        if not 1 <= hour <= 12:
            return None
        candidate = local.replace(hour=hour % 12 + (12 if period.lower() == "pm" else 0),
                                  minute=int(minute or 0), second=0, microsecond=0)
        if date:
            month, day = date.split()
            month_number = datetime.strptime(month[:3].title(), "%b").month
            candidate = candidate.replace(month=month_number, day=int(day))
            if candidate < local - timedelta(days=1):
                candidate = candidate.replace(year=candidate.year + 1)
        elif candidate < local:
            candidate += timedelta(days=1)
        return candidate.timestamp()
    except (ValueError, ZoneInfoNotFoundError):
        return None


def claude_limit(log: Path, now: float | None = None) -> UsageLimit | None:
    """Read CLI control/error records, never search task or tool content for limits."""
    if not log.is_file():
        return None
    now = time.time() if now is None else now
    limit = None
    with log.open(errors="replace") as stream:
        for line in stream:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if not isinstance(event, dict):
                continue
            if event.get("type") == "rate_limit_event":
                info = event.get("rate_limit_info") or {}
                if not isinstance(info, dict):
                    continue
                # overageStatus=rejected alone is normal subscription metadata.
                if info.get("status") == "rejected":
                    reset = info.get("resetsAt", info.get("resets_at"))
                    try:
                        reset = float(reset)
                        if not math.isfinite(reset) or reset <= 0:
                            reset = None
                    except (ValueError, TypeError):
                        reset = None
                    limit = UsageLimit(reset)
            elif event.get("type") == "assistant" and event.get("error") in ("rate_limit", "rate_limit_error"):
                content = (event.get("message") or {}).get("content") or []
                text = " ".join(c.get("text", "") for c in content if isinstance(c, dict))
                reset = reset_time(text, now)
                limit = UsageLimit(reset or (limit.reset_at if limit else None))
            elif event.get("type") == "result" and event.get("is_error"):
                errors = event.get("errors") or []
                text = " ".join(str(error) for error in errors) if isinstance(errors, list) else str(errors)
                text += " " + str(event.get("result", ""))
                if re.search(r"you(?:'|’)?ve hit your (?:usage )?limit", text, re.I):
                    reset = reset_time(text, now)
                    limit = UsageLimit(reset or (limit.reset_at if limit else None))
            elif event.get("type") == "result" and not event.get("is_error", False) and event.get("subtype") == "success":
                # The CLI may have recovered itself after an intermediate limit.
                limit = None
    return limit


class SessionProgress:
    """One atomic JSON checkpoint; outputs survive loss of node-local scratch.

    Replay completed session outputs through the normal loop on --resume. This
    keeps review decisions identical without spending another model session.
    """

    def __init__(self, tasks, args):
        self.path = args.out / "instruction-progress.json"
        self.fallback = getattr(args, "usage_limit_retry_seconds", 900)
        fingerprint = hashlib.sha256()
        settings = {key: getattr(args, key) for key in (
            "agent", "model", "agent_kwargs", "max_reviews", "placeholder_marker")}
        fingerprint.update(json.dumps(settings, sort_keys=True).encode())
        for path in sorted(args.prompts_dir.glob("*.md")):
            fingerprint.update(path.name.encode() + b"\0" + path.read_bytes())
        for name, task in sorted(tasks.items()):
            fingerprint.update(name.encode() + b"\0")
            for path in sorted(task.rglob("*")):
                if path.is_file():
                    fingerprint.update(str(path.relative_to(task)).encode() + b"\0")
                    fingerprint.update(str(path.stat().st_mode & 0o777).encode() + b"\0")
                    fingerprint.update(hashlib.sha256(path.read_bytes()).digest())
        signature = fingerprint.hexdigest()
        if self.path.exists():
            if not getattr(args, "resume", False):
                raise ValueError(f"Progress exists at {self.path}; use --resume")
            self.data = json.loads(self.path.read_text())
            if self.data.get("signature") != signature:
                raise ValueError("Cannot resume: tasks, prompts or review settings changed")
        else:
            self.data = {"version": 1, "signature": signature, "sessions": {}, "retry_at": 0}
            self.save()

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.data, ensure_ascii=False) + "\n")
        temporary.replace(self.path)

    def defer(self, limits):
        now = time.time()
        self.data["retry_at"] = max(
            limit.reset_at + 60 if limit.reset_at and limit.reset_at > now else now + self.fallback
            for limit in limits)
        self.save()
        stamp = datetime.fromtimestamp(self.data["retry_at"], timezone.utc).isoformat()
        print(f"Claude usage limit: progress saved; pausing all sessions until {stamp}. "
              "If no future reset was readable, using the configured retry delay.", flush=True)

    def wait(self):
        while (remaining := self.data["retry_at"] - time.time()) > 0:
            time.sleep(min(remaining, 60))
        if self.data["retry_at"]:
            self.data["retry_at"] = 0
            self.save()
