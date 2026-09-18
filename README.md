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

Four checks of rising cost, one entry point:

```bash
python verify_pipeline.py tests                 # seconds: unit tests (43)
python verify_pipeline.py reward <dir>          # ~1 min: gold 1 / nonsense 0, on this machine
python verify_pipeline.py sandbox <dir>         # minutes: the same, built and run through Harbor
python verify_pipeline.py model                 # ~1 h: real run, 8 attempts, then pass@k
python verify_pipeline.py all <dir>             # in order, stopping at the first failure
```

- **tests** — the patcher's rules and the run layer (`tests/`, pytest).
- **reward** — grades nine answer variants per task with the task's own verifier,
  on this machine: the reference and a re-indented reference must score 1, and
  empty, garbage, lone `}`/`;`/`{`, `return null;` and *the reference with one
  identifier renamed* must score 0. That last variant is the one that catches a
  verifier paying for a prefix match. No container, ~1 min for 1,000 tasks.
- **sandbox** — the reference and garbage again, but the image is built and the
  task's own `tests/test.sh` runs inside the container. This is the build and
  sandbox check: it fails if the Dockerfile breaks, the image has no python, or
  the reward never reaches `/logs/verifier/reward.json`. Needs a running bridge,
  so it belongs inside a Slurm job.
- **model** — submits a real Slurm job (default Qwen3-Coder-30B-A3B-Instruct,
  8 attempts, 5 tasks per language, sampling from the model card). This is the
  only check that exercises the whole chain, and its oracle stage proves the
  sandbox independently of the model by running every `solution/solve.sh`
  through Harbor.

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
sbatch hpc/helma/run_pilot.sbatch weak smoke     # 1 task per language, ~15 min
sbatch hpc/helma/run_pilot.sbatch strong full    # 250 per language
```

Always pass a smoke before a full run. Knobs and the failures worth not
repeating: `.agents/skills/run-teachers/SKILL.md`.

## Layout

| Path | What it is |
| --- | --- |
| `data/<dataset>/` | One folder per dataset pipeline: `patch.py` plus its data files. Currently `crosscodeeval/`. |
| `tests/` | pytest: 34 patcher tests, 9 run-layer tests (skipped without `OTAGENT_ROOT`). |
| `verify/` | Dataset-agnostic checks: `check_reward.py` (local), `check_reward_harbor.py` (build + sandbox), `check_solvability.py`, `check_isolation.py`, `pass_at_k.py`, `plot_pass_rates.py`. |
| `teacher_traces/` | Driving a run: selection and configs (`prepare_run.py`), trial archiving, completion gate. |
| `harbor_patches/` | Everything that works around Harbor: `bridge_worker.py` (a Slurm step per trial, network isolation probe, own-staging-only cleanup) and the startup isolation check. |
| `hpc/<cluster>/` | Cluster-specific launchers. `helma/` works; `zih/` is a skeleton. |
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
