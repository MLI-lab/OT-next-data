"""Generate or review task instructions with agent sessions run through Harbor.

Every step is the same kind of session: the task's own environment is
started exactly as a solving agent would get it, but the session's instruction
is one of the shared instruction prompts. The complete task under review is available
at /task and the agent writes its result under /output.

    propose_scratch  no usable instruction yet: write /output/instruction.md
    review           rate /task/instruction.md, write /output/review.json
    propose          integrate /task/review.json into /output/instruction.md

Input is a Harbor tasks.parquet, a root with <group>/tasks.parquet files,
or a directory of rendered tasks. Missing or blank instructions, and instructions
matching a configured --placeholder-marker, begin with propose_scratch. Then review and propose alternate, at most --max-reviews
reviews. Per input Parquet the results are:

    tasks.parquet     accepted: alignment, leakage and quality rated PASS and
                      task validity not FAIL; packaged with the new instruction
    needs_human/      everything else, as task directories with the last
                      candidate instruction and its review.json, sorted by cause:
                        task_validity/<reason>/  a review failed task validity,
                                                 which ends the loop for the task
                        <category>/              the category still not PASS
                                                 after the last review
                        session_error/           an agent session gave no output
    audit.jsonl       every round's instruction and review, one line per task

Needs a Slurm allocation; the script starts its own Apptainer bridge there.
Run in an allocation with the workspace environment and runtime configured:

    python -m data.utils.instruction_loop --tasks /path/to/input \
      --out /path/to/results --work-dir /node-local/scratch/instruction-loop
"""


import argparse
import asyncio
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tarfile
import tomllib
from types import SimpleNamespace


REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
PROMPTS = Path(__file__).parent / "instruction_prompts"
# Session step -> prompt file and the file the agent must leave in /output.
STEPS = {
    "propose_scratch": ("propose_scratch.md", "instruction.md"),
    "review": ("review_agent.md", "review.json"),
    "propose": ("propose.md", "instruction.md"),
}
# A proposer can act on the first three; task validity is about the task itself.
INSTRUCTION_CATEGORIES = ("instruction_verifier_alignment", "solution_leakage", "instruction_quality")
CATEGORIES = (*INSTRUCTION_CATEGORIES, "task_validity")
RATINGS = ("PASS", "UNCERTAIN", "FAIL")
VALIDITY_REASONS = ("none", "reference_details", "unrelated_changes", "unreliable_tests",
                    "unavailable_resources", "other")
# Runs before the agent, after the task's own setup. Archives stay packed on the
# host (file quota) and are unpacked only inside the container.
SESSION_SETUP = """#!/bin/bash
set -euo pipefail
if [ -f /setup_files/setup.sh ]; then
  bash /setup_files/setup.sh
fi
rm -rf /task /output
cp -r /setup_files/session/task /task
for archive in /task/tests/*.tar.gz /task/solution/*.tar.gz; do
  [ -f "$archive" ] || continue
  mkdir -p "${archive%.tar.gz}"
  tar -xzf "$archive" -C "${archive%.tar.gz}"
done
mkdir -p /output
if [ -f /setup_files/session/working-instruction.md ]; then
  cp /setup_files/session/working-instruction.md /output/instruction.md
fi
chmod -R a+rwX /output
"""

# The session's verifier only checks that the expected output is usable.
CHECK_REVIEW = f"""#!/bin/bash
mkdir -p /logs/verifier
echo 0 > /logs/verifier/reward.txt
python3 - <<'PY' || exit 0
import json
review = json.load(open('/output/review.json'))
for name in {CATEGORIES!r}:
    assert review[name]['rating'] in {RATINGS!r}, name
    assert isinstance(review[name]['description'], str), name
assert review['task_validity']['reason'] in {VALIDITY_REASONS!r}
PY
echo 1 > /logs/verifier/reward.txt
"""
CHECK_INSTRUCTION = """#!/bin/bash
mkdir -p /logs/verifier
echo 0 > /logs/verifier/reward.txt
[ -s /output/instruction.md ] && echo 1 > /logs/verifier/reward.txt
exit 0
"""


