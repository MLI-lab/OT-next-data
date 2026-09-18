# OT_next_data

Build agent-task datasets, run teacher models on them, and prove the result
measures what it claims to measure.

## Setup

```bash
./setup.sh /path/to/workspace     # clones OpenThoughts-Agent at its pin, installs Harbor + tooling
source env.sh                     # PILOT_ROOT, OTAGENT_ROOT, PATH, PYTHONPATH
```

The workspace holds what must not live in the repo: the OT-Agent checkout, the
python env, model weights, task archives, run outputs. Harbor (`7faf878c`) and
OpenThoughts-Agent (`75a438d2`) are **pinned dependencies, not vendored** — their
import closure is 48 files of launcher internals, so copying them would fork
them.

## Verifying a pipeline

`verify_pipeline.py` runs every check in `verify/`, cheapest first, and stops at
the first failure. Each check is also a normal script you can run alone.

```bash
python verify_pipeline.py all <dir with tasks/> [--parquet <patched>.parquet]
python verify_pipeline.py reward <dir with tasks/>     # or any single check
```

| Check | What it proves | Needs |
| --- | --- | --- |
| `tests` | the patcher's rules and the run layer behave | nothing |
| `reward` | each task's own verifier scores the reference 1 and nonsense 0 | nothing |
| `images` | the dataset needs few enough distinct container images | nothing |
| `solvability` | every name in a graded reference is knowable from what the agent sees (dataset-specific) | `--parquet` |
| `sandbox` | the image builds and the task's `tests/test.sh` scores correctly inside the container | a running bridge |
| `isolation` | two containers at once cannot see each other's files, cgroups or loopback | a running bridge |
| `model` | the whole chain, agent included; submits a run and then you report pass@k | a Slurm allocation |

The reward checks are generic with a dataset plug-in. Every dataset gets the same
two variants — the reference must score 1, an empty answer 0 — and adds its own in
`data/<dataset>/rewards.py`. CrossCodeEval adds seven, each of which a broken
verifier once passed: a **re-indented** reference (must still score 1, which the
old Java diff failed), lone `}`, `;`, `{` and `return null;` (what the retired
TaskTrove verifier paid partial credit for), a garbage call, and **the reference
with one identifier renamed** (which catches a verifier rewarding a prefix or
first-token match). The Harbor variant needs no plug-in at all: it takes the
reference from the task's own `solution/solve.sh` and the wrong answer from
running the tests with nothing written.

Then report:

```bash
python verify/pass_at_k.py $PILOT_ROOT/runs/<run-id> ... --k 1 4 16
python verify/plot_pass_rates.py "Model A=<run dir>" "Model B=<dir>,<dir>" -o pass_rates.png
```

## Patching a dataset

```bash
python data/crosscodeeval/patch.py --input <src>.parquet --output <dst>.parquet \
       --archive $CCEVAL_ARCHIVE
```

Writes `<output>.report.json` with every kept, dropped and unmatched task ID, the
retrieval variant chosen per task, and the unknowable names per tried retrieval.
See `data/crosscodeeval/README.md` for what the patch changes inside a task.

## Running teachers

```bash
python verify_pipeline.py model --stage smoke    # detects the cluster, fills in the resources
sbatch --gres=gpu:h200:1 hpc/helma/run_pilot.sbatch weak smoke     # or submit it yourself
sbatch --gres=gpu:h200:4 hpc/helma/run_pilot.sbatch strong full
```

Always pass a smoke before a full run. Knobs and the failures worth not
repeating: `.agents/skills/run-teachers/SKILL.md`.

## Layout

| Path | What it is |
| --- | --- |
| `data/<dataset>/` | One folder per dataset pipeline: `patch.py`, `rewards.py` (how its answers are graded and which nonsense to try), dataset-specific checks, data files. |
| `tests/` | pytest: 34 patcher tests, 9 run-layer tests (skipped without `OTAGENT_ROOT`). |
| `verify/` | Dataset-agnostic checks: `check_reward.py` (local), `check_reward_harbor.py` (build + sandbox), `check_images.py`, `check_isolation.py`, `pass_at_k.py`, `plot_pass_rates.py`. |
| `teacher_traces/` | Driving a run: selection and configs (`prepare_run.py`), trial archiving, completion gate. |
| `harbor_patches/` | Everything that works around Harbor: `bridge_worker.py` (a Slurm step per trial, network isolation probe, own-staging-only cleanup) and the startup isolation check. |
| `hpc/` | `clusters.py` maps this hostname to a cluster and its submit arguments; `hpc/<cluster>/` holds that cluster's launcher. `helma/` works; `zih/` is a skeleton. |
| `.agents/skills/` | How-tos for agents working in this repo. |

## Where the CrossCodeEval dataset stands

7,763 upstream tasks → 6,710 kept (C# 1,353, Java 1,895, Python 432,
TypeScript 3,030); all 6,710 pass the reward check. On a 1,000-task selection
(Terminus-2, 32k context): Qwen3.5-122B-A10B (thinking) pass@1 20.0%;
Qwen3-Coder-30B-A3B-Instruct pass@1 7.0%, pass@4 14.3%, pass@16 22.3%.
Submitted upstream as
[TaskTrove PR #3](https://huggingface.co/datasets/open-thoughts/TaskTrove/discussions/3).

## Boundaries

- Trajectory generation goes through OT-Agent's `generate_trajectories.py`.
  Replacing it with a thin Harbor driver would make this repo standalone — and
  would mean reimplementing vLLM startup, agent wiring, resume and attempt
  summaries, then re-verifying every number above.
- The solvability filter's "parts" rule is deliberately lenient. The strict
  variant is computed for reporting only
  (`data/crosscodeeval/strict_subset_1000.json`); both models score 2-3 points
  higher on that subset.
