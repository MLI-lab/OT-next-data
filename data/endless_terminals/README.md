# Endless Terminals patcher

Source: `obiwan96/endless-terminals`, revision
`26ecf78458e7f756e4d06780d5fbf3dd78e91815`, containing 2,492 Harbor tasks.
The patcher requires the unmodified source Parquet checksum recorded in the audit.

```bash
PYTHONDONTWRITEBYTECODE=1 python data/endless_terminals/patch.py \
  --source /path/to/workspace/datasets/endless_terminals/original/tasks.parquet \
  --output /path/to/workspace/runs/et-patch
```

Use a new output directory for each run. Generated data, reports, images and logs
belong under the workspace `runs/` directory. Outputs include `tasks.parquet`,
the standard patch manifest and archive, a patcher snapshot, `build-manifest.jsonl`,
`image-groups.json` and `summary.json`. IDs remain stable. The default preserves
all tasks; `--filter-reviewed` archives the two source-bound exclusions described
below, including their original payloads and reasons. All task changes originate
in this patcher. The shared builder, bridge and validation setup hook are unchanged.

## Files and workflows

| File | Purpose |
| --- | --- |
| [README.md](README.md) | Patcher behavior, repair scope and validation overview. |
| [patch.py](patch.py) | Compile source tasks, apply reviewed repairs, group shared images and prepare or probe pilots. |
| [decisions.md](decisions.md) | Design decisions and evidence behind image sharing and compatibility repairs. |
| [audit_identity.py](audit_identity.py) | Prepare source-bound expectations and measure identity, ownership and access in fresh containers. |
| [identity-audit.md](identity-audit.md) | Identity audit commands, expectation format and result interpretation. |
| [prepare_setup_checks.py](prepare_setup_checks.py) | Derive bounded setup assertions from literal source Dockerfile commands without executing them. |
| [setup-checks.md](setup-checks.md) | Deterministic setup-check workflow, supported operations and coverage limits. |
| [setup_agent.py](setup_agent.py) | Prepare agent review copies, validate proposals and collect expectations for independent container replay. |
| [setup-agent.md](setup-agent.md) | Optional agent-assisted setup review and replay instructions. |
| [correctness_pilot.py](correctness_pilot.py) | Run bounded correctness repair proposals, validation probes, independent review and accepted-task export. |
| [correctness_prompt.txt](correctness_prompt.txt) | Instructions and constraints supplied to the correctness proposal model. |
| [correctness-pilot.md](correctness-pilot.md) | Correctness pilot commands, acceptance gates and export workflow. |

Start with `patch.py`. For setup review, prepare a plan with `audit_identity.py`,
derive literal checks with `prepare_setup_checks.py`, then replay the plan with
`audit_identity.py run`. Agent-assisted setup review is optional. Correctness
repair pilots are a separate workflow and do not replace setup evidence.
All example storage paths are placeholders; choose shared workspace storage
appropriate for the target cluster and keep generated artifacts outside the checkout.

## Initialization separation and matching dependency recipes

The default output combines `et-apptainer-build-v6` compatibility repairs,
`et-offline-verifier-v1` grading, `et-reviewed-recipes-v4` image sharing and
`et-static-repairs-v2` static repairs.
A task joins a shared image only
when another task has the same normalized dependency execution recipe and runtime
declarations. Package choices, package argument order, versions, install policy,
effective environment, working directory and cache targets are part of the recipe.
Packages are not combined, upgraded or installed by runtime setup.

The normalizer removes `-y`, `--yes`, `--assume-yes`, `-q` and `--quiet` from
literal apt update commands containing only these options. For literal apt install
commands containing only assume-yes options, `--no-install-recommends` and named
packages, it canonicalizes assume-yes to `-y` and puts the independent boolean
options in a fixed position and order. Package order and pins remain intact.
Unknown options, configuration overrides, combined short options and options after
packages keep their original argv. Quiet install options, apt versus apt-get,
pip invocation forms, caches and cleanup remain distinct. Each applied rewrite
is recorded with its source line and before/after command in `build-manifest.jsonl`.

Only literal apt/pip dependency prefixes are normalized. Flat `&&` chains can
be split at a reviewed boundary; explicit exports are captured and source shell
flags are replayed for initialization. Complete initialization suffixes and later
RUN programs retain their bytes, instruction-local ENV/cwd and separate shells.
Dependency failures still stop the build. Runtime ENV and cwd remain part of the
image identity even where the existing bridge does not fully honor them.

Task initialization moves into guarded `setup_files/setup.sh`. Images record the
build shell's umask and the common home directory's mode. Setup restores these
creation defaults before replaying source commands, preserving permissions when
the runtime shell's umask or empty-WORKDIR bind differs. Source commands still
apply their explicit permissions. Existing finite startup repairs run afterwards,
with their original runtime shell defaults.

