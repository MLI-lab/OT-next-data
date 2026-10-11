# CalibForge integration plan: images and preparation

Source: `AweAI-Team/CalibForge`, revision `fb1e75441a94b8bb0ced08acd6b59e711704d70a`,
`tasks.parquet` (5,431 tasks). This is a proposal. Nothing in the dataset or in
`patch.py` was changed while writing it. No jobs were run, so every timing
statement below is an expectation to confirm in a pilot, not a measurement.

## Summary

1. **Keep one image per distinct environment.** Moving task initialization into
   runtime setup is not available for this dataset and would not pay off
   (reasons below).
2. **Small image reduction (optional, low risk):** give tasks whose build inputs
   differ only in Dockerfile comments and blank lines one canonical Dockerfile.
   Expected 5,381 to about 5,307 unique images, 140 tasks in 18 groups touched.
3. **Verifier preparation (the real stage 3 `--review-setup` work):** 3,141
   tasks install curl, uv, a Python interpreter and pytest from the network
   inside `tests/test.sh`. Bake those tools into the existing task images and
   move the preparation commands into `tests/setup.sh`. No new images.

## What the source looks like

Image audit (`python -m validation.patch_repair_loop.image_audit SOURCE`):

| Measure | Value |
| --- | --- |
| Tasks | 5,431 |
| Unique Dockerfiles | 5,361 |
| Unique build inputs (whole `environment/`) | 5,381 |
| Tasks with `setup_files/`, `environment/setup.sh`, `tests/setup.sh`, `solution/solve.sh` | 0 each |
| Base `ubuntu:24.04` | 2,616 tasks |
| Base `aweaiteam/calibforge:tb2-verifier-base-v1@sha256:0f61b2...` | 2,405 tasks |
| Base `python:3.13-slim-bookworm` | 319 tasks |
| Other 12 base sequences | 91 tasks |

The Apptainer image cache key is the hash of the task's `environment/`
directory, so the current structure gives 5,381 images. The 50 tasks that
already share are 10 groups with byte-identical build inputs (largest group:
26 tasks whose Dockerfile is only `FROM` the verifier base plus `WORKDIR /app`).

Additional analysis of the Dockerfiles (heuristic, done for this plan):

- Grouping by `FROM` + install-like `RUN` steps + `ENV` gives 2,783 distinct
  dependency layers, 2,468 of them used by a single task. The ten largest cover
  1,761 tasks.
- 2,131 tasks have further `RUN` steps that are not package installation:
  running generator scripts (825), `chown`/`chmod`/ACLs (724), compilation (767),
  service or database initialization (252), user and group creation (109).
- 5,062 tasks `COPY` files. 120 tasks carry more than 1 MB of build context and
  11 more than 20 MB (largest 537 MB,
  `contrastive_solver-system-administration_20260604_003113_007`).
- 124 tasks declare `CMD` or `ENTRYPOINT`, 13 declare `USER`.

## Why runtime task setup is not proposed

- **No trigger exists for this dataset.** The shared runner starts task setup
  only when `solution/solve.sh` invokes `/setup_files/.../setup.sh`
  (`validation/stages/task_setup.py::detect`, used by stage 3, stage 5 and the
  fresh verifier). CalibForge has no reference solutions, the approved policy
  forbids generating substitutes, and shared infrastructure must not change.
  Files in `setup_files/` would be uploaded but never executed in validation.
  Converting tasks to Harbor multi-step tasks to get its step `setup.sh` hook
  would restructure every task and stage 3 would still not run or time it.
- **The saving would be limited.** With 2,468 single-task dependency layers the
  best case is roughly 2,800 images, and only after auditing 2,131 Dockerfiles
  with stateful build steps (users, permissions, compiled artifacts, databases,
  generated challenge data) one by one. Replaying those at container start is
  exactly the slow or unreliable setup the protocol says to keep in images.
- **Build context sizes.** Uploading up to 537 MB per container start cannot
  meet the 30 second mean.

Result: task preparation stays "start the container", which is the fastest and
most reliable option under `--review-setup`.

## Change A: canonical Dockerfile for equivalent environments (optional)

