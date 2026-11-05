# OT-next-data

This repo collects patches to fix and harden existing RL environments and provides an automated task-validation pipeline. It runs static checks, checks that tasks build, reference solutions score 1 and no-op agents score 0, then measures teacher pass@k and trajectory statistics. It also supports LLM-as-a-judge reviews, with separate rubrics for instructions and agent traces.

## Validation pipeline

The pipeline takes an upstream datasource through preparation, validation and model evaluation. The stages below group checks by the question they answer; each check links to its existing explanation, repairs and result fields.

| Stage | Question | Checks and methods |
| --- | --- | --- |
| 0. Task & environment preparation | Can we standardize and prepare the task efficiently? | [Datasource patchers](data/README.md): Harbor conversion, Docker → Apptainer adaptation, image minimization and cached runtimes |
| 1. Configuration & compatibility | Is the task correctly configured and compatible with our infrastructure? | [`task-toml-schema`](validation/rules/README.md#task-toml-schema), [`resource-sizes`](validation/rules/README.md#resource-sizes), [`task-timeout`](validation/rules/README.md#task-timeout), [`dockerfile-platform`](validation/rules/README.md#dockerfile-platform), [`compose-host-binds`](validation/rules/README.md#compose-host-binds), [`instruction-suffix`](validation/rules/README.md#instruction-suffix) |
| 2. Environment setup & reproducibility | Can the task and verifier environments be set up reliably and reproducibly? | [`build`](validation/rules/README.md#build), [`dockerfile-sanity`](validation/rules/README.md#dockerfile-sanity), [`pip-pinning`](validation/rules/README.md#pip-pinning), [`nproc`](validation/rules/README.md#nproc), [`python_missing_imports`](validation/rules/README.md#python_missing_imports), [`artifact_efficiency`](validation/rules/README.md#artifact_efficiency), [`declared_workdir`](validation/rules/README.md#declared_workdir), [`declared_internet_access`](validation/rules/README.md#declared_internet_access) |
| 3. Instruction & verifier alignment | Are the instructions clear, and does the verifier enforce their requirements? | [`task-absolute-path`](validation/rules/README.md#task-absolute-path), [`test-file-references`](validation/rules/README.md#test-file-references), [`do_not_modify`](validation/rules/README.md#do_not_modify), [`do_not_modify_dynamic`](validation/rules/README.md#do_not_modify_dynamic), [`no_behavioural_tests`](validation/rules/README.md#no_behavioural_tests) |
| 4. Verifier protection | Can the agent modify the verifier or its grading tools? | [`agent_editable_test`](validation/rules/README.md#agent_editable_test), [`verifier_tamper`](validation/rules/README.md#verifier_tamper) |
| 5. Solution leakage | Can the agent access solutions or hints through files, Git history or timestamps? | [`no-cheat-dir`](validation/rules/README.md#no-cheat-dir), [`dockerfile-references`](validation/rules/README.md#dockerfile-references), [`repository_history`](validation/rules/README.md#repository_history), [`repository_timestamps`](validation/rules/README.md#repository_timestamps), [`reference_leaks`](validation/rules/README.md#reference_leaks) |
| 6. Reference solution validation | Does the reference solution consistently score 1? | [`reference`](validation/rules/README.md#reference), [`reference_determinism`](validation/rules/README.md#reference_determinism) |
| 7. No-op baseline validation | Does doing nothing consistently score 0? | [`nop`](validation/rules/README.md#nop), [`nop_determinism`](validation/rules/README.md#nop_determinism) |
| 8. LLM task-quality review | What problems were missed by deterministic checks? | [LLM judges and task-quality rubrics](validation/rubrics/README.md) |
| 9. Agent evaluation (pass@k) | Which tasks can the target RL model and a stronger model solve? | Repeated agent trials with both models; keep missing attempts separate from completed unsuccessful attempts |
| 10. Trajectory analysis | What do execution statistics and trajectories reveal about failures and reward hacking? | Trace metrics and LLM trajectory review |
| 11. Adversarial testing (optional) | Can an agent receive reward without completing the intended task? | Adversarial trials and the experimental hacker/fixer loop |

### Preparation inputs and outputs

Preparation starts with an upstream datasource and produces patched Harbor tasks in `tasks.parquet`, together with patch reports. Keep the reproducible preparation script at `data/<datasource>/patch.py` and the generated datasets under `$OT_WORKSPACE/datasets/<dataset>/`. The patcher records source-specific conversion and repairs; shared helpers handle reusable preparation work. See the [datasource documentation](data/README.md) for each patcher's arguments.

Preparation includes adapting Docker environments for Apptainer, minimizing and sharing images, and preparing cached agent and verifier dependencies. Follow the [offline compatibility](#offline-compatibility) and [verifier execution](#verifier-execution) design. Runtime migration is still in progress; having a patcher does not establish that a datasource is ready for offline execution or has passed validation.

### Validation and evaluation outputs

Tasks that pass the required deterministic checks form **v1**. LLM task-quality reviews provide additional findings. Evaluate v1 with the target RL model; tasks solved at least once form **v2**. Evaluate the remaining unsolved tasks with a stronger model; newly solved tasks form **v3**. Together, v2 and v3 are the solved set, with v3 providing harder tasks for later training. Trajectory analysis helps explain failures and detect possible reward hacking. Adversarial testing is optional and experimental.

Validation writes `results.parquet`, separate from the prepared `tasks.parquet`, with per-task check outcomes and repair evidence. Missing prerequisites or measurements are incomplete results, never passes. Use all checks selected by the default training profile, preserving its intentional exclusions of upstream benchmark conventions. AI-detection checks are intentionally excluded by user policy.

**Running the pipeline:** these conceptual stages 0–11 differ from the current CLI stage IDs. See the [execution-stage mapping](validation/README.md#execution-stage-mapping) and [quickstart](validation/README.md#quickstart) for commands; the [rules reference](validation/rules/README.md) is the source for detailed check behavior.

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
