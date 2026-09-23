# OT-next-data

Build agent-task datasets and verify them by running teacher models on the
resulting tasks. This repo keeps dataset patches, verification scripts, cluster
launchers, and Harbor fixes together, and reports pass@k: the estimated chance
of solving a task within k attempts.

Runs use OpenThoughts-Agent to coordinate generation, Harbor to run and score
tasks, and Harbor's Apptainer bridge to execute agent commands in task containers
on the cluster. The default agent is `terminus-2`. The agent and bridge come from
the installed dependencies; `harbor_patches/` contains local bridge adjustments.

CrossCodeEval is the current end-to-end example. To run teachers on another
Harbor-format dataset, pass `--dataset-config` as described below. The default
without that option preserves the original CrossCodeEval selections.

## Setup

```bash
./setup.sh /path/to/workspace
source env.sh
```

`setup.sh` downloads OpenThoughts-Agent at a fixed Git commit, installs Harbor and the python
requirements into a virtual environment inside the workspace, and writes
`env.sh`.

`env.sh` sets the four environment variables everything here expects, so paths do
not have to be passed to every script:

- `PILOT_ROOT` — the workspace: model weights, built images, task archives, run
  outputs. None of that belongs in the repo.
- `OTAGENT_ROOT` — the pinned OpenThoughts-Agent checkout, which provides the
  trajectory generation entry point the launcher drives.
- `PATH` — puts the workspace's python environment first.
- `PYTHONPATH` — puts this repo first, so `data.<dataset>` imports resolve.

`requirements.txt` is for wherever you run the patcher, the checks and the
analysis — a cluster login node, or your laptop. It is deliberately small
(pyarrow, pyyaml, pytest, matplotlib): patched tasks carry a
standard-library-only verifier, and the GPU side runs in a container built from
`hpc/<cluster>/runtime.def`.

Harbor (`7faf878c`) and OpenThoughts-Agent (`75a438d2`) are installed at fixed
Git commits, rather than copied into this repo. This lets us reuse their code
without maintaining separate copies of their internal run-management code.
The Harbor dependency is the `marin-community/harbor` fork at that commit,
including its `terminus-2` agent, not an automatically updated installation of
the latest official `harbor-framework/harbor`. The serving container's Harbor
version is set by the requirements file passed to `build_runtime.sh`.

## Patching a dataset

A dataset pipeline is one script, `data/<dataset>/patch.py`, that turns a published dataset at a fixed version into a repaired one: it reads the original Parquet file, fixes each
task, and writes a new Parquet file plus a report. Add one per dataset — for a
TaskTrove set or any other Hugging Face dataset of tasks. `data/crosscodeeval/`
is the complete example, and its README says what that patch changes inside a task.

```bash
python data/crosscodeeval/patch.py \
    --input upstream/tasks.parquet \
    --output out/tasks.parquet \
    --archive "$CCEVAL_ARCHIVE" \
    --review reviews/
```

`--archive` is particular to CrossCodeEval — the compressed archive of the original benchmark the
patch takes its cross-file context from. `--review` writes `tasks.jsonl` (per
task: kept or dropped, why, which names could not be determined from the provided context) and `sample.md`, a
repeatable random sample of dropped tasks to read before trusting the filter.

The patch script contains both the filtering rules and the code that reports
its decisions. Running it on the same original dataset version should reproduce
the published repaired dataset exactly; `verify_pipeline.py reproduce` checks
this.

## Verifying

`verify_pipeline.py` runs the checks in `verify/`, cheapest first, stopping at the
first failure. Under `all`, checks with missing inputs are skipped with a
reason, so a successful command does not necessarily mean every check ran.
Each check is also a script you can run on its own.

```bash
python verify_pipeline.py all <dir with tasks/> [--parquet out/tasks.parquet]
python verify_pipeline.py reward <dir with tasks/>        # or any single check
```

- **tests** — runs pytest over `tests/`: the patcher's rules, the run layer, and
  the interface each dataset must provide.
- **reward** — grades answers with each task's own verifier, on this machine. The
  reference must score 1 and an empty answer 0; a dataset adds its own variants
  in `data/<dataset>/rewards.py`.
- **images** — prints how many distinct container images the dataset needs and
  which tasks share each. Harbor builds one image per distinct Dockerfile, so
  fewer is better: many distinct images turn a run into a build queue and fill
  the image cache. `--max-images N` makes the check fail if the count exceeds N.
