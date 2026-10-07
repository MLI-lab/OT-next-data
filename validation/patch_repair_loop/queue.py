"""Run dataset repair pilots sequentially, stopping each at its human review gate."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from validation.patch_repair_loop.orchestrator import ROOT, load_config, save


TERMINAL = {"awaiting_human_review", "complete", "repair_limit", "full_run_findings",
            "blocked", "needs_human"}
FINISHED = {"awaiting_human_review", "complete", "needs_human"}


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def create_config(entry, settings):
    if entry.get("config"):
        path = Path(entry["config"])
        if not path.is_file():
            return None, "configuration file missing"
        return path, None
    required = ("source", "patcher", "source_revision", "dataset")
    if any(not entry.get(key) for key in required):
        return None, "full-source adapter or pinned source missing"
    source = Path(entry["source"])
    patcher = Path(entry["patcher"])
    if not source.exists() or not patcher.is_file():
        return None, "pinned source or patcher not present"
    work = Path(settings["work_root"])
    config_path = work / "configs" / f"{entry['id']}.json"
    config = {
        "dataset": entry["dataset"], "source_revision": entry["source_revision"],
        "source": str(source), "patcher": str(patcher),
        "patch_command": ["{python}", "{patcher}", "--source", "{source}",
                          "--output", "{output}"],
        "work_root": str(work / entry["id"] / "loop"),
        "workspace": settings["workspace"], "python": settings["python"],
        "agent_provider": "claude", "agent_model": "sonnet",
        "agent_models": {"propose_rule": "opus", "review": "opus"},
        "usage_retry_seconds": 600, "time": settings.get("time", "06:00:00"),
        "partition": "cpu", "cpus": 48, "memory": "128G", "concurrency": 8,
        "network_mode": "host",
    }
    for key in ("required_stages", "static_exclusions", "validation_policy_reason"):
        if key in entry:
            config[key] = entry[key]
    if config_path.exists():
        if json.loads(config_path.read_text()) != config:
            return None, "frozen config differs from queue manifest"
    else:
        save(config_path, config)
    load_config(config_path)
    # Execute the patcher's CLI before calling it ready. A path-only check missed
    # import failures in nine sources and let the queue skip their first pilots.
    env = {**os.environ, "PYTHONPATH": str(ROOT) + os.pathsep + os.environ.get("PYTHONPATH", "")}
    smoke = subprocess.run([config["python"], str(patcher), "--help"], cwd=ROOT,
                           env=env, capture_output=True, text=True, timeout=60)
    if smoke.returncode:
        detail = (smoke.stderr or smoke.stdout).strip().splitlines()
        return None, f"patcher CLI preflight failed: {detail[-1] if detail else smoke.returncode}"
    return config_path, None


def loop_state(config_path):
    config = load_config(config_path)
    path = Path(config["work_root"]) / "loop-state.json"
    return json.loads(path.read_text()) if path.exists() else None


def run_queue(manifest_path, *, once=False):
    manifest_path = Path(manifest_path)
    settings = json.loads(manifest_path.read_text())
    work = Path(settings["work_root"])
    work.mkdir(parents=True, exist_ok=True)
    state_path = work / "queue-state.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {"entries": {}}
    state["status"] = "running"
    save(state_path, state)
    while True:
        settings = json.loads(manifest_path.read_text())
        entries = settings["entries"]
        if len({entry["id"] for entry in entries}) != len(entries):
            raise ValueError("queue entry IDs must be unique")
        blocked_by_existing = False
        for entry in entries:
            key = entry["id"]
            record = state["entries"].get(key, {})
            stopped_external = (entry.get("wait_for_existing") and
                record.get("reason") == "external tmux controller stopped with an active loop")
            if record.get("status") in FINISHED and not stopped_external and not entry.get("wait_for_existing"):
                continue
            try:
                config_path, reason = create_config(entry, settings)
            except Exception as exc:
                config_path, reason = None, f"configuration error: {exc}"
            if reason:
                state["entries"][key] = {"status": "needs_preparation",
                                          "reason": reason, "updated_at": timestamp()}
                save(state_path, state)
                continue
            current = loop_state(config_path)
            if current and current["status"] in TERMINAL:
                status = ("awaiting_human_review" if current["status"] ==
                          "awaiting_human_review" else
                          "complete" if current["status"] == "complete" else "needs_human")
                state["entries"][key] = {"status": status, "loop_status": current["status"],
                                          "blocker": current.get("blocker"),
                                          "config": str(config_path), "updated_at": timestamp()}
                save(state_path, state)
                continue
            if entry.get("wait_for_existing"):
                alive = subprocess.run(["tmux", "has-session", "-t", entry["tmux_session"]],
                                       capture_output=True).returncode == 0
                if alive:
                    state["entries"][key] = {"status": "running_external",
                                              "config": str(config_path), "updated_at": timestamp()}
                    save(state_path, state)
                    blocked_by_existing = True
                    continue
                state["entries"][key] = {"status": "needs_human",
                                          "reason": "external tmux controller stopped with an active loop",
                                          "config": str(config_path), "updated_at": timestamp()}
                save(state_path, state)
                continue
            state["entries"][key] = {"status": "running", "config": str(config_path),
                                      "updated_at": timestamp()}
            state["status"] = "running"
            save(state_path, state)
            log = work / f"{key}.controller.log"
            with log.open("a") as stream:
                result = subprocess.run([sys.executable, "-m",
                    "validation.patch_repair_loop.orchestrator", "run", "--config",
                    str(config_path)], stdout=stream, stderr=subprocess.STDOUT)
            current = loop_state(config_path)
            raw = current["status"] if current else "not_started"
            status = ("awaiting_human_review" if raw == "awaiting_human_review" else
                      "complete" if raw == "complete" else "needs_human")
            state["entries"][key] = {"status": status, "loop_status": raw,
                "blocker": current.get("blocker") if current else None,
                "exit_code": result.returncode, "config": str(config_path),
                "log": str(log), "updated_at": timestamp()}
            save(state_path, state)
        if all(state["entries"].get(entry["id"], {}).get("status") in FINISHED
               for entry in entries):
            state["status"] = ("finished_with_blockers" if any(
                state["entries"][entry["id"]]["status"] == "needs_human" for entry in entries)
                else "all_pilots_at_review")
            save(state_path, state)
            return
        state["status"] = "waiting_for_external" if blocked_by_existing else "waiting_for_preparation"
        save(state_path, state)
        if once:
            return
        time.sleep(max(10, settings.get("poll_seconds", 120)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("run", "status", "scan"))
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    settings = json.loads(args.manifest.read_text())
    state_path = Path(settings["work_root"]) / "queue-state.json"
    if args.command == "status":
        print(state_path.read_text() if state_path.exists() else "No queue started")
    else:
        run_queue(args.manifest, once=args.command == "scan")


if __name__ == "__main__":
    main()