Singleton recipes keep initialization in their current compatibility image,
including the offline grading layer. The same applies to
unreviewed startup behavior, non-root execution, later installation/identity/download
operations, mount-sensitive state, mixed cache-mounted initialization and dynamic
interpreter cases. Recursive `/home/user` permission changes with `HOME=/home/user`
also retain the original image because moving them would affect package caches
created by bridge startup. Reasons are recorded per task, without excluding it.

To share the actual build-context hash, three unused source files move from
`environment/` to `source_metadata/environment/`: `container.def`, `task.json` and
`test_initial_state.py`. Their contents and modes are preserved. In particular,
`task.json` contains reference truth and is kept outside agent-visible setup.
The shared image context contains only its Dockerfile. Task configuration and
verifier assertions are unchanged. Five reviewed truncated prompts recover the
complete original description, with both source texts checked by hash.
A single setup invocation is inserted after the solution
shebang; the complete original solution body follows it.

The existing preparation hook runs setup before agents and no-op trials. A
completion marker under `/setup_files` is written only after success, so the
oracle's later setup call does not reset agent changes. Neither setup nor archived
source metadata participates in the image cache key.

## Compatibility and reviewed task repairs

The compatibility compiler lowers complete Docker RUN programs, including heredocs,
to encoded single-line commands accepted by the installed Harbor converter. Each
RUN restores its original ENV and cwd. The implicit Ubuntu cwd is explicit `/`.
Three reviewed BuildKit cache targets use temporary build directories that persist
between mounted commands, restore the underlying image directories afterwards,
and are discarded when the build completes.

The compiler also undoes the builder's premature final-WORKDIR creation before
source RUN programs. It removes only empty directories below the reviewed Ubuntu
`/home` root, then source-position mkdir commands recreate them. This preserves
`useradd -m` skeleton creation; no nonempty directory is removed.

Six source-Dockerfile hashes pin finite startup repairs:

- `task_000000_089a919e`: create the three FinOps input CSVs and apply home permissions.
- `task_000000_0119f5d7`: apply the SSH task's startup home permissions.
- `task_000000_1925b899`: apply the deployment task's startup home permissions.
- `task_000000_4c58315f`: provision the documentation skeleton and permissions.
- `task_000000_c66c3f8e`: create the initial system log and permissions.
- `task_000000_da9fb171`: refresh the build log's startup timestamp.

Each invokes the original finite ENTRYPOINT with `/bin/true` as payload. Image
sharing composes initialization before that invocation and preserves the one-time
guard. Ten reviewed timestamp cases run source-relative touch commands once per
fresh instance; one already receives this through shared initialization. Retained
images keep their fixtures, and setup refreshes only their timestamps. The
directory refresh in `87d17935` is withheld after an Apptainer oracle regression;
its fixtures already exceed the task's seven-day cutoff as the cache ages.

Five truncated instructions recover the exact original `task.json` description,
not its hidden truth. Instructions and descriptions are pinned separately by hash.
Except for the reviewed CPU and dependency edits described below, oracle bodies
remain byte-identical to the source. Final-state assertions are unchanged.

`--no-share-images` keeps full initialization in images while retaining the current
repairs. `--no-offline-verifier` keeps the source grading wrapper;
`--no-reviewed-repairs` keeps original prompts and build-relative timestamps.
`--no-task-setup` disables runtime startup/timestamp repairs. Historical v5 output
remains reproducible with its frozen patcher snapshot. No PATH wrapper, substitute
identity or host capability gate is added.

## Offline grading tools

Each image installs uv 0.9.5's managed Python 3.12 and pytest 8.4.1 under
`/opt/et-verifier/venv`. The original task Python and pytest versions remain intact.
The wrapper invokes this isolated pytest directly, with its bin directory first
on PATH as the source uvx invocation did. Assertions are unchanged; grading no
longer installs curl, uv, Python or pytest. It preserves pytest's cache behavior.
An additional grading environment increases image bytes without adding image
contexts; package versions in the task environment are not pinned or upgraded.

Exit 0 scores 1, assertion failure (exit 1) scores 0, and other pytest exits produce
a verifier error without a reward. Stale reward files are cleared. Individual
tests can still request network access; this change removes grading bootstrap
downloads rather than imposing a task network policy.

## Validation and evidence

### Static repairs and reviewed filtering

