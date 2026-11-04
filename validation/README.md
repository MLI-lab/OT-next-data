# Task validation

## Quickstart

### 1. Prepare a contract

A contract records the tasks, stages, settings and code used for a run. Generate it with the command below; do not write or edit it manually.

Before submitting validation, prepare and verify all required images in the target cluster’s cache.

```bash
export OT_REQUIRE_PREBUILT_IMAGES=1

python validation/run.py /path/to/tasks \
  --stages 0,1,2,3 \
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

The example runs the full task-filtering pipeline, stages 0 through 3.

To run a single stage, prepare a separate contract with, for example, `--stages 0`. For several stages, use a comma-separated list. Model stages also need their model settings; stages that analyse saved trajectories need `--trials`.

`--stages all` includes the experimental stages 9 and 10, so use an explicit stage list for normal validation.

Prepare a new contract when tasks, settings or code change. Run `python validation/run.py --help` for all options.

### 3. Publishing the results pm HF

See [publishing](publishing/README.md) for how READMEs are generated.

## Validation protocol

### Stage 0: static checks

Run the 18 rules that only read task files. They never stop a task: every task continues to stage 1, whatever its static results. See [static rules](rules/README.md).

### Stage 1: build, setup and runtime checks

Build and set up each task in an Apptainer container. In that container:

- `declared_workdir`: the starting directory matches `task.toml`.
- `declared_internet_access`: internet access matches `allow_internet`.
- `repository_history` and `repository_timestamps`: the Git repository does not give away the solution. Timestamps are repaired automatically.
- The lookup for `task-absolute-path`: where the instruction's relative paths really are.

Failed builds are retried up to three times. Only a failed build stops a task: its stage 2 and 3 checks become `blocked`.

### Stage 2: reference solution

Run `solution/solve.sh`, then the verifier. The reference solution must score **1**.

- `do_not_modify_dynamic`: change a protected file after the solution; the verifier must then not give reward 1.
- `reference_determinism`: repeat the run with shuffled Python directory listings; it must still score 1.

Tasks without a reference solution are not applicable here.

### Stage 3: no-op baseline

Run the verifier without a solution. It must score **0**.

- `reference_leaks`: before the no-op starts, scan the clean container for copies of the files the reference solution changed.
- `verifier_tamper`: check that the tools the verifier uses were not changed.
- `nop_determinism`: repeat the run with shuffled Python directory listings; it must still score 0.

Tasks that pass the required checks form **v1**. The [rules documentation](rules/README.md) describes each check, its repairs and exclusions.

### Stage 5: LLM task-quality review

A judge reviews the instructions, task implementation and, by default, the task image against a rubric.

See [rubrics](rubrics/README.md) for available rubrics, calibration and execution options. These reviews provide additional findings; stage 6 uses the v1 tasks.

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

Uses Terminal-Bench’s [trajectory-review rubric](https://github.com/harbor-framework/terminal-bench/blob/1dcda8716784493721921c23e4bc7f7d988b4494/docs/prompts/trial-analysis.toml) and [prompt template](https://github.com/harbor-framework/terminal-bench/blob/1dcda8716784493721921c23e4bc7f7d988b4494/docs/prompts/trial-analysis.txt). The rubric may need adjustment for training use.

### Stage 9: adversarial trials

Optional and untested. Run an agent with instructions to find ways of passing without completing the intended task.

Uses Terminal-Bench’s [adversarial prompt](https://github.com/harbor-framework/terminal-bench/blob/1dcda8716784493721921c23e4bc7f7d988b4494/docs/prompts/hack-trial-prompt.md), prepended to the task instruction. The resulting trajectories can be reviewed with the stage 8 rubric and prompt.

### Stage 10: hacker/fixer loop

Optional and untested. A hacker searches for exploits, a fixer repairs the task, and a solver tests the result.

Uses [harden-v0](https://github.com/few-sh/harden-v0/tree/342b8474e0c0cf96e4a8313fd2e26c7a11d51193), including its [hacker and fixer prompt templates and solver guidance](https://github.com/few-sh/harden-v0/blob/342b8474e0c0cf96e4a8313fd2e26c7a11d51193/harden/instructions.py) and [additional fixer guidance](https://github.com/few-sh/harden-v0/blob/342b8474e0c0cf96e4a8313fd2e26c7a11d51193/prompts/fixer_guidance.md).