def toml_tables(data: dict, prefix: str = "") -> str:
    """Write nested tables of scalars, which is all a task.toml here contains."""
    scalars = "".join(f"{key} = {json.dumps(value)}\n" for key, value in data.items()
                      if not isinstance(value, dict))
    text = (f"[{prefix}]\n" if prefix else "") + scalars
    for key, value in data.items():
        if isinstance(value, dict):
            text += toml_tables(value, f"{prefix}.{key}" if prefix else key)
    return text


def session_task(task: Path, step: str, target: Path, instruction: str | None = None,
                 review: dict | None = None, agent_timeout: float = 1800.0,
                 prompts_dir: Path = PROMPTS) -> Path:
    """Wrap one task as a Harbor task whose instruction is the prompt for `step`.

    `instruction` replaces the task's instruction.md in the copy under /task; a
    propose session also gets it as the working copy in /output. Harbor uploads
    setup_files before the agent starts and tests only afterwards, so the task
    copy, including its private tests and solution, travels in setup_files.
    """
    prompt, output = STEPS[step]
    session = target / "setup_files/session"
    shutil.copytree(task / "environment", target / "environment")
    if (task / "setup_files").is_dir():
        shutil.copytree(task / "setup_files", target / "setup_files")
    shutil.copytree(task, session / "task", ignore=shutil.ignore_patterns("setup_files"))
    if instruction is not None:
        (session / "task/instruction.md").write_text(instruction)
    elif step == "propose_scratch":
        (session / "task/instruction.md").unlink(missing_ok=True)
    if review is not None:
        (session / "task/review.json").write_text(json.dumps(review, indent=2) + "\n")
    if step == "propose":
        (session / "working-instruction.md").write_text(instruction)
    from validation.stages.task_setup import detect
    setup = detect(task)
    if setup is None and (task / "setup_files/setup.sh").is_file():
        setup = "bash /setup_files/setup.sh"
    (session / "setup.sh").write_text(SESSION_SETUP.replace(
        'if [ -f /setup_files/setup.sh ]; then\n  bash /setup_files/setup.sh\nfi', setup or ':'))
    (target / "instruction.md").write_text((prompts_dir / prompt).read_text())
    config = tomllib.loads((task / "task.toml").read_text())
    config.pop("artifacts", None)
    config.setdefault("metadata", {})["setup_command"] = "bash /setup_files/session/setup.sh"
    config["agent"] = {"timeout_sec": agent_timeout}
    config["verifier"] = {"timeout_sec": 60.0}
    (target / "task.toml").write_text(
        f'artifacts = [{{source = "/output/{output}", destination = "{output}"}}]\n'
        + toml_tables(config))
    # validation.stages.nop_setup.detect reads the setup call from the oracle script.
    (target / "solution").mkdir()
    (target / "solution/solve.sh").write_text("#!/bin/bash\nbash /setup_files/session/setup.sh\n")
    (target / "tests").mkdir()
    (target / "tests/test.sh").write_text(CHECK_REVIEW if step == "review" else CHECK_INSTRUCTION)
    return target


