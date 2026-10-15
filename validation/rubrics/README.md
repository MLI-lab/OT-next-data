# Rubrics

We use the patched Terminal-Bench rubric to review training tasks.

- Judge calibration: [TB human labels](labeled_examples/tb_human_labeled/README.md), [TB3/TB4](labeled_examples/tb3_tb4_assumed_positive/README.md), and [Charlie’s MiMo tasks](labeled_examples/charlie_hackable/README.md).
- Each [example group](labeled_examples/) has its own README and `results/` folder with verdicts, explanations, comparisons, and costs.
- [Terminal-Bench rubric](rubric_terminal_bench/): our default criteria, patches, and source metadata.
- [Harbor rubric](rubric_harbor/): alternative criteria, patches, and source metadata.

Each rubric folder contains `upstream/` (original criteria), `patches/` (our
changes), and `UPSTREAM.json` (pinned source versions and checksums).

## Judge calibration experiments

We ran judges on tasks with upstream human-backed labels for selected rubric
criteria. After excluding four tasks whose label evidence referred to different
tasks, the comparison contains 137 labels across 44 tasks: 81 positive and 56
negative. These are criterion-level labels, not whole-task pass/fail labels.
We count a `not_applicable` verdict as a mismatch. Later PR versions with
assumed-positive labels are excluded from this table.

| Judge | Positive labels correct | Negative labels correct | Total correct | Mean reported cost per task (USD) |
| --- | ---: | ---: | ---: | ---: |
| Opus 5.5 | 61/81 | 44/56 | 105/137 | $0.3137 |
| Fable 5.1 | 60/81 | 43/56 | 103/137 | $1.0944 |
| Sonnet 5 | 63/81 | 38/56 | 101/137 | $0.4158 |
| Astra medium | 52/81 | 45/56 | 97/137 | $0.5711 |

Cost averages use all 57 reviewed task versions per model, including excluded
and later PR versions; the accuracy columns use only the 44 eligible tasks.
Claude costs are CLI-reported token costs; Astra costs are API-equivalent
estimates from CLI token usage. Both exclude container compute and subscription
charges. See the [recorded costs](labeled_examples/tb_human_labeled/README.md)
for totals and estimation details.

No judge matched every label. Opus had the highest total agreement and lowest
reported per-task cost among these compared runs. These measurements do not
establish accuracy on other datasources. The human-label experiment provided
task files and shell tools, but did not inspect task images. See the
[human-label experiment](labeled_examples/tb_human_labeled/README.md) for
per-criterion outcomes, explanations, revision limitations, and observed costs.

