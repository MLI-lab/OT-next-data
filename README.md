# OT_next_data

Build agent-task datasets and verify them by running teacher models on the
resulting tasks.

## Setup

```bash
./setup.sh /path/to/workspace
source env.sh
```

`setup.sh` clones OpenThoughts-Agent at its pin, installs Harbor and the python
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

Harbor (`7faf878c`) and OpenThoughts-Agent (`75a438d2`) are pinned dependencies,
never vendored: their import closure is 48 files of launcher internals, so
copying them would fork them.

## Patching a dataset

A dataset pipeline is one script, `data/<dataset>/patch.py`, that turns a pinned
published dataset into a repaired one: it reads the upstream parquet, fixes each
task, and writes a new parquet plus a report. Add one per dataset — for a
TaskTrove set or any other Hugging Face dataset of tasks. `data/crosscodeeval/`
is the worked example, and its README says what that patch changes inside a task.

```bash
python data/crosscodeeval/patch.py \
    --input  upstream/tasks.parquet \   # the pinned published dataset
    --output out/tasks.parquet \        # the repaired one
    --archive $CCEVAL_ARCHIVE \         # dataset-specific input: here the benchmark's own data
    --review reviews/                   # optional: the filter's audit, for reading
```

`--archive` is particular to CrossCodeEval — the upstream benchmark tarball the
patch takes its cross-file context from. `--review` writes `tasks.jsonl` (per
task: kept or dropped, why, which names were not inferable) and `sample.md`, a
seeded sample of dropped tasks to read before trusting the filter.

Everything a patcher decides lives in that one script, audit included, so the
filter and its report cannot drift apart. The consequence worth keeping: running
it on the pinned upstream reproduces the published dataset exactly, which
`verify_pipeline.py reproduce` checks.

## Verifying

`verify_pipeline.py` runs the checks in `verify/`, cheapest first, stopping at the
first failure. Each is also a normal script you can run alone.

```bash
python verify_pipeline.py all <dir with tasks/> [--parquet out/tasks.parquet]
python verify_pipeline.py reward <dir with tasks/>        # or any single check
```

- **tests** — runs pytest over `tests/`: the patcher's rules, the run layer, and
  the dataset plug-in contract.
- **reward** — grades answers with each task's own verifier, on this machine. The
  reference must score 1 and an empty answer 0; a dataset adds its own variants
  in `data/<dataset>/rewards.py`.
- **images** — prints how many distinct container images the dataset needs and
  which tasks share each. Harbor builds one image per distinct Dockerfile, so
  fewer is better: many distinct images turn a run into a build queue and fill
  the image cache. `--max-images N` turns the report into a gate.
- **reproduce** — checks that the patched parquet passed with `--parquet`
  contains exactly the tasks recorded in `data/<dataset>/published_digests.json`,
  compared task by task on file contents. Those digests come from the parquets
  that were actually published (for CrossCodeEval, the ones in the TaskTrove PR).
  It works on local files, so `hf download` first if you want to compare against
  what is currently on the Hub.
- **sandbox** — builds the task image and runs the task's own `tests/test.sh`
  inside the container: the task's oracle (`solution/solve.sh`) must score 1, and
  running the tests with no answer written must score 0. This is what fails when
  the Dockerfile breaks or the reward never reaches `/logs/verifier/reward.json`.
- **isolation** — two containers at once must not see each other's files, cgroups
  or loopback.
- **model** — lets an agent actually solve tasks: submits a run (default
  `coder-30b`, 8 attempts per task), which you then report with pass@k.

## Reporting

Both scripts are dataset-agnostic and read what the run itself recorded:

```bash
python verify/pass_at_k.py <run dir> [<run dir> ...] --k 1 4 16 [--subset f.json]
python verify/plot_pass_rates.py "Model A=<run dir>" "Model B=<dir>,<dir>" -o pass_rates.png
```

