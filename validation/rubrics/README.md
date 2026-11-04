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
<summary><b>River details: accuracy, error overlap, and SWE analysis by judge</b></summary>

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

<details>
<summary><b>Errors by category: false alarms versus missed defects</b></summary>

Each bar pools judge–task reviews, with a different color for each judge. A task
can contribute up to three errors. The panels show all tasks, SWE, and TB.

![River errors by category](results/river-errors-by-category.png)

- **Top: clean → defect (FP).** Group by the River category the judge assigned.
  Across all tasks, VERIFIER-TOO-WEAK produces 16 false alarms, ANSWER-LEAK 9,
  and INSTR-VERIFIER-MISMATCH 7. These remain disagreements with the original
  clean labels, including supported criticisms outside the human audit’s scope.
- **Bottom: defect → CLEAN (FN).** Group by the original source defect labels.
  These are **not** River categories: a missed task has only a CLEAN verdict,
  so the judge does not tell us which River category it should have chosen.
  Instruction/test mismatch has 11 missed reviews out of 33 opportunities.
  Scoring-logic defects have the highest observed miss rate, 5/9, but that is
  only three tasks reviewed by three judges—not nine independent tasks.

Rates use the number of tasks in each reference group, multiplied by three
judges. Reference groups can overlap: 29 binary FN yield 30 group entries.
A correct defective verdict in a different category is not counted as FP/FN;
this is **binary error analysis by category**, not a validated multiclass
confusion matrix or proof of which River criterion is intrinsically hardest.
The original source terms `false_negative` and `false_positive` describe tests
rejecting valid solutions and accepting invalid ones, respectively; they are
not the judge-level FN and FP counts.

[Counts and denominators](results/river-category-counts.json) ·
[Vector figure](results/river-errors-by-category.svg) ·
[Plot generator](utils/plot_river_categories.py)

</details>

**SWE error analysis by judge.** “Wrong” below means disagreement with the
original human reference, not necessarily a bad judgment. **FP** = a clean-labeled
task flagged defective; **FN** = a defective-labeled task called clean. Labels and
scores remain unchanged. A suspected reference-label problem is not a confirmed
human mistake.

The 40 SWE tasks come from the original human audit, not an all-Verified sample:
20 are reference-clean (recorded here as SWE-bench Verified) and 20 are
reference-defective. The released records contain three individual annotations
per task. Each task row below gives **defective votes out of three**, followed
by the three reviewers’ **confidence scores out of five**. These are self-rated
confidence scores, not calibrated probabilities. All 14 SWE tasks with a judge
disagreement have unanimous human binary labels; unanimity does not resolve
differences between the human audit and River’s criteria.

<details>
<summary><b>Sonnet 5.5 — 4 SWE disagreements (3 FP, 1 FN)</b></summary>

**Overview: two interpretive cases and two suspected human-label problems.**
We lean toward the judges on the latter two, but have not independently confirmed
that those human labels are wrong.

| Task | Error | Human annotation | Brief explanation and assessment |
| --- | --- | --- | --- |
| `django__django-16899` | FP | Clean; 0/3 defect votes; confidence 4, 5, 5 | **Interpretive wording requirement.** The request says to include the invalid field's name, using existing messages as examples. Sonnet thinks any clear wording should count; humans think the examples specify the sentence pattern. For example, “Invalid field: x” and “Field x is invalid” convey the same information, but only one pattern may pass. These are illustrative phrases, not the actual test strings. |
| `django__django-13300` | FN | Defective; 3/3 defect votes; confidence 5, 4, 4 | **Interpretive scope.** The request asks a database query to return a constant instead of full columns, “like .exists()”. Tests also require it to stop sorting rows. Humans treat that as an extra requirement; Sonnet treats copying the other method's optimizations as implied. Sorting is unnecessary for checking whether a match exists, but that does not automatically make removing it part of the request. |
| `sympy__sympy-21930` | FP | Clean; 0/3 defect votes; confidence 4, 4, 5 | **Possible human-label problem; we lean toward the judges.** The issue reports broken formatting involving a dagger and a power. All three judges say the tests additionally demand braces around a tensor expression with neither that dagger nor the reported double superscript. A fix to the reported problem could therefore fail for leaving a separate expression unchanged. Independent adjudication is still pending. |
| `django__django-14034` | FP | Clean; 0/3 defect votes; confidence 5, 5, 4 | **Possible human-label problem; we lean toward the judges.** The issue concerns whether the server accepts submitted form data. All three judges say the decisive test instead checks whether HTML marks an input as required. Browser markup and server validation are related but different behaviors. The saved experiments support the criticism, but the judges differ on the parent field's `required` setting; this case has not been independently rerun here. |

