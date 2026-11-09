# Task-quality judging

Layout: our rubric in `rubric_ot/`, the others in `alternatives/`, judge experiments and their results in `judge_results/`.

**The default rubric is [`rubric_ot/`](rubric_ot/rubric.toml).** We tuned its definitions in the
[judge experiments](judge_results/README.md) to remove as much subjectiveness as possible, so that
repeated reviews and different judges reach the same verdict.

We use [our rubric](rubric_ot/rubric.toml) (`ot`, adapted from River; see [rubric_ot/README.md](rubric_ot/README.md)) with two
prompting strategies:

- **Labeling:** judge every criterion independently as pass (defect absent) or
  fail (defect present), using the [River labeling prompt](prompt_templates/modified/river_labeling.txt).
- **Classification:** select one primary category and explain the decision,
  using the [classification prompt](prompt_templates/modified/classification.txt).
  If `CLEAN` is missing, add its definition from the
  [original River rubric](alternatives/rubric_river/rubric.toml) and emit a warning.

Select labeling with `--review-rubric ot --review-format labeling`,
or classification with `--review-rubric ot --review-format classification`.

By default, each judge starts in the task's own cached image, with the task's
workspace and setup available for inspection. If no matching task image is
cached, it uses a base Ubuntu image instead. You can also select this directly
with `--review-evidence files`, or require the task's image with
`--review-evidence image`.

**Which tasks are judged.** Stage 5 skips tasks with a failed stage 0–3 check. It
reads the run's `results.parquet`, any `--judge-results` files and the stages 0–3
of the same run. Warnings and checks without a result (execution error, timeout,
out of memory, blocked) do not exclude a task. Skipped tasks are recorded as
`skipped` with the failed checks. `--judge-failed-tasks include` judges every task.

## Data

We use ground-truth labeled tasks from
[FWeindel/task-defect-ground-truth](https://huggingface.co/datasets/FWeindel/task-defect-ground-truth)
to evaluate the judges' predictions against the reference labels.

## Judge configuration

We consider **Sonnet 5.5, GLM-5.3, GLM-5.3-Flash and Qwen3.8-27B** as judges,
all with thinking on. The open-weight models use their published Hugging Face
sampling defaults and default reasoning efforts: **max** for both GLMs and
**xhigh** for Qwen. Sonnet uses its provider defaults with adaptive thinking
and our selected **medium** effort.

For the three local models, we limit each response to **65,536 output tokens,
including thinking**, within a **262,144-token context window**. These are our
experiment limits, not the models' HF defaults. Sonnet keeps Claude Code's default
context and output limits. Existing local endpoints must be started with the
matching context window and native tool-call parsing enabled; request settings
cannot enlarge a running server's limit.

The local models use **MiniSWE (mini-swe-agent 2.4.6)** by default, keeping
reasoning between tool calls in the conversation history. The harness runs on
the host and executes Bash commands inside the task image, without installing
agent packages there. Each Bash call starts a new shell; files persist, but
shell variables and directory changes do not. Commands have MiniSWE’s default
30-second limit, adjustable with `command_timeout_sec` in agent kwargs.
MiniSWE keeps the full history without automatic context compression; reaching
the context limit ends the attempt.

MiniSWE is also the default for pass@k. Select `--agent terminus-2` for pass@k
or `--judge-harness terminus-2` for API/served judges to use Terminus-2 instead.

Sonnet uses **Claude Code** with
`WebSearch` and `WebFetch` disabled. This blocks
those web tools, not internet access through Bash or other tools. Network access
depends on the runtime.

The shared configuration is in [config/models.py](../../config/models.py), under
`MODELS` and `JUDGE_MODELS`. All judges use these settings unless you choose
different values for a run. Any settings left unspecified use the HF checkpoint
or provider defaults.

## Session limits and correction messages

All judges have a default **5-hour review session**. If the judge has not
written output by the end of that time, we send:

> The review time limit has been reached. Do not investigate further. Write /app/verdicts.json now from the evidence you have. You have 60 minutes.

The judge then has **another hour** to finish its answer.

If the judge tries to finish with invalid output—for example, malformed JSON or
a missing criterion—we send:

> /app/verdicts.json is not valid, so the review cannot be accepted yet: <error>. Correct the file, then mark the task complete again.

The judge can correct the existing file once within the remaining session time,
including the answer-writing hour. This message does not start a new hour.

**Fresh retry:** if the session still ends incomplete, `--review-retries 1`
allows one new review with its own full session budget. Valid labels are kept,
and the new review gets only the missing or invalid criteria. If the JSON cannot
be parsed or has no usable labels, the full rubric is retried. Classification
retries use the full category list. This is separate from the answer-writing period.

## Previous experiments

- [Experiment 1 — Judge calibration](old/experiment-1.md)
- [Experiment 2 — Task-quality rubric comparison](old/experiment-2.md)
- [Supplementary labeled examples](old/labeled_examples/README.md)
