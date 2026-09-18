# OT_next_data

Build agent-task datasets, run teacher models on them, and verify that the
result measures what it claims to measure.

The pipeline was developed on TaskTrove's CrossCodeEval tasks, where the
shipped versions had no cross-file context and a verifier that paid full reward
for a prefix match. What is here is the repaired pipeline plus the checks that
proved the repair: patch a dataset, prove its verifier discriminates, prove its
tasks are solvable from what the agent sees, generate teacher trajectories on a
Slurm cluster, and report pass@k.

## Layout

| Path | What it is |
| --- | --- |
| `patchers/crosscodeeval.py` | The whole CrossCodeEval patch: retrieval context, benchmark verifier, oracle, and the solvability filter. One file on purpose — the filter rules and the verifier must not drift apart. |
| `tests/` | 43 tests. 34 cover the patcher and run anywhere; 9 cover the run layer and skip without `OTAGENT_ROOT`. |
| `verify/solvability_check.py` | Re-derives the filter verdicts from the patched parquets and samples them for review. |
| `verify/verifier_discrimination.py` | Grades gold, re-indented gold, empty, garbage, `}`/`;`/`{`, `return null;` and a one-name-changed near miss for every task, inside the task image. |
| `verify/trial_isolation.py` | Two-container probe: cgroups, cross-trial files, OOM containment, loopback. |
| `verify/pass_at_k.py` | pass@k per language (unbiased estimator), for all tasks and for the strict subset. |
| `verify/plot_pass_rates.py` | The bar chart of those numbers, one panel per model. |
| `run/run_pilot.sbatch` | The Slurm job: stage the tasks, serve the model, drive Harbor, archive trials. |
| `run/prepare_run.py` | Per-run selection, serving config and Harbor template. |
| `run/bridge_worker.py` | Wrapper on Harbor's apptainer worker: a Slurm step per trial, network isolation where the node allows it, own-staging-only cleanup. |
| `run/archive_trials.py`, `run/check_run.py`, `run/check_isolation.py` | Periodic archiving under a file-count quota, completion gate, startup isolation check. |
| `run/runtime.def`, `run/build_runtime.sh` | The serving container (vLLM 0.20, Torch 2.11, Ray 2.58). |

## Dependencies

Two upstreams are **pinned, not vendored**, so this repo stays diffable against
them and does not rot:

- **Harbor** (marin-community fork) `7faf878c` — the agent/environment runtime;
  `run/bridge_worker.py` wraps its apptainer worker.
- **OpenThoughts-Agent** `75a438d2` — provides
  `data/teacher_ranking_proxy/generate_trajectories.py`, which `run/` drives.
  Its import closure is 48 files (1.5 MB) including the cloud/Daytona/Supabase
  launcher stack, which is why it is a dependency rather than copied code.

Everything under `patchers/` and `verify/` is free of both and needs only
pyarrow (plus matplotlib for the plot). Each patched task's verifier is python3
standard library only, so it runs inside any task image.

## Setup

```bash
./setup.sh /path/to/workspace     # clones OT-Agent at its pin, builds the env
source env.sh                     # PILOT_ROOT, OTAGENT_ROOT, PATH, PYTHONPATH
pytest tests -q                   # 43 passed
```

The workspace holds what must not live in the repo: the OT-Agent checkout, the
python env, model weights, task archives and run outputs.

## The pipeline

**1. Patch a dataset.** Per source parquet:

```bash
python patchers/crosscodeeval.py \
  --input  upstream/laion__exp_rpt_crosscodeeval-python-v2/tasks.parquet \
  --output out/laion__exp_rpt_crosscodeeval-python-v3/tasks.parquet \
  --archive $CCEVAL_ARCHIVE          # amazon-science/cceval 40c68d2b data
```

Writes `<output>.report.json`: kept, dropped and unmatched task IDs, the
retrieval variant chosen per task, and the unknowable names per tried
retrieval. Task IDs keep their original numbers, with gaps where tasks were
dropped.

**2. Verify the dataset** before spending GPU hours — see
`.agents/skills/verify-dataset/SKILL.md`. In short: the tests, then
`verifier_discrimination.py` inside the task image (gold must score 1 and every
nonsense answer 0 for *every* task), then `solvability_check.py`.

**3. Generate teacher trajectories** on Slurm — see
`.agents/skills/run-teachers/SKILL.md`:

```bash
sbatch run/run_pilot.sbatch weak smoke     # 1 task per language, ~15 min
sbatch run/run_pilot.sbatch strong full    # the 1,000-task run
```

**4. Report.**

```bash
python verify/pass_at_k.py $PILOT_ROOT/runs/<run-id> ... --k 1 4 16
python verify/plot_pass_rates.py "Model A=<run dir>" "Model B=<dir>,<dir>" -o pass_rates.png
```

## Status of the CrossCodeEval dataset

7,763 upstream tasks → 6,710 kept (C# 1,353, Java 1,895, Python 432,
TypeScript 3,030). All 6,710 reference solutions score reward 1 and every
nonsense variant scores 0. On a 1,000-task selection (250 per language,
Terminus-2, 32k context): Qwen3.5-122B-A10B (thinking) pass@1 20.0%;
Qwen3-Coder-30B-A3B-Instruct pass@1 7.0%, pass@4 14.3%, pass@16 22.3%.
Submitted to TaskTrove as
[PR #3](https://huggingface.co/datasets/open-thoughts/TaskTrove/discussions/3).

## Known boundaries

- The run layer is written for one Slurm cluster (H200 partition, apptainer,
  node-local `$TMPDIR`, `lfs` file-count quota). Porting means editing
  `run/run_pilot.sbatch` and `run/prepare_run.py`, not the rest.
- Trajectory generation goes through OT-Agent. Replacing it with a thin Harbor
  driver would make the repo standalone; it would also mean reimplementing vLLM
  startup, the agent loop wiring, resume and attempt summaries, and
  re-verifying every number above.
- The solvability filter's "parts" rule is deliberately lenient: a name counts
  as knowable if its camelCase parts all appear somewhere, even scattered. The
  strict variant (parts contiguous in one visible identifier) is computed for
  reporting only, in `verify/data/selection1000_strict_subset.json`; both models
  score 2-3 points higher on that subset.