- **reproduce** — checks that what the patcher produces (`--parquet`) contains
  exactly the tasks that were published (`--reference`), compared task by task on
  file contents. Download the published Parquet files with `hf download` first; the
  published dataset is the reference, nothing is stored in the repo.
- **sandbox** — builds the task image and runs the task's own `tests/test.sh`
  inside the container: the task's reference solution (`solution/solve.sh`) must score 1, and
  running the tests with no answer written must score 0. This is what fails when
  the Dockerfile breaks or the reward never reaches `/logs/verifier/reward.json`.
- **isolation** — two containers at once must not see each other's files, process resource groups
  or local network services.
- **model** — lets an agent actually solve tasks: submits a run (default
  `coder-30b`, 8 attempts per task), which you then report with pass@k.

## Reporting

Both scripts work across datasets and read what the run itself recorded:

```bash
python verify/pass_at_k.py <run dir> [<run dir> ...] --k 1 4 16 [--subset f.json]
python verify/plot_pass_rates.py "Model A=<run dir>" "Model B=<dir>,<dir>" -o pass_rates.png
```

`pass_at_k.py` reads existing results; it does not launch runs. A pass@16 split over four jobs
of four attempts is merged per task, then pass@k is computed with the unbiased
estimator, grouped by the saved task groups, falling back to the middle part of the task
id for older runs (the language for CrossCodeEval). Timeouts count as failures and are reported separately, so
timeouts remain visible when interpreting model performance. `--subset` reports a
harder subset of tasks beside the full set.

`plot_pass_rates.py` draws those same numbers from the same files: one panel per
model, one bar per group, lighter to darker colors for increasing k.

## Running teachers

```bash
python verify_pipeline.py model --stage smoke --time 00:45:00    # cluster, GPUs and concurrency filled in
sbatch --gres=gpu:h200:1 --time=00:45:00 hpc/helma/teacher_traces.sbatch coder-30b smoke   # or submit it yourself
```

`--time` is required: a job that reserves more than it needs waits longer in the
queue, and a smoke asking for twelve hours can sit behind everything. For the default CrossCodeEval setup, stages are
tasks per language: `smoke` 1, `diag` 5, `sweep` 25, `full` 250. Always run the small `smoke` check successfully before a larger run.

- `python config/models.py` — the teachers: weights size, GPU count,
  nodes, the tensor/pipeline split and the sampling from each model card.
  `--download <key>` fetches the weights into `$PILOT_ROOT/models`. `weak` and
  `strong` still work as aliases for `coder-30b` and `qwen35-122b`.
- `python config/clusters.py` — the detected cluster, its GPU request, how many
  trials fit in parallel there, and the workspace layout.

### Running a different dataset

Package Harbor task directories in a gzip-compressed tar archive with paths
`tasks/<task-id>/task.toml`, `tasks/<task-id>/instruction.md`, and the task's
remaining files. Store the archive and its JSON configuration in your workspace.
For example, `new-data.json`:

```json
{
  "dataset": "new-data",
  "archive": "tasks/new-data.tar.gz",
  "sha256": "REPLACE_WITH_THE_ARCHIVE_SHA256",
  "groups": {
    "coding": ["task-one", "task-two"],
    "reasoning": ["task-three"]
  },
  "artifacts": []
}
```

Archive paths are relative to the configuration file; absolute paths also work.
Use `sha256sum tasks/new-data.tar.gz` to get the checksum. Preparation verifies
it before creating the run. Each task ID must appear in exactly one group.
Choose group names other than `total` (reserved for the summary), or use one
`all` group. List tasks in your desired order:
`smoke`, `diag`, and `sweep` take the first 1, 5, and 25 tasks per group (or all
available tasks if fewer); `full` takes every listed task. There is no 250-task
requirement. Optional `task_repo` and `task_revision` record the source version;
otherwise the archive checksum identifies the task version. `artifacts` lists
absolute paths inside each task container to save after an attempt.

```bash
python verify_pipeline.py model --dataset-config /path/to/new-data.json \
    --model coder-30b --stage smoke --attempts 8 --time 00:45:00
```

Add `--dry-run` to inspect the submission. Direct Slurm submissions accept the
configuration path as a third argument after the model and stage. Task groups
are saved with each run and used by pass@k reporting and plots. New datasets
still need valid Harbor task environments and scoring scripts; this option does
not convert raw data into tasks. The same Slurm/Apptainer runtime is required.