Goal: let tasks with the same effective build share one cached image.

- **Affected tasks:** groups where all non-Dockerfile files under `environment/`
  are identical (names and bytes) and the Dockerfiles are equal after removing
  blank lines, full-line `#` comments and trailing whitespace. My looser
  whitespace-collapsing check found 18 such groups with 140 tasks
  (examples: `contrastive_solver-data-processing_20260603_181444_004` with
  `contrastive_solver-model-training_20260618_093107_001`;
  `contrastive_solver-machine-learning_20260510_062946_002` with
  `multi_solver-machine-learning_20260413_174325_002`). Recompute with the strict
  rule in `patch.py` and report the actual numbers; they may be slightly lower.
- **Implementation in `patch.py`:**
  1. First pass over the Parquet: compute the group key per task from the
     upstream bytes. Skip Dockerfiles containing heredocs (`<<`) or a parser
     directive line (`# syntax=`, `# escape=`).
  2. For each group with more than one distinct Dockerfile, pick the
     Dockerfile of the lexicographically smallest task ID as canonical and
     write it to the other members before R1/R2 run. R1/R2 are deterministic,
     so equal inputs stay equal.
  3. The group key must also include whatever Change B appends to the
     Dockerfile, otherwise groups split again. Only merge tasks whose appended
     verifier layer is identical.
- **Check:** rerun the image audit on the patched output and compare
  `unique_build_payloads` with the expected count.
- **Reporting:** label `shared-image`, reason "Use the comment-free equivalent
  Dockerfile of task X so identical environments share one cached image."

This changes comments only, never an instruction, so the built environment is
unchanged. If the implementer prefers zero risk, skipping Change A costs 74
extra image builds (1.4%).

## Change B: verifier preparation in images and `tests/setup.sh`

Classification of `tests/test.sh` (965 distinct scripts):

| Class | Tasks | Preparation at verification time |
| --- | --- | --- |
| Verifier base, pytest from `/opt/verifier` | 2,249 | none |
| TB2 template: `apt-get update`, `apt-get install -y curl`, uv 0.9.5 installer, `uvx -p <py> -w ... pytest` | 2,569 | network, every run |
| TB2 template plus extra `apt-get install` and/or `pip install` | 175 | network, every run |
| CalibForge template (`CF_UVX_BIN`, installs uv only when missing) | 102 | network, every run |
| Other scripts with network installs | 295 | network, every run |
| Other scripts without installs | 41 | none |

All 3,038 uv installs pin uv 0.9.5. Requested interpreters: 3.13 (2,636),
3.12 (71), 3.11 (60). Most `-w` sets are `pytest==8.4.1` and
`pytest-json-ctrf==0.3.5` only (2,182 tasks); 164 tasks have at least one
unpinned `-w` package.

Without `tests/setup.sh` the review measures no verifier preparation at all,
so these tasks would pass trivially while every real verification downloads a
Python build and wheels. Verifier limits are tight for some tasks: 5% of the
verifier timeout is 6 s for 102 tasks (120 s), 15 s for 1,056 tasks (300 s) and
30 s for 2,352 tasks (600 s).

### B1: bake the verifier toolchain into the task image (3,141 tasks)

Append one final `RUN` to the task's Dockerfile, generated from that task's own
`test.sh`, that performs the same installation at build time:

- `apt-get update && apt-get install -y curl` (plus any extra apt packages the
  script installs before its checks);
- the pinned uv installer (`https://astral.sh/uv/0.9.5/install.sh`) with
  `HOME=/root`, so uv lands in `/root/.local/bin` where the script expects it;
- one warm-up call with the script's exact `-p` and `-w` arguments
  (`uvx -p 3.13 -w pytest==8.4.1 -w pytest-json-ctrf==0.3.5 pytest --version`),
  which stores the interpreter and the tool environment in uv's default
  locations under `/root`.

Notes for the implementer:

- Use uv's default paths. They need no `ENV` and are what the unmodified
  script populates anyway. In the non-fakeroot fallback the worker copies
  `/root` into a per-instance directory at start; measure that cost in the
  pilot.
