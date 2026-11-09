# OT-next-data

This repo collects patches to fix and harden existing RL environments and provides an automated task-validation pipeline. It runs static checks, checks that tasks build, reference solutions score 1 and no-op agents score 0, then measures teacher pass@k and trajectory statistics. It also supports LLM-as-a-judge reviews, with separate rubrics for instructions and agent traces.

## Validation pipeline

The pipeline takes an upstream datasource through preparation, validation and model evaluation. The steps
below group the checks by the question they answer; each check links to its explanation, repairs and result
fields. The commands use execution stages instead, which run checks from several steps together (see each
step's *Checks run* and the [execution-stage mapping](validation/README.md#execution-stage-mapping)).

### 1. Task standardization & runtime preparation

Convert upstream datasources into standardized Harbor tasks, adapt environments for Apptainer, minimize
container images, prepare offline runtime caches, and validate infrastructure compatibility (task
configuration, resource limits, timeouts, and filesystem dependencies).

**Input:** Upstream datasource
**Output:** Datasource patch script, standardized Harbor tasks, minimized Apptainer images, offline runtime
caches, and configuration and compatibility check results.

<details>
<summary>Checks run</summary>

[`task-toml-schema`](validation/rules/README.md#task-toml-schema), [`resource-sizes`](validation/rules/README.md#resource-sizes), [`task-timeout`](validation/rules/README.md#task-timeout), [`dockerfile-platform`](validation/rules/README.md#dockerfile-platform), [`compose-host-binds`](validation/rules/README.md#compose-host-binds), [`instruction-suffix`](validation/rules/README.md#instruction-suffix)

*Where they run:* the [datasource patchers](data/README.md) outside the pipeline, then the static checks of
pipeline stage 0, which read only the task files and run together with the static checks of steps 2 and 3.

</details>

### 2. Environment & verifier validation

Check that container images build successfully, task environments start, and basic setup and dependency
checks pass. Check dependency version pinning, whether scripts respect the allocated CPU limits, and whether
the working directory and internet access match the task configuration. Run the reference solution and a
no-op agent four times each to verify that the reference consistently receives reward 1 and the no-op
receives reward 0. Check for basic instruction–verifier mismatches and whether tests only inspect code that
the instructions require the agent to write or modify, without executing it.

**Input:** Standardized Harbor tasks and prepared runtimes
**Output:** Per-task validation results (pass/fail/warning per check and repair status), resource usage
measurements (wall-clock time, CPU time, and peak memory usage), and aggregate statistics (tasks passed,
failed, or unresolved per check and overall).

<details>
<summary>Checks run</summary>

**Environment setup and dependencies:** [`build`](validation/rules/README.md#build), [`dockerfile-sanity`](validation/rules/README.md#dockerfile-sanity), [`pip-pinning`](validation/rules/README.md#pip-pinning), [`nproc`](validation/rules/README.md#nproc), [`python_missing_imports`](validation/rules/README.md#python_missing_imports), [`artifact_efficiency`](validation/rules/README.md#artifact_efficiency), [`declared_workdir`](validation/rules/README.md#declared_workdir), [`declared_internet_access`](validation/rules/README.md#declared_internet_access)

**Instruction–verifier consistency:** [`task-absolute-path`](validation/rules/README.md#task-absolute-path), [`test-file-references`](validation/rules/README.md#test-file-references), [`do_not_modify`](validation/rules/README.md#do_not_modify), [`no_behavioural_tests`](validation/rules/README.md#no_behavioural_tests), [`verifier_paths`](validation/rules/README.md#verifier_paths)

**Reference and no-op execution:** [`reference`](validation/rules/README.md#reference), [`reference_determinism`](validation/rules/README.md#reference_determinism), [`nop`](validation/rules/README.md#nop), [`nop_determinism`](validation/rules/README.md#nop_determinism)

*Where they run:* the file checks in pipeline stage 0; `build`, `declared_workdir` and
`declared_internet_access` in stage 1 (one container start per task); `reference`, `reference_determinism`
and `do_not_modify` in stage 2; `nop`, `nop_determinism` and `verifier_paths` in stage 3. The leakage and
tampering checks of step 3 run in the same containers, so each task's environment is built and started once.

</details>

### 3. Leakage & tampering checks

Check whether the agent can access reference solutions, hidden tests, or other grading information through
task files, container images, Git history, or timestamps. Verify that the agent cannot modify protected
verifier files or grading tools.

**Input:** Standardized Harbor tasks and prepared runtimes
**Output:** Per-task validation results (pass/fail/warning per check and repair status) and aggregate
statistics (tasks passed, failed, or unresolved per check and overall).

<details>
<summary>Checks run</summary>

**Solution and test leakage:** [`no-cheat-dir`](validation/rules/README.md#no-cheat-dir), [`dockerfile-references`](validation/rules/README.md#dockerfile-references), [`repository_history`](validation/rules/README.md#repository_history), [`repository_timestamps`](validation/rules/README.md#repository_timestamps), [`reference_leaks`](validation/rules/README.md#reference_leaks)

**Verifier tampering:** [`agent_editable_test`](validation/rules/README.md#agent_editable_test), [`verifier_tamper`](validation/rules/README.md#verifier_tamper)

*Where they run:* `no-cheat-dir`, `dockerfile-references` and `agent_editable_test` with the file checks of
stage 0; `repository_history` and `repository_timestamps` in the stage-1 containers; `reference_leaks` and
`verifier_tamper` in the stage-3 no-op runs. They reuse the containers of step 2 instead of starting their own.

</details>

TO DO: implement a repair agent loop for those still need to specifiy

### 4. LLM-based task quality assessment

Use an LLM as a judge to identify task quality issues by reviewing task instructions, environment files, and
verifier code, without using agent execution traces. Check whether the instructions are clear and consistent
with the environment and verifier, whether the verifier could reward incomplete or incorrect solutions, and
whether tasks are too trivial or reveal their answers.

**Input:** Task files (instructions, environment configuration, and verifier code) for all tasks that have no
fail in any of the previous stages.
**Output:** Per-task judgments for each criterion (pass/fail), with explanations and supporting evidence, and
aggregate statistics (number of tasks flagged per criterion and overall).
**Rubric:** [revised River rubric](validation/rubrics/rubric_ot/rubric.toml); judges and prompts in
the [judge README](validation/rubrics/README.md). Runs as pipeline stage 5.

### 5. Target RL model pass@k & resource calibration

Evaluate tasks with the target RL model to estimate task difficulty and determine which tasks the model can
solve. Run k=4 independent agent attempts per task while monitoring CPU, memory, disk usage, and execution
times. Following MiMo's resource-profiling methodology, automatically increase resources and rerun all k
attempts if any attempt encounters a resource-related failure. Repeat until all attempts are resource-valid or
the configured resource limits are reached. Use the final valid batch to calculate pass@k. Unlike MiMo, a timeout
(agent or verifier) counts as a resource problem only when the CPUs were busy or contended, and resources go up
only while no attempt has solved the task: once one attempt solves it, other attempts with the same resources
that run out of memory or disk count as failed attempts instead of raising the resources. A task's requirement
comes from its smallest solved attempt (per resource the smaller of the two models when both solved it), and on
tasks the target model solved, the teacher starts at that requirement and never goes up.

**Input:** Task files (instructions, environment configuration, and verifier code) for all tasks that have no
fail in any of the previous stages.
**Output:** Per-task pass@k results (number of successful attempts, pass@k outcome), individual attempt
results (reward, execution status, and resource measurements), and calibrated per-task resource requirements
(vCPU, memory, disk), including calibration history and unresolved resource failures.
**Default model:** `Qwen/Qwen3.5-35B-A3B` (configurable). Runs as pipeline stage 6.

### 6. Strong teacher pass@k & resource recalibration

Evaluate every task that reached Stage 5 with a stronger model (`--teacher-tasks all`, the default), which gives
a second, stronger difficulty signal per task; `--teacher-tasks unsolved` limits this to the tasks the target RL
model could not solve in any of its k=4 valid attempts. Start with the resources calibrated in Stage 5 where a
task has them, else with its `task.toml`, and repeat the same automatic resource-calibration
procedure, since the stronger model may reach more resource-intensive parts of the task. If any attempt
encounters a resource-related failure, increase the affected resources and rerun all k attempts. Use the final
valid batch to calculate pass@k and update the task's resource requirements.

**Input:** Task files (instructions, environment configuration, and verifier code) for all tasks without a
failed check in Stages 1-3; with `--teacher-tasks unsolved`, only tasks with 0/k successful attempts in Stage 5,
excluding tasks with unresolved resource failures.
**Output:** Per-task strong-teacher pass@k results (number of successful attempts, pass@k outcome), individual
attempt results (reward, execution status, and resource measurements), and updated per-task resource
requirements (vCPU, memory, disk), including calibration history and unresolved resource failures.
**Default model:** `zai-org/GLM-5.3-Flash` (configurable). Runs as pipeline stage 7.

### 7. Trajectory analysis

Use execution statistics and LLM trajectory review to explain failures and detect possible reward hacking.
Runs as pipeline stages 8 (trace metrics) and 9 (trajectory review).
**Default rubric and prompt (stage 9):** Terminal-Bench's
[trial-analysis.toml](https://github.com/harbor-framework/terminal-bench/blob/1dcda8716784493721921c23e4bc7f7d988b4494/docs/prompts/trial-analysis.toml)
and [trial-analysis.txt](https://github.com/harbor-framework/terminal-bench/blob/1dcda8716784493721921c23e4bc7f7d988b4494/docs/prompts/trial-analysis.txt).

### 8. Adversarial testing (optional)

Check whether an agent can receive reward without completing the intended task, with adversarial trials and
the experimental hacker/fixer loop. Runs as pipeline stages 10 and 11.

### Preparation inputs and outputs

Preparation starts with an upstream datasource and produces patched Harbor tasks in `tasks.parquet`, together with patch reports. Keep the reproducible preparation script at `data/<datasource>/patch.py` and the generated datasets under `$OT_WORKSPACE/datasets/<dataset>/`. The patcher records source-specific conversion and repairs; shared helpers handle reusable preparation work. See the [datasource documentation](data/README.md) for each patcher's arguments.

Preparation includes adapting Docker environments for Apptainer, minimizing and sharing images, and preparing cached agent and verifier dependencies. Follow the [offline compatibility](#offline-compatibility) and [verifier execution](#verifier-execution) design. Runtime migration is still in progress; having a patcher does not establish that a datasource is ready for offline execution or has passed validation.

### Validation and evaluation outputs

Validation writes `results.parquet`, separate from the prepared `tasks.parquet`: one row per task with its check
outcomes and repair evidence, the judge's verdict per rubric criterion, pass@k of both models and the calibrated
resources. No separate dataset versions are created. Missing prerequisites or measurements are incomplete
results, never passes. Use all checks selected by the default training profile, preserving its intentional
exclusions of upstream benchmark conventions. AI-detection checks are intentionally excluded by user policy.

## Setup

On Helma, install Python with `uv` (already available), then run setup:

```bash
uv python install 3.12.14
./setup.sh /path/to/workspace --cluster helma --python "$(uv python find 3.12.14)"
source env.sh # to activate the environment in a new shell
python -m validation.upstream setup
```

`/path/to/workspace`, saved as `$OT_WORKSPACE`, saves datasets, model weights,
cached task images and a copy of the repo's Python environment. Jobs copy the
files they need to a temporary folder on the compute node for faster file access.
Choose a shared directory accessible from compute nodes. Save downloaded and
prepared datasets under `$OT_WORKSPACE/datasets/<dataset>/`; the repo's `data/`
has code, patches and small metadata. Download model weights with `python config/models.py --download MODEL` into
`/path/to/workspace/models/`; set `OT_MODELS` to use another directory. For run outputs, pass `--out "$OT_WORKSPACE/runs/NAME"`;
without `--out`, validation currently writes to the repo's `validation/results/`.

**MiniSWE** is the default harness for pass@k and local/API LLM judges.
Use `--agent terminus-2` for solver runs or `--judge-harness terminus-2` for
judges to select Terminus-2. Sonnet judges use Claude Code. See the
[judge configuration](validation/rubrics/README.md#judge-configuration).

Setup installs the Python packages in `requirements.txt` into one virtual
environment for validation and teacher generation. The default location is
`/path/to/workspace/envs/prep`. To choose another location, run
`export OT_PREP_ENV=/path/to/venv` before setup.

Helma already provides Apptainer at `/usr/bin/apptainer`, Slurm and CUDA;
no separate installation is needed. Setup loads the CUDA module automatically.
`config/clusters.py` specifies the versions, modules to load,
and Slurm settings. Setup and jobs load those modules automatically. Currently only
Helma is fully configured.

See [storage locations](hpc/README.md#storage-locations) for cluster directories,
their `$` names, disk/file quotas, and dataset and result locations. Quotas are
currently recorded only for your Helma account.

Helma and ZIH use the same Slurm pipeline; see [cluster setup](hpc/README.md).

### Offline compatibility

*This describes the agreed design; the implementation still needs to be aligned with it.*

Tasks can share a base image and install extra dependencies in their setup scripts: under `setup_files/` before the agent starts, or in `tests/setup.sh` or `tests/test.sh` for verification. This avoids building a separate image for every dependency combination.

To support offline execution, we prepare those dependencies once with internet access and cache them separately. We follow this convention:

- **Build the cache using the setup script.** Each setup script can also be run with an option to install and save its dependencies for later reuse. They are saved under `$OT_WORKSPACE/runtimes`, configurable through `OT_RUNTIME_CACHE`. Multiple tasks can share the same runtime. Users can build it locally or download a published copy from Hugging Face’s `runtimes/` folder.
- **Record which runtime the task uses.** When a runtime is saved, its ID and checksum are written to `setup_files/ot-task-runtime.json` for agent dependencies or `tests/ot-verifier-runtime.json` for verifier dependencies. During a normal run, a shared helper uses the cached dependencies or tries installation if they are missing or incomplete.
- **Keep the shared cache unchanged.** The cached runtime is read-only. Each agent run and verifier execution gets its own private writable layer over a clean runtime. Any additional installations stay private to that run and are discarded afterward, so other tasks always start from the clean cached version.

Offline execution requires all dependencies and other task assets to be available locally.

### Verifier execution

Verification runs either in the agent’s environment (`shared`) or in a separate environment (`separate`), configured through `[verifier].environment_mode` in `task.toml`.

In shared mode, the agent is root in the container that later grades its work. Files uploaded from `tests/` after the agent stops are clean, but the Python interpreter and grading tools already in the container may have been changed. On a SWE-Lego task without a clean runtime, an agent that did not fix the bug got reward 1 in three ways: changing conda's `activate` script, replacing `pytest` with a script that reports passing tests, and wrapping `python` so that running the grader writes the reward.

That is why we are moving tasks to clean verifier runtimes. A runtime contains a Python interpreter, the packages needed for grading, and supporting tools and libraries. In shared mode, the tests use this clean runtime inside the agent's container.

In shared mode, the verifier can use the agent's edited files, running services, and system tools and libraries, including those installed through `apt`. For Python imports, it searches the clean runtime's package directories first. If a top-level package is missing there, it can fall back to the agent's package directories, which we record before switching runtimes. This fallback requires the same Python version (for example, both using Python 3.12) and skips `.pth` files and startup hooks in the agent's Python environment, which Python normally processes automatically when it starts. **Limitation:** A package in the clean runtime still overrides the agent's modified copy, and fallback packages may be incompatible with packages in the verifier runtime. **TODO:** Count affected tasks, check which package copies their verifiers load, and fix any that grade an untouched copy or cannot load the package the agent was asked to change.

Clean runtimes do not fully isolate grading from the agent's container. Grading still starts through the container's own `bash`; on a migrated BugsInPy task, replacing `/bin/bash` still got reward 1. This is an accepted limitation of shared-mode grading. Some tasks need this mode because they require changes to the existing environment.

#### Loading agent and verifier runtimes on a cluster

Publish and download prepared runtimes as SquashFS images, with checksum manifests, in Hugging Face's `runtimes/` folder. A SquashFS image is a compressed file containing a runtime's files and directories. On a cluster, download the images to storage accessible to the compute nodes. Optionally copy them to node-local disk for faster access. Our Apptainer harness mounts the task’s runtime image read-only inside the container. Its files are decompressed as needed when read.

Previously, we copied and unpacked a runtime archive for each verification into memory-backed storage (`/dev/shm`). For one inspected runtime, the 538 MiB archive and its roughly 1.86 GiB unpacked contents together exceeded the task’s 2 GiB memory limit during preparation. Mounting the image from disk avoids keeping that full temporary copy in memory storage.

The agent and verifier each get their own writable layer over the clean runtime. This lets setup scripts, the agent, or the tests install packages and change runtime files without changing the shared image. The agent's layer stays available until grading finishes, so the tests can evaluate its changes. The verifier gets a fresh layer for each verification. Both layers are discarded when their work is finished; neither is saved back to the shared image.

## Use

Prepare a dataset with `data/<dataset>/patch.py`. See the
[data layout and commands](data/README.md#dataset-code) for source subcommands and shared helpers. A contract is a JSON file
that fixes the input tasks, validation stages and settings for a run.
Create one, then run it (this example checks 10 tasks on Helma without an LLM):

Before submission, prepare missing images in a separate image-only job and verify all required cache entries on the target cluster.

```bash
export OT_REQUIRE_PREBUILT_IMAGES=1
python validation/run.py /path/to/tasks --stages 0,1,2,3 \
  --resolve-path-root / \
  --limit 10 --submit helma --partition cpu --cpus 48 --time 02:00:00 \
  --out "$OT_WORKSPACE/runs/check" \
  --prepare-contract "$OT_WORKSPACE/contracts/check.json"
```

Then run validation:

```bash
python validation/run.py --contract "$OT_WORKSPACE/contracts/check.json"
```

See the [validation workflow](validation/README.md#quickstart) for the full
validation protocol and the [datasource index](data/README.md) for patchers and dataset documentation.
Run repository tests with `python -m pytest tests -q`.