GLM-Flash and Qwen share the last three disagreements. Both classify
Django-16899 correctly against the reference.

<details>
<summary>Original Sonnet explanations</summary>

These are the recorded judge explanations, not new verification results.

**`django__django-13300` — `CLEAN` (FN)**

> This is a standard SWE-bench task (django-13300). The verifier applies the upstream test patch and runs expressions.tests. It then grades the FAIL_TO_PASS and PASS_TO_PASS lists with the swebench parser. The new test checks that the Exists subquery SQL has no pk column, has LIMIT 1, and has no ORDER BY. That behavior is what the issue describes by asking Exists to behave like .exists(). It runs real queries, so a hardcoded answer would not pass. I did not run the reference solution or the tests.

**`django__django-16899` — `INSTR-AMBIGUOUS` (FP)**

> The instruction only says the readonly_fields error should 'include the field name', like the other checks. The verifier (the SWE-bench test patch in tests/test.sh) does exact string comparison against "The value of 'readonly_fields[1]' refers to 'nonexistent', which is not a callable, an attribute of 'SongAdmin', or an attribute of 'admin_checks.Song'." The instruction never gives this wording. The only example, for list_editable, uses a different tail ('which is not contained in list_display'). Other reasonable messages that do include the field name, such as "The value of 'readonly_fields[0]' ('x') is not a callable...", would fail. Passing depends on guessing the exact phrasing or knowing the upstream Django fix.

**`sympy__sympy-21930` — `INSTR-VERIFIER-MISMATCH` (FP)**

> The issue only describes Commutator(Bd(a)**2, B(a)) producing 'b^\dagger_{0}^{2}' and suggests '{b^\dagger_{0}}^{2}'. The verifier's test patch also changes unrelated expected outputs that the issue never mentions. Bd(i) alone must print '{b^\dagger_{i}}'. Fd(p) must print '{a^\dagger_{p}}'. NO(...) must print '\left\{{a^\dagger_{a}} {a^\dagger_{i}}\right\}'. AntiSymmetricTensor must print '{t^{ab}_{ij}}', which has no dagger at all. A solver who fixes only the doubled-superscript case, for example by bracing only when a power is applied, would fail test_create, test_Tensors and others. The unconditional bracing in every _latex method, including the tensor class, is a behaviour the instruction doesn't specify.

**`django__django-14034` — `INSTR-VERIFIER-MISMATCH` (FP)**

> The issue only asks that is_valid() be False when a required sub-field is empty and the MultiValueField has required=False. The SWE-bench test (test_render_required_attributes) also uses a required=True field and asserts exact HTML. It expects the 'required' attribute on the f_0 input (whose sub-field is required) and none on f_1 (whose sub-field is optional). It also checks that a partially filled form is valid. The instruction says nothing about per-subwidget required rendering. A fix that follows the issue and corrects only validation could fail the test. This conclusion comes from reading the task files. I did not run any solution against the verifier.


</details>

</details>

<details>
<summary><b>Qwen3.8-27B — 7 SWE disagreements (3 FP, 4 FN)</b></summary>

