# Validation protocol

See the [validation README](README.md) for the workflow, quickstart, and
publishing instructions.

## Overview of the validation pipeline

| Runner stage | Operation | Input |
| --- | --- | --- |
| 0 (automatic) | Static checks | Source tasks |
| 1 | Build, setup and runtime checks | Tasks without static failures or incomplete checks |
| 2 | Reference solution (expected reward 1) | Stage-1 passes |
| 3 | No-op baseline (expected reward 0) | Stage-1 passes |
| 4 | Final static checks and reference-leak results | Task files after repairs and evidence from stages 1-3 |
| 5 | LLM task-quality review | Stage-4 kept |
| 6 | Student pass@k, then teacher pass@2 on tasks the student never solved | Stage-4 kept, not filtered by stage 5 |
| 7 | Trace metrics (no model calls) | Stage-6 trajectories |
| 8 | LLM judge on trajectories | Stage-6 trajectories |
| 9 | Adversarial cheat trials (optional, untested) | TBD (e.g. overlap of stages 5 and 6) |
| 10 | Hacker/fixer loop (optional, untested) | TBD (e.g. overlap of stages 5 and 6) |

### Stage 0: static checks

First, we run all rules that only need task files, without starting a container.
This is a cheap prefilter: only tasks that pass the static filters continue to the
more expensive checks that need containers. Warnings do not filter out tasks.
See [validation rules](rules/README.md) for an overview of the static filters.

### Stage 1: build, setup and runtime checks

Starts and sets up each task in an Apptainer container, then checks that its
working directory and internet access match the task configuration. Task-specific
setup goes in `setup_files/setup.sh` so tasks can share images.

Failed builds are retried up to three times. Consistent task failures are archived
as build failures; mixed success and failure is marked as not robust.
Infrastructure failures remain unresolved, without archiving the task. Tasks that
do not pass skip the reference and no-op stages.

To do: change setup scripts to use a cached runtime, and upload a runtime for each
task, so tasks can run offline. Many setup scripts now install packages and need
internet access.

### Stage 2: reference solution

Runs `solution/solve.sh` and then the verifier.
Reward **1** passes and reward **0** fails the task. If a run ends with an error or
writes no reward, the verifier could not finish, which says nothing about the task:
it is retried, and if that keeps happening the task is labelled an infrastructure
error and not archived. Tasks without a reference solution are not applicable here.

### Stage 3: no-op baseline

Runs task setup and the verifier without an agent.
Every run must return reward **0**.

Reward **1** fails the task. A failed container, node or disk, a verifier timeout, or a
verifier that was killed for lack of memory is retried and then labelled an infrastructure
error, as in stage 2. Anything else counts as a valid 0, also when the verifier crashes or
writes no reward, because many verifiers need something the agent creates to run at all.
A 0 without a test report showing that tests ran is recorded in a note on the task.

### Stage 4: final checks