- These are test-runner tools (pytest, CTRF plugin, and for some tasks numpy,
  pandas, scipy and similar). They sit in uv's cache, not on the agent's
  interpreter path, and upstream already lets the agent download them. They
  are acceptable in the task image; no separate verifier image is needed.
- For the 164 tasks with unpinned `-w` packages the warm-up fixes whatever
  version resolves at build time, but `uvx` may still consult the index later.
  Do not rewrite the requirement; record these tasks and judge them by the
  review timings.
- Appending a `RUN` does not add images: every affected environment is already
  unique, and Change A groups stay merged when the appended line is identical.
- Reporting: label `verifier-dependencies-in-image`, reason "Install the
  verifier's pinned test runner at image build time instead of at each
  verification."

### B2: move preparation commands into `tests/setup.sh`

The protocol requires separating setup from checks so the review can time it.
The constraint not to modify verifiers is respected by moving lines verbatim
and leaving the pytest command, arguments, versions and reward handling
untouched:

- **TB2 template (2,744 tasks including variants):** `tests/setup.sh` receives
  the leading preparation block (`apt-get update`, `apt-get install -y curl`,
  the uv installer, and any further leading `apt-get`/`pip install` lines).
  `tests/test.sh` keeps everything from `source $HOME/.local/bin/env` onward:
  the working-directory check, the `uvx ... pytest` call and the reward lines.
- **CalibForge template (102 tasks):** the script already skips installation
  when `uvx` is on `PATH`, but looks in `/tmp/calibforge-uv-bin` otherwise.
  Move the bootstrap block (from `CF_UVX_BIN=` to the closing `fi`) into
  `setup.sh`; `test.sh` keeps the `CF_UVX_BIN` lookup with the same fallback
  path, so it finds the binary whether setup installed it or the image did.
- **Other network scripts (295 tasks):** move only a maximal leading run of
  recognized installation commands (`apt-get`, uv installer, `pip install`,
  `uv pip install`, `uv venv`). If the script does not start with such a block,
  or the block shares shell state with later lines other than `PATH`, leave the
  task unsplit and list it in the manifest for review.
- **No-preparation scripts (2,290 tasks):** unchanged, no `setup.sh`.

Do not call `setup.sh` from `test.sh`; the runner does that. With B1 in place
`setup.sh` should reduce to an `apt-get update`, a no-op `apt-get install` and a
re-run of the uv installer. If the pilot shows that this still exceeds a task's
5% limit or fails intermittently (most likely for the 102 tasks with a 120 s
verifier timeout), the next step is to guard each command with a presence check
(`command -v curl`, `[ -x "$HOME/.local/bin/uv" ]`). That changes verifier text
beyond a verbatim move, so it needs explicit approval before use.

Reporting: label `verifier-setup-separation`, reason "Move verifier
installation commands from tests/test.sh to tests/setup.sh unchanged so setup
is timed separately; checks and reward logic are unchanged." Pass these through
`change_labels` and `change_reasons` of `write_patch_report`, because the
existing `file_labels` prefix mapping cannot distinguish them from R1 to R3.

## What stays as it is

- Per-task images for all task-specific files, generated data, users,
  permissions, compiled artifacts and service state.
- Shared verifier mode. `environment_mode = "separate"` would need per-task
  artifact declarations and brings no benefit here, since verification inspects
  system state in many tasks.
- `task.toml`, instructions (beyond existing R3), expected outputs, reward
  logic, and the existing rules R1 to R3.

## Validation sequence for the implementer

1. Implement A and B in `data/calibforge/patch.py`; extend the manifest with
   per-rule counts and the list of unsplit verifier scripts.
2. Rerun the image audit on the patched Parquet and confirm the image count.
3. Stage 3 with `--review-setup` on a pilot that covers every `test.sh` class
   above, the three interpreter versions, a task with unpinned `-w` packages,
   a 120 s verifier timeout task and one Change A group.
4. Extend to all tasks, then stages 1 and 5 as required by the approved policy.

Observations outside this plan's scope that may surface later: the Apptainer
builder ignores `CMD`, `ENTRYPOINT` and `USER` (124 and 13 tasks), and it copies
all `COPY` sources before running any `RUN` step.