**Overview: two interpretive cases, two suspected human-label problems, one
confirmed wording mismatch Qwen missed, one answer-leak criterion difference,
and one case where our new experiment favors Qwen over the summation-based
human criticism.** Three cases overlap with Sonnet; four are additional.

| Task | Error | Human annotation | Brief explanation and assessment |
| --- | --- | --- | --- |
| `django__django-13300` | FN | Defective; 3/3 defect votes; confidence 5, 4, 4 | **Interpretive; shared with Sonnet.** Does “like .exists()” require only selecting a constant, or removing sorting too? Qwen reads it broadly; humans read it narrowly. |
| `sympy__sympy-21930` | FP | Clean; 0/3 defect votes; confidence 4, 4, 5 | **Possible human-label problem; shared with Sonnet.** The tests reportedly require a tensor-formatting change beyond the reported dagger bug. We lean toward the judges, pending independent adjudication. |
| `django__django-14034` | FP | Clean; 0/3 defect votes; confidence 5, 5, 4 | **Possible human-label problem; shared with Sonnet.** The issue describes server validation; the decisive test reportedly checks HTML required attributes. We lean toward the judges, pending independent adjudication. |
| `django__django-11129` | FN | Defective; 3/3 defect votes; confidence 5, 5, 4 | **Interpretive wording requirement.** The request asks for a helpful error, but the tests require a particular exception and message. Humans say other sensible errors should count. Qwen considers the existing Django error convention sufficient guidance. Sonnet and GLM-Flash flag the problem. |
| `sympy__sympy-12906` | FN | Defective; 3/3 defect votes; confidence 5, 4, 5 | **New experiment favors Qwen on the summation objection.** The task asks that putting an expression into standard form twice give the same result as doing it once. Some human notes claim passing also requires a summation fix. We changed only canonicalization: all locally selected scored tests pass with summation code untouched. That particular criticism is unsupported; whether the additional canonical-form rules are implied remains interpretive. GLM-Flash also calls it clean. |
| `django__django-8119` | FN | Defective; 3/3 defect votes; confidence 5, 5, 5 | **Confirmed wording mismatch missed by Qwen.** The issue asks for expressions in `.filter()`, without specifying an error sentence. With the reference feature implemented, replacing only the error text with an equivalent explanation causes a scored test to fail. This supports the human defect label. Sonnet and GLM-Flash catch it. |
| `django__django-16139` | FP | Clean; 0/3 defect votes; confidence 5, 5, 5 | **Criterion difference, with a confirmed supplied fix.** The instruction gives the exact password-link replacement used by the reference solution. Qwen and GLM-Flash correctly identify that disclosure under River's ANSWER-LEAK criterion. Humans judged the issue clear and fairly tested. A clear task can still give away its answer; this is not evidence that the humans were wrong about clarity. |

**What we independently checked:**

- **SymPy-12906:** the unfixed source fails `test_canonical`. Applying only the
  reference change to `sympy/core/relational.py` passes 62 test items covering all
  61 scored names (one name occurs in two modules). `summations.py` remains
  byte-identical to the original. The disputed convergence assertion is commented
  out by the test patch, and `test_is_convergent` is not in the scored lists.
  **Correction to the earlier interpretation: an unrelated summation fix is not
  required by these scored tests.**
- **Django-8119:** with the reference patch, the two test modules run 217 tests:
  215 pass and 2 skip. Replacing “When() supports a Q object, a boolean expression,
  or lookups as a condition.” with “When() requires a Q object, a boolean
  expression, or keyword lookups.” leaves the behavior intact but causes exactly
  one failure, `test_invalid_when_constructor_args`, which is in FAIL_TO_PASS.
  The assertion requires the original sentence as a substring; it is not an
  exact whole-string comparison.
- **Django-16139:** direct comparison of the instruction and reference patch
  confirms the supplied replacement `f"../../{self.instance.pk}/password/"`.
  This was source inspection, not a new runtime test.

