# Validation protocol

See the [validation README](README.md) for the workflow, quickstart, and
publishing instructions.

## Overview of the validation pipeline

| Runner stage | Operation | Input |
| --- | --- | --- |
| 1 | Build, setup and runtime checks | Source tasks |
| 2 | Reference solution (expected reward 1) | Stage-1 passes |
| 3 | No-op baseline (expected reward 0) | Stage-1 passes |
| 4 | All static checks | Stages 1-3 kept |
| 5 | LLM task-quality review | Stage-4 kept |
| 6 | Student pass@k, then teacher pass@2 on tasks the student never solved | Stage-4 kept, not filtered by stage 5 |
| 7 | Trace metrics (no model calls) | Stage-6 trajectories |
| 8 | LLM judge on trajectories | Stage-6 trajectories |
| 9 | Adversarial cheat trials (optional, untested) | TBD (e.g. overlap of stages 5 and 6) |
| 10 | Hacker/fixer loop (optional, untested) | TBD (e.g. overlap of stages 5 and 6) |

Below we describe each stage.

## Environment reliability (stages 1-3)

### Stage 1: build, setup and runtime checks

Tasks run as Apptainer containers converted from their Docker images (see
[runtime behavior](RUNTIME.md)). To keep the number of images small, task-specific
steps such as git checkouts, task files and small configuration changes go into
`setup_files/setup.sh`, so many tasks can share one image; only slow or unreliable
installs go into the image. `--review-setup` times task and verifier preparation to
find this balance.

Each task is built, started and set up, then checked for:

- **Working directory:** matches the Dockerfile's `WORKDIR`.
- **Internet access:** HTTPS probes match `allow_internet` in `task.toml`.

A build that fails because of the task itself is archived. A build that fails
because of our infrastructure is retried up to three times; if it still fails, the
task is archived as not robust. The cause is read from the error message: a bridge
outage, a full disk (`No space left on device`), dead workers, or a crash without a
verdict count as infrastructure; a failed or timed-out image build (default
limit 1 hour per image, `--image-build-timeout-sec`) or container start and task
setup (limit `build_timeout_sec` in `task.toml`, Harbor default 600 s), and any
other failure, count as the task's own. Stages 2 and 3 skip tasks
that did not build.

Rules that need a running container (dynamic rules, such as the repository leak
checks) also run here; static rules run in stage 4. Both are described in
[validation rules](rules/README.md).

To do: change setup scripts to use a cached runtime, and upload a runtime for each
task, so tasks can run offline. Many setup scripts now install packages and need
internet access.

### Stage 2: reference solution

Runs `solution/solve.sh` and then the verifier, three times in fresh containers.
Every run must return reward **1**. Tasks without a reference solution are skipped.
The three runs catch reference solutions and verifiers that are not deterministic,
e.g. depending on timing, randomness or network responses.

### Stage 3: no-op baseline

Runs task setup and the verifier without an agent, three times in fresh containers.
Every run must return reward **0**.

A reward of 0 alone is not accepted as a valid no-op: there must also be evidence
that the tests actually ran. Without it, the 0 can come from a node failure or a
broken verifier that never executes the tests. Exceptions are verifiers that fail
because a file the agent should create is missing, and verifiers that fail to run
their tests on the unsolved task when the reference solution scored 1 with the same
verifier, so the verifier has been shown to work.

One extra no-op run shuffles the order of Python directory listings
(`--nop-shuffled-runs`, default 1). File listing order can differ between
filesystems and nodes, so a task whose bug depends on it can look already solved
where the order happens to be stable.
## Task quality (stages 4-5)

### Stage 4: static checks

Runs all static rules on the task files after the automatic repairs (instruction
suffix, instruction paths, pip pins). This includes the Terminal-Bench default checks
with our patches, our own rules, and the reference-solution leak check, which uses
file fingerprints recorded in stage 2. Benchmark-only Terminal-Bench checks are
excluded. Rules, repairs and exclusions are described in
[validation rules](rules/README.md).

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

## Trajectory quality (stages 6-10)

### Stage 6: pass@k

Runs the student model with Terminus-2 on all stage-4 kept tasks at pass@4, or
pass@8/16 when compute allows. Default students are
`Qwen/Qwen3-30B-A3B-Instruct-2507` or `Qwen/Qwen3-Coder-30B-A3B-Instruct`, thinking
off. `Qwen/Qwen3-30B-A3B` with thinking on is optional; in our runs it took longer
and solved fewer tasks. Tasks solved at least once form v2.

Tasks the student never solves get a pass@2 run with a stronger teacher (default
`Qwen/Qwen3.8-27B`, thinking on). Tasks it solves are hard but still valid and form
v3, which contains only these newly solved tasks; v2 and v3 together are the full
solved set.

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