@contextlib.contextmanager
def bridge(scratch: Path, workers: int):
    """Start the Apptainer bridge for this allocation, as hpc/helma/publish_worker.py does."""
    from hpc.validation_worker import free_port, wait_ready
    base = Path(os.environ["OT_WORKSPACE"])
    for key in ("APPTAINER_BIND", "APPTAINER_BINDPATH", "SINGULARITY_BIND", "SINGULARITY_BINDPATH"):
        os.environ.pop(key, None)
    os.environ.update(
        APPTAINER_TMPDIR=str(scratch / "apptainer"), APPTAINER_CACHEDIR=str(base / "cache/apptainer"),
        HARBOR_SIF_CACHE=str(base / "images"), APPTAINER_NO_MOUNT="hostfs,bind-paths,cwd",
        APPTAINER_BINDPATH="/etc/resolv.conf:/etc/resolv.conf:ro", BRIDGE_USE_FAKEROOT="1",
        BRIDGE_INSTANCE_REUSE="0", BRIDGE_WORKERS_DEAD_TIMEOUT="60",
        BRIDGE_START_CONCURRENCY=str(workers), OT_NET_ISOLATION="0", OT_TRIAL_SRUN="0")
    (scratch / "apptainer").mkdir(parents=True)
    for name in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "no_proxy", "NO_PROXY"):
        if os.environ.get(name):
            os.environ["APPTAINERENV_" + name] = os.environ[name]
    port = free_port(40000 + int(os.environ["SLURM_JOB_ID"]) % 10000)
    url = f"http://127.0.0.1:{port}"
    os.environ["APPTAINER_BRIDGE_URL"] = url
    launcher = str(REPO / "validation/stages/service_entrypoint.py")
    processes = []
    try:
        processes.append(subprocess.Popen(
            [sys.executable, "-u", launcher, str(REPO / "harbor_patches/bridge_server.py"),
             "--host", "127.0.0.1", "--port", str(port)], start_new_session=True))
        wait_ready(url + "/status", processes, timeout=600, diagnostics=True)
        processes.append(subprocess.Popen(
            [sys.executable, "-u", launcher, str(REPO / "harbor_patches/bridge_worker.py"),
             "--bridge-url", url, "--sif-cache", str(base / "images"),
             "--staging-base", str(scratch / "bridge"), "--num-workers", str(workers)],
            start_new_session=True))
        wait_ready(url + "/status", processes, timeout=600, workers=True, diagnostics=True)
        yield
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)


def run_sessions(sessions: dict[str, Path], jobs: Path, args) -> dict[str, Path | str]:
    """Run the wrapped tasks as one Harbor job.

    Returns the artifact directory of each session that left a valid output,
    and a short reason for each one that did not.
    """
    from validation.stages import harbor as runtime
    if args.agent == "codex":
        # Use the Codex CLI login in ~/.codex/auth.json, not an API key.
        os.environ.setdefault("CODEX_FORCE_AUTH_JSON", "1")
    settings = SimpleNamespace(
        backend="apptainer", environment_kwargs={}, dry_run=False, force_build=False,
        trial_cpus=args.trial_cpus, trial_memory_mb=args.trial_memory_mb, agent=args.agent,
        agent_kwargs=json.loads(args.agent_kwargs), api_base=None, attempts=1,
        concurrency=args.concurrency)
    first = next(iter(sessions.values()))
    config = runtime.job_config(first, jobs, settings, args.agent, args.model)
    config["tasks"] = [{"path": str(path.resolve())} for path in sessions.values()]
    job = asyncio.run(runtime.execute_job(config))
    outcome: dict[str, Path | str] = {name: "no trial result" for name in sessions}
    for trial, result in runtime.trial_results(job):
        name = result.get("task_name")
        if name not in outcome:
            continue
        reward = ((result.get("verifier_result") or {}).get("rewards") or {}).get("reward")
        if result.get("exception_info"):
            error = result["exception_info"]
            outcome[name] = f"{error.get('exception_type')}: {str(error.get('exception_message'))[:300]}"
        elif reward != 1:
            outcome[name] = "the agent left no valid output file"
        else:
            outcome[name] = trial / "artifacts"
    return outcome


