# Terminal-Bench labeled tasks

Harbor provides [task labels backed by human evidence](https://github.com/harbor-framework/terminal-bench/blob/1dcda8716784493721921c23e4bc7f7d988b4494/scripts/checks/test-tasks/labels.json),
covering selected criteria for synthetic defects and historical submissions.
The original has **167 labels across 61 tasks**; our [filtered labels](labels.json)
keep **141 labels across 48 tasks** for our patched rubric. Four tasks are excluded
because their labels cite PRs about different tasks, leaving **137 labels across
44 tasks**. Unknown reviewed revisions do not change the original labels.

We copied synthetic tasks from upstream and fetched historical tasks from Git
history. Nine later task versions from merged PRs receive separate assumed-pass
labels for previously failing criteria. Sonnet 5 judged all **57 task versions**
in an Apptainer review container, with full task directories and our full patched
rubric. The task images themselves were not built or inspected.

We use the [upstream Terminal-Bench review prompt](https://github.com/harbor-framework/terminal-bench/blob/1dcda8716784493721921c23e4bc7f7d988b4494/scripts/rubric-regression/templates/instruction.md)
with [our patched rubric](../../rubric_terminal_bench/patches/).

<!-- BEGIN TB_RESULTS -->
| Expected label | Labels | Judge: pass | Judge: fail | Judge: not applicable |
| --- | ---: | ---: | ---: | ---: |
| Original positive | 81 | 63 (77.8%) | 12 (14.8%) | 6 (7.4%) |
| Original negative | 56 | 18 (32.1%) | 38 (67.9%) | 0 (0.0%) |
| Later PR assumed positive | 9 | 5 (55.6%) | 4 (44.4%) | 0 (0.0%) |

A fail on a positive label is a false rejection; a pass on a negative label is a missed defect. Not-applicable judgments are counted as mismatches here.

Reported cost: **$23.7010 total**, **$0.4158 per task** across 57 tasks with costs.

[Sonnet-generated labels and explanations](results/terminal-bench-reviews.json).
<!-- END TB_RESULTS -->

<!-- BEGIN MODEL_RESULTS -->
**Additional judges**

| Model | Expected labels | Judge: pass | Judge: fail | Not applicable | Pending or invalid |
| --- | --- | ---: | ---: | ---: | ---: |
| Opus 5.5 | Original positive (81) | 61 | 15 | 5 | 0 |
| Opus 5.5 | Original negative (56) | 12 | 44 | 0 | 0 |
| Opus 5.5 | Later PR positive (9) | 3 | 6 | 0 | 0 |
| Fable 5.1 | Original positive (81) | 60 | 17 | 4 | 0 |
| Fable 5.1 | Original negative (56) | 13 | 43 | 0 | 0 |
| Fable 5.1 | Later PR positive (9) | 6 | 3 | 0 | 0 |
| GPT-6 Astra (medium) | Original positive (81) | 52 | 24 | 5 | 0 |
| GPT-6 Astra (medium) | Original negative (56) | 11 | 45 | 0 | 0 |
| GPT-6 Astra (medium) | Later PR positive (9) | 3 | 6 | 0 | 0 |

| Model | Status | Total reported cost | Mean per task with cost | Labels and explanations |
| --- | --- | ---: | ---: | --- |
| Opus 5.5 | complete | $17.8783 | $0.3137 | [Opus 5.5-generated labels and explanations](results/opus-5.5.json) |
| Fable 5.1 | complete | $62.3797 | $1.0944 | [Fable 5.1-generated labels and explanations](results/fable-5.1.json) |
| GPT-6 Astra (medium) | complete | $32.5552 | $0.5711 | [GPT-6 Astra (medium)-generated labels and explanations](results/astra-medium.json) |

Claude costs are CLI-reported token costs; Astra costs are [API-equivalent estimates](https://developers.openai.com/api/docs/models/gpt-6-astra) from CLI token usage at standard-context rates. Both exclude container compute and subscription charges.

Each linked JSON includes task-level labels, explanations, and reported costs.
<!-- END MODEL_RESULTS -->

<!-- BEGIN STABILITY_RESULTS -->
## Classification stability (14 October 2026)

We selected 20 fixed tasks (10 synthetic and 10 historical) from the usable
human-label set, using seed 42 and prioritizing criterion and label coverage
without consulting previous model verdicts. The sample covers all 22 criteria
and contains 84 human labels (56 positive, 28 negative). We requested **five
fresh reviews per task per model**, with all 22 criteria assessed each time.
The earlier calibration reviews above are not counted as a sixth repetition.

Task files, rubric, prompt, tool access, and requested model settings were held
fixed. Each review used a fresh session and an isolated neutral container with
task files only. Human labels and previous reviews were hidden from the judge.
Task images were not inspected.

The run stopped after reaching the provider session quota: **172 valid reviews**
(99 Opus and 73 Sonnet). Sonnet has 27 quota-failed review slots; one Opus
review was stopped without a valid result. Two earlier invalid-format Sonnet
responses triggered fresh replacement attempts, with the failed attempts retained.
Missing and invalid responses are not classification disagreements.

### How agreement is measured

- **Unanimous agreement:** the percentage of task/criterion combinations for
  which all five reviews returned the same verdict.
- **Pairwise agreement:** the percentage of unordered pairs of reviews that
  returned the same verdict for the same task and criterion. Five reviews
  provide 10 pairs. Every complete task/criterion combination has equal weight.

The three verdicts (`pass`, `fail`, `not_applicable`) are distinct. For example,
`pass, pass, pass, pass, fail` is not unanimous, but 6/10 pairs agree (60%).
No review, including the first one, is treated as the reference answer.
These measures describe consistency, not agreement with human labels or correctness.

### Matched comparison

For the **same seven tasks**, we evaluated **22 criteria per task**, giving
**7 × 22 = 154 task/criterion classifications, each repeated five times per model**.
Both models have all five valid reviews for these tasks. This is the primary
comparison because both models are measured on the same tasks.

| Result | Opus 5.5 | Sonnet |
| --- | ---: | ---: |
| Always gave the same label across all five reviews | 144/154 (93.5%) | 132/154 (85.7%) |
| Changed the label at least once | 10/154 | 22/154 |
| Pairwise agreement | 96.6% | 93.4% |

Five reviews provide 10 unordered pairs per classification, giving
**154 × 10 = 1,540 pairwise comparisons per model**. Opus agrees in
1,488/1,540 pairs; Sonnet agrees in 1,438/1,540 pairs.

Opus was more consistent on this completed subset. It contains **six synthetic
tasks and one historical task**, so it is not representative of the full
20-task sample or other datasources. Completion was limited by runtime and quota.
Three Opus reviews of `cron-broken-network` reported both Opus 5.5 and Opus 4.8
usage. That task is absent from the complete groups and the matched comparison.

### Agreement by criterion on the same seven tasks

Each unanimous percentage below has a denominator of seven tasks; each pairwise
percentage has a denominator of 70 review pairs. Criteria are ordered by the
combined number of non-unanimous tasks, with alphabetical ties.

| Criterion | Opus unanimous | Opus pairwise | Sonnet unanimous | Sonnet pairwise |
| --- | ---: | ---: | ---: | ---: |
| `deterministic_reproducible` | 6/7 (85.7%) | 91.4% | 2/7 (28.6%) | 65.7% |
| `essential_difficulty` | 6/7 (85.7%) | 94.3% | 4/7 (57.1%) | 82.9% |
| `resource_configuration` | 3/7 (42.9%) | 71.4% | 7/7 (100.0%) | 100.0% |
| `anti_cheat_robustness` | 6/7 (85.7%) | 91.4% | 5/7 (71.4%) | 88.6% |
| `interesting` | 7/7 (100.0%) | 100.0% | 4/7 (57.1%) | 80.0% |
| `outcome_verified` | 6/7 (85.7%) | 94.3% | 6/7 (85.7%) | 94.3% |
| `reviewable` | 6/7 (85.7%) | 91.4% | 6/7 (85.7%) | 94.3% |
| `structured_data_schema` | 7/7 (100.0%) | 100.0% | 5/7 (71.4%) | 82.9% |
| `agentic` | 7/7 (100.0%) | 100.0% | 6/7 (85.7%) | 91.4% |
| `artifact_efficiency` | 7/7 (100.0%) | 100.0% | 6/7 (85.7%) | 91.4% |
| `ctrf_reporting` | 7/7 (100.0%) | 100.0% | 6/7 (85.7%) | 94.3% |
| `functional_verification` | 6/7 (85.7%) | 91.4% | 7/7 (100.0%) | 100.0% |
| `solution_quality` | 7/7 (100.0%) | 100.0% | 6/7 (85.7%) | 94.3% |
| `solvable` | 7/7 (100.0%) | 100.0% | 6/7 (85.7%) | 94.3% |
| `do_not_modify_enforced` | 7/7 (100.0%) | 100.0% | 7/7 (100.0%) | 100.0% |
| `instruction_concision` | 7/7 (100.0%) | 100.0% | 7/7 (100.0%) | 100.0% |
| `no_extraneous_files` | 7/7 (100.0%) | 100.0% | 7/7 (100.0%) | 100.0% |
| `task_security` | 7/7 (100.0%) | 100.0% | 7/7 (100.0%) | 100.0% |
| `test_instruction_alignment` | 7/7 (100.0%) | 100.0% | 7/7 (100.0%) | 100.0% |
| `typos` | 7/7 (100.0%) | 100.0% | 7/7 (100.0%) | 100.0% |
| `verifiable` | 7/7 (100.0%) | 100.0% | 7/7 (100.0%) | 100.0% |
| `verifier_execution_isolation` | 7/7 (100.0%) | 100.0% | 7/7 (100.0%) | 100.0% |

### Additional completed groups

For completeness, the separate model totals below use every task with five
valid reviews for that model. **The task sets differ**, so these percentages
should not be used as the primary comparison between models.

| Model | Complete tasks | Unanimous agreement | Pairwise agreement |
| --- | ---: | ---: | ---: | ---: |
| Opus 5.5 | 19 | 383/418 (91.6%) | 4010/4,180 (95.9%) |
| Sonnet 5 | 7 | 132/154 (85.7%) | 1438/1,540 (93.4%) |

### Saved results

- [Agreement summary CSV](results/stability-20261014/agreement-summary.csv).
- [Per-criterion agreement CSV](results/stability-20261014/criterion-agreement.csv), with matched and all-complete groups, plus pass/fail and not-applicable changes.
- [Per-task agreement CSV](results/stability-20261014/task-agreement.csv), including review counts and matched-subset membership.
- [Individual verdicts CSV](results/stability-20261014/verdict-agreement.csv), with all five repetition columns and agreement measures. Incomplete groups have blank agreement values.
- [Frozen results JSON](results/stability-20261014/results.json) and [sample manifest](results/stability-20261014/sample-manifest.json).

CSV agreement values are fractions from 0 to 1; the tables display percentages.
Raw logs, failed attempts, frozen inputs, and the execution snapshot are retained
at `/hnvme/workspace/y500bb12-optiagent/rubric-stability-20261014`.
The historical table-generation utilities have been removed. Compare new stage-2 runs with `python -m validation.rubrics.analysis REPORT...`.

<!-- END STABILITY_RESULTS -->

<!-- BEGIN LOCAL_JUDGES -->
## Local GLM judges

### GLM-5.3-Flash

1/57 completed; 16 errors.


[GLM-5.3-Flash: individual verdicts and explanations](results/glm-5.3-flash.json). [Criterion counts](results/glm-5.3-flash-criterion-counts.csv).

| Category | Expected | Match | Mismatch | Pending |
| --- | --- | ---: | ---: | ---: |
| candidate | fail | 0 | 0 | 18 |
| candidate | pass | 0 | 0 | 81 |
| excluded | fail | 0 | 0 | 3 |
| excluded | pass | 0 | 0 | 1 |
| later_PR_assumed_positive | pass | 0 | 0 | 9 |
| reviewed | fail | 0 | 0 | 15 |
| synthetic | fail | 1 | 0 | 22 |
### GLM-5.3

0/57 completed; 0 errors.


[GLM-5.3: individual verdicts and explanations](results/glm-5.3.json). [Criterion counts](results/glm-5.3-criterion-counts.csv).

| Category | Expected | Match | Mismatch | Pending |
| --- | --- | ---: | ---: | ---: |
| candidate | fail | 0 | 0 | 18 |
| candidate | pass | 0 | 0 | 81 |
| excluded | fail | 0 | 0 | 3 |
| excluded | pass | 0 | 0 | 1 |
| later_PR_assumed_positive | pass | 0 | 0 | 9 |
| reviewed | fail | 0 | 0 | 15 |
| synthetic | fail | 0 | 0 | 23 |

Missing or invalid reviews are reported separately. Local inference has no API charge; GPU time is recorded with each model.
<!-- END LOCAL_JUDGES -->

## Missing-criterion retries

For seven Flash reviews, we retain 137 valid judgments and request only the 17 missing criteria again. [Merged results and per-criterion provenance](results/glm-5.3-flash-repaired.json) preserve the original answers and mark retry judgments separately. Missing judgments remain unjudged until a valid answer arrives. Retries are not independent stability repetitions.