`et-static-repairs-v2` reuses the shared timeout/anti-cheating suffix normalizer.
Four reviewed solutions use `[environment].cpus` for their xargs/pigz worker
count. The host-measurement task uses and logs `nproc --all`. Eight solutions
receive package versions captured by the shared resolver after reward-1 oracle
runs, followed by normal reference/no-op validation. The source hashes and exact
edits live in `patch.py`; all changes are recorded in the patch manifest and
`build-manifest.jsonl`. Image recipes and test assertions do not change.

For `task_000000_b5807114`, the isolated pytest entry point remains absolute,
but its wrapper no longer prepends the grading Python to subprocess PATH.
This lets the generated JSON-schema helper use the task Python and its installed
jsonschema package when the verifier reruns it. The original wrapper made this
reference score zero even though the helper ran successfully in the agent environment.

The shared static adapters also correct Markdown punctuation escapes, quoted
report text mistaken for paths, unused literal reference sets, discoverable
initial inputs in encoded image RUN programs, shell command boundaries, local
package subdirectories with literal absolute directory assignments, and
`command -v nproc` availability probes. These corrections operate on checker
copies. They do not rewrite valid task requirements or exempt unknown installs.
Relative instruction paths remain advisory and are not automatically rewritten.

`--filter-reviewed` archives exactly:

* `task_000000_9719b9f3`, whose executed reference replaces PyYAML with an empty
  local module while the version-only grader still awards reward 1. This is a
  reference-integrity finding, not a claim that all local pip installs are invalid.
* `task_000000_bc9c5a3e`, whose original and repaired references both score zero
  on the recorded Helma/Apptainer profile because ICMP latency to 8.8.8.8 is
  unavailable, leaving an empty value and invalid summary JSON. This exclusion
  is specific to that execution profile.

Filtering is opt-in. `--no-static-repairs` reproduces the earlier output when
combined with the historical patcher options. The current evidence and generated
datasets belong under workspace `runs/phase1-repairs-20261015/`: `repaired/`
preserves 2,492 tasks, and `filtered/` retains 2,490 with the two originals in
its archive. Full static validation and sampled runtime evidence are separate;
these runs do not establish full-dataset runtime validity.

Select tasks for the shared pipeline with repeated `--task TASK_ID` arguments:

```bash
PYTHONDONTWRITEBYTECODE=1 python data/endless_terminals/patch.py prepare-pilot \
  --source /path/to/workspace/runs/et-patch/tasks.parquet \
  --output /path/to/workspace/runs/et-pilot \
  --task task_000000_1102c710 --task task_000000_1925b899
```

Without `--task`, selection uses the historical ten-task compatibility sample.
Run a diverse ten-task pilot through `validation/run.py`, stages 1, 2, 3 and 4
(environment/build/setup, no-op, reference solution and static task checks).
Use `--no-fix-instruction-paths`, `--no-fix-pip-pins` and
`--no-fix-instruction-suffix` to prevent additional task edits. Keep every ET Slurm
job within one hour. `patch.py probe --help` adds direct and interactive-terminal
state observations using the shared environment lifecycle in an active allocation.
Current runner stage numbers follow [the validation protocol](../../validation/PROTOCOL.md).
Historical reports retain their original stage meanings and frozen code; prepare
new contracts for the current layout rather than reusing legacy contracts.

The earlier full output is under workspace `runs/reviewed-task-repairs-v2/patched/`:
424 tasks use 109 shared recipes, leaving 2,177 distinct contexts instead of 2,492.
All 2,492 IDs remain, with no exclusions. The reduction is 315 cached image
contexts (12.6%), including one fewer than the normalization output;
actual disk savings depend on image sizes. The obsolete projection was not used.
See that run's report for paired pilot evidence and limitations, and
[decisions.md](decisions.md) for the permission findings that shaped this implementation.

The normalization pilot remains recorded under `runs/normalized-recipes-v3/`.
The reviewed-repair run tests the pulled runtime, isolated grading, finite startup,
user-home skeleton creation, reconstructed prompts and timestamp behavior.
See its paired and supplemental reports for rewards, regressions withheld,
original instruction-suffix/PATH flags and inherited source failures.

The patcher does not certify runtime compatibility or filter publication failures.
Builds, setup, state checks, oracle/no-op rewards and static checks remain separate
evidence. PATH, identity, ownership and startup flags are retained. Full-dataset
validation and publication filtering are still required; a small pilot does not
establish that every shared task will pass.

Historical evidence remains under `runs/apptainer-conversion-v1/`,
`runs/apptainer-conversion-v2/`, `runs/task-only-repairs-v1/` and
`runs/task-only-repairs-v2/`. The second conversion run used a runtime adapter
that was subsequently removed. Its results do not describe the current bridge.
`runs/phase3-gate-refinement/` and earlier image-sharing projections use older
specifications and are not inputs to this implementation.