### Models bigger than one node

A teacher whose weights exceed one node's GPU memory is served tensor-parallel inside
each node and pipeline-parallel across nodes; the split lives in the model entry:

| model | weights | GPUs | nodes | TP x PP |
| --- | --- | --- | --- | --- |
| `coder-30b` | 61 GB | 1 | 1 | 1 x 1 |
| `qwen35-122b` | 245 GB | 4 | 1 | 4 x 1 |
| `qwen3-coder-480b-fp8` | 482 GB | 8 | 2 | 4 x 2 |
| `glm-5.3` (FP8) | 756 GB | 8 | 2 | 4 x 2 |

The configured Hugging Face checkpoints and archived test coverage are:

| Checkpoint | Precision used | Recorded evaluation |
| --- | --- | --- |
| [Qwen3-Coder-30B-A3B-Instruct](https://huggingface.co/Qwen/Qwen3-Coder-30B-A3B-Instruct) | BF16 | Completed 1,000-task runs |
| [Qwen3.5-122B-A10B](https://huggingface.co/Qwen/Qwen3.5-122B-A10B) | BF16 | Completed 1,000-task run |
| [Qwen3-Coder-480B-A35B-Instruct-FP8](https://huggingface.co/Qwen/Qwen3-Coder-480B-A35B-Instruct-FP8) | FP8 | Completed four-task smoke run, job 873790 |
| [GLM-5.3](https://huggingface.co/zai-org/GLM-5.3) | FP8 | Incomplete four-task smoke run, job 873986: two infrastructure errors and two timeouts |

These statuses come from the run summaries archived on 2026-09-22. GLM-5.3's
[checkpoint configuration](https://huggingface.co/zai-org/GLM-5.3/blob/main/config.json)
specifies FP8 E4M3 quantization even though its name has no FP8 suffix. Some
components retain higher precision; the launcher uses `dtype=auto` for the FP8
checkpoints.

For more than one node the launcher starts a Ray head on the first node and a
worker on each other node, waits until the cluster reports every GPU, binds the
bridge to the node's address instead of localhost, and runs one trial worker per
node so trials spread over the allocation. Single-node runs skip the extra Ray workers and use the local bridge.

## Layout

- `config/` — what exists, independent of any run: `clusters.py` (hostname to
  cluster, its GPU request, GPUs and cores per GPU, storage layout) and
  `models.py` (the teachers: weights, GPUs, sampling, measured limits on useful parallel work).
- `data/<dataset>/` — one dataset pipeline per folder: `patch.py` (the whole
  patch, filter and audit included), `rewards.py` (how its answers are graded and
  which wrong answers to try), and its data files.
- `verify/` — checks shared across datasets and reporting: `check_reward.py`,
  `check_reward_harbor.py`, `check_images.py`, `check_reproducible.py`,
  `check_isolation.py`, `pass_at_k.py`, `plot_pass_rates.py`.
- `tests/` — pytest: the patcher's rules, the run layer, the dataset interface.
- `run/` — run preparation and management: task selection and settings
  (`prepare_run.py`), compressing attempt files to stay within storage file-count
  limits, and checking whether a run finished.
- `harbor_patches/` — adjustments to Harbor's bridge worker: resource limits for each
  attempt, container file-copy and test-directory fixes, network-isolation
  detection, and cleanup limited to the worker's own temporary directories.
- `hpc/` — `clusters.py` maps this hostname to a cluster, its GPU request, its
  concurrency and its storage layout; `hpc/<cluster>/` holds that cluster's
  launcher and container definition. `helma/` works, `zih/` is an unfinished starting template.

## Boundaries

- Generating and recording agent solution attempts uses OpenThoughts-Agent's
  `generate_trajectories.py`. Running without that dependency would require
  replacing its model-server startup, agent setup, run resuming, and attempt
  summaries, then checking the results again.
- Cluster jobs currently use Slurm, Apptainer, and temporary storage on each
  compute node. To support another cluster with that setup, add or update its
  entry in `config/clusters.py` and its job scripts and container build files in
  `hpc/<cluster>/`. For example, `hpc/helma/` contains Helma's files.
  Dataset patching and pass@k calculations should usually stay the same.
  A different job scheduler or container system may also require changes to
  run management and the Harbor bridge worker.
