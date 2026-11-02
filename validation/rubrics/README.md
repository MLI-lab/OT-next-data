# Task-quality rubrics

We use **five rubrics in six rubric/prompt configurations** to judge whether an
RL task is clean or defective.

| Rubric | Prompt/output strategy | Prompt template |
| --- | --- | --- |
| [Terminal-Bench](rubric_terminal_bench/upstream/task-implementation.toml), with [our patches](rubric_terminal_bench/README.md) | Per-criterion pass/fail | [Terminal-Bench prompt](prompt_templates/modified/terminal_bench.txt) |
| [Harbor](rubric_harbor/upstream/default-rubric.toml), with [our patch](rubric_harbor/patches/remove-training-inapplicable-criteria.patch) | Per-criterion pass/fail | [Terminal-Bench prompt](prompt_templates/modified/terminal_bench.txt), with Harbor's rubric inserted |
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

Judges should run in the task's sandbox so they can demonstrate exploit behaviour.
In file-only runs, all 8 unresolved Terminal-Bench reviews were shortcuts the judge
found but could not run. Some judges also could not find the SWE tests: those
tasks were not in Harbor layout, so `test.patch` sorted after thousands of
repository files, and GPT-5.4's transcripts show it searching for the usual file
names instead of paging through the listing. Sandbox accuracy has not been measured yet.

## Judges

Sonnet uses the Claude CLI signed in with Claude. API keys are not used for this judge.
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

The new comparison plans **Claude Sonnet 5.5, GLM-5.3-Flash and Qwen3.8-27B**.

We skipped GLM-5.3. In a 3-task test on Helma, served across 2 nodes, it made only
14 model requests in 30 minutes and wrote no verdicts. A full review takes about
15-25 steps, so we estimated about 2 hours per review, or around 13 days of GPU
time for all 480 reviews.

GLM-5.3-Flash ended two sandbox reviews by running `exit`, which closes the judge's shell session before it writes a verdict, so all prompts now tell judges not to run it.

## Planned evaluation

Use [download_gt.py](download_gt.py) to select and download **80 tasks**: 20 clean
and 20 defective each from SWE and Terminal-Bench, preferring high confidence,
then moderate. Exclude low confidence and Mobius; no holdout. Use
[convert_swe_to_harbor.py](convert_swe_to_harbor.py) to turn the SWE tasks into
Harbor tasks; `--swe-bench-pro` converts the SWE-bench Pro tasks.
**Six configurations × five judges = 2,400 reviews**, before repairs.

Compare clean/defective accuracy and cost, with expandable task-level evidence
and criterion findings. Minor and major both count as defective. Exact defect labels are diagnostic, not the primary score.

## Results

Partial, file-only results. Sandbox reviews have not run. Sonnet 5.5 has all 480
file-only reviews. GPT-5.4 has 119: Terminal-Bench 80/80 and Harbor 39/80. Those
39 Harbor reviews are all defective tasks, so they give no accuracy yet.

Sonnet 5.5 on the 40 Terminal-Bench tasks, with defective as positive. The SWE
tasks are left out because of the findings below.

| Rubric | Strategy | TP | FP | TN | FN | Unresolved |
| --- | --- | --- | --- | --- | --- | --- |
| Terminal-Bench | Labeling | 18 | 20 | 0 | 2 | 0 |
| Harbor | Labeling | 14 | 12 | 8 | 6 | 0 |
| Custom | Labeling | 7 | 8 | 10 | 8 | 7 |
| Custom | Classification | 8 | 7 | 13 | 12 | 0 |
| River | Classification | 6 | 4 | 16 | 14 | 0 |
| Custom | Severity, minor or worse | 17 | 13 | 7 | 2 | 1 |
| Custom | Severity, major only | 5 | 2 | 18 | 14 | 1 |

Findings from the file-only runs:

- **File-only reviews cannot verify exploits.** All 8 unresolved Terminal-Bench
  reviews are anti-cheat: the judge found a plausible shortcut but could not run
  it. 6 of the 8 are on defective tasks. Judges need the sandbox for this.
- **File-only reviews often miss the SWE tests.** SWE inputs are not Harbor tasks.
  `test.patch` and `tests.json` sort after thousands of `repository/` files, and
  one listing page holds at most 300. All 96 `incomplete` SWE notes say the
  verifier or tests were missing. In the Terminal-Bench rubric, GPT-5.4 read them
  in only 20 of 40 SWE reviews.
- **Unresolved reviews count as not correct.** These runs allowed `incomplete`
  in the custom rubrics. It has since been removed, so judges now always decide:
  anti-cheat fails only on a demonstrated exploit.
- **Some clean SWE tasks can be hacked through git history.** Some SWE-bench
  evaluation images keep future git history. In 5 of the 20 clean SWE tasks, the
  reference fix is in a commit in the image, and applying it from git history
  passes the real verifier (reward 1) without solving the task. This comes from
  the upstream images, not our conversion. All 5 are outside SWE-bench Verified:
  the 15 clean tasks in Verified do not contain the fix. Labels are unchanged, and
  we report these tasks separately.
- **Most SWE tasks can be passed without fixing the bug.** SWE-bench runs the
  repository's own tests from a directory the agent can edit, resetting only the
  named test files, so an agent can change other files the tests load (a
  `conftest.py`, the test runner) to make them report success unfixed. This
  affects 39 of 40 SWE tasks, including all 20 clean ones (all in SWE-bench
  Verified); only `sphinx-doc__sphinx-9711` differs. It is an upstream property,
  not from our conversion. We confirmed reward 1 on three tasks; the rest is a
  structural count. We therefore report SWE anti-cheat results separately.
