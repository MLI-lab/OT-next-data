# Task validation

Combines the [Terminal-Bench task validation protocol](https://github.com/harbor-framework/terminal-bench/blob/1dcda8716784493721921c23e4bc7f7d988b4494/docs/TASK_REVIEW_AUTOMATION.md) at commit `1dcda8716784` with the [Agentic RL dataset validation protocol](https://gist.github.com/marianna13/f9bc94d2ecb39c302c6c9b540ea6100a).

## Workflow

1. Validate a diverse 10-task pilot with stages 1, 2, 3 and 4. Collect fixes in
   `data/<dataset>/patch.py`, then validate the full patched dataset.
2. Pilot teacher generation with stages 6 and 7. Inspect scores, errors and
   trajectories before increasing the task count and attempts.
3. Pilot LLM reviews with stages 5 and 8.
   `data/create_pilot.py` samples earlier results for review.

| Stage | Purpose |
| --- | --- |
| [1](PROTOCOL.md#stage-1-build-setup-and-runtime-checks) | Container build, setup and runtime checks |
| [2](PROTOCOL.md#stage-2-reference-solution) | Reference solution: reward 1 |
| [3](PROTOCOL.md#stage-3-no-op-baseline) | No-op agent: reward 0 |
| [4](PROTOCOL.md#stage-4-static-checks) | All static checks |
| [5](PROTOCOL.md#stage-5-llm-task-quality-review) | LLM rubric review of instructions and task implementation |
| [6](PROTOCOL.md#stage-6-passk) | Student pass@k, then teacher pass@2 on unsolved tasks |
| [7](PROTOCOL.md#stage-7-trace-metrics) | Trajectory statistics |
| [8](PROTOCOL.md#stage-8-llm-trajectory-review) | LLM rubric review of trajectories |
| [9](PROTOCOL.md#stage-9-adversarial-trials-optional-untested) | Adversarial agent attempts |
| [10](PROTOCOL.md#stage-10-hackerfixer-loop-optional-untested) | Hacker/fixer task hardening |

You can run any subset of stages. A task whose build fails is skipped in stages 2 and 3.
All static rules run together in stage 4, after the automatic repairs, and dynamic rules run in
stages 1-3; see [rules/README.md](rules/README.md). `python -m validation.rules TASKS --rules static`
runs all static rules without a job.

## Folders

| Folder | Contents |
| --- | --- |
| `stages/` | Runs each stage (`runner.py`) |
| `rules/` | The rules stages apply, static and dynamic, with their automatic repairs |
| `metrics/` | Rewards, pass@k and trajectory statistics |
| `rubrics/` | Rubrics for the LLM reviews (stages 5 and 8) |
| `publishing/` | Reports, plots, Hugging Face PRs, dataset cards, image releases |
| `checkpoints/` | Reusing finished work when a run is resumed |
| `data/` | Selecting tasks and unpacking TaskTrove Parquets |
| `dataset_checks/` | Whole-dataset checks: image counts, reproducibility, isolation |
| `upstream/` | Downloads and hash-checks the pinned Terminal-Bench, harbor-validation and harden-v0 code |

## Quickstart

### Create a contract

A contract is the plan of a run, fixed before it starts: the exact task IDs and
their content hashes, the stages, settings and code version. This is what lets a
run be checked, resumed or reproduced later. You do not write it by hand and never
edit it: `--prepare-contract` creates it, and you prepare a new one when tasks,
settings or code change.

Pass your settings as command-line arguments to `--prepare-contract`. It
generates a contract JSON that records the selected tasks, stages, and settings
without submitting a job. You do not need to write the JSON manually. Options
you omit use their defaults.

```bash
python validation/run.py /path/to/tasks \
  --stages 1,2,3,4 --limit 10 \
  --submit helma --partition cpu --cpus 48 \
  --concurrency 4 --time 02:00:00 \
  --out "$OT_WORKSPACE/validation-pilot" \
  --prepare-contract "$OT_WORKSPACE/contracts/check.json"
```

### Contract versions and evidence reuse

New contracts use schema version 3 and stage layout `environment-first-v2`
(until 2026-10-17, `environment-first-v1` had 2 = no-op and 3 = reference; old
records are translated by stage name).
Reports record the same layout. Existing contracts, reports, and submitted jobs
retain their original stage meanings and must be inspected or resumed with their
frozen code. The current runner rejects legacy contracts instead of reinterpreting
stage numbers. Prepare a new contract to use this layout; do not edit old contracts.
Expected-outcome files now use `ot-expected-outcomes-v2` for the same reason.
Stages 2 and 3 run each task 3 times by default (`--reference-runs`, `--nop-runs`), each run
in a fresh container. Stage 1 builds each task once and retries a failed build up to 3 times.
The `--reuse-build` option reuses compatible stage-1 build evidence; the old
`--reuse-stage3` spelling remains an alias, not a way to import legacy evidence.

The repair controller retains passing build/setup evidence across repair generations
only when task content, source revision, runtime dependencies, execution settings,
validation implementation and acceptance criteria match. After a completed full
build/setup run, it skips repeated pilots when compatible passing evidence exists
and submits only changed, restored, failed or previously untested tasks. Its full
report combines old and new observations with per-task provenance; it is not a
new execution of the unchanged tasks. Runtime or protocol changes invalidate reuse.
The final validation reuses the compatible passing build/setup checkpoint while
running its remaining checks. First runs and incompatible repairs still follow
the pilot progression. Full-dataset image prebuilding is optional; normally each
pilot builds missing images and reuses the resulting cache in later runs.

### Arguments

Set the following arguments when preparing the contract:

| Argument | Meaning |
| --- | --- |
| `/path/to/tasks` | Input task directory or TaskTrove Parquet. |
| `--stages` | One stage or several separated by commas, such as `1,2,3,4`, `6,7`, or `5`. Stages 9 and 10 are optional and untested. |
| `--limit N` | Select at most N tasks. Omit for the full dataset. |
| `--submit helma` or `--submit zih` | Cluster to submit to. |
| `--partition` | Slurm partition. The default `auto` chooses according to whether local model serving needs GPUs. |
| `--cpus`, `--memory`, `--gpus` | Resources requested for the job allocation. |
| `--time HH:MM:SS` | Maximum Slurm job duration. |
| `--concurrency N` | Tasks or trials running at once, default 1. Choose according to their CPU and memory needs. |
| `--static-concurrency N` | Number of tasks checked at once by the static rules. Defaults to `--concurrency`, limited by available CPUs. |
| `--image-build-concurrency N` | Images built simultaneously before validation, default 2. |
| `--out` | Directory for results and submission files. |
| `--prepare-contract PATH` | Save the selected tasks and settings in a contract JSON without running validation. |
| `--contract PATH` | Load a previously prepared contract and run validation with its saved settings. |
| `--exclude NAME=reason` | Skip another stage-4 static check, with the reason recorded. |
| `--static-profile terminal-bench` | Use the stricter benchmark checks instead of the default `training` profile. |
| `--nop-shuffled-runs N`, `--listing-order-check warn\|fail` | Extra stage-3 no-op runs with shuffled directory listings (default 1, `0` disables), and whether a finding only warns (default) or fails the task. |
| `--path-resolution-report PATH` | Stage-1 report whose path observations stage 4 reuses, when stages 1 and 4 run separately. |

For teacher generation and LLM reviews:

| Argument | Meaning |
| --- | --- |
| `--agent` | Agent harness, default `terminus-2`. |
| `--model PROVIDER/MODEL` | Model for teacher generation (stage 6) and the optional stages 9 and 10. |
| `--serve-model MODEL` | Start a local model configured in `config/models.py`. |
| `--api-base URL` | Connect to an existing model endpoint. |
| `--attempts K` | Agent attempts per task in stage 6, default 1: `k` for solved counts and pass@k. Also the default number of LLM reviews. |
| `--reference-runs N`, `--nop-runs N` | Runs per task in stages 2 and 3, default 3, each in a fresh container. All reference runs must score 1 and all no-op runs 0. Stage 1 builds each task once and retries a failed build up to 3 times. |
| `--review-runs N` | Independent stage-5 judge runs per task; defaults to `--attempts`. |
| `--review-retries N` | Repair rounds for missing/invalid judgments, default 2. |
| `--review-resume` / `--no-review-resume` | Resume matching stage-5 checkpoints (enabled by default), or request fresh reviews. |
| `--review-agent`, `--review-model` | Override stage 5 defaults; also apply to stage 8 unless separately overridden. |
| `--analysis-agent`, `--analysis-model` | Override only the stage 8 judge. |
| `--review-local` | Use the locally served model for reviews; requires `--serve-model`. |
| `--trials JOB_DIR` | Saved trial results and trajectories for stages 7 and 8. Supplied automatically when run together with stage 6. |

### Run validation using the contract

Submit using the saved contract:

```bash
python validation/run.py --contract "$OT_WORKSPACE/contracts/check.json"
```

Results are saved under `<out>/submissions/ID/`: `report/` contains summaries,
stage reports, and automatic plots; `evidence.tar.gz` contains detailed run
evidence. Prepare a new contract when changing tasks or settings.
If the submission folder's filesystem is out of quota when the run ends (file count
or space), the evidence, `execution.json` and `report/` go to
`$OT_EVIDENCE_FALLBACK` (default `$HOME`) under `validation-evidence-fallback/<mirrored path>`,
`evidence-relocated.json` beside the request points there, and the job fails only if
that location is full too.

Run `python validation/run.py --help` for all options.

Large contracts run as an array of shards under the same contract:

```bash
python validation/shards.py plan --contract "$OT_WORKSPACE/contracts/check.json" --count 40 --out "$OT_WORKSPACE/runs/check" --time 06:00:00 --submit
python validation/shards.py merge "$OT_WORKSPACE/runs/check" --contract "$OT_WORKSPACE/contracts/check.json" --out "$OT_WORKSPACE/runs/check/merged/report"
```

Each shard validates one slice of the frozen task list; `merge` writes one report per stage
once every slice has reported, and `publish.py` takes the merged directory like any run.

## Run details

### Retries after the run

After all stages have run once, every task of runner stages 1, 2 and 3 is classified as
the publisher classifies it: passed, archived, or not run (crash, node failure,
missing reward, missing runner evidence). Tasks that are not run are rerun in
those stages up to three more times, runner stage 1 first so a recovered build lets its
trials run in the same round. A wrong reward or a failed build is a result and is
not retried. A task still without a result after the last attempt is archived as
not robust under this infrastructure, so every run ends in a decision for every
task.

When an earlier audit of the same tasks is supplied with `--expected-outcomes`
(e.g. from a previous manual audit), results that differ from it are rerun
instead of applying the classifier, and a task that never reproduces the audit
is archived as not robust. The pull request reports, per stage, how many tasks
matched on the first attempt, after one, two or three retries, or were archived,
and where the expected outcomes came from.

### Sharded runs of one contract

A frozen contract may be executed in shards. Each shard job validates one slice of the
contract's task list (tasks sorted by ID, taken round-robin by shard index), verifies the
source files against the contract and the content of its own slice, and marks its stage
reports with the slice. `validation/shards.py merge` combines the shard reports into one report per stage
only when every slice is present exactly once and belongs to the same contract; the merged
report records each shard's job and timing. Retries on unexpected outcomes run inside each
shard. A merged run is published like a single run.

### Sampling a diverse pilot

After runner stage 7, [`create_pilot.py`](data/create_pilot.py) groups tasks by reward
outcome: always solved, always zero, varying rewards, constant partial credit,
or no reward. Within these groups, it splits by short, medium, and long
trajectories, then task family. Runner stage 8 also groups by how trajectories ended.
It samples as evenly as possible at each level, redistributing places when a
group is too small. Within each final group, it favors examples with different
command usage.

```bash
python validation/data/create_pilot.py /path/to/tasks \
  --trace-metrics /path/to/stage7/trace-metrics.json \
  --stage 5 --size 20 --family-from-id --seed 42 \
  --out "$OT_WORKSPACE/pilot-stage5"
```

`--trace-metrics` gives runner stage 7 statistics, including command usage. `--stage`
selects task review (`5`) or trajectory review (`8`); `--size` limits tasks or
trajectories respectively, and `--seed` makes selection repeatable.
`--family-from-id` reads families from `<dataset>-<family>-<number>` IDs when
metadata is missing; use `--families mapping.json` for a custom mapping. For
runner stage 8, also supply `--trials /path/to/teacher/job`. `--out` receives the selected
`tasks/`, a `pilot.json` describing the selection, and, for runner stage 8, the selected
`trials/`.

### pass@k reports

Acceptance checks collection completeness, independently of stages 1 through 3.
Every selected task must have exactly the requested number of distinct terminal
attempts.

> An attempt succeeds only if verification returns a passing reward. Any other
> outcome, including a verifier failure, counts as unsolved.

This rule applies to recorded terminal attempts, regardless of the failure's
cause. It does not turn missing or unfinished attempts into completed attempts.
Completed attempts with a recorded error and no valid verifier reward receive
**reward 0** in stage-6 and stage-7 results. The original verifier output and
exceptions are preserved for diagnosis, separately from the assigned reward.
No replacement attempt is generated. Reports expose the verifier-error rate;
these scored failures do not contribute to the no-reward rate. Missing, duplicate, unfinished
or unaccounted-for attempts keep collection incomplete. Reference and no-op
validation retain their strict execution and reward requirements. In particular,
stage 3 (NOP) still requires a measured zero reward and valid test execution,
subject to its documented missing-file exception. The pass@k completion checker
does not relax NOP validation.

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

Stage-5 reviews automatically repair missing/invalid criterion output (two repair
rounds by default; `--review-retries`), retain valid judgments, and resume matching
checkpoints. `--review-runs N` requests independent runs per task. See
[review execution and analysis](rubrics/README.md#running-and-comparing-reviews).

## Publishing after validation

After validation, you can automatically open a Hugging Face PR containing the
dataset and validation results, and optionally generate a README for each data
source. Add `--publish-repo FWeindel/validated-tasks` when preparing the contract,
and `--publish-readme` to generate dataset READMEs.

Publishing opens a pull request for review; it does not merge it. A Hugging Face
login with write access is required.

For each data source, it uploads retained tasks as `tasks.parquet`, excluded
tasks with their reasons as `archive.parquet`, validation results, and eligible
cached images. Failures in stages 1, 2, 3 and 4 can exclude tasks; other stages
provide additional measurements and reviews.

To publish an existing run, use [`publish.py`](publishing/publish.py). Add
`--dry-run` to preview the files without opening a pull request. Teacher solved
counts are published separately with
[`publish_pass_counts.py`](publishing/publish_pass_counts.py), allowing tasks to
be filtered by model success rate.

### Automatic PR text

The PR summarizes which tasks stay in the dataset, which were archived, and
which our patchers changed.

For each data source, it first shows the total tasks, how many remain, and how
many were archived. A table lists archive labels, task counts, and explanations.
Each archived task has one label. Default labels describe the failed check or
stage that caused the task to be archived. If you provide a custom failure
label, the PR uses it instead. When several static checks fail, the first label
alphabetically is used; all failures remain recorded in the detailed results.

It then shows how many original tasks our patcher modified, with the upstream
source link and revision. A second table lists change labels, task counts, and
optional explanations. A task can have several changes, but the modified total
counts it once. Changes without labels appear as `other-changes`.

You can add details in two places:

**In the patch script**, describe modified tasks through `write_patch_report(...)`:

```python
change_labels={"task-001": ["executable-verification"]},
change_reasons={
    "task-001": "Replace text-only verification with compilation and execution."
},
```

For tasks excluded by the patcher, give a category and reason:

```python
dropped={
    "task-002": {
        "category": "environment-instruction-mismatch",
        "reason": "The environment contains a different project from the instructions.",
    },
},
```

**After inspecting validation results, before publishing**, add these fields to
the failed task's entry in its stage report:

```json
{
  "failure_label": "environment-instruction-mismatch",
  "failure_explanation": "The environment contains a different project from the instructions."
}
```

These descriptions explain the changes or exclusions; they do not change
validation outcomes.

### Automatic README generation

Add `--publish-readme` when preparing the contract to generate a README for each
data source during publishing. Claude Code inspects up to ten randomly sampled
tasks that remain in the dataset, including their instructions and verifier
code, using our [prompt template](../data/annotate_dataset/prompt_template.txt).

The README includes:

- **Original source:** the dataset or benchmark name and a link to its paper or
  official page.
- **Domains:** labels from our
  [domain taxonomy](../data/annotate_dataset/domain_taxonomy.txt), with
  explanations of why they fit.
- **Agent capabilities:** labels from our
  [capability taxonomy](../data/annotate_dataset/capability_taxonomy.txt),
  explaining what the tasks are intended to exercise.
- **Expected benchmark transfer:** benchmarks from our
  [evaluation suite](../data/annotate_dataset/evaluation_suite.txt) that training
  on these tasks might help, with reasoning. These are expectations, not
  measured improvements.
- **Dataset files:** a brief explanation of `tasks.parquet` and `archive.parquet`.

Use `--publish-readme-evidence FOLDER=PATH` to provide additional source
documentation and `--publish-readme-seed` to make the task sample repeatable.
Validation results and patch statistics appear in the PR rather than the
dataset README.

Existing READMEs are preserved unless `--publish-readme-force` is also set.
Generation requires Claude Code credentials and container network access. For
standalone publishing, use `--readme` and optionally `--readme-force`.