The first two experiments used isolated copies of the bundled source and tests
with Python 3.9 and local dependencies. They did **not** rerun the original
Apptainer verifier. [Source excerpts, patches, commands, and test results](results/river-swe-verification.json)
record their scope and results.

</details>

<details>
<summary><b>GLM-5.3-Flash — 11 SWE disagreements (9 FP, 2 FN)</b></summary>

**Overview: five answer-disclosure findings, three instruction/test mismatch
findings, one weak-test finding, and two accepted tasks labeled defective.**
**Overlap:** five of the 11 are shared with Qwen; three of those are also shared
with Sonnet. The six additional cases are Django-13807, Django-15467,
Django-13109, Django-14007, SymPy-14711, and Matplotlib-23299.

**Independent review of those six:** three supported answer-disclosure findings
(with two factual corrections), one interpretive case where we lean toward
overflagging, one reproduced weak-test concern, and one reproduced scope
difference requiring further adjudication. None is established here as a
confirmed wrong human annotation. Source comparisons and focused experiments
are recorded in the [verification evidence](results/river-swe-verification.json).

| Task | Error | Human annotation | Brief explanation and assessment |
| --- | --- | --- | --- |
| `django__django-13300` | FN | Defective; 3/3 defect votes; confidence 5, 4, 4 | **Interpretive; shared with both judges.** GLM-Flash considers removal of SQL sorting implied by “like .exists()”; humans do not. |
| `sympy__sympy-12906` | FN | Defective; 3/3 defect votes; confidence 5, 4, 5 | **Summation objection not supported; shared with Qwen.** Our canonicalization-only experiment passes the scored tests without modifying summation code. Other canonical-form scope objections remain interpretive. |
| `django__django-16139` | FP | Clean; 0/3 defect votes; confidence 5, 5, 5 | **Confirmed supplied fix / criterion difference; shared with Qwen.** The instruction gives the reference password-link replacement. River flags answer disclosure; humans judged the task clear. |
| `django__django-13807` | FP | Clean; 0/3 defect votes; confidence 5, 5, 5 | **Subjective; we lean toward overflagging.** The issue diagnoses missing SQL quotes, but the reference fix uses the quoting helper at additional table/column sites. This is substantial guidance, not the complete implementation. Treating every detailed diagnosis as an answer leak is too broad. |
| `django__django-15467` | FP | Clean; 0/3 defect votes; confidence 5, 5, 5 | **Answer-disclosure criterion difference, with a correction.** The instruction supplies the core fix. Its `get(...) or default` is not identical to the reference `get(..., default)` for falsey values, so GLM-Flash overstates the exact match. The intended custom-label repair is still largely given away. |
| `django__django-13109` | FP | Clean; 0/3 defect votes; confidence 5, 5, 5 | **Supported answer-disclosure finding / criterion difference.** The instruction prescribes `_base_manager` instead of `_default_manager`; the reference patch makes that single change. This fits River even though a human can reasonably label the task clear and fairly tested. |
| `sympy__sympy-14711` | FP | Clean; 0/3 defect votes; confidence 5, 5, 5 | **Supported answer disclosure, with a correction.** The instruction traceback reveals `if other == 0: return self`, matching the reference fix. Contrary to GLM-Flash’s explanation, that comment is absent from the bundled baseline source. The disclosure is in the instruction itself. |
| `sympy__sympy-21930` | FP | Clean; 0/3 defect votes; confidence 4, 4, 5 | **Possible human-label problem; shared with both judges.** GLM-Flash objects to tensor-formatting requirements beyond the reported dagger bug. We lean toward the judges, but have not independently reproduced this case. |
| `django__django-14034` | FP | Clean; 0/3 defect votes; confidence 5, 5, 4 | **Possible human-label problem; shared with both judges.** GLM-Flash reports that the test distinguishes HTML rendering while the instruction describes validation. We lean toward the judges, pending independent adjudication. |
| `matplotlib__matplotlib-23299` | FP | Clean; 0/3 defect votes; confidence 5, 4, 5 | **Reproduced scope difference; possible label problem.** A targeted `get_backend()` change preserves the figure in the issue reproduction but fails the scored test requiring a backend setting to survive `rc_context`. The reference fix passes both. This supports the concern, but the workaround has not been validated across all backends/regressions, so a wrong human label is not established. |
| `django__django-14007` | FP | Clean; 0/3 defect votes; confidence 5, 5, 5 | **Reproduced weak-test concern / criterion difference.** A partial conversion fix passes all 13 scored SQLite tests while leaving the RETURNING branches unchanged; the bulk-insert test skips. River’s criticism is supported for this backend. Human “clean” annotations did not establish that incomplete fixes would be rejected. |