def loop(tasks: dict[str, Path], args) -> dict[str, dict]:
    state = {}
    for name, task in tasks.items():
        path = task / "instruction.md"
        text = path.read_text() if path.exists() else None
        if text is not None and (not text.strip() or any(marker in text for marker in args.placeholder_marker)):
            text = None
        state[name] = {"task_id": name, "agent": args.agent, "model": args.model,
                       "start": "existing_instruction" if text else "from_scratch",
                       "instruction": text, "rounds": [], "status": None}

    def step(kind: str, number: int, names: list[str]) -> dict[str, Path]:
        """Run one kind of session for the named tasks; mark those that produce nothing."""
        if not names:
            return {}
        root = args.work_dir / f"round-{number}-{kind}"
        sessions = {}
        for name in names:
            rounds = state[name]["rounds"]
            sessions[name] = session_task(
                tasks[name], kind, root / "tasks" / name, state[name]["instruction"],
                rounds[-1]["review"] if kind == "propose" else None, args.agent_timeout,
                args.prompts_dir)
        artifacts = {}
        for name, result in run_sessions(sessions, root / "jobs", args).items():
            if isinstance(result, Path):
                artifacts[name] = result
            else:
                state[name].update(status=f"needs_human: {kind} session in round {number}: {result}",
                                   folder="session_error")
        print(f"round {number} {kind}: {len(artifacts)}/{len(names)} sessions gave output", flush=True)
        return artifacts

    scratch = step("propose_scratch", 0, [n for n, s in state.items() if s["instruction"] is None])
    for name, artifacts in scratch.items():
        state[name]["instruction"] = (artifacts / "instruction.md").read_text()
    for number in range(1, args.max_reviews + 1):
        active = [name for name, item in state.items() if item["status"] is None]
        for name, artifacts in step("review", number, active).items():
            review = json.loads((artifacts / "review.json").read_text())
            state[name]["rounds"].append({"round": number, "instruction": state[name]["instruction"],
                                          "review": review})
            if review["task_validity"]["rating"] == "FAIL":
                state[name].update(status="needs_human: task validity: " + review["task_validity"]["description"],
                                   folder="task_validity/" + review["task_validity"]["reason"])
            elif all(review[category]["rating"] == "PASS" for category in INSTRUCTION_CATEGORIES):
                # An uncertain validity rating is nothing a proposer can change;
                # accept the instruction and leave the doubt in the audit.
                state[name]["status"] = "accepted"
                state[name]["task_validity_uncertain"] = review["task_validity"]["rating"] != "PASS"
        active = [name for name, item in state.items() if item["status"] is None]
        if number == args.max_reviews:
            for name in active:
                ratings = {category: state[name]["rounds"][-1]["review"][category]["rating"]
                           for category in INSTRUCTION_CATEGORIES}
                # One folder per task: a failed category before an uncertain one.
                open_categories = sorted((category for category in ratings if ratings[category] != "PASS"),
                                         key=lambda category: ratings[category] != "FAIL")
                state[name].update(
                    status=f"needs_human: not accepted after {number} reviews: " + ", ".join(
                        f"{category} {ratings[category]}" for category in open_categories),
                    folder=open_categories[0])
            break
        for name, artifacts in step("propose", number, active).items():
            state[name]["instruction"] = (artifacts / "instruction.md").read_text()
    return state


def read_tasks(source: Path, unpacked: Path) -> dict[str, dict[str, Path]]:
    """Return task directories by output group, unpacking Parquet rows into `unpacked`."""
    if source.is_file():
        parquets = {"": source}
    else:
        parquets = {path.parent.name: path for path in sorted(source.glob("*/tasks.parquet"))}
    if not parquets:
        return {"": {path.name: path for path in sorted(source.iterdir())
                     if (path / "task.toml").is_file()}}
    import pyarrow.parquet as pq
    groups = {}
    for group, parquet in parquets.items():
        groups[group] = {}
        for row in pq.read_table(parquet).to_pylist():
            target = unpacked / row["path"]
            target.mkdir(parents=True)
            with tarfile.open(fileobj=io.BytesIO(row["task_binary"])) as archive:
                archive.extractall(target, filter="data")
            groups[group][row["path"]] = target
    return groups