`pass_at_k.py` gathers and computes, nothing more. A pass@16 split over four jobs
of four attempts is merged per task, then pass@k is computed with the unbiased
estimator, grouped by the middle part of the task id (the language, for
CrossCodeEval). Timeouts count as failures and are reported separately, so an
infrastructure problem cannot masquerade as a weak model. `--subset` reports a
harder subset of tasks beside the full set.

`plot_pass_rates.py` draws those same numbers from the same files: one panel per
model, one bar per group, k as a light-to-dark ramp.

## Running teachers

```bash
python verify_pipeline.py model --stage smoke --time 00:45:00    # cluster, GPUs and concurrency filled in
sbatch --gres=gpu:h200:1 --time=00:45:00 hpc/helma/teacher_traces.sbatch coder-30b smoke   # or submit it yourself
```

`--time` is required: a job that reserves more than it needs waits longer in the
queue, and a smoke asking for twelve hours can sit behind everything. Stages are
tasks per language: `smoke` 1, `diag` 5, `sweep` 25, `full` 250. Always pass a
smoke first.

- `python teacher_traces/models.py` — the teachers: weights size, GPU count,
  nodes, the tensor/pipeline split and the sampling from each model card.
  `--download <key>` fetches the weights into `$PILOT_ROOT/models`. `weak` and
  `strong` still work as aliases for `coder-30b` and `qwen35-122b`.
- `python hpc/clusters.py` — the detected cluster, its GPU request, how many
  trials to run in parallel there, and the workspace layout.

### Models bigger than one node

A teacher whose weights exceed one node's HBM is served tensor-parallel inside
each node and pipeline-parallel across nodes; the split lives in the model entry:

| model | weights | GPUs | nodes | TP x PP |
| --- | --- | --- | --- | --- |
| `coder-30b` | 61 GB | 1 | 1 | 1 x 1 |
| `qwen35-122b` | 245 GB | 4 | 1 | 4 x 1 |
| `glm-5.1-fp8` | 756 GB | 8 | 2 | 4 x 2 |
| `glm-5.1` | 1508 GB | 16 | 4 | 4 x 4 |

For more than one node the launcher starts a Ray head on the first node and a
worker on each other node, waits until the cluster reports every GPU, binds the
bridge to the node's address instead of localhost, and runs one trial worker per
node so trials spread over the allocation. On one node nothing of that runs and
the path is exactly the one all published results came from.

## Layout

- `data/<dataset>/` — one dataset pipeline per folder: `patch.py` (the whole
  patch, filter and audit included), `rewards.py` (how its answers are graded and
  which wrong answers to try), `published_digests.json`, and its data files.
- `verify/` — dataset-agnostic checks and reporting: `check_reward.py`,
  `check_reward_harbor.py`, `check_images.py`, `check_reproducible.py`,
  `check_isolation.py`, `pass_at_k.py`, `plot_pass_rates.py`.
- `tests/` — pytest: the patcher's rules, the run layer, the plug-in contract.
- `teacher_traces/` — driving a run: the model registry, per-run selection and
  configs, trial archiving, the completion gate.
- `harbor_patches/` — the workarounds Harbor needs: a Slurm step per trial, the
  network-isolation probe, own-staging-only cleanup, the startup check.
- `hpc/` — `clusters.py` maps this hostname to a cluster, its GPU request, its
  concurrency and its storage layout; `hpc/<cluster>/` holds that cluster's
  launcher and container definition. `helma/` works, `zih/` is a skeleton.
- `.agents/skills/` — how-tos for agents working here: patching, verifying,
  running teachers, plotting.

## Boundaries

- Trajectory generation goes through OT-Agent's `generate_trajectories.py`.
  Replacing it with a thin Harbor driver would make this repo standalone, and
  would mean reimplementing vLLM startup, agent wiring, resume and attempt
  summaries, then re-verifying every published number.
- The run layer is written for Slurm with apptainer and node-local scratch.
  Porting means editing `hpc/<cluster>/`, not the rest.