</details>

<details>
<summary><b>Individual human ratings for the 14 SWE disagreement tasks</b></summary>

Reviewer 1–3 means the order in that task’s released annotations, not the same
three people across tasks. **U** = underspecification (0–3); **T** = tests rejecting
reasonable solutions (0–3); **O** = other major issues (0/1); **C** = confidence
(1–5). Larger U/T means a more serious concern. A reviewer’s defective flag is
U ≥ 2, T ≥ 2, or O ≥ 1. The reference binary label is the majority of those flags.

| Task | Human label / defect votes | Reviewer 1 | Reviewer 2 | Reviewer 3 |
| --- | --- | --- | --- | --- |
| `django__django-11129` | Defective; 3/3 | U2 T3 O0; C5 | U1 T2 O0; C5 | U0 T2 O0; C4 |
| `django__django-13109` | Clean; 0/3 | U0 T0 O0; C5 | U0 T0 O0; C5 | U0 T0 O0; C5 |
| `django__django-13300` | Defective; 3/3 | U0 T3 O0; C5 | U0 T2 O0; C4 | U0 T2 O0; C4 |
| `django__django-13807` | Clean; 0/3 | U0 T0 O0; C5 | U0 T0 O0; C5 | U0 T0 O0; C5 |
| `django__django-14007` | Clean; 0/3 | U0 T0 O0; C5 | U0 T0 O0; C5 | U0 T0 O0; C5 |
| `django__django-14034` | Clean; 0/3 | U0 T0 O0; C5 | U0 T0 O0; C5 | U0 T0 O0; C4 |
| `django__django-15467` | Clean; 0/3 | U0 T0 O0; C5 | U0 T0 O0; C5 | U0 T0 O0; C5 |
| `django__django-16139` | Clean; 0/3 | U0 T0 O0; C5 | U0 T0 O0; C5 | U0 T0 O0; C5 |
| `django__django-16899` | Clean; 0/3 | U0 T0 O0; C4 | U0 T0 O0; C5 | U0 T0 O0; C5 |
| `django__django-8119` | Defective; 3/3 | U1 T2 O0; C5 | U3 T3 O1; C5 | U2 T2 O0; C5 |
| `matplotlib__matplotlib-23299` | Clean; 0/3 | U0 T0 O0; C5 | U0 T0 O0; C4 | U0 T0 O0; C5 |
| `sympy__sympy-12906` | Defective; 3/3 | U1 T3 O0; C5 | U1 T2 O0; C4 | U1 T3 O0; C5 |
| `sympy__sympy-14711` | Clean; 0/3 | U0 T0 O0; C5 | U0 T0 O0; C5 | U0 T0 O0; C5 |
| `sympy__sympy-21930` | Clean; 0/3 | U0 T0 O0; C4 | U0 T0 O0; C4 | U0 T0 O0; C5 |

Full human explanations are retained in [the task evidence](results/river-task-results.json).

</details>

Evidence: [all 80 tasks with the three verdicts and explanations](results/river-task-results.json)
(including human annotations for all 14 SWE tasks with a disagreement), and
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