See [dynamic rules](rules/README.md#dynamic-rules) for an overview of the container
checks. They are distributed across stages 1-3 to reuse existing runs and avoid
separate container starts where possible.

### Stage 5: LLM task-quality review

A judge (default: Claude Code with `anthropic/claude-sonnet-5`) reviews each task
against a rubric, inspecting the task files and, by default, the task image. The
rubrics, our changes to them, ground-truth examples, calibration results and run
options are described in [rubrics](rubrics/README.md).

To do:

- Calibrate the judge on a subset of the
  [task-defect ground truth](https://huggingface.co/datasets/FWeindel/task-defect-ground-truth).
- Choose the rubric from the ones we have (Terminal-Bench, Harbor, custom, custom
  severity, River).
- Choose the prompting strategy: label each criterion pass/fail, classify each
  task into one failure category or a clean category, or grade each criterion by
  severity (none, minor, major).

For experiments, [`create_pilot.py`](data/create_pilot.py) can select a small,
diverse task sample (see [sampling a diverse pilot](README.md#sampling-a-diverse-pilot)).

### Stage 6: pass@k

#### 6.1: Model to train with RL

Run the model intended for RL training with Terminus-2 on all stage-4 kept tasks,
at pass@4 or pass@8/16 when compute allows. Our default is
`Qwen/Qwen3-Coder-30B-A3B-Instruct`: in our runs,
`Qwen/Qwen3-Coder-30B-A3B` with thinking on was slower and solved fewer tasks. Tasks solved
at least once form v2, a training set matched to the model's starting ability.

#### 6.2: Stronger model

Run a stronger model on tasks the RL model never solved. Our default is
`Qwen/Qwen3.8-27B`, with thinking on, at pass@2. Tasks it solves form v3: harder
tasks with evidence that they are solvable. These can be introduced later as the
RL model improves and needs harder tasks. Together, v2 and v3 form the full solved set.

### Stage 7: trace metrics

Computes statistics from the stage-6 trajectories without calling a model: turns,
tokens and context use, time per phase, tool and command use, termination reasons
and errors.

### Stage 8: LLM trajectory review

A judge reads the task and one trajectory and answers the six criteria of the
Terminal-Bench [trial-analysis rubric][analysis-rubric] (with its
[prompt][analysis-prompt]), unchanged: are the instructions sufficient, did the
agent reward-hack, did it fail for the intended reason, was it a near miss, did it
refuse, and was it still making progress at the timeout. The rubric may need
adjusting for training tasks.

For experiments, [`create_pilot.py`](data/create_pilot.py) can select a small,
diverse sample of trajectories.

### Stage 9: adversarial trials (optional, untested)

Adds the Terminal-Bench [cheat prompt][cheat-prompt] to a task copy and starts an
agent session.

### Stage 10: hacker/fixer loop (optional, untested)

Runs a loop on a task copy: a hacker finds exploits, a fixer repairs the task, and a
solver tests the result, using the [harden-v0 instructions][harden-instructions],
[fixer guidance][fixer-guidance] and Terminal-Bench [loop settings][fortify].

[implementation-prompt]: https://github.com/harbor-framework/terminal-bench/blob/1dcda8716784493721921c23e4bc7f7d988b4494/scripts/rubric-regression/templates/instruction.md
[implementation-rubric]: https://github.com/harbor-framework/terminal-bench/blob/1dcda8716784493721921c23e4bc7f7d988b4494/docs/prompts/task-implementation.toml
[review-builder]: https://github.com/harbor-framework/terminal-bench/blob/1dcda8716784493721921c23e4bc7f7d988b4494/scripts/review/stage_task.py
[review-defaults]: https://github.com/harbor-framework/terminal-bench/blob/1dcda8716784493721921c23e4bc7f7d988b4494/.github/harbor-run-defaults.yml
[terminus-agent]: https://github.com/marin-community/harbor/tree/7faf878c14b6d72737579ec936ebf5f74ba3194d/src/harbor/agents/terminus_2
[analysis-prompt]: https://github.com/harbor-framework/terminal-bench/blob/1dcda8716784493721921c23e4bc7f7d988b4494/docs/prompts/trial-analysis.txt
[analysis-rubric]: https://github.com/harbor-framework/terminal-bench/blob/1dcda8716784493721921c23e4bc7f7d988b4494/docs/prompts/trial-analysis.toml
[analysis-verifier]: https://github.com/harbor-framework/terminal-bench/blob/1dcda8716784493721921c23e4bc7f7d988b4494/scripts/ci/stage_hosted_analysis.py
[cheat-prompt]: https://github.com/harbor-framework/terminal-bench/blob/1dcda8716784493721921c23e4bc7f7d988b4494/docs/prompts/hack-trial-prompt.md
[harden-instructions]: https://github.com/few-sh/harden-v0/blob/342b8474e0c0cf96e4a8313fd2e26c7a11d51193/harden/instructions.py
[fixer-guidance]: https://github.com/few-sh/harden-v0/blob/342b8474e0c0cf96e4a8313fd2e26c7a11d51193/prompts/fixer_guidance.md
[fortify]: https://github.com/harbor-framework/terminal-bench/blob/1dcda8716784493721921c23e4bc7f7d988b4494/scripts/fortify/fortify.py
