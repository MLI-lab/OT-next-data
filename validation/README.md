# Task validation

## Quickstart

### 1. Prepare a contract

A contract records the tasks, stages, settings and code used for a run. Generate it with the command below; do not write or edit it manually.

Before submitting validation, prepare and verify all required images in the target cluster’s cache.

```bash
export OT_REQUIRE_PREBUILT_IMAGES=1

python validation/run.py /path/to/tasks \
  --stages 1,2,3,4 \
  --submit helma --partition cpu --cpus 48 \
  --concurrency 4 --time 02:00:00 \
  --out "$OT_WORKSPACE/validation" \
  --prepare-contract "$OT_WORKSPACE/contracts/validation.json"
```

This prepares the contract without submitting validation. Add `--limit 10` for a small pilot; omit it for the full dataset.

### 2. Run the contract

```bash
python validation/run.py \
  --contract "$OT_WORKSPACE/contracts/validation.json"
```

The example runs the full task-filtering pipeline: automatic stage 0, then stages 1 through 4.

To run a single stage, prepare a separate contract with, for example, `--stages 4`. For several stages, use a comma-separated list. Model stages also need their model settings; stages that analyse saved trajectories need `--trials`.

`--stages all` includes the experimental stages 9 and 10, so use an explicit stage list for normal validation.

Prepare a new contract when tasks, settings or code change. Run `python validation/run.py --help` for all options.

### 3. Inspect the results

For cluster runs, reports are collected under `<out>/submissions/ID/report/`, with detailed evidence in `evidence.tar.gz`.

`results.parquet` records rule outcomes and evaluations by task revision. Failed required checks exclude tasks; warnings do not. Incomplete checks and infrastructure errors remain unresolved.

See [publishing](publishing/README.md) for creating v1/v2/v3 selections, READMEs and Hugging Face releases.

## Validation protocol

### Stage 0: static checks

First, run all selected rules that only need task files. This is a cheap prefilter before starting containers. Tasks with failed or incomplete checks do not continue; warnings are allowed.

Stage 0 runs automatically when the pipeline includes stage 4. See [static rules](rules/README.md).

### Stage 1: build, setup and runtime checks

Start and set up each task in an Apptainer container. Check that its working directory and internet access match the task configuration.

Failed builds are retried up to three times. Consistent task failures are archived; mixed success and failure is marked as not robust. Infrastructure failures remain unresolved. Tasks that do not pass skip the reference and no-op stages.

### Stage 2: reference solution

Run `solution/solve.sh`, then the verifier. The reference solution must score **1**.

Execution errors or missing rewards remain incomplete and may be retried. Tasks without a reference solution are not applicable here.

### Stage 3: no-op baseline

Run setup and verification without an agent. The no-op baseline must score **0**; a positive reward flags the task.

Infrastructure errors remain unresolved. Verifier failures caused by missing agent-created files follow the no-op checker’s documented handling.

### Stage 4: final checks

Recheck task files after automatic repairs and collect the dynamic-check results.

[Dynamic checks](rules/README.md#dynamic-rules) run across stages 1 through 3 where they can reuse containers and evidence. The rules documentation describes their behavior, repairs and exclusions.

Tasks that pass the required validation checks form **v1**.

### Stage 5: LLM task-quality review

A judge reviews the instructions, task implementation and, by default, the task image against a rubric.

See [rubrics](rubrics/README.md) for available rubrics, calibration and execution options. These reviews provide additional findings; stage 6 uses stage-4 kept tasks.

### Stage 6: pass@k

#### 6.1: Model to train with RL

Run the intended RL model on validated tasks, typically with four attempts per task, or eight or sixteen when compute allows.

Our default is `Qwen/Qwen3-Coder-30B-A3B-Instruct`. In our runs, `Qwen/Qwen3-Coder-30B-A3B` with thinking enabled was slower and solved fewer tasks.

Tasks solved at least once form **v2**, matched to the model’s starting ability.

#### 6.2: Stronger model

Evaluate tasks the student never solved with a stronger model. Our default is `Qwen/Qwen3.8-27B`, with thinking enabled and two attempts per task.

Newly solved tasks form **v3**. These provide harder, demonstrably solvable tasks that can be introduced as the RL model improves. V2 and v3 together form the full solved set.

Missing attempts remain separate from completed unsuccessful attempts.

### Stage 7: trace metrics

Compute trajectory statistics without calling a model: rewards, turns, tokens, context use, timings, tool use and errors.

### Stage 8: LLM trajectory review

A judge examines trajectories for instruction problems, reward hacking, failure causes, near misses, refusals and progress at timeout.

See [rubrics](rubrics/README.md) for review configuration.

### Stage 9: adversarial trials

Optional and untested. Run an agent with instructions to find ways of passing without completing the intended task.

### Stage 10: hacker/fixer loop

Optional and untested. A hacker searches for exploits, a fixer repairs the task, and a solver tests the result.