The [classification stability table](labeled_examples/tb_human_labeled/README.md#matched-comparison)
reports unanimous and pairwise agreement across five fresh reviews per task,
with a matched model comparison, a per-criterion breakdown, and per-task CSVs.
The quota-limited pilot measures repeatability separately from label accuracy.

In the separate [Charlie hackability experiment](labeled_examples/charlie_hackable/README.md),
Opus, Fable, and Astra each detected 27/27 labeled negative examples with task
image access. That result covers only `anti_cheat_robustness`; it does not imply
perfect agreement on the broader rubric or prove the effect of image access in
a matched model comparison.

<!-- BEGIN LOCAL_JUDGES -->
## Local GLM judges

The two local judges use the same example groups and evidence settings as the earlier runs. They use an OpenAI-compatible tool loop instead of the provider CLIs, so harness differences remain. Stability uses the same 20 tasks, five fresh reviews each, with previous verdicts and labels hidden.

| Model | Calibration reviews | Stability reviews | Status |
| --- | ---: | ---: | --- |
| GLM-5.3-Flash | 1/104 | 0/100 | stopped_after_repeated_invalid_reviews |
| GLM-5.3 | 0/104 | 0/100 | failed |

[Per-category calibration results](labeled_examples/) and [stability results by category and criterion](labeled_examples/tb_human_labeled/results/local-stability-20261015/agreement.csv). [Accuracy variation across repeats](labeled_examples/tb_human_labeled/results/local-stability-20261015/accuracy-variance.csv) includes the mean and sample standard deviation. Only complete sets of five reviews enter these statistics. The matched comparison uses tasks completed by both GLM models, Opus and Sonnet, excluding mixed-model reviews.

| Local model | Human labels correct | TB3 all criteria pass | TB4 all criteria pass | Hackable tasks detected |
| --- | ---: | ---: | ---: | ---: |
| GLM-5.3-Flash | 1/1 | 0/0 | 0/0 | 0/0 |
| GLM-5.3 | 0/0 | 0/0 | 0/0 | 0/0 |

Denominators above count completed valid reviews or returned labels. Full targets are 137 human labels, 10 TB3 tasks, 10 TB4 tasks and 27 hackable tasks. TB3/TB4 acceptance is an assumed positive, not criterion-level ground truth.

| Matched stability model | Classifications | Unanimous | Pairwise agreement |
| --- | ---: | ---: | ---: |
| glm-5.3-flash | 0 | 0 | pending |
| glm-5.3 | 0 | 0 | pending |
| opus-5.5 | 0 | 0 | pending |
| sonnet-5 | 0 | 0 | pending |
<!-- END LOCAL_JUDGES -->

## Harbor and Terminal-Bench rubric comparison

This comparison uses Harbor's pinned default rubric at commit
`d5ac1be17f575852eaf4fffc4072fd18481c209b` and the pinned Terminal-Bench
`task-implementation.toml` rubric. The upstream rubrics are in
[`rubric_harbor/`](rubric_harbor/) and [`rubric_terminal_bench/upstream/`](rubric_terminal_bench/upstream/).
Our changes are in [`rubric_harbor/patches/`](rubric_harbor/patches/) and
[`rubric_terminal_bench/patches/`](rubric_terminal_bench/patches/), and are described below.

We adjust the rubrics to make them suitable for filtering training tasks.

### Terminal-Bench

| Criterion | Change | Explanation and reason |
| --- | --- | --- |
| `verifiable` | Changed | Added `tests/setup.sh` alongside `tests/test.sh` because some tasks separate verifier setup from execution. This lets Stage 3's `review-setup` measure preparation separately from verifier execution. |
| `solvable` | Conditionally skipped | Skip when the complete datasource has no reference solution for any task. |
| `difficult` | Dropped | It helps construct challenging benchmarks, but is too restrictive when selecting useful RL training tasks. |
| `novel` | Dropped | Resistance to training-corpus memorization is an evaluation concern, not a requirement for RL training tasks. |
| `instruction_concision` canary exemption | Dropped | Training examples do not use a canary marker, so there is no need for a canary-specific exemption. |
| `instruction_concision` timeout/anti-cheat sentence | Conditionally kept | Keep the rubric's exemption only when every task in the datasource includes the standard sentence; otherwise remove the exemption. |
| `instruction_concision` formatting paragraph | Dropped | Training instructions do not need Terminal-Bench-specific formatting conventions. |
| `separate_verifier_configured` | Dropped | Training datasets have many tasks, so image reuse and storage must be balanced with fast task setup instead of requiring a dedicated agent and verifier image per task. |
| `environment_hygiene` | Dropped | It assumes benchmark-specific agent and verifier images and dependency placement. Our training setup prioritizes image reuse, storage, and fast task setup instead of requiring a separate image pair for every task. |
| `difficulty_explanation_quality`, `solution_explanation_quality`, `verification_explanation_quality`, `category_and_tags`, `task_name`, `task_readme`, `expert_time_estimate` | Dropped | These criteria require benchmark-style explanations, metadata, naming, or time estimates. Training tasks do not need those benchmark-specific fields and sections. |
| `task_toml_schema` | Replaced with a Stage 1 static check | `check-task-toml-schema.py` checks Harbor's schema more cheaply and reliably than an LLM judge. Static failures still fail Stage 1. |
| `binary_reward` | Dropped | Binary rewards are not required for every RL training task; no replacement static check was added. |

### Harbor

| Criterion | Change | Explanation and reason |
| --- | --- | --- |
| `pinned_dependencies` | Dropped | Stage 1's `check-pip-pinning.sh` checks dependency pinning more cheaply and reliably than an LLM judge. |
| `test_deps_in_image` | Dropped | Training tasks may put reusable tools such as pytest in shared images to reduce image storage and setup time. |

## How the two rubrics compare: Harbor criteria and their closest Terminal-Bench matches

| Harbor criterion | Closest Terminal-Bench criterion | Relationship |
| --- | --- | --- |
| `behavior_in_task_description` | `test_instruction_alignment` | One half of the same two-way check: every behavior tested should be stated in the instruction. |
| `behavior_in_tests` | `test_instruction_alignment` | The other half: every behavior requested by the instruction should be tested. Terminal-Bench combines both directions in one criterion. |
| `informative_test_structure` | `test_instruction_alignment` (readability sub-requirement) | This is a partial match I initially missed: Terminal-Bench says tests should be hand-written, readable, and concise. Harbor is more specific about clear sections or comments; Terminal-Bench does not explicitly require those. `instruction_concision` concerns the task instruction, not the test script, and `outcome_verified` concerns grading outcomes rather than test organization. |
| `anti_cheating_measures` | `anti_cheat_robustness` | Closely aligned. Terminal-Bench gives more detailed adversarial examples and describes what the agent can access in separate-verifier mode. |
| `structured_data_schema` | `structured_data_schema` | Direct match: same criterion name and same core description, requiring an exact schema when structured output is expected. |
| `pinned_dependencies` | `deterministic_reproducible`; partly `verifiable` | Covered by the broader reproducibility topic in `deterministic_reproducible`. Harbor's criterion is removed because Stage 1 checks dependency pinning in code; the Terminal-Bench criterion itself also covers live services and run-to-run consistency. |
| `typos` | `typos` | Direct match: the names and descriptions are nearly the same, focusing on typos in critical file names, paths, commands, and variables. |
| `tests_or_solution_in_image` | `environment_hygiene`; partly `anti_cheat_robustness` | The image-cleanliness portion is covered by `environment_hygiene`; keeping tests and solutions from agent access also supports anti-cheat robustness. `environment_hygiene` is currently skipped in our training rubric. |
| `test_deps_in_image` | `environment_hygiene`; partly `verifiable` | Same broad topic: where test-only dependencies are installed. This Harbor criterion is removed for training because reusable tools such as pytest may be installed in shared task images to reduce image storage and setup time. Terminal-Bench guidance about avoiding runtime verifier installs is a separate reliability concern. |
| `hardcoded_solution` | `solution_quality`; related to `agentic` | Both `hardcoded_solution` and `solution_quality` ask whether the solution derives the answer through commands or computation rather than simply printing it. Terminal-Bench gives more context, including a precomputed-artifact exception. `agentic` separately asks whether the task requires multi-step environment interaction; it does not assess reference-solution quality. |
| `file_reference_mentioned` | `instruction_concision` and `test_instruction_alignment` | A filename should be explicit when the agent must produce it (`test_instruction_alignment`); `instruction_concision` also requires absolute paths. This is a narrower requirement within those broader instruction criteria. |

In these pinned versions, Harbor's criteria are generally shorter and state a
compact check. Terminal-Bench more often expands a criterion into detailed
guidance, edge cases, and explicit pass/fail conditions. The criteria also
differ in scope: some Harbor checks are split into separate items, while
Terminal-Bench combines related checks or adds task and verifier properties
that Harbor's default rubric does not cover.


## Terminal-Bench criteria without a Harbor equivalent

The current patched Terminal-Bench training rubric includes criteria with no
direct Harbor counterpart: `verifiable`, `solvable`, `interesting`,
`outcome_verified`, `task_security`, `functional_verification`,
`deterministic_reproducible`, `essential_difficulty`, `agentic`, `reviewable`,
`instruction_concision`, `resource_configuration`, `no_extraneous_files`,
`artifact_efficiency`, `verifier_execution_isolation`, `ctrf_reporting`, and
`do_not_modify_enforced`.

Some overlapping criteria conflict in their recommendations. Harbor's
`test_deps_in_image` says to install test dependencies from `test.sh`, while
Terminal-Bench's `verifiable` says verifier tooling should be baked into
`tests/Dockerfile` to avoid network installs at verification time. Harbor's
criterion is removed in our Harbor variant. We kept Terminal-Bench's broader
`verifiable` criterion because it also covers determinism, reliability, and
objective grading; its only local change is to mention `tests/setup.sh` as well
as `tests/test.sh`. These dependency recommendations still differ, so consider
them in light of the training pipeline's shared-image setup. The separate
verifier and image-hygiene criteria are dropped because they prescribe
benchmark-specific image management. Harbor's anti-cheat criterion assumes
tests and solutions are not visible to the agent, so a review should check that
assumption against the task's actual container flow.

## Which rubric we use for environment filtering

For now, we use the patched Terminal-Bench rubric for environment filtering:

1. Terminal-Bench added its task rubric on 5 March 2026, after Harbor added its
   task-quality rubric on 26 February 2026 ([Terminal-Bench commit](https://github.com/harbor-framework/terminal-bench/commit/c908a20574d53664277995ffd0c921cecda03a08),
   [Harbor commit](https://github.com/harbor-framework/harbor/commit/c1f266c596c700cb8db53f0ee4bf3853a930b3bd)).
2. Terminal-Bench covers more criteria. As the mapping above shows, most Harbor
   criteria are covered by one or more Terminal-Bench criteria.

The final criteria are:

- `verifiable`
- `solvable`
- `interesting`
- `outcome_verified`
- `anti_cheat_robustness`
- `task_security`
- `functional_verification`
- `deterministic_reproducible`
- `essential_difficulty`
- `test_instruction_alignment`
- `agentic`
- `reviewable`
- `instruction_concision`
- `solution_quality`
- `structured_data_schema`
- `typos`
- `resource_configuration`
- `no_extraneous_files`
- `artifact_efficiency`
- `verifier_execution_isolation`
- `ctrf_reporting`
- `do_not_modify_enforced`

`solvable` is skipped when the complete datasource has no reference solutions.
