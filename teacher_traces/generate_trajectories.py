#!/usr/bin/env python3
"""Run a reproducible sample of a named, validated dataset through trace
generation.

The underlying data/local/run_tracegen.py is "run this folder of tasks": it
serves the model (Ray + vLLM) and executes every task in a local directory via
Harbor. This wrapper adds the experiment layer on top: resolve a dataset name
through the validated manifest from prepare_dataset.py (which already excludes
broken/mismatched tasks), draw a deterministic --n-tasks/--seed sample with
recorded provenance, materialize only those tasks, then hand them to
run_tracegen.py — plus resume, per-task termination reasons, run metadata, and
the runtime diagnostic (--check-runtime, EXPERIMENT_SPEC.md §9) and
Docker↔Apptainer parity test (--parity-check, §10). Both runtimes are
supported.

    python teacher_traces/generate_trajectories.py \
        --dataset terminal_lego --model Qwen/Qwen3-8B --runtime docker \
        --n-tasks 10 --seed 42

    python teacher_traces/generate_trajectories.py --check-runtime
    python teacher_traces/generate_trajectories.py --parity-check \
        --n-tasks 10 --seed 42

Reads the manifest written by prepare_dataset.py; contains no dataset registry
and no HF repo IDs of its own (EXPERIMENT_SPEC.md §15.1).

IMPORTANT — container images must be cached before a real run.
On an HPC cluster there is no Docker and no layer cache, so Apptainer builds
every image from scratch: pulling the base image over the network, then running
the Dockerfile's apt/pip steps, then compressing the result. Measured on an idle
Capella node that is 22s for a bare base import and 81s for a full build, and
several builds run concurrently. Each task's task.toml allows only
build_timeout_sec (120s for Terminal-Lego) for the whole of environment start,
building included, so a task whose image is NOT yet cached can die with
EnvironmentStartTimeoutError before its agent ever starts.

This is a first-encounter cost only: once a task's SIF is in the cache,
environment start is ~1s and every task passes on its stock budget. The task's
budget is therefore NEVER relaxed by default. Warm a new task set once with

    TRP_BUILD_TIMEOUT_MULTIPLIER=8 python .../generate_trajectories.py ...

and run normally afterwards. A run whose images are not cached says so before
it starts (warn_if_images_cold).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def select_gpu():
    """Leave GPU visibility to the scheduler."""
    return None


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = Path(os.environ["OTAGENT_ROOT"]).resolve()
DEFAULT_MANIFEST = SCRIPT_DIR / "artifacts" / "task_manifest.jsonl"
# Storage root for caches/venv/runs; override on other hosts (e.g. HPC scratch).
BIG_DISK = Path(os.environ.get("WS_ROOT",
                               "/mnt/hdd_pool_bigsur/userdata/franziska"))
DEFAULT_RUNS_TARGET = Path(os.environ.get("TRP_RUNS_DIR", str(BIG_DISK / "teacher_ranking_proxy" / "runs")))
RUNS_LINK = SCRIPT_DIR / "runs"

# Working Python env with harbor/vllm/ray (see run metadata); overridable.
VENV = Path(os.environ.get(
    "TEACHER_PROXY_VENV", str(BIG_DISK / "teacher_ranking_proxy" / "venv")))
# Bridge mode runs on marin-community/harbor@main (newer generation, native
# bridge support) in its OWN venv; the master venv keeps the local-mode fork.
BRIDGE_VENV = Path(os.environ.get(
    "HARBOR_BRIDGE_VENV", str(BIG_DISK / "teacher_ranking_proxy" / "venv_bridge")))
APPTAINER_CACHE = BIG_DISK / "teacher_ranking_proxy" / "apptainer_cache"

DATAGEN_YAML = Path(os.environ.get(
    "TRP_DATAGEN_YAML",
    str(REPO_ROOT / "hpc" / "datagen_yaml" / "qwen3_8b_vllm_serve_32k_1xH200.yaml")))
HARBOR_TEMPLATE = Path(os.environ.get("TRP_HARBOR_TEMPLATE", str(REPO_ROOT / "hpc" / "harbor_yaml" / "trace_docker_16concurrency_ctx32k.yaml")))



# ---------------------------------------------------------------------------
# Environment plumbing (duplicated from prepare_dataset.py by design — scripts
# share data through files on disk, not imports; EXPERIMENT_SPEC.md §15)
# ---------------------------------------------------------------------------

def ensure_big_disk_env() -> dict:
    os.environ.setdefault("HF_HOME", str(BIG_DISK / "hf_cache"))
    os.environ.setdefault(
        "HF_DATASETS_CACHE", str(Path(os.environ["HF_HOME"]) / "datasets"))
    # Harbor↔Apptainer-1.5 compatibility shim (see apptainer_patch/apptainer);
    # must come before the real binary (system install, /usr/bin) in PATH.
    shim_dir = SCRIPT_DIR / "apptainer_patch"
    if (os.environ.get("TRP_APPTAINER_SHIM", "1") == "1"
            and shim_dir.is_dir() and str(shim_dir) not in os.environ["PATH"]):
        os.environ["PATH"] = f"{shim_dir}:{os.environ['PATH']}"
    os.environ.setdefault("APPTAINER_CACHEDIR", str(APPTAINER_CACHE))
    os.environ["HARBOR_SIF_CACHE"] = str(resolve_sif_cache())
    return {"HF_HOME": os.environ["HF_HOME"],
            "HF_DATASETS_CACHE": os.environ["HF_DATASETS_CACHE"],
            "APPTAINER_CACHEDIR": os.environ["APPTAINER_CACHEDIR"],
            "HARBOR_SIF_CACHE": os.environ["HARBOR_SIF_CACHE"]}


def ensure_runs_root() -> Path:
    DEFAULT_RUNS_TARGET.mkdir(parents=True, exist_ok=True)
    if "TRP_RUNS_DIR" in os.environ:
        return DEFAULT_RUNS_TARGET.resolve()
    if RUNS_LINK.is_symlink() or RUNS_LINK.exists():
        resolved = RUNS_LINK.resolve()
        if resolved != DEFAULT_RUNS_TARGET.resolve():
            raise SystemExit(f"{RUNS_LINK} resolves to {resolved}, expected "
                             f"{DEFAULT_RUNS_TARGET}; refusing to overwrite.")
    else:
        RUNS_LINK.symlink_to(DEFAULT_RUNS_TARGET)
    return RUNS_LINK.resolve()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def draw_sample(task_ids, n_tasks: int, seed: int) -> list:
    """Pinned sampling algorithm — EXPERIMENT_SPEC.md §3. Do not vary."""
    ids = sorted(set(task_ids))
    if n_tasks > len(ids):
        raise SystemExit(f"--n-tasks {n_tasks} exceeds available tasks ({len(ids)})")
    return random.Random(seed).sample(ids, n_tasks)


def load_manifest(path: Path) -> tuple:
    with open(path) as f:
        header = json.loads(f.readline())
        rows = [json.loads(line) for line in f if line.strip()]
    if header.get("kind") != "task_manifest_header":
        raise SystemExit(f"{path}: first line is not a manifest header; "
                         f"run prepare_dataset.py first")
    return header, rows


def resolve_sample(args, runs_root: Path) -> tuple:
    """Return (run_id, run_dir, sampled_task_ids), writing the sample file
    before any agent starts, or reusing+cross-checking an existing one."""
    if args.sample_file and args.n_tasks is not None:
        raise SystemExit("Pass either --sample-file or --n-tasks/--seed, not both.")
    if args.sample_file:
        sample_path = Path(args.sample_file).resolve()
        sample = json.loads(sample_path.read_text())
        run_id = args.run_id or sample_path.parent.name
        return run_id, sample_path.parent, sample["task_ids"]
    if args.n_tasks is None:
        raise SystemExit("Provide --n-tasks/--seed or --sample-file.")

    header, rows = load_manifest(Path(args.manifest))
    if header["dataset"] != args.dataset:
        raise SystemExit(f"manifest is for dataset {header['dataset']!r}, "
                         f"requested {args.dataset!r}")
    manifest_sha = sha256_file(Path(args.manifest))
    run_id = args.run_id or f"{args.dataset}-n{args.n_tasks}-s{args.seed}"
    run_dir = runs_root / run_id
    sample_path = run_dir / "sampled_task_ids.json"
    if sample_path.exists():
        existing = json.loads(sample_path.read_text())
        for key, want in [("n_tasks", args.n_tasks), ("seed", args.seed),
                          ("manifest_sha256", manifest_sha),
                          ("dataset", args.dataset)]:
            if existing.get(key) != want:
                raise SystemExit(
                    f"{sample_path} exists but {key}={existing.get(key)!r} "
                    f"disagrees with requested {want!r}; pass an explicit "
                    f"--run-id for a fresh directory.")
        print(f"[sample] reusing {sample_path}")
        return run_id, run_dir, existing["task_ids"]
    sampled = draw_sample([r["task_id"] for r in rows], args.n_tasks, args.seed)
    run_dir.mkdir(parents=True, exist_ok=True)
    sample_path.write_text(json.dumps({
        "task_ids": sampled, "n_tasks": args.n_tasks, "seed": args.seed,
        "manifest_sha256": manifest_sha, "dataset": args.dataset}, indent=2) + "\n")
    print(f"[sample] wrote {sample_path}")
    return run_id, run_dir, sampled


# ---------------------------------------------------------------------------
# Task materialization (EXPERIMENT_SPEC.md §4: only the selected tasks)
# ---------------------------------------------------------------------------

def materialize_tasks(header: dict, task_ids, dest: Path) -> Path:
    """Download the selected task dirs from the runnable-task repo and copy
    them (dereferencing HF-cache symlinks) into dest. Idempotent.

    Uses per-file hf_hub_download from a paginated listing: snapshot_download's
    allow_patterns silently truncates on this 120k-file repo.
    """
    if header.get("local_tasks_dir"):
        source = Path(header["local_tasks_dir"]).resolve()
        for task_id in task_ids:
            if Path(task_id).name != task_id or task_id in (".", ".."):
                raise ValueError(f"Invalid task ID: {task_id}")
            task = source / task_id
            if not (task / "task.toml").is_file():
                raise FileNotFoundError(task / "task.toml")
            if not (dest / task_id / "task.toml").exists():
                shutil.copytree(task, dest / task_id, symlinks=False)
        return dest

    from huggingface_hub import HfApi, hf_hub_download

    missing = [t for t in task_ids if not (dest / t / "task.toml").exists()]
    if not missing:
        print(f"[tasks] all {len(task_ids)} task dirs already materialized in {dest}")
        restore_empty_task_file_dirs(dest, task_ids)
        return dest
    api = HfApi()
    repo, rev = header["task_repo"], header["task_revision"]
    listing_cache = (BIG_DISK / "teacher_ranking_proxy" / "tasks" /
                     f"filelist_{repo.replace('/', '__')}_{rev}.json")
    if listing_cache.exists():
        files = json.loads(listing_cache.read_text())
    else:
        files = api.list_repo_files(repo, repo_type="dataset", revision=rev)
        listing_cache.parent.mkdir(parents=True, exist_ok=True)
        listing_cache.write_text(json.dumps(files))
    want = [f for f in files if f.split("/")[0] in set(missing)]
    print(f"[tasks] materializing {len(missing)} tasks ({len(want)} files) "
          f"from {repo}@{rev[:12]}")
    for f in want:
        local = Path(hf_hub_download(repo, f, repo_type="dataset", revision=rev))
        target = dest / f
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(local, target)  # dereferences the blob symlink
    for t in missing:
        if not (dest / t / "task.toml").exists():
            raise SystemExit(f"[tasks] {t} incomplete after materialization")
    restore_empty_task_file_dirs(dest, task_ids)
    return dest


def restore_empty_task_file_dirs(dest: Path, task_ids) -> None:
    """Recreate empty environment/task_file/ dirs the HF upload lost.

    Git/HF cannot store empty directories; some Terminal-Lego tasks carry a
    .gitkeep for this (e.g. task_07587), others lost the dir entirely while
    their Dockerfile still says `COPY ./task_file ...` — which docker build
    treats as a hard error. Restoring the empty dir reconstructs the intended
    task, it does not alter it. Always logged, never silent."""
    for t in task_ids:
        env_dir = dest / t / "environment"
        dockerfile = env_dir / "Dockerfile"
        task_file = env_dir / "task_file"
        if (dockerfile.exists() and not task_file.exists()
                and "task_file" in dockerfile.read_text()):
            task_file.mkdir()
            print(f"[tasks] {t}: restored empty environment/task_file/ "
                  f"(dropped by HF upload; Dockerfile COPYs it)")


# ---------------------------------------------------------------------------
# --check-runtime (§9): report and exit; loads no model, runs no task
# ---------------------------------------------------------------------------

def cmd_version(binary: str, *args: str) -> str | None:
    path = shutil.which(binary)
    if not path:
        return None
    out = subprocess.run([binary, *args], capture_output=True, text=True)
    first = (out.stdout or out.stderr).strip().splitlines()
    return f"{first[0] if first else '?'} ({path})"


def check_runtime() -> int:
    report = {
        "docker": cmd_version("docker", "--version"),
        "apptainer": cmd_version("apptainer", "--version"),
        "singularity": cmd_version("singularity", "--version"),
    }
    for name, ver in report.items():
        print(f"  {name:12s} {ver or 'NOT AVAILABLE'}")
    cache = Path(os.environ["HARBOR_SIF_CACHE"])
    n_sifs = len(list(cache.glob("*.sif"))) if cache.is_dir() else 0
    print(f"  sif cache    {cache} ({n_sifs} images cached)")

    bridge_url = os.environ.get("APPTAINER_BRIDGE_URL")
    if bridge_url:
        try:
            import urllib.request
            with urllib.request.urlopen(f"{bridge_url}/status", timeout=10) as r:
                status = json.loads(r.read())
            print(f"  bridge       {bridge_url} UP "
                  f"(workers_alive={status.get('workers_alive')}, "
                  f"workers={status.get('num_workers', '?')})")
        except Exception as e:
            print(f"  bridge       {bridge_url} UNREACHABLE ({e})")
    else:
        print("  bridge       APPTAINER_BRIDGE_URL not set (local runtimes only)")
    if gpu_select_sequoia.is_this_host():
        gpus = gpu_select_sequoia.query_gpus()
        print(f"  gpus (sequoia; healthy ids "
              f"{sorted(gpu_select_sequoia.HEALTHY_GPUS)}): "
              + (", ".join(f"{g['index']}: {g['memory_used_mib']}MiB used, "
                           f"{g['utilization_pct']}% util" for g in gpus)
                 or "nvidia-smi unavailable"))
    else:
        out = subprocess.run(["nvidia-smi", "--query-gpu=index,name,memory.used",
                              "--format=csv,noheader"], capture_output=True,
                             text=True)
        print(f"  gpus (generic host): "
              + (out.stdout.strip().replace(chr(10), "; ")
                 if out.returncode == 0 else "nvidia-smi unavailable"))
        print(f"  CUDA_VISIBLE_DEVICES={os.environ.get('CUDA_VISIBLE_DEVICES')!r} "
              f"(left to the scheduler on non-sequoia hosts)")
    return 0


def resolve_sif_cache() -> Path:
    """Pick a SIF cache directory that this node can actually reach.

    Filesystem visibility on ZIH is per-node, not per-cluster: /data/cat is
    reliable on Capella and invisible everywhere else, while /data/horse is
    visible to the other clusters but was missing on some Capella compute nodes
    (2026-08-25: present on c145, absent on c91). Symlinking harbor's default
    cache path does not help — a symlink into an unmounted filesystem dangles —
    so choose a reachable candidate here and hand it to harbor through
    HARBOR_SIF_CACHE. Candidates are tried in order and the first whose PARENT
    directory exists wins, so a node that lost a mount falls through instead of
    failing mid-run.
    """
    candidates = [os.environ.get("HARBOR_SIF_CACHE"),
                  os.environ.get("TRP_BRIDGE_SIF_CACHE"),
                  str(Path.home() / ".apptainer" / "harbor_cache")]
    for cand in candidates:
        if not cand:
            continue
        path = Path(cand)
        if path.parent.is_dir():
            path.mkdir(parents=True, exist_ok=True)
            return path
    raise SystemExit(
        "No reachable SIF cache: none of HARBOR_SIF_CACHE, "
        "TRP_BRIDGE_SIF_CACHE or ~/.apptainer resolves on this node. The node "
        "may be missing a filesystem mount — check with `ls /data/cat/ws "
        "/data/horse/ws` and resubmit excluding it.")


def warn_if_images_cold(task_ids, runtime: str) -> None:
    """Warn when sampled tasks have no cached image yet.

    With the build budget left at the task's own value (§6), the FIRST run over
    an uncached task dies with EnvironmentStartTimeoutError partway through the
    build. That is a confusing failure to meet cold, so name it up front and
    give the exact warm-up command.
    """
    if runtime == "docker":
        return
    cache = Path(os.environ.get("HARBOR_SIF_CACHE") or resolve_sif_cache())
    def is_cold(task: str) -> bool:
        sifs = list(cache.glob(f"build_{task}-*.sif"))
        if not sifs:
            return True
        # A registry-imported image is only warm once its deferred-build overlay
        # exists too; otherwise the overlay is built at instance start and eats
        # the same budget (harbor fork: _deferred_overlay_path).
        return any(s.with_suffix(".deferred.json").exists()
                   and not s.with_suffix(".overlay.img").exists() for s in sifs)

    cold = [t for t in task_ids if is_cold(t)]
    if not cold:
        return
    print(f"[images] WARNING: {len(cold)}/{len(task_ids)} tasks have no cached "
          f"image in {cache} (e.g. {', '.join(sorted(cold)[:3])}).\n"
          f"[images] Cold Apptainer builds do not fit the task's own "
          f"build_timeout_sec, so those trials will fail with "
          f"EnvironmentStartTimeoutError.\n"
          f"[images] Warm them first with the same command under "
          f"TRP_BUILD_TIMEOUT_MULTIPLIER=8, then rerun normally.")


def require_runtime(runtime: str) -> None:
    if runtime == "apptainer_bridge":
        # Remote execution: no local container binary needed - a reachable
        # bridge is. Fail early and clearly when it is not configured.
        url = os.environ.get("APPTAINER_BRIDGE_URL")
        if not url:
            raise SystemExit(
                "--runtime apptainer_bridge requires APPTAINER_BRIDGE_URL "
                "(e.g. http://<bridge-host>:9910). Start the bridge with "
                "harbor's apptainer_bridge/start_bridge_zih.sh and workers "
                "with zih_workers.sbatch.")
        try:
            import urllib.request
            with urllib.request.urlopen(f"{url}/status", timeout=10) as r:
                status = json.loads(r.read())
        except Exception as e:
            raise SystemExit(f"Bridge at {url} unreachable: {e}")
        if not status.get("workers_alive"):
            print(f"[bridge] WARNING: bridge at {url} is up but reports no "
                  f"alive workers yet (status: {status}) - trials will wait "
                  f"for workers up to BRIDGE_WORKERS_DEAD_TIMEOUT.")
        return
    binary = {"docker": "docker", "apptainer": "apptainer"}.get(runtime)
    if binary is None:
        raise SystemExit(f"Unknown runtime {runtime!r}")
    if runtime == "apptainer" and not shutil.which("apptainer"):
        # Singularity is a CLI-compatible fallback name, not a separate backend.
        if shutil.which("singularity"):
            return
    if not shutil.which(binary):
        raise SystemExit(f"Requested runtime {runtime!r} is unavailable "
                         f"(no {binary!r} in PATH). Not falling back.")


# ---------------------------------------------------------------------------
# Harbor job config generation (based on the repo's template YAML)
# ---------------------------------------------------------------------------

def build_harbor_config(env_type: str, tasks_dir: Path, jobs_dir: Path,
                        agent: str, n_attempts: int, *, oracle: bool,
                        max_turns: int | None = None) -> dict:
    import yaml
    cfg = yaml.safe_load(HARBOR_TEMPLATE.read_text())
    cfg["jobs_dir"] = str(jobs_dir)
    cfg["n_attempts"] = n_attempts
    cfg["environment"]["type"] = env_type
    cfg["environment"]["force_build"] = False
    # Honor task.toml resources and timeouts (§6): no global overrides.
    for key in ("override_cpus", "override_memory_mb", "override_storage_mb"):
        cfg["environment"][key] = None
    cfg["verifier"]["disable"] = False
    # Default 1.0: the task's own build budget is honoured, never quietly
    # relaxed. Cold Apptainer builds do not fit in it (no layer cache; a
    # measured build of task_06714 takes 81s on an idle node, more when several
    # run at once), so images must be WARMED first — see warn_if_images_cold().
    # The warm-up pass is the one place that raises this, via
    # TRP_BUILD_TIMEOUT_MULTIPLIER=8. Once cached, environment start is ~1s and
    # every task passes on its stock budget (verified: 11/11 at multiplier 1).
    cfg["environment_build_timeout_multiplier"] = float(
        os.environ.get("TRP_BUILD_TIMEOUT_MULTIPLIER", "1"))
    if env_type == "apptainer_bridge":
        # marin-main harbor: the bridge env's type string IS "apptainer"
        # (their generation replaced the local env). bridge_url comes from
        # APPTAINER_BRIDGE_URL in the driver env; no local-runtime kwargs.
        cfg["environment"]["type"] = "apptainer"
        cfg["environment"]["kwargs"] = {
            "bridge_url": os.environ["APPTAINER_BRIDGE_URL"],
            "sif_cache": str(resolve_sif_cache()),
        }
        cfg["environment"]["delete"] = True
    if env_type == "apptainer":
        # use_fakeroot: tasks and verifiers assume they are root (apt-get etc.),
        # which Docker provides; rootless apptainer emulates it via fakeroot.
        cfg["environment"]["kwargs"] = {"use_gpu": False, "network_none": False,
                                        "use_fakeroot": True}
        # delete=True would remove the freshly built SIF after every trial;
        # keep the SIF cache (instances are still stopped after each trial).
        cfg["environment"]["delete"] = False
    cfg["datasets"] = [{"path": str(tasks_dir)}]
    agent_cfg = cfg["agents"][0]
    agent_cfg["override_timeout_sec"] = None  # per-task timeouts from task.toml
    if oracle:
        cfg["agents"] = [{"name": "oracle"}]
        cfg["orchestrator"]["n_concurrent_trials"] = 4
        cfg["orchestrator"]["retry"]["max_retries"] = 0
        # File-state comparison for the parity test (§10).
        cfg["artifacts"] = cfg.get("artifacts", ["/app/task_file"])
    else:
        agent_cfg["name"] = agent
        if max_turns is not None:
            agent_cfg.setdefault("kwargs", {})["max_episodes"] = max_turns
    if env_type == "apptainer_bridge":
        # marin's JobConfig has no `orchestrator` block: concurrency and retry
        # policy are top-level fields (unknown keys are silently ignored, so
        # leaving them nested would quietly run with marin's defaults).
        orch = cfg.pop("orchestrator", {})
        if "n_concurrent_trials" in orch:
            cfg["n_concurrent_trials"] = orch["n_concurrent_trials"]
        if "retry" in orch:
            cfg["retry"] = orch["retry"]
    return cfg


def run_harbor_job(cfg: dict, cfg_path: Path, log_path: Path,
                   extra_env: dict | None = None, venv: Path | None = None) -> int:
    import yaml
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(yaml.safe_dump(cfg, sort_keys=False))
    harbor = (venv or VENV) / "bin" / "harbor"
    if not harbor.exists():
        raise SystemExit(f"harbor CLI not found at {harbor}; set TEACHER_PROXY_VENV")
    if venv is not None and venv == BRIDGE_VENV:
        # marin-community/harbor CLI: no --plain-output/--auto-resume (an
        # existing jobs_dir is resumed by default); --yes skips the prompt.
        cmd = [str(harbor), "run", "-c", str(cfg_path), "--yes"]
    else:
        cmd = [str(harbor), "run", "-c", str(cfg_path), "--plain-output",
               "--quiet", "--auto-resume"]
    env = os.environ.copy()
    env.update(extra_env or {})
    print(f"[harbor] {' '.join(cmd)}\n[harbor] log: {log_path}")
    with open(log_path, "a") as log:
        return subprocess.run(cmd, stdout=log, stderr=log, env=env,
                              cwd=str(REPO_ROOT)).returncode


def collect_trial_results(job_dir: Path) -> dict:
    """Map task_name -> parsed trial result.json (+ trial dir), scanning all
    Harbor jobs under job_dir. The job-level result.json has no task_name and
    is skipped. Where a task has several trials (e.g. a failed first job and a
    rerun), the EARLIEST clean (exception-free) trial is canonical — it is the
    true first attempt; a failed trial is superseded by any later clean one."""
    results = {}
    for res in sorted(job_dir.rglob("result.json")):
        try:
            data = json.loads(res.read_text())
        except json.JSONDecodeError:
            continue
        if "task_name" not in data:
            continue
        task = data["task_name"]
        prev = results.get(task)
        if prev and trial_is_complete(prev["result"]):
            continue
        results[task] = {"trial_dir": res.parent, "result": data}
    return results


def trial_is_complete(result: dict) -> bool:
    """A trial counts as a completed attempt if it ran without infrastructure
    errors. Task timeouts are legitimate terminations (§6), not rerun fodder."""
    exc = (result.get("exception_info") or {}).get("exception_type") or ""
    return not exc or "AgentTimeout" in exc or "VerifierTimeout" in exc


def extract_reward(result: dict):
    """Reward from a trial result; harbor nests it as verifier_result.rewards.reward."""
    vr = result.get("verifier_result") or {}
    rewards = vr.get("rewards")
    if isinstance(rewards, dict) and "reward" in rewards:
        return rewards["reward"]
    rew = vr.get("reward")
    if isinstance(rew, dict):
        rew = rew.get("reward")
    return rew


# ---------------------------------------------------------------------------
# --parity-check (§10): oracle command sequence in both runtimes, no LLM
# ---------------------------------------------------------------------------

# Normalizations applied before comparing text output (documented in report):
NORMALIZATIONS = [
    (re.compile(r"\b[0-9a-f]{12,64}\b"), "<HEX>"),          # container/image ids
    (re.compile(r"root@[0-9a-zA-Z_-]+"), "root@<HOST>"),    # container hostname
    (re.compile(r"\b\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}[.,0-9]*(Z|[+-]\d{2}:?\d{2})?"),
     "<TIMESTAMP>"),
    (re.compile(r"\b(Mon|Tue|Wed|Thu|Fri|Sat|Sun) [A-Z][a-z]{2} +\d+ \d{2}:\d{2}:\d{2}( [A-Z]{2,5})? \d{4}"),
     "<DATE>"),
    (re.compile(r"/tmp/[A-Za-z0-9._-]+"), "/tmp/<TMP>"),    # temp paths
    (re.compile(r"\bpid[= ]?\d+\b", re.I), "pid=<PID>"),
    (re.compile(r"\b\d+(\.\d+)?\s*(ms|s|sec|seconds|MB/s|kB/s|B/s)\b"), "<RATE>"),
    (re.compile(r"(Fetched .* in |Reading package lists|Building dependency tree|Reading state information)[^\n]*"),
     r"\1<APT>"),
    (re.compile(r"Get:\d+ [^\n]*\n"), ""),                  # apt download ordering
    (re.compile(r"debconf: [^\n]*\n"), ""),
    # pip fetch-source wording: "Downloading x.whl" vs "Using cached x.whl"
    # depends only on wheel-cache warmth, not on what gets installed.
    (re.compile(r"(?m)^(\s*)(Downloading|Using cached) "), r"\1<FETCHED> "),
    # pip's "Successfully installed" line includes version suffixes in some
    # environments and omits them in others; the actual installed trees are
    # compared via artifact hashes.
    (re.compile(r"(?m)^(Successfully installed [^\n]*)$"),
     lambda m: re.sub(r"-\d[\w.!+]*", "", m.group(1))),
    # `ls -l` metadata: block totals, mode strings (umask differs between
    # docker-root and the rootless user namespace), link counts, directory
    # sizes (tmpfs overlay vs ext4) and mtimes. File *sizes* are kept, and
    # file *contents* are compared separately via artifact hashes.
    (re.compile(r"(?m)^total \d+"), "total <N>"),
    (re.compile(r"(?m)^([-dlbcps])[rwxsStT-]{9}\.?\s+\d+\s+(\S+)\s+(\S+)\s+(\d+)\s+[A-Z][a-z]{2} +\d+ +[\d:]{4,5}"),
     lambda m: (f"{m.group(1)}<MODE> <L> {m.group(2)} {m.group(3)} "
                f"{'<DSZ>' if m.group(1) == 'd' else m.group(4)} <LSDATE>")),
]


def normalize_text(text: str) -> str:
    for pat, repl in NORMALIZATIONS:
        text = pat.sub(repl, text)
    return text


def hash_artifact_tree(trial_dir: Path) -> dict:
    """sha256 of every file under the trial's downloaded artifacts.

    Text files are hashed after normalize_text(), so embedded timestamps and
    other §10-exempt nondeterminism do not count as state differences.
    Symlinks are followed (one runtime dereferences e.g. a venv's lib64->lib
    symlink during artifact download, the other preserves it — same content).
    Excluded: compiled bytecode (__pycache__/*.pyc embeds source mtimes) and
    Harbor's own download manifest.json at the artifact root."""
    art = trial_dir / "artifacts"
    hashes = {}
    if not art.is_dir():
        return hashes
    for root, dirs, files in os.walk(art, followlinks=True):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for name in files:
            f = Path(root) / name
            rel = str(f.relative_to(art))
            if rel == "manifest.json" or name.endswith(".pyc"):
                continue
            try:
                raw = f.read_bytes()
            except OSError:
                continue
            try:
                data = normalize_text(raw.decode("utf-8")).encode("utf-8")
            except UnicodeDecodeError:
                data = raw
            hashes[rel] = hashlib.sha256(data).hexdigest()
    return hashes


def compare_trials(task: str, docker: dict, other: dict, other_name: str) -> dict:
    dd, od = docker["trial_dir"], other["trial_dir"]
    dr, orr = docker["result"], other["result"]

    reward = extract_reward

    def exit_code(trial_dir):
        p = trial_dir / "agent" / "exit-code.txt"
        return int(p.read_text().strip()) if p.exists() else 0

    def oracle_out(trial_dir):
        p = trial_dir / "agent" / "oracle.txt"
        return normalize_text(p.read_text()) if p.exists() else "<MISSING>"

    d_hashes, o_hashes = hash_artifact_tree(dd), hash_artifact_tree(od)
    file_diffs = sorted(
        set(d_hashes) ^ set(o_hashes)
        | {k for k in set(d_hashes) & set(o_hashes) if d_hashes[k] != o_hashes[k]})

    checks = {
        "command": "chmod +x /solution/solve.sh && /solution/solve.sh",
        "solve_exit_code": (exit_code(dd), exit_code(od)),
        "oracle_output_normalized_equal": (oracle_out(dd) == oracle_out(od)),
        "verifier_reward": (reward(dr), reward(orr)),
        "exception": (bool(dr.get("exception_info")), bool(orr.get("exception_info"))),
        "artifact_files_compared": len(set(d_hashes) | set(o_hashes)),
        "artifact_files_differing": file_diffs,
    }
    equivalent = (
        checks["solve_exit_code"][0] == checks["solve_exit_code"][1]
        and checks["oracle_output_normalized_equal"]
        and checks["verifier_reward"][0] == checks["verifier_reward"][1]
        and checks["exception"] == (False, False)
        and not file_diffs)
    return {"task": task, "runtimes": ["docker", other_name],
            "equivalent": equivalent, "checks": checks,
            "docker_trial": str(dd), f"{other_name}_trial": str(od)}


def has_oracle_solution(task_dir: Path) -> bool:
    """Use Harbor's OS-aware script discovery, including per-step overrides."""
    from harbor.models.task.task import Task
    task = Task(task_dir)
    task_os = task.config.environment.os
    discover = getattr(task.paths, "discovered_solve_path_for", None)
    root_script = discover(task_os) if discover else task.paths.solve_path
    steps = getattr(task.config, "steps", None) or []
    if not steps:
        return root_script is not None and root_script.is_file()
    for step in steps:
        if task.paths.step_solution_dir(step.name).exists():
            script = task.paths.discovered_step_solve_path_for(step.name, task_os)
        else:
            script = root_script
        if script is None or not script.is_file():
            return False
    return True


def parity_check(args, runs_root: Path) -> int:
    ensure_big_disk_env()
    header, _ = load_manifest(Path(args.manifest))
    run_id, run_dir, task_ids = resolve_sample(args, runs_root)
    tasks_dir = materialize_tasks(header, task_ids, run_dir / "tasks")
    warn_if_images_cold(task_ids, args.runtime)
    parity_dir = run_dir / "parity"
    parity_dir.mkdir(parents=True, exist_ok=True)

    if args.docker_only and args.apptainer_only:
        raise SystemExit("--docker-only and --apptainer-only are exclusive.")
    selected_task_ids = list(task_ids)
    skipped = [t for t in task_ids if not has_oracle_solution(tasks_dir / t)]
    task_ids = [t for t in task_ids if t not in skipped]
    for task_id in skipped:
        print(f"[oracle] WARNING: {task_id}: no runnable oracle solution script; "
              "skipping oracle validation. Task remains selected for model generation.")
    oracle_coverage = {
        "selected_task_ids": selected_task_ids,
        "oracle_checked_task_ids": task_ids,
        "oracle_skipped": [{"task": t, "reason": "missing_oracle_solution"} for t in skipped],
    }
    if not task_ids:
        report = {"run_id": run_id, "task_ids": [], **oracle_coverage,
                  "oracle_all_reward_1": None, "parity_passed": None,
                  "status": "skipped_no_oracle_solutions", "comparisons": []}
        (parity_dir / "parity_report.json").write_text(json.dumps(report, indent=2) + "\n")
        print("[oracle] WARNING: no oracle solutions available; oracle validation not performed.")
        return 0
    if skipped:
        # Keep the original task directory/sample untouched for model generation.
        selection_hash = hashlib.sha256(json.dumps(task_ids).encode()).hexdigest()[:16]
        oracle_tasks_dir = parity_dir / f"oracle_tasks_{selection_hash}"
        oracle_tasks_dir.mkdir(exist_ok=True)
        for task_id in task_ids:
            target = oracle_tasks_dir / task_id
            if not target.exists():
                target.symlink_to((tasks_dir / task_id).resolve(), target_is_directory=True)
        tasks_dir = oracle_tasks_dir

    if args.apptainer_only:
        # Baseline-independent oracle validation: run the golden solutions in
        # Apptainer alone and check every task scores reward 1. Validates the
        # runtime on Docker-less hosts (e.g. Capella) without the sequoia
        # baseline; the docker<->apptainer comparison still requires both.
        runtimes = ["apptainer_bridge"] if args.runtime == "apptainer_bridge" \
            else ["apptainer"]
    else:
        runtimes = ["docker"] + ([] if args.docker_only else ["apptainer"])
    for rt in runtimes:
        # A runtime whose oracle results are already complete in this run dir
        # (e.g. a Docker baseline imported from another host) is only READ —
        # its binary is not needed. Require the binary only when the half
        # still has to run. Completeness is re-derived identically below.
        job_dir_probe = run_dir / "parity" / f"oracle_{rt}"
        probe = {t: r for t, r in collect_trial_results(job_dir_probe).items()
                 if not r["result"].get("exception_info")}
        if set(probe) >= set(task_ids):
            print(f"[parity] {rt}: complete cached oracle results found - "
                  f"binary not required")
            continue
        require_runtime(rt)

    jobs = {}
    for rt in runtimes:
        job_dir = parity_dir / f"oracle_{rt}"
        existing = {t: r for t, r in collect_trial_results(job_dir).items()
                    if not r["result"].get("exception_info")}
        if set(existing) >= set(task_ids):
            print(f"[parity] reusing completed {rt} oracle job in {job_dir}")
            jobs[rt] = existing
            continue
        # Drop failed trials so --auto-resume reruns them instead of keeping
        # the old exception result.
        for t, r in collect_trial_results(job_dir).items():
            if r["result"].get("exception_info"):
                print(f"[parity] {rt}: removing failed trial {r['trial_dir'].name}")
                shutil.rmtree(r["trial_dir"], ignore_errors=True)
        cfg = build_harbor_config(rt, tasks_dir, job_dir, agent="oracle",
                                  n_attempts=1, oracle=True)
        cfg["job_name"] = f"parity-{rt}"
        if rt == "apptainer_bridge":
            # marin harbor lives in its own venv, and workers look for SIFs
            # under the 12-char short name (link_sifs_for_bridge).
            link_sifs_for_bridge(runs_root)
        rc = run_harbor_job(cfg, parity_dir / f"harbor_{rt}.yaml",
                            parity_dir / f"harbor_{rt}.log",
                            venv=BRIDGE_VENV if rt == "apptainer_bridge" else None)
        print(f"[parity] {rt} oracle job exited rc={rc}")
        jobs[rt] = collect_trial_results(job_dir)
        missing = set(task_ids) - set(jobs[rt])
        if missing:
            print(f"[parity] WARNING: {rt} job missing results for {sorted(missing)}")

    report = {"run_id": run_id, "task_ids": task_ids, **oracle_coverage,
              "runtimes": runtimes,
              "command_sequence": "oracle solution (solution/solve.sh) per task, "
                                  "then the task verifier (tests/test.sh)",
              "normalizations": [p.pattern for p, _ in NORMALIZATIONS],
              "comparisons": [], "docker_only": bool(args.docker_only),
              "apptainer_only": bool(args.apptainer_only)}
    if args.apptainer_only:
        for t in task_ids:
            a = jobs[runtimes[0]].get(t)
            report["comparisons"].append({
                "task": t, "apptainer_reward": a and extract_reward(a["result"]),
                "note": "oracle-in-apptainer only; docker parity NOT tested"})
        rewards = [c["apptainer_reward"] for c in report["comparisons"]]
        report["oracle_all_reward_1"] = all(r == 1.0 for r in rewards)
        report["parity_passed"] = None
        out = parity_dir / "parity_report.json"
        out.write_text(json.dumps(report, indent=2, default=str) + "\n")
        print(f"[parity] report: {out}")
        print(f"[parity] oracle_all_reward_1={report['oracle_all_reward_1']} "
              f"rewards={rewards}")
        return 0 if report["oracle_all_reward_1"] else 1
    if args.docker_only:
        for t in task_ids:
            d = jobs["docker"].get(t)
            report["comparisons"].append({
                "task": t, "docker_reward": d and extract_reward(d["result"]),
                "note": "baseline only; parity NOT tested"})
        report["parity_passed"] = None
    else:
        for t in task_ids:
            d, a = jobs["docker"].get(t), jobs["apptainer"].get(t)
            if not d or not a:
                report["comparisons"].append(
                    {"task": t, "equivalent": False,
                     "error": f"missing trial (docker={bool(d)}, apptainer={bool(a)})"})
                continue
            report["comparisons"].append(compare_trials(t, d, a, "apptainer"))
        report["parity_passed"] = all(
            c.get("equivalent") for c in report["comparisons"])
    out = parity_dir / "parity_report.json"
    out.write_text(json.dumps(report, indent=2, default=str) + "\n")
    print(f"[parity] report: {out}")
    if not args.docker_only:
        print(f"[parity] PASSED={report['parity_passed']}")
    return 0 if (args.docker_only or report["parity_passed"]) else 1


# ---------------------------------------------------------------------------
# Student rollouts (§5, §11): thin wrapper over data/local/run_tracegen.py
# ---------------------------------------------------------------------------

TERMINATION_TAXONOMY_NOTE = """§6 taxonomy mapping:
success             verifier reward == 1 and no exception
verifier failure    trial completed, reward == 0 (or verifier error)
task timeout        AgentTimeoutError / VerifierTimeoutError
max-turn limit      agent stopped at --max-turns (only if that flag was passed)
model/API failure   exceptions from the LLM endpoint (APIError, Connection...)
environment failure environment build/start/exec exceptions
"""


def link_sifs_for_bridge(runs_root: Path) -> int:
    """Publish marin-style short SIF names for images built by local mode.

    Local mode names images build_<task>-<sha256>.sif; bridge workers look for
    build_<task>-<sha256[:12]>.sif over the same Dockerfile bytes. The fork now
    creates the short name at build time, but images built before that (or by
    another host) still need it — link them here, idempotently, so a bridge run
    starts with a warm cache instead of rebuilding every task image.
    """
    cache = BIG_DISK / "teacher_ranking_proxy" / "harbor_sif_cache"
    cache.mkdir(parents=True, exist_ok=True)
    linked = 0
    for images in runs_root.glob("*/traces/*/*/harbor_jobs/trace_images"):
        for sif in images.glob("build_*.sif"):
            stem = sif.name[:-4]
            task, _, digest = stem.rpartition("-")
            if len(digest) != 64:
                continue
            short = cache / f"{task}-{digest[:12]}.sif"
            if not short.exists():
                try:
                    short.symlink_to(sif)
                    linked += 1
                except OSError:
                    pass
    if linked:
        print(f"[bridge] linked {linked} local-mode SIFs into {cache}")
    return linked


def classify_termination(result: dict, max_turns_used: bool) -> str:
    exc = result.get("exception_info") or {}
    exc_type = exc.get("exception_type", "") or ""
    if exc_type:
        if "AgentTimeout" in exc_type or "VerifierTimeout" in exc_type:
            return "task timeout"
        if any(s in exc_type for s in ("API", "Connection", "RateLimit",
                                       "BadRequest", "LLM", "Engine")):
            return "model/API failure"
        return "environment failure"
    if extract_reward(result) == 1:
        return "success"
    agent_result = result.get("agent_result") or {}
    if max_turns_used and agent_result.get("n_episodes") is not None:
        # only meaningful when an explicit --max-turns was requested
        pass
    return "verifier failure"


def generate(args, runs_root: Path) -> int:
    ensure_big_disk_env()
    require_runtime(args.runtime)
    venv = VENV
    if args.runtime == "apptainer_bridge":
        venv = BRIDGE_VENV
        if not (venv / "bin" / "python").exists():
            raise SystemExit(
                f"bridge venv not found at {venv}; build it with\n"
                "  uv venv --python 3.12 $WS_ROOT/teacher_ranking_proxy/venv_bridge\n"
                "  uv pip install -p .../venv_bridge/bin/python -e '.[datagen,harbor-docker]'\n"
                "(pins harbor to marin-community/harbor@main per pyproject) or set HARBOR_BRIDGE_VENV.")
        # marin-main CLI supports (and non-interactively needs) --yes:
        os.environ.pop("OTAGENT_HARBOR_NO_YES", None)
        link_sifs_for_bridge(runs_root)
    if args.runtime == "apptainer":
        # Enable the shim's anchored per-instance tmux server (survives
        # across execs; short-lived-exec-spawned servers die on this host).
        # Harbor's ApptainerEnvironment/TmuxSession honor this gate.
        os.environ.setdefault("HARBOR_TMUX_ANCHOR", "1")
    header, _ = load_manifest(Path(args.manifest))
    run_id, run_dir, task_ids = resolve_sample(args, runs_root)
    tasks_dir = materialize_tasks(header, task_ids, run_dir / "tasks")

    model_slug = args.model.replace("/", "__")
    trace_root = run_dir / "traces" / model_slug / args.runtime
    trace_root.mkdir(parents=True, exist_ok=True)
    jobs_dir = trace_root / "harbor_jobs"
    job_name = f"student-{model_slug}-{args.runtime}"

    done = {t: r for t, r in collect_trial_results(jobs_dir).items()
            if trial_is_complete(r["result"])}
    todo = [t for t in task_ids if t not in done]
    gpu_used = None
    rc = 0
    if args.resume and not todo and args.attempts == 1:
        print(f"[gen] all {len(task_ids)} trials already complete; nothing to do")
    else:
        # Each invocation runs as its own Harbor job: the vLLM served-model id
        # is fresh every time, so Harbor's --auto-resume rejects the old job
        # dir as "different config". Resume instead = new job restricted to
        # the tasks without a clean trial; collect_trial_results() merges.
        resume_batch = args.resume and args.attempts == 1 and todo != list(task_ids)
        n = 1
        while (jobs_dir / job_name).exists():
            n += 1
            job_name = f"student-{model_slug}-{args.runtime}-r{n}"
        gpu = gpu_used = select_gpu()
        if gpu is not None:
            print(f"[gen] using GPU {gpu}")
        else:
            print(f"[gen] non-sequoia host: GPU visibility left to the "
                  f"scheduler (CUDA_VISIBLE_DEVICES="
                  f"{os.environ.get('CUDA_VISIBLE_DEVICES')!r})")
        if resume_batch:
            print(f"[gen] resume: {len(todo)} tasks still needed -> job "
                  f"{job_name}: {sorted(todo)}")
        cfg = build_harbor_config(args.runtime, tasks_dir, jobs_dir,
                                  agent=args.agent, n_attempts=args.attempts,
                                  oracle=False, max_turns=args.max_turns)
        import yaml
        cfg_path = trace_root / "harbor_job.yaml"
        cfg_path.write_text(yaml.safe_dump(cfg, sort_keys=False))

        runner = REPO_ROOT / "data" / "local" / "run_tracegen.py"
        py = venv / "bin" / "python"
        cmd = [str(py), str(runner),
               "--harbor_config", str(cfg_path),
               "--tasks_input_path", str(tasks_dir),
               "--datagen_config", str(DATAGEN_YAML),
               # Upstream infers Apptainer from environment.type in cfg_path.
               "--model", args.model,
               "--agent", args.agent,
               "--job_name", job_name,
               "--n_concurrent", str(args.n_concurrent),
               "--n_attempts", str(args.attempts),
               "--gpus", str(args.gpus),
               "--experiments_dir", str(trace_root / "experiments"),
               ]
        # Job-specific ports (set by hpc/nhr/run_pilot.sbatch): run_tracegen's CLI
        # defaults (8000/6379) otherwise override the datagen YAML's backend ports,
        # so two jobs on one node would share one vLLM server.
        for flag, var in (("--api_port", "TRP_API_PORT"), ("--ray_port", "TRP_RAY_PORT")):
            if os.environ.get(var):
                cmd += [flag, os.environ[var]]
        if resume_batch:
            # run_tracegen passes the dataset via harbor's -p flag, which
            # bypasses any dataset filter in the config; restrict via -t.
            for t in sorted(todo):
                cmd += ["--harbor_extra_arg=-t", f"--harbor_extra_arg={t}"]
        if args.dry_run:
            cmd += ["--dry_run"]
        env = {"PYTHONPATH": str(REPO_ROOT),
               "PATH": f"{venv / 'bin'}:{os.environ['PATH']}"}
        if gpu is not None:
            env["CUDA_VISIBLE_DEVICES"] = str(gpu)
            # make CUDA indices match the nvidia-smi/NVML indices picked from
            env["CUDA_DEVICE_ORDER"] = "PCI_BUS_ID"
        print(f"[gen] {' '.join(cmd)}")
        log_path = trace_root / "run_tracegen.log"
        with open(log_path, "a") as log:
            rc = subprocess.run(cmd, stdout=log, stderr=log,
                                env={**os.environ, **env},
                                cwd=str(REPO_ROOT)).returncode
        print(f"[gen] run_tracegen exited rc={rc}; log: {log_path}")

    if args.attempts > 1:
        from attempt_summary import summarize_attempts
        report = summarize_attempts(jobs_dir, task_ids, args.attempts)
        (trace_root / "attempt_summary.json").write_text(json.dumps(report, indent=2) + "\n")
        print(f"[gen] all-attempt metrics: {report['overall']}")

    # Summarize results with §6 termination reasons.
    trials = collect_trial_results(jobs_dir)
    summary = {}
    for t in task_ids:
        tr = trials.get(t)
        if not tr:
            summary[t] = {"status": "missing", "termination_reason": "not run"}
            continue
        r = tr["result"]
        summary[t] = {
            "reward": extract_reward(r),
            "termination_reason": classify_termination(r, args.max_turns is not None),
            "trial_dir": str(tr["trial_dir"]),
            "exception": (r.get("exception_info") or {}).get("exception_type"),
        }
    meta_dir = run_dir / "metadata"
    meta_dir.mkdir(exist_ok=True)
    write_metadata(args, header, run_id, task_ids, summary, meta_dir,
                   trace_root, gpu_used)
    n_ok = sum(1 for s in summary.values() if s.get("reward") == 1)
    print(f"[gen] rollout summary ({n_ok}/{len(task_ids)} reward=1):")
    for t, s in summary.items():
        print(f"    {t}: reward={s.get('reward')} -> {s['termination_reason']}")
    print(f"[gen] traces under {trace_root}")
    return rc


def _resolve_gpus_used(gpu_used, meta_dir: Path):
    """GPU for this invocation, or the recorded one on summary-only passes."""
    if gpu_used is not None:
        return [gpu_used]
    prev = meta_dir / "generation_metadata.json"
    if prev.exists():
        try:
            return json.loads(prev.read_text()).get("gpu_devices_used")
        except json.JSONDecodeError:
            pass
    return None


def write_metadata(args, header, run_id, task_ids, summary, meta_dir: Path,
                   trace_root: Path, gpu_used=None) -> None:
    # Version metadata must reflect the venv that actually ran the job
    # (bridge runs use BRIDGE_VENV with marin-main harbor).
    used_venv = BRIDGE_VENV if args.runtime == "apptainer_bridge" else VENV
    def _cmd_out(*cmd):
        try:
            return subprocess.run(cmd, capture_output=True, text=True,
                                  cwd=str(REPO_ROOT)).stdout.strip()
        except OSError:
            return None

    import yaml
    datagen = yaml.safe_load(DATAGEN_YAML.read_text())
    model_rev = None
    hub_dir = Path(os.environ["HF_HOME"]) / "hub" / \
        ("models--" + args.model.replace("/", "--")) / "snapshots"
    if hub_dir.is_dir():
        revs = [d.name for d in hub_dir.iterdir() if d.is_dir()]
        model_rev = revs[0] if len(revs) == 1 else revs
    marker = Path(args.model) / "download_complete.json"
    if marker.is_file():
        model_rev = json.loads(marker.read_text())["revision"]
    meta = {
        "openthoughts_agent_commit": _cmd_out("git", "rev-parse", "HEAD"),
        "task_repo": header["task_repo"],
        "task_dataset_revision": header["task_revision"],
        "teacher_traj_repo": header["traj_repo"],
        "teacher_traj_revision": header["traj_revision"],
        "student_model": args.model,
        "student_model_revision": model_rev,
        "harbor_version": _cmd_out(str(used_venv / "bin" / "python"), "-c",
                                   "import harbor;print(harbor.__version__)"),
        "vllm_version": _cmd_out(str(used_venv / "bin" / "python"), "-c",
                                 "import vllm;print(vllm.__version__)"),
        "container_backend": args.runtime,
        "agent": args.agent,
        "seed": args.seed,
        "run_id": run_id,
        "sampled_task_ids": task_ids,
        "attempts_per_task": args.attempts,
        "timeout_configuration": "per-task task.toml [agent]/[verifier] "
                                 "timeout_sec; no global override (§6)",
        "max_turns": args.max_turns,
        "generation_parameters": {
            "datagen_yaml": str(DATAGEN_YAML),
            "vllm_server": datagen.get("vllm_server"),
            "engine": datagen.get("engine"),
            "agent_kwargs": yaml.safe_load(HARBOR_TEMPLATE.read_text())["agents"][0].get("kwargs", {}),
        },
        "gpu_devices_used": _resolve_gpus_used(gpu_used, meta_dir),
        "resolved_paths": {
            "HF_HOME": os.environ.get("HF_HOME"),
            "HF_DATASETS_CACHE": os.environ.get("HF_DATASETS_CACHE"),
            "runs_root": str(DEFAULT_RUNS_TARGET),
            "trace_root": str(trace_root),
            "venv": str(used_venv),
        },
        "termination_taxonomy": TERMINATION_TAXONOMY_NOTE,
        "rollout_summary": summary,
        "rollout_summary_scope": "first clean attempt per task; see attempt_summary.json for all attempts",
    }
    out = meta_dir / "generation_metadata.json"
    out.write_text(json.dumps(meta, indent=2) + "\n")
    print(f"[meta] wrote {out}")


def maybe_upload(args, trace_root: Path) -> None:
    """§12: opt-in only; never called unless --upload-hf was passed."""
    repo = os.environ.get("HF_STUDENT_TRACES_REPO")
    if not repo:
        raise SystemExit("--upload-hf requires HF_STUDENT_TRACES_REPO in the "
                         "environment/.env (never hard-coded).")
    sys.path.insert(0, str(REPO_ROOT))
    from hpc.launch_utils import upload_traces_to_hf
    for job_dir in sorted((trace_root / "harbor_jobs").iterdir()):
        upload_traces_to_hf(job_dir=job_dir, hf_repo_id=repo, hf_private=True,
                            hf_token=os.environ.get("HF_TOKEN"),
                            hf_episodes="last", dry_run=False)


# ---------------------------------------------------------------------------


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dataset", default="terminal_lego")
    p.add_argument("--model", default="Qwen/Qwen3-8B")
    p.add_argument("--runtime", choices=["docker", "apptainer", "apptainer_bridge"],
                   default="docker",
                   help="apptainer_bridge runs tasks on bridge workers "
                        "(requires APPTAINER_BRIDGE_URL and HARBOR_BRIDGE_VENV, "
                        "which holds marin-community/harbor@main)")
    p.add_argument("--n-tasks", type=int, default=None)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--gpus", type=int, default=1,
                   help="GPUs for the vLLM serve (raise with tensor_parallel_size "
                        "in the datagen YAML, e.g. 2 for a 32B student)")
    p.add_argument("--sample-file", default=None)
    p.add_argument("--run-id", default=None)
    p.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    p.add_argument("--attempts", type=int, default=1)
    p.add_argument("--agent", default="terminus-2")
    p.add_argument("--max-turns", type=int, default=None,
                   help="Explicit experiments only (§6); off by default.")
    p.add_argument("--n-concurrent", type=int, default=10)
    p.add_argument("--resume", action="store_true")
    p.add_argument("--dry-run", dest="dry_run", action="store_true",
                   help="Print the run_tracegen command without executing.")
    p.add_argument("--upload-hf", action="store_true",
                   help="Opt-in HF upload (§12); never on by default.")
    p.add_argument("--check-runtime", action="store_true")
    p.add_argument("--parity-check", action="store_true")
    p.add_argument("--docker-only", action="store_true",
                   help="With --parity-check: run only the Docker baseline "
                        "(parity criterion then remains not satisfied).")
    p.add_argument("--apptainer-only", action="store_true",
                   help="With --parity-check: run only the oracle solutions "
                        "in Apptainer and require reward 1 on every task - "
                        "baseline-independent runtime validation for "
                        "Docker-less hosts (docker parity remains untested).")
    args = p.parse_args()

    if args.check_runtime:
        ensure_big_disk_env()
        return check_runtime()

    runs_root = ensure_runs_root()
    if args.parity_check:
        return parity_check(args, runs_root)

    rc = generate(args, runs_root)
    if rc == 0 and args.upload_hf:
        model_slug = args.model.replace("/", "__")
        run_id, run_dir, _ = resolve_sample(args, runs_root)
        maybe_upload(args, run_dir / "traces" / model_slug / args.runtime)
    return rc


if __name__ == "__main__":
    sys.exit(main())
