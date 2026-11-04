# Task-quality rubrics

We compare **five rubrics in six rubric/prompt configurations** on 80
human-annotated tasks. Three judges review every task in every configuration:
**Claude Sonnet 5.5, GLM-5.3-Flash and Qwen3.8-27B**, so 6 × 80 = 480 reviews per
judge and 1,440 in total.

## Tasks

The 80 tasks are in
[FWeindel/task-defect-ground-truth](https://huggingface.co/datasets/FWeindel/task-defect-ground-truth)
([judge-calibration-80/tasks.parquet](https://huggingface.co/datasets/FWeindel/task-defect-ground-truth/blob/main/judge-calibration-80/tasks.parquet)):

| Benchmark | Label | Tasks | Source | Confidence |
| --- | --- | ---: | --- | --- |
| SWE | Clean | 20 | [OpenAI SWE-bench audit](https://openai.com/index/introducing-swe-bench-verified/) ([annotations](https://cdn.openai.com/introducing-swe-bench-verified/swe-bench-annotation-results.zip)) | High |
| SWE | Defective | 20 | [OpenAI SWE-bench audit](https://openai.com/index/introducing-swe-bench-verified/) | High |
| TB | Clean | 20 | [Z.ai Terminal-Bench 2 Verified](https://huggingface.co/datasets/zai-org/terminal-bench-2-verified) publisher review | Moderate |
| TB | Defective | 10 | [Terminal-Bench 2 to 2.1 update](https://github.com/harbor-framework/terminal-bench-2/pull/53) | High |
| TB | Defective | 5 | [Z.ai targeted review (August 2026)](https://huggingface.co/datasets/zai-org/terminal-bench-2-verified), [pre-fix versions](https://huggingface.co/datasets/zai-org/terminal-bench-2-verified/tree/8b000db63b1217760921564ddf5c5dd724f10b57) | High |
| TB | Defective | 5 | [Epoch AI Terminal-Bench 4 review](https://epoch.ai/benchmarks/terminal-bench-4/review) | High |

[convert_swe_to_harbor.py](convert_swe_to_harbor.py) turns the SWE tasks
into Harbor tasks that run on Apptainer. The Terminal-Bench tasks are already Harbor tasks; we build their images for
Apptainer.

## Rubric overview

| Rubric | Prompt/output strategy | Prompt template |
| --- | --- | --- |
| [Terminal-Bench](rubric_terminal_bench/upstream/task-implementation.toml), with [our patches](rubric_terminal_bench/README.md) | Per-criterion pass/fail | [Terminal-Bench prompt](prompt_templates/modified/terminal_bench.txt) |
| [Harbor](rubric_harbor/upstream/default-rubric.toml), with [our patch](rubric_harbor/patches/remove-training-inapplicable-criteria.patch) | Per-criterion pass/fail | [Terminal-Bench prompt](prompt_templates/modified/terminal_bench.txt), with Harbor's rubric inserted |
| [Custom](rubric_custom/rubric.toml) | Per-criterion pass/fail | [Labeling prompt](prompt_templates/modified/labeling.txt) |
| [Custom classification](rubric_custom/classification.toml) | One category, including CLEAN | [Classification prompt](prompt_templates/modified/classification.txt) |
| [River](rubric_river/rubric.toml) | One category, including CLEAN | [Classification prompt](prompt_templates/modified/classification.txt) |
| [Custom severity](rubric_custom_severity/rubric.toml) | None/minor/major per criterion | [Severity prompt](prompt_templates/modified/custom_severity.txt) |

## Runtime configuration

By default, each judge inspects a **fresh sandbox using the task's actual
runtime** (`--review-evidence image`). It contains the task's starting files and
dependencies, plus the rubric, instructions, tests and reference solution for
review. 

**File-only review** (`--review-evidence files`) does not use the task's image.
The judge gets a copy of the task files under `/app/task-under-review` in a plain
`ubuntu:24.04` container with `curl`, `git`, `python3`, `nodejs`/`npm` and the
Claude Code CLI (Terminal-Bench's upstream review image). The task's
dependencies are not installed, so the judge can read the files but cannot run
the task's tests or demonstrate an exploit.


## Judge configuration

Choose the judge independently of the solver with `--judge-mode` and
`--judge-model`. These options apply to stages 5 and 8:

```bash
# Start a local server in the cluster allocation, using Terminus 2.
--judge-mode serve --judge-model Qwen/Qwen3-Coder-30B-A3B-Instruct

# Connect to an already running server, local or remote, using Terminus 2.
--judge-mode api --judge-model openai/my-model --judge-api-base http://HOST:8000/v1

# Provider API, using Terminus 2 and ANTHROPIC_API_KEY.
--judge-mode api --judge-model anthropic/MODEL_ID

# Optional custom credential variable (the name, never the secret).
--judge-mode api --judge-model openai/MODEL_ID --judge-api-key-env MY_JUDGE_KEY

# Supported CLI, using that CLI's own agent and authentication.
--judge-mode cli --judge-cli claude-code --judge-model MODEL_ID
--judge-mode cli --judge-cli codex --judge-model MODEL_ID
```

Append these arguments to `python validation/run.py /path/to/tasks --stages 5
--out /path/to/results`, with the usual runtime and cluster options. Existing
image-cache prerequisites still apply. `local` is accepted as an alias for
`serve`. For an existing endpoint, the model ID must match the name exposed by
that endpoint (with the API adapter prefix, such as `openai/`).

Serve mode accepts registered model keys and Hugging Face IDs. Known IDs reuse
registered hardware and sampling settings. Unregistered IDs use a generic
one-GPU, auto-dtype configuration; the model must be supported by vLLM and its
chat template. Prepare weights before submission, using `--serve-weights` for
an existing directory; the default location is `$OT_MODELS/ORG/MODEL` for an
unregistered ID. Existing `--serve-context`, `OT_SERVE_GPUS` and serving options
control resource configuration. This does not automatically download weights
or determine family-specific reasoning parsers.

API credentials use exported environment variables first, then `.env` in the
working directory, then `~/.env`. Set `OT_SECRETS_FILE` to select a different
file instead. `--judge-api-key-env` selects a custom variable instead of the
provider's standard variable. Missing custom credentials fail before review.
Only the variable name is stored in arguments and contracts. The custom-key
mapping supports OpenAI, hosted_vllm, Anthropic, Gemini, Together AI, Groq,
DeepSeek, Mistral, Cohere and OpenRouter; other adapters use their standard
credential environment variables. CLI authentication remains managed by the
CLI; Claude Code requires subscription OAuth in this pipeline.

`--model`, `--api-base` and `--agent` remain solver settings. New judge flags
cannot be mixed with conflicting legacy reviewer flags. Existing
`--review-local`, `--review-agent`, `--review-model`, `--analysis-agent` and
`--analysis-model` commands remain supported. With the legacy interface,
`--review-local` still reads `--model`. Rubric, evidence, timeout and retry
options are unchanged.


All judges run inside the reviewed task's own container, with the same prompt,
time limit and retries.

**Claude Sonnet 5.5** runs as Claude Code (Harbor's `claude-code` agent), signed
in with a Claude subscription:

- **Session timeout:** 30 minutes per review attempt.
- **Settings:** reasoning effort medium, no cost limit, all Claude Code tools
  except web search and web fetch, so it cannot look up published reviews of the
  tasks.
- **Usage limit:** if the subscription limit is reached, the review waits until
  the limit resets and then runs again.

**GLM-5.3-Flash and Qwen3.8-27B** are served locally with vLLM and run in
Terminus 2:

- **Session timeout:** 30 minutes per review attempt.
- **Context:** 131,072 tokens, with interleaved thinking.
- **Output per turn, including thinking:** 32,768 tokens for GLM-5.3-Flash,
  32,767 for Qwen3.8-27B.
- **Sampling:** each model's default from its model card.
  - GLM-5.3-Flash: temperature 1.0, top_p 0.95.
  - Qwen3.8-27B, thinking on: temperature 1.0, top_p 0.95, top_k 20, min_p 0,
    presence_penalty 0, repetition_penalty 1.0.

<details>
<summary><b>Retries and repairs</b></summary>

All judges:

- **Time limit:** after 30 minutes the judge gets 10 more minutes. Its next
  prompt (Claude Code: its next tool result) ends with:
  "The review time limit has been reached. Do not investigate further. Write
  /app/verdicts.json now from the evidence you have. You have 10 minutes."
  Harbor stops the agent at 42 minutes (30 + 10 + 2 slack).
- **Valid verdict at the hard limit:** if the agent is stopped but
  `verdicts.json` is already valid, the verdict is kept.
- **Invalid verdict at completion:** when the judge finishes, `verdicts.json`
  is checked. If it is invalid, the judge gets the exact error and is asked to
  correct the file, once, in the same session:
  "/app/verdicts.json is not valid, so the review cannot be accepted yet:
  &lt;error&gt;. Correct the file, then mark the task complete again."
- **One retry** (`--review-retries 1`): a fresh review with the exact same
  prompt and its own 30-minute timeout. Criteria missing from an otherwise valid
  verdict are retried with `/app/rubric.toml` reduced to them; a missing or
  unusable verdict, or a failed session, is retried with the full rubric.

Local judges only (Claude Code has no persistent terminal):

- **Lost terminal** (for example after `exit`, or `set -e` and a failing
  command): a new terminal is started in the same container, at most 3 times in
  a row, and the judge gets: "NOTE: The shell exited during your last command
  (for example after `exit`, or `set -e` followed by a failing command), which
  closed the terminal. A new terminal has been started in the same container.
  Files written so far are kept. The working directory, environment variables,
  shell options and any processes started from the old shell are gone. Commands
  that did not run before the shell closed were not repeated."
- **Unresponsive terminal:** if a command does not return in time, the
  container is checked again every 30 s until it answers or the hard limit is
  reached.

</details>
<br>

We skipped GLM-5.3. In a 3-task test on Helma, served across 2 nodes, it made only
14 model requests in 30 minutes and wrote no verdicts. A full review takes about
15-25 steps, so we estimated about 2 hours per review.

Earlier experiments tested **Claude Opus 5.5, Claude Fable 5.1, GPT-6 Astra
(medium), GLM-5.3 and GLM-5.3-Flash**. Their examples and results + pricing are in
[old/labeled_examples](old/labeled_examples).

## Results

Accuracy on all 80 tasks (40 clean, 40 defective), defective counted as positive.

| Rubric | Prompt/output strategy | Sonnet 5.5 | GLM-5.3-Flash | Qwen3.8-27B |
| --- | --- | ---: | ---: | ---: |
| Terminal-Bench | Per-criterion pass/fail | 54/80 (68%) | 42/80 (52%) | 46/80 (57%) |
| Harbor | Per-criterion pass/fail | 53/80 (66%) | 41/80 (51%) | 56/80 (70%) |
| Custom | Per-criterion pass/fail | 54/80 (68%) | 38/80 (48%) | 51/80 (64%) |
| Custom | Per-criterion, strict exploits | 54/80 (68%) | 41/80 (51%) | 51/80 (64%) |
| Custom | Classification | 56/80 (70%) | 44/80 (55%) | 54/80 (68%) |
| River | Classification | **59/80 (74%)** | **54/80 (68%)** | **59/80 (74%)** |
| Custom severity | Minor or worse = defective | 56/80 (70%) | 42/80 (52%) | 57/80 (71%) |
| Custom severity | Major only = defective | 56/80 (70%) | 42/80 (52%) | 56/80 (70%) |

By benchmark (40 tasks each: 20 clean, 20 defective). Sonnet and Qwen do much
better on SWE than on Terminal-Bench; on Terminal-Bench all three judges are
close to chance.

| Rubric | Prompt/output strategy | Sonnet SWE | Sonnet TB | GLM-Flash SWE | GLM-Flash TB | Qwen SWE | Qwen TB |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Terminal-Bench | Per-criterion pass/fail | 35/40 (88%) | 19/40 (48%) | 22/40 (55%) | 20/40 (50%) | 28/40 (70%) | 18/40 (45%) |
| Harbor | Per-criterion pass/fail | 33/40 (82%) | 20/40 (50%) | 23/40 (57%) | 18/40 (45%) | 34/40 (85%) | 22/40 (55%) |
| Custom | Per-criterion pass/fail | 35/40 (88%) | 19/40 (48%) | 20/40 (50%) | 18/40 (45%) | 30/40 (75%) | 21/40 (52%) |
| Custom | Per-criterion, strict exploits | 35/40 (88%) | 19/40 (48%) | 22/40 (55%) | 19/40 (48%) | 30/40 (75%) | 21/40 (52%) |
| Custom | Classification | 34/40 (85%) | 22/40 (55%) | 22/40 (55%) | 22/40 (55%) | 33/40 (82%) | 21/40 (52%) |
| River | Classification | 36/40 (90%) | 23/40 (57%) | 29/40 (72%) | 25/40 (62%) | 33/40 (82%) | 26/40 (65%) |
| Custom severity | Minor or worse = defective | 34/40 (85%) | 22/40 (55%) | 20/40 (50%) | 22/40 (55%) | 31/40 (78%) | 26/40 (65%) |
| Custom severity | Major only = defective | 37/40 (92%) | 19/40 (48%) | 20/40 (50%) | 22/40 (55%) | 32/40 (80%) | 24/40 (60%) |

These reviews ran before the current judge configuration: Sonnet reviewed from
the host through an `inspect` tool, the local judges' history did not keep
earlier reasoning, Qwen3.8-27B used the model card's non-thinking sampling, and
up to 2 retries were allowed. GLM-5.3-Flash custom-criteria has no verdict for
one task, counted as wrong.

<details>
<summary><b>River details: accuracy, error overlap, and Sonnet's four SWE disagreements</b></summary>

River has the highest observed overall accuracy for each judge in this comparison.
These are the same historical runs described above, with successful retries merged;
all 240 River judgments are resolved. Percentages below are rounded to the nearest
whole percent, with half percentages rounded up.

| Judge | Overall | SWE-bench | Terminal-Bench |
| --- | ---: | ---: | ---: |
| Sonnet 5.5 | 59/80 — 74% | 36/40 — 90% | 23/40 — 58% |
| GLM-5.3-Flash | 54/80 — 68% | 29/40 — 73% | 25/40 — 63% |
| Qwen3.8-27B | 59/80 — 74% | 33/40 — 83% | 26/40 — 65% |

**Defective is the positive class.** FP (false positive) means a reference-clean
task was flagged defective; FN (false negative) means a reference-defective task
was called clean. These measure disagreement with the reference labels, not an
independent adjudication of whether each judge's criticism is valid.

| Judge | Overall FP | Overall FN | SWE FP | SWE FN | TB FP | TB FN |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Sonnet 5.5 | 10 | 11 | 3 | 1 | 7 | 10 |
| GLM-5.3-Flash | 21 | 5 | 9 | 2 | 12 | 3 |
| Qwen3.8-27B | 8 | 13 | 3 | 4 | 5 | 9 |

GLM-Flash misses fewer defects overall but flags more reference-clean tasks.
Sonnet and Qwen have the same overall accuracy with different error distributions.

**Where do their errors overlap?** Each column below is one task, and each row
is one judge. Orange = FP, purple = FN, gray = correct. Vertically aligned
colored cells identify shared errors. Tasks are grouped by reference label, then
sorted by number of judges wrong and task name. All 40 tasks appear in each panel.

![River SWE-bench error overlap](results/river-errors-swe.png)

![River Terminal-Bench error overlap](results/river-errors-tb.png)

| Judges wrong on the same task | SWE-bench | Terminal-Bench |
| --- | ---: | ---: |
| Sonnet + GLM-Flash | 3 | 7 |
| Sonnet + Qwen | 3 | 11 |
| GLM-Flash + Qwen | 5 | 6 |
| All three | 3 | 4 |
| At least one | 14 | 26 |

Pairwise counts include tasks missed by all three; the rows are not additive.

**Sonnet's four SWE disagreements** comprise three FP and one FN, rather than
four missed defects. All three judges disagree with the reference on the same
three tasks; only `django__django-16899` is a Sonnet-only error.

| Task | Human reference | Sonnet 5.5 | GLM-5.3-Flash | Qwen3.8-27B |
| --- | --- | --- | --- | --- |
| `django__django-13300` | Defective | FN: CLEAN | FN: CLEAN | FN: CLEAN |
| `django__django-16899` | Clean | FP: INSTR-AMBIGUOUS | Correct: CLEAN | Correct: CLEAN |
| `sympy__sympy-21930` | Clean | FP: INSTR-VERIFIER-MISMATCH | FP: INSTR-VERIFIER-MISMATCH | FP: INSTR-VERIFIER-MISMATCH |
| `django__django-14034` | Clean | FP: INSTR-VERIFIER-MISMATCH | FP: INSTR-VERIFIER-MISMATCH | FP: INSTR-VERIFIER-MISMATCH |

- **Django-13300 — an extra SQL optimization requirement.** The issue asks
  `Exists()` to use `SELECT 1` like `.exists()`. The tests also require removal
  of `ORDER BY`. All three human reviewers flagged overly restrictive tests:
  removing ordering was not requested, so a reasonable fix could fail. All
  three judges called it clean. Sonnet explicitly treated the SQL requirements
  as implied by “like .exists()”. This is the one reference defect Sonnet missed.
- **Django-16899 — exact error-message wording.** The issue asks the
  `readonly_fields` error to include the field name, following analogous admin
  checks. Sonnet argued that exact-string tests require unspecified phrasing.
  The three human reviewers considered the expected wording sufficiently clear;
  GLM-Flash and Qwen likewise treated the example and existing conventions as
  enough guidance. No major defect is recorded in the reference.
- **SymPy-21930 — LaTeX tests broader than the reported dagger bug.** The issue
  describes double superscripts involving a dagger. All three judges objected
  that the tests also require braces around standalone operators and an
  `AntiSymmetricTensor` without a dagger. The human reviewers instead regarded
  those tests as appropriate coverage of the requested rendering fix and
  unanimously labeled the task clean. This is a shared disagreement about the
  required scope of the fix, not a reference-labeled defect.
- **Django-14034 — validation versus HTML rendering.** The issue discusses
  `MultiValueField.is_valid()` when a required subfield is empty. All three
  judges argued that the decisive test demands per-subwidget HTML `required`
  attributes, which the issue does not request. The human reviewers considered
  the validation and rendering assertions aligned with the issue and
  unanimously labeled it clean. Sonnet's conclusion was based on reading;
  GLM-Flash and Qwen reported experiments supporting their mismatch criticism.

**How subjective are these disagreements?** The first two depend strongly on
what a developer is expected to infer from an example or comparison. That does
not make the labels arbitrary: the disagreement is about how much the written
request commits the developer to doing. The following is an interpretation of
the recorded evidence, not a new independent verification or a label correction.

**Django-16899: must the message convey the right information, or use the exact
same sentence pattern?** Imagine being asked to make an error message name the
invalid field. “The field 'x' is invalid” and “Invalid field: 'x'” both communicate
that information. These are illustrative examples, not the actual test strings.
A test that accepts only one wording can reject a change that meets the plain
request. That is Sonnet's objection. However, this issue also points to existing
messages as examples of the desired consistency. The human reviewers, GLM-Flash,
and Qwen read that as “follow the existing wording pattern”, rather than “use
any wording that names the field”. Both readings have a rationale. We therefore
regard this as a relatively subjective question of how precisely the example
specifies the required wording.

**Django-13300: does “do it like this other method” mean copy one behavior or all
its optimizations?** The task concerns a database query that asks whether any
matching row exists. It requests `SELECT 1`: return a simple constant instead
of fetching the row's columns. The tests additionally demand removal of
`ORDER BY`, which sorts matching rows. Sorting does not change whether a match
exists, so removing it is a sensible optimization, but it is a separate change
from replacing the selected columns. The human reviewers took the request
narrowly: a developer could implement `SELECT 1`, leave sorting in place, and
still have done what was asked. The judges read “like .exists()” more broadly:
use the other method's optimizations, including removal of sorting. The test
requirement is concrete; whether the comparison implies it is interpretive.

**SymPy-21930: the judges raise a stronger concern about an extra requirement.**
The reported problem is a mathematical expression whose dagger symbol and power
are formatted incorrectly. The judges say the tests also require a formatting
change to a tensor expression that has neither that dagger nor the reported
double-superscript problem. In plain language: a developer might repair the
reported display problem and still fail because an apparently separate
expression was not changed too. The human reviewers regarded the broader tests
as appropriate coverage of the rendering fix. Based on the recorded evidence,
we lean toward the judges' mismatch interpretation, especially the tensor
example. Before changing the label, an independent review should establish
whether that extra change is required by a general rendering rule implied by
the issue, or whether a complete, valid fix can leave it unchanged and fail.

**Django-14034: checking submitted data and marking an input as required are
different behaviors.** Server-side validation decides whether submitted form
data is acceptable. An HTML `required` attribute tells the browser that an input
must be filled in. They are related, but changing the HTML attribute does not
itself change the server's validation logic. The issue describes the validation
result; the judges say the decisive failing assertion concerns the HTML
attribute instead. GLM-Flash and Qwen report that the test's validation assertions
already pass before the fix. If independently confirmed against the exact task,
that would be strong evidence that the test rewards a different repair from the
one the issue describes. We lean toward the mismatch concern here too, but the
judge accounts differ about the parent field's `required` setting. That detail
must be resolved before declaring the human label wrong.

**Our assessment:** Django-16899 and Django-13300 are interpretive boundary
cases. SymPy-21930 and Django-14034 are plausible reference-label problems with
more concrete mismatch evidence. We would not dismiss the latter two merely
because they count as FP, nor mark them as confirmed human-label errors solely
because three judges agree. Their original labels and all reported scores stay
unchanged pending independent adjudication.

<details>
<summary><b>Read Sonnet’s recorded reasons for these four judgments</b></summary>

These are the original judge explanations, reproduced verbatim. The first task
was accepted by Sonnet despite its defective reference label; the other three
were rejected by Sonnet despite their clean reference labels. Statements below
describe the judge’s reasoning and are not new verification results.

**`django__django-13300` — Accepted: `CLEAN` (FN)**

> This is a standard SWE-bench task (django-13300). The verifier applies the upstream test patch and runs expressions.tests. It then grades the FAIL_TO_PASS and PASS_TO_PASS lists with the swebench parser. The new test checks that the Exists subquery SQL has no pk column, has LIMIT 1, and has no ORDER BY. That behavior is what the issue describes by asking Exists to behave like .exists(). It runs real queries, so a hardcoded answer would not pass. I did not run the reference solution or the tests.

**`django__django-16899` — Rejected: `INSTR-AMBIGUOUS` (FP)**

> The instruction only says the readonly_fields error should 'include the field name', like the other checks. The verifier (the SWE-bench test patch in tests/test.sh) does exact string comparison against "The value of 'readonly_fields[1]' refers to 'nonexistent', which is not a callable, an attribute of 'SongAdmin', or an attribute of 'admin_checks.Song'." The instruction never gives this wording. The only example, for list_editable, uses a different tail ('which is not contained in list_display'). Other reasonable messages that do include the field name, such as "The value of 'readonly_fields[0]' ('x') is not a callable...", would fail. Passing depends on guessing the exact phrasing or knowing the upstream Django fix.

**`sympy__sympy-21930` — Rejected: `INSTR-VERIFIER-MISMATCH` (FP)**

> The issue only describes Commutator(Bd(a)**2, B(a)) producing 'b^\dagger_{0}^{2}' and suggests '{b^\dagger_{0}}^{2}'. The verifier's test patch also changes unrelated expected outputs that the issue never mentions. Bd(i) alone must print '{b^\dagger_{i}}'. Fd(p) must print '{a^\dagger_{p}}'. NO(...) must print '\left\{{a^\dagger_{a}} {a^\dagger_{i}}\right\}'. AntiSymmetricTensor must print '{t^{ab}_{ij}}', which has no dagger at all. A solver who fixes only the doubled-superscript case, for example by bracing only when a power is applied, would fail test_create, test_Tensors and others. The unconditional bracing in every _latex method, including the tensor class, is a behaviour the instruction doesn't specify.

**`django__django-14034` — Rejected: `INSTR-VERIFIER-MISMATCH` (FP)**

> The issue only asks that is_valid() be False when a required sub-field is empty and the MultiValueField has required=False. The SWE-bench test (test_render_required_attributes) also uses a required=True field and asserts exact HTML. It expects the 'required' attribute on the f_0 input (whose sub-field is required) and none on f_1 (whose sub-field is optional). It also checks that a partially filled form is valid. The instruction says nothing about per-subwidget required rendering. A fix that follows the issue and corrects only validation could fail the test. This conclusion comes from reading the task files. I did not run any solution against the verifier.

</details>

The two shared mismatch findings are useful candidates for manual adjudication;
agreement among judges alone does not establish that the human label is wrong.
The original labels remain unchanged here. The human annotation field
`false_negative` describes tests rejecting valid solutions; it is distinct from
the judge-level FN count above.

Evidence: [all 80 tasks with the three verdicts and explanations](results/river-task-results.json)
(including the human annotations for these four cases), and
[plot generator](utils/plot_river_errors.py). Counts were recomputed from the
archived stage-5 reports selected by the saved merge manifest and checked against
the original scoring summary. Vector figures: [SWE](results/river-errors-swe.svg)
and [TB](results/river-errors-tb.svg).

</details>

Additional findings:

- **Most SWE tasks can be passed without fixing the bug.** SWE-bench runs the
  repository's own tests from a directory the agent can edit, resetting only the
  named test files, so an agent can change other files the tests load (a
  `conftest.py`, the test runner) to make them report success unfixed. This
  affects 39 of 40 SWE tasks, including all 20 clean ones (all in SWE-bench
  Verified); only `sphinx-doc__sphinx-9711` differs. It is an upstream property,
  not from our conversion. Note: the agent sees the verfier, which makes it easier to find the exploit and may not be found if we ask an agent to cheat without acess to the verifier/reference solution. 
