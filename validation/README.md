# Task validation

Combines the [Terminal-Bench task validation protocol](https://github.com/harbor-framework/terminal-bench/blob/1dcda8716784493721921c23e4bc7f7d988b4494/docs/TASK_REVIEW_AUTOMATION.md) at commit `1dcda8716784` with the [Agentic RL dataset validation protocol](https://gist.github.com/marianna13/f9bc94d2ecb39c302c6c9b540ea6100a).

## Workflow

1. Validate a diverse 10-task pilot with stages 1, 3, 4 and 5. Collect fixes in
   `data/<dataset>/patch.py`, then validate the full patched dataset.
2. Pilot teacher generation with stages 6 and 7. Inspect scores, errors and
   trajectories before increasing the task count and attempts.
3. Pilot LLM reviews with stages 2 and 8.
   `data/create_pilot.py` samples earlier results for review.

| Stage | Purpose |
| --- | --- |
| [1](PROTOCOL.md#stage-1-static-checks) | Static task checks |
| [2](PROTOCOL.md#stage-2-llm-rubric-review) | LLM rubric review of instructions and task implementation |
| [3](PROTOCOL.md#stage-3-build-and-runtime-validation) | Container build and runtime checks |
| [4](PROTOCOL.md#stage-4-reference-solution) | Reference solution: reward 1 |
| [5](PROTOCOL.md#stage-5-no-op-baseline) | No-op agent: reward 0 |
| [6](PROTOCOL.md#stage-6-teacher-agent-trials) | Teacher agent attempts and reward statistics |
| [7](PROTOCOL.md#stage-7-trace-metrics) | Trajectory statistics |
| [8](PROTOCOL.md#stage-8-llm-trajectory-analysis) | LLM rubric review of trajectories |
| [9](PROTOCOL.md#stage-9-adversarial-trials-optional-untested) | Adversarial agent attempts |
| [10](PROTOCOL.md#stage-10-hackerfixer-loop-optional-untested) | Hacker/fixer task hardening |

## Quickstart

### Create a contract

Pass your settings as command-line arguments to `--prepare-contract`. It
generates a contract JSON that records the selected tasks, stages, and settings
without submitting a job. You do not need to write the JSON manually. Options
you omit use their defaults.

```bash
python validation/run.py /path/to/tasks \
  --stages 1,3,4,5 --limit 10 \
  --submit helma --partition cpu --cpus 48 \
  --concurrency 4 --time 02:00:00 \
  --out "$OT_WORKSPACE/validation-pilot" \
  --prepare-contract "$OT_WORKSPACE/contracts/check.json"
```

### Arguments

Set the following arguments when preparing the contract:

| Argument | Meaning |
| --- | --- |
| `/path/to/tasks` | Input task directory or TaskTrove Parquet. |
| `--stages` | One stage or several separated by commas, such as `1,3,4,5`, `6,7`, or `2`. Stages 9 and 10 are optional and untested. |
| `--limit N` | Select at most N tasks. Omit for the full dataset. |
| `--submit helma` or `--submit zih` | Cluster to submit to. |
| `--partition` | Slurm partition. The default `auto` chooses according to whether local model serving needs GPUs. |
| `--cpus`, `--memory`, `--gpus` | Resources requested for the job allocation. |
| `--time HH:MM:SS` | Maximum Slurm job duration. |
| `--concurrency N` | Tasks or trials running at once, default 1. Choose according to their CPU and memory needs. |
| `--static-concurrency N` | Number of tasks checked at once in stage 1. Defaults to `--concurrency`, limited by available CPUs. |
| `--image-build-concurrency N` | Images built simultaneously before validation, default 2. |
| `--out` | Directory for results and submission files. |
| `--prepare-contract PATH` | Save the selected tasks and settings in a contract JSON without running validation. |
| `--contract PATH` | Load a previously prepared contract and run validation with its saved settings. |

For teacher generation and LLM reviews:

| Argument | Meaning |
| --- | --- |
| `--agent` | Agent harness, default `terminus-2`. |
| `--model PROVIDER/MODEL` | Model for teacher generation (stage 6) and the optional stages 9 and 10. |
| `--serve-model MODEL` | Start a local model configured in `config/models.py`. |
| `--api-base URL` | Connect to an existing model endpoint. |
| `--attempts K` | Attempts per task, default 1. In stage 6 this is `k` for solved counts and pass@k. Also sets repetitions for reference, no-op, and review trials. |
| `--review-agent`, `--review-model` | Override stage 2 defaults; also apply to stage 8 unless separately overridden. |
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

## Publishing after validation

After validation, you can automatically open a Hugging Face PR containing the
dataset and validation results, and optionally generate a README for each data
source. Add `--publish-repo FWeindel/validated-tasks` when preparing the contract,
and `--publish-readme` to generate dataset READMEs.

Publishing opens a pull request for review; it does not merge it. A Hugging Face
login with write access is required.

For each data source, it uploads retained tasks as `tasks.parquet`, excluded
tasks with their reasons as `archive.parquet`, validation results, and eligible
cached images. Failures in stages 1, 3, 4, and 5 can exclude tasks; other stages
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
