# Task-quality rubrics

We use **five rubrics in six rubric/prompt configurations** to judge whether an
RL task is clean or defective.

| Rubric | Prompt/output strategy | Prompt template |
| --- | --- | --- |
| [Terminal-Bench](rubric_terminal_bench/upstream/task-implementation.toml), with [our patches](rubric_terminal_bench/README.md) | Per-criterion pass/fail | [Terminal-Bench prompt](prompt_templates/upstream/terminal_bench.md) |
| [Harbor](rubric_harbor/upstream/default-rubric.toml), with [our patch](rubric_harbor/patches/remove-training-inapplicable-criteria.patch) | Per-criterion pass/fail | [Terminal-Bench prompt](prompt_templates/upstream/terminal_bench.md), with Harbor's rubric inserted |
| [Custom](rubric_custom/rubric.toml) | Per-criterion pass/fail | [Labeling prompt](prompt_templates/modified/labeling.txt) |
| [Custom classification](rubric_custom/classification.toml) | One category, including CLEAN | [Classification prompt](prompt_templates/modified/classification.txt) |
| [River](rubric_river/rubric.toml) | One category, including CLEAN | [Classification prompt](prompt_templates/modified/classification.txt) |
| [Custom severity](rubric_custom_severity/rubric.toml) | None/minor/major per criterion | [Severity prompt](prompt_templates/modified/custom_severity.txt) |


## How judges run

By default, each judge inspects a **fresh sandbox using the task's actual
runtime** (`--review-evidence image`). It contains the task's starting files and
dependencies, plus the rubric, instructions, tests and reference solution for
review. Ground-truth labels are kept outside.

**File-only review** (`--review-evidence files`) provides the files in a basic
container instead. It is not recommended for the main comparison because it
cannot establish how the task behaves in its actual runtime.

## Judges

GPT-5.4 uses your OpenAI API key, Claude uses your CLI, and DeepSeek uses its API.
Sandbox API/local judges use Terminus 2. Model calls run on the host; inspection
commands run inside the sandbox. Login-node file-only runs use read-only file
tools and do not execute task code.

Default limits:

- **Session timeout:** 30 minutes per review attempt. Repair rounds get their own
  timeout, so a task can take longer overall.
- **GLM-5.3 / GLM-5.3-Flash:** 131,072-token context window and up to 32,768 output
  tokens per turn, including thinking. Context includes the prompt, conversation
  and current reply; it is not a cumulative token budget for the whole session.
- Earlier GLM-Flash runs used **16,384 output tokens per turn**. The
  [model configuration](../../config/models.py) records that this cut off thinking,
  so the current limit was raised to 32,768. Other models use their own context limits.

Earlier experiments tested **Claude Opus 5.5, Claude Fable 5.1, GPT-6 Astra
(medium), GLM-5.3 and GLM-5.3-Flash**. Their examples and results are preserved in
[old/labeled_examples](old/labeled_examples).

The new comparison plans **GPT-5.4, Claude Sonnet 5.5, DeepSeek Flash,
GLM-5.3-Flash and Qwen3.8-27B**. GPT-5.4 API and Sonnet CLI file-only smoke
reviews have completed successfully.

## Planned evaluation

Use [download_gt.py](download_gt.py) to select and download **80 tasks**: 20 clean
and 20 defective each from SWE and Terminal-Bench, preferring high confidence,
then moderate. Exclude low confidence and Mobius; no holdout.
**Six configurations × five judges = 2,400 reviews**, before repairs.

Compare clean/defective accuracy and cost, with expandable task-level evidence
and criterion findings. Minor and major both count as defective; incomplete
reviews stay unresolved. Exact defect labels are diagnostic, not the primary score.

## Results

No results yet for the new 80-task comparison.

| Judge | Rubric / strategy | Correct / 80 | Accuracy | Cost | Task details |
| --- | --- | --- | --- | --- | --- |
