# Validation protocol

See the [validation README](README.md) for the workflow, quickstart, and
publishing instructions.

## Stage 1: static checks

Inspects task files after automatic path and pip-pin preparation, which can start
containers and run the reference solution. Disable these independently with
`--no-fix-instruction-paths` and `--no-fix-pip-pins`. See the pinned
[Terminal-Bench check overview](https://github.com/harbor-framework/terminal-bench/blob/1dcda8716784493721921c23e4bc7f7d988b4494/docs/TASK_REVIEW_AUTOMATION.md#static-checks)
for each upstream check and its purpose.

**Checks we adapted** (`check-*.sh` names shortened):

- `instruction-suffix`: automatically append or update the required sentence in
  the validation copy, using the task's agent timeout for `N`:

  > You have N seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.

  Disable this automatic fix with `--no-fix-instruction-suffix`.
- `task-absolute-path`: ignores fenced code blocks and URLs. Automatically
  searches inside the container for flagged relative paths, also checking after
  the reference solution if needed. Unique matches replace relative paths in the
  validation copy's `instruction.md`. Unchanged tasks reuse previous search
  results. Disable with `--no-fix-instruction-paths`.
- `test-file-references`: ignores URLs and standard system paths such as
  `/usr/bin/python`. Extended to follow file references in the instructions
  to resolve paths flagged as missing from them: the agent may find these by
  reading supplied files or directories named there, such as a config
  that specifies the output path.
- `pip-pinning`: requires pinned package installations, accepting exact versions
  and SHA-256-pinned download URLs. Recognizes quoted requirements and ignores
  shell output redirections such as `> install.log 2>&1`. Installer scripts that
  receive package arguments when invoked are exempt.
  For flagged tasks, `data/utils/resolve_pip_pins.py` builds the environment and
  runs the reference solution and verifier. If the reward is 1, it freezes the
  environment and replaces unpinned requirements with the observed versions.
  Patched tasks continue through normal validation. Unresolved pins still fail
  stage 1 and are archived during publication.
- `nproc`: allows `make -j$(nproc)`, `cpu_cores=$(nproc)`, and
  `logical_cores=$(nproc 2>/dev/null)` in the four reviewed SETA solutions, plus
  `command -v nproc` to check availability. These reported 1 and 2 CPUs for
  matching Helma allocations. Exceptions match the tested scripts and Dockerfiles;
  other uses still require testing. The previously reviewed InferredBugs Python
  build also allows `make -j"$(nproc)"`. The strict Terminal-Bench profile is unchanged.

**Checks we introduced:**

- Before running the upstream checks, verify that required task files and the
  environment directory exist, and that `task.toml` can be parsed.

**Off by default:**

| Check | What it checks | Why we turn it off |
| --- | --- | --- |
| `test-sh-sanity` | Requires tests to create a separate Python environment with `uv`, or explicitly document using the existing Python installation. | Tests can use Python and packages already installed in the container. Stages 4 and 5 check that the tests actually run. |
| `separate-verifier` | Requires tests to run in a separate container from the agent. | Some tasks ask the agent to install software or change system settings. Their tests need to inspect the same container to check those changes. |
| `allow-internet` | Rejects tasks that explicitly disable internet access. | Some tasks are intended to run offline. |
| `no-allow-internet-true` | Rejects tasks that explicitly enable internet access, because Terminal-Bench expects them to use its default. | We allow tasks to state that they need internet access. Stage 3 checks the declared network setting. |
| `pytest-version` | Requires pinned versions of `pytest` and its JSON report plugin to match Terminal-Bench's chosen versions. | Datasets may need different versions. We still check that package versions are pinned. |
| `gpu-types` | Requires GPU names from the Modal cloud platform's list. | We run on Slurm clusters, which use their own GPU names and settings. |
| `canary` | Requires a special identifying string in task files so benchmark content can be detected in training data. | These datasets do not need Terminal-Bench's identifying string. |
| `instruction-headings` | Rejects Markdown headings in instructions. | We allow Markdown in instructions. |
| `task-fields` | Requires benchmark author details, categories, difficulty estimates, and explanatory README sections. | We accept datasets without that benchmark-specific metadata. |
| `task-package-name` | Requires every task's package name to be `terminal-bench/<folder>`. | Tasks can keep their own dataset's package names. |
| `task-slug` | Limits task folder names to three words separated by hyphens. | Longer task names are allowed. |
| `task-changelog` | Requires a changelog entry when a pull request changes an existing benchmark task. | We validate datasets without requiring a Terminal-Bench pull request or its change history. |

LLM rubric review belongs to stage 2. GPTZero's AI-detection check runs only when
`GPTZERO_API_KEY` is set.

Results include each check's status and logs. Use `--exclude NAME=reason` to skip
another check, or `--static-profile terminal-bench` for stricter benchmark rules
(the changelog check remains excluded).

## Stage 2: LLM rubric review

An implementation reviewer reads the instruction, tests, solution and environment
files. It assesses whether tests match the requested outcome, the solution is
valid, dependencies and verification are reliable, and the task is useful,
appropriately difficult and resistant to cheating. It uses Terminal-Bench's
[rubric][implementation-rubric] and [prompt template][implementation-prompt].
We may need to relax some criteria, such as novelty, because our tasks are for
training rather than evaluation.

After the run finishes, `report/reviews.json` contains each task's review and
explanations. `report/summary.json` lists which tasks passed, failed, or had a
review error.

The default is Claude Code (`claude-code`) with `anthropic/claude-sonnet-5`.
Change these with `--review-agent` and `--review-model`.

Start with a small pilot because LLM reviews are expensive. Use
[`create_pilot.py`](data/create_pilot.py) to select a diverse sample from earlier
runs, as explained under [Sampling a diverse pilot](#sampling-a-diverse-pilot).

## Stage 3: build and runtime validation

Slurm jobs build missing images before validation starts and reuse cached images
on later runs. Changed environment build files or `--force-build` trigger a rebuild.
Building has its own timeout, **1 hour per image** by default
(`--image-build-timeout-sec`).

Stage 3 starts containers and runs task setup within
`environment.build_timeout_sec` from `task.toml`, then checks:

- **Working directory:** matches the Dockerfile's `WORKDIR`, when it can be determined.
- **Internet access:** compares HTTPS requests with `allow_internet` in `task.toml`.
  With `true`, at least one request must succeed; with `false`, all must fail.
  Without a setting, results are recorded only. We try a few public addresses,
  so this does not prove that every internet connection is blocked or allowed.

Outside setup review, a failed task gets three retries in fresh containers,
reusing cached images.
This gives tasks a chance to recover from temporary infrastructure errors.
All three must pass; otherwise the task is labeled `unstable-build-under-our-infra`
and archived when publishing. Stages 4 and 5 skip it in that run because reference
and no-op checks require a working environment.

When you publish the dataset to Hugging Face, the publishing script automatically
includes available cached container images for kept tasks that passed stage 3 and only includes
images with a recorded build history.

### What goes in setup vs image

- **Setup:** start with git clones/checkouts, initial task files and small
  configuration changes. These are usually quick and let many tasks share an image.
- **Task image:** install tools and dependencies the agent may use here when
  preparing them during setup is slow or unreliable. A dependency used by the
  verifier can also go here if the agent is allowed to have it. Dependencies
  the agent must not access belong in a **separate verifier image**.

For clean verification, start a fresh container from the cached task image,
repeat task setup, then copy in the submitted files. This gives the verifier
original tools even if the agent changed its installation, with **no additional
unique image**. Set `environment_mode = "separate"` under `[verifier]`
in `task.toml`; a verifier without its own image definition reuses the task image.
Declare submitted files through the existing artifact settings. A custom
verifier image can instead provide dependencies the agent must not access.

Use stage 3's `--review-setup` to find the balance. It runs task setup followed by
verifier preparation in **five fresh runs**, using prepared images, and times them
separately. Move expensive installation steps into images and repeat until:

- **Task preparation:** mean at most **30 seconds**.
- **Verifier preparation:** mean at most **5% of the verifier timeout** in `task.toml`.
- **Both:** every run succeeds and no individual run exceeds **60 seconds**.

The **task timer** includes starting the agent container, uploading setup files
and running task setup. The **verifier timer** includes starting a separate
verifier container if needed, uploading test files, transferring declared
submission files, repeating task setup when using the task image, and running
verifier setup. File transfers performed by the runner count
even when they are outside the setup script. Each timer is checked against its
own limits above.

Slow or failed tasks are saved in **task-setup-needs-review** and/or
**verifier-setup-needs-review** buckets with timings and logs. These five runs
replace the usual failure retries.

Put verifier preparation in `tests/setup.sh` and the actual verification in
`tests/test.sh`. Stage 3's setup review runs only `setup.sh`; normal verification
runs it first, then `test.sh`. If setup fails, verification stops. Omit `setup.sh`
when no preparation is needed. Some existing `test.sh` scripts mix setup and
verification; **separate them first** so the review measures all preparation.
See [setup review details](docs/runtime.md#setup-review) for timing and options.

## Stage 4: reference solution

Runs `solution/solve.sh`, then the task's verifier. Every attempt must return
reward **1** without execution errors. Tasks without a reference solution are skipped.

## Stage 5: no-op baseline

Runs task setup and the verifier without a solving agent. Every attempt must
return reward **0**; verifier crashes or missing rewards do not count as a pass,
except for the missing-file case below.

Sometimes the no-op check fails because the verifier crashes while opening a file
that the agent was supposed to create. To handle this expected missing file,
change the verifier to assert that the file exists before opening it. The test
then fails normally and returns reward **0**, which passes the no-op check.

**UPDATE:** Stage 5 now automatically counts `FileNotFoundError` for a path
mentioned in `instruction.md` as a valid no-op (reward **0**), even if no reward
was written.

## Stage 6: teacher agent trials

Runs the selected agent and model `k` times per task (`--attempts k`), saving
rewards and trajectories. Each task gets a solved count: **0/k, 1/k, …, k/k**.

After a Slurm run, results are saved automatically under
`<output>/submissions/ID/report/` in `stage-6-*.json` and `summary.json`. They include:

- **pass@1:** average success rate across tasks. A task solved 2 out of 4 times has 50%.
- **pass@k:** percentage of tasks solved at least once in their `k` attempts.
- **Mean reward:** average score over graded attempts. With binary rewards and no missing grades, this equals pass@1; otherwise it can differ.
- **Partial-credit rate:** percentage of attempts scoring between 0 and 1. Only relevant for nonbinary rewards; it is zero for binary rewards.
- **Missing rewards and errors:** attempts that could not be graded, with execution errors recorded separately. Missing rewards count as unsolved in pass rates.

The report automatically generates two plots beside the JSON files:

- `stage-6-*-pass-rates.png`: pass-rate bars for each task family and all tasks.
- `stage-6-*-solved-counts.png`: number of tasks solved 0/k, 1/k, …, k/k times,
  with separate panels for different attempt counts.

[`publish_pass_counts.py`](publishing/publish_pass_counts.py) uploads a
`pass_counts.parquet` table per data source to Hugging Face. Each row contains
the task ID, its version hash, the model, and the solved and attempt counts.
This lets you filter the dataset by a model's success rate, for example selecting
only tasks it solved 2 out of 4 times.

## Stage 7: trace metrics

Reads trajectories from stage 6 without calling a model (the trajectory path is
supplied automatically when stages 6 and 7 run together in one Slurm job; for a
separate run, pass `--trials JOB_DIR` pointing to the stage 6 trial directory).
Computes statistics saved to `<output>/submissions/ID/report/stage-7-*.json`
after a Slurm run, including:

- Agent turns, token usage, and peak context usage.
- Time spent on model calls, agent execution, and verification.
- Tool use, command patterns, and tool failures.
- Termination reasons and model, environment, or verifier errors.
- Model request failures and GPU usage when local-serving logs are available.

Results include individual trajectories and summaries per task, task family, and
overall. Missing measurements remain `null`.

The report automatically saves histograms of turns, tokens, context usage, and
runtimes, plus bar charts of termination reasons and errors beside the JSON files.

## Stage 8: LLM trajectory analysis

A judge reads the task and trajectory using the Terminal-Bench
[prompt template][analysis-prompt] and [rubric][analysis-rubric] to assess
instruction sufficiency, reward hacking, intended difficulty, near misses,
refusals, and whether the time limit interrupted useful progress. Each review
is saved as `analysis.json`. We may need to adjust the rubric for training
rather than evaluation tasks.

Defaults follow stage 2; use `--analysis-agent` and `--analysis-model` to override
them separately. Start with a [small pilot](#sampling-a-diverse-pilot) to limit
review costs. The trajectory path is supplied automatically when run together
with stage 6; for a separate run, pass `--trials JOB_DIR` pointing to the stage 6
trial directory.

## Stage 9: adversarial trials (optional, untested)

Adds the Terminal-Bench [cheat prompt][cheat-prompt] to a task copy and starts an
agent session.

## Stage 10: hacker/fixer loop (optional, untested)

Runs a loop on a task copy: a hacker finds exploits, a fixer repairs the task,
and a solver tests the result. Uses the
[hacker, solver, and fixer instructions][harden-instructions],
[fixer guidance][fixer-guidance], and Terminal-Bench [loop settings][fortify].

`--model` selects the model for all three roles. `--max-iterations` and
`--timeout-minutes` limit the loop. Results include a summary, logs, and the
edited task.

## Sampling a diverse pilot

After stage 7, [`create_pilot.py`](data/create_pilot.py) groups tasks by reward
outcome: always solved, always zero, varying rewards, constant partial credit,
or no reward. Within these groups, it splits by short, medium, and long
trajectories, then task family. Stage 8 also groups by how trajectories ended.
It samples as evenly as possible at each level, redistributing places when a
group is too small. Within each final group, it favors examples with different
command usage.

```bash
python validation/data/create_pilot.py /path/to/tasks \
  --trace-metrics /path/to/stage7/trace-metrics.json \
  --stage 2 --size 20 --family-from-id --seed 42 \
  --out "$OT_WORKSPACE/pilot-stage2"
```

`--trace-metrics` gives stage 7 statistics, including command usage. `--stage`
selects task review (`2`) or trajectory review (`8`); `--size` limits tasks or
trajectories respectively, and `--seed` makes selection repeatable.
`--family-from-id` reads families from `<dataset>-<family>-<number>` IDs when
metadata is missing; use `--families mapping.json` for a custom mapping. For
stage 8, also supply `--trials /path/to/teacher/job`. `--out` receives the selected
`tasks/`, a `pilot.json` describing the selection, and, for stage 8, the selected
`trials/`.

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

### Automatic change attribution

Automatic fixes record cumulative per-task `automation_changes` in `source.json`
and the frozen contract. The default labels are `anti-cheat-instruction` for
suffix edits, `relative-path-fix` for evidence-backed path replacements, and
`dependency-pinning` for pip pins captured from a successful reference run.
A label is added only when that automation changes content. Later normalization
copies preserve earlier labels and restrict them to the selected task IDs.
Publication carries these labels into task `patch_labels` and the PR change
table, with a separate count of unique tasks changed by automation. Category
counts can overlap. These record automation activity, while the source comparison
continues to describe net changes against upstream. Older frozen contracts
without this metadata do not gain inferred automation labels.