def write_group(out: Path, tasks: dict[str, Path], state: dict[str, dict]) -> None:
    """Package accepted tasks; lay out the rest for a human, sorted by cause."""
    import pyarrow as pa
    import pyarrow.parquet as pq
    from data.utils.full_source.harbor_parquet import pack_task

    rows = []
    with (out / "audit.jsonl").open("w") as audit:
        for name, task in tasks.items():
            item = state[name]
            files = {str(path.relative_to(task)): path.read_bytes()
                     for path in sorted(task.rglob("*")) if path.is_file()}
            if item["status"] == "accepted":
                files["instruction.md"] = item["instruction"].encode()
                rows.append({"path": name, "task_binary": pack_task({
                    filename: (content, (task / filename).stat().st_mode & 0o777
                               if (task / filename).exists() else 0o644)
                    for filename, content in files.items()})})
            else:
                # The converter's draft stays next to the last candidate.
                target = out / "needs_human" / item["folder"] / name
                shutil.copytree(task, target)
                if item["instruction"]:
                    if "instruction.md" in files:
                        (target / "instruction.draft.md").write_bytes(files["instruction.md"])
                    (target / "instruction.md").write_text(item["instruction"])
                if item["rounds"]:
                    (target / "review.json").write_text(
                        json.dumps(item["rounds"][-1]["review"], indent=2) + "\n")
                (target / "status.txt").write_text(item["status"] + "\n")
            audit.write(json.dumps(item) + "\n")
            print(f"{name}: {item['status'][:160]} ({len(item['rounds'])} review(s))", flush=True)
    schema = pa.schema([("path", pa.string()), ("task_binary", pa.binary())])
    pq.write_table(pa.Table.from_pylist(rows, schema=schema), out / "tasks.parquet",
                   compression="zstd")


def main(argv=None, *, placeholder_markers=()) -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tasks", type=Path, required=True,
                        help="tasks.parquet, a patch.py output root, or a directory of rendered tasks")
    parser.add_argument("--only", action="append", help="Task name to include; repeatable")
    parser.add_argument("--out", type=Path, required=True, help="Root for the results")
    parser.add_argument("--work-dir", type=Path, required=True,
                        help="Scratch for session tasks, Harbor jobs and containers; node-local")
    parser.add_argument("--max-reviews", type=int, default=5)
    parser.add_argument("--agent", default="claude-code")
    parser.add_argument("--model", default="anthropic/claude-opus-5-5")
    parser.add_argument("--agent-kwargs", default='{"reasoning_effort": "medium"}')
    parser.add_argument("--agent-timeout", type=float, default=1800.0)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--trial-cpus", type=int, default=2)
    parser.add_argument("--trial-memory-mb", type=int, default=8192)
    parser.add_argument("--prompts-dir", type=Path, default=PROMPTS,
                        help="Directory containing propose_scratch.md, review_agent.md and propose.md")
    parser.add_argument("--placeholder-marker", action="append", default=list(placeholder_markers),
                        help="Instruction substring identifying a placeholder; repeatable")
    args = parser.parse_args(argv)
    if args.max_reviews < 1 or args.concurrency < 1:
        parser.error("--max-reviews and --concurrency must be positive")
    if any(not marker.strip() for marker in args.placeholder_marker):
        parser.error("--placeholder-marker must not be blank")
    for prompt, _ in STEPS.values():
        if not (args.prompts_dir / prompt).is_file():
            parser.error(f"Missing prompt: {args.prompts_dir / prompt}")
    groups = read_tasks(args.tasks, args.work_dir / "input")
    if args.only:
        groups = {group: {name: path for name, path in tasks.items() if name in args.only}
                  for group, tasks in groups.items()}
    groups = {group: tasks for group, tasks in groups.items() if tasks}
    tasks = {name: path for group in groups.values() for name, path in group.items()}
    if not tasks or args.only and set(args.only) - set(tasks):
        parser.error("No matching tasks, or an --only name is missing")
    for group in groups:
        if (args.out / group / "tasks.parquet").exists():
            parser.error(f"Refusing to overwrite existing results: {args.out / group}")
        (args.out / group).mkdir(parents=True, exist_ok=True)
    try:
        if os.environ.get("APPTAINER_BRIDGE_URL"):
            state = loop(tasks, args)
        else:
            with bridge(args.work_dir, args.concurrency):
                state = loop(tasks, args)
    finally:
        # Agent logs and trajectories of every session, as one file for the file quota.
        jobs = sorted(path.relative_to(args.work_dir) for path in args.work_dir.glob("round-*/jobs"))
        if jobs:
            subprocess.run(["tar", "-czf", str(args.out / "harbor-jobs.tar.gz"), "-C",
                            str(args.work_dir), *map(str, jobs)], check=False)
    for group, members in groups.items():
        write_group(args.out / group, members, state)



if __name__ == "__main__":
    main()
