# MiMo tasks: negative examples for anti_cheat_robustness

We review [3 MiMo Code tasks](labels.json) with only **`anti_cheat_robustness`**,
expecting `fail`. All three are archived in [mimo_v1](https://huggingface.co/datasets/FWeindel/validated-tasks/tree/main/mimo_v1)
because the image contains reward-hack residue (installed hidden tests or the fixed
code): two from Charlie's report and one from our own audit.

The set was reduced from 27 to 3: MiMo re-pushed fixed images on 2026-10-16, so
the other 24 reported tasks are no longer hackable. The hack is in the image,
so run these tasks with `--review-evidence image`; a task-files-only review cannot detect it.

## Earlier results: 27 tasks, images before 2026-10-16

Judges reviewed the task files only, or the task files plus the task image, using the [upstream Terminal-Bench review prompt](https://github.com/harbor-framework/terminal-bench/blob/1dcda8716784493721921c23e4bc7f7d988b4494/scripts/rubric-regression/templates/instruction.md)
with only the unchanged `anti_cheat_robustness` criterion. The judge received
neither the expected labels nor Charlie's findings.

<!-- BEGIN CHARLIE_RESULTS -->
| Evidence, both with shell tools | Reviewed | Judge: fail (detected) | Judge: pass (missed) | Not applicable | Errors |
| --- | ---: | ---: | ---: | ---: | ---: |
| Task files only | 27/27 | 2 | 25 | 0 | 0 |
| Task files + task image | 21/27 | 1 | 20 | 0 | 1 |

**Task files only:** Reported cost: **$0.8912 total**, **$0.0330 per task** across 27 tasks with costs. [Sonnet-generated labels and explanations](results/charlie-files-tools-reviews.json).

**Task files + task image:** Reported cost: **$0.7319 total**, **$0.0349 per task** across 21 tasks with costs. [Sonnet-generated labels and explanations](results/charlie-image-reviews.json).
<!-- END CHARLIE_RESULTS -->

<!-- BEGIN MODEL_RESULTS -->
**Additional judges**

| Model, task files + image | Reviewed | Fail (detected) | Pass (missed) | Not applicable | Errors |
| --- | ---: | ---: | ---: | ---: | ---: |
| Opus 5.5 | 27/27 | 27 | 0 | 0 | 0 |
| Fable 5.1 | 27/27 | 27 | 0 | 0 | 0 |
| GPT-6 Astra (medium) | 27/27 | 27 | 0 | 0 | 0 |

| Model | Status | Total reported cost | Mean per task with cost | Labels and explanations |
| --- | --- | ---: | ---: | --- |
| Opus 5.5 | complete | $4.1405 | $0.1534 | [Opus 5.5-generated labels and explanations](results/opus-5.5.json) |
| Fable 5.1 | complete | $20.8439 | $0.7720 | [Fable 5.1-generated labels and explanations](results/fable-5.1.json) |
| GPT-6 Astra (medium) | complete | $8.4439 | $0.3127 | [GPT-6 Astra (medium)-generated labels and explanations](results/astra-medium.json) |

Claude costs are CLI-reported token costs; Astra costs are [API-equivalent estimates](https://developers.openai.com/api/docs/models/gpt-6-astra) from CLI token usage at standard-context rates. Both exclude container compute and subscription charges.

Each linked JSON includes task-level labels, explanations, and reported costs.
<!-- END MODEL_RESULTS -->

<!-- BEGIN LOCAL_JUDGES -->
## Local GLM judges

### GLM-5.3-Flash

0/27 completed; 0 errors.


[GLM-5.3-Flash: individual verdicts and explanations](results/glm-5.3-flash.json). [Criterion counts](results/glm-5.3-flash-criterion-counts.csv).

| Category | Expected | Match | Mismatch | Pending |
| --- | --- | ---: | ---: | ---: |
| reported_hackable | fail | 0 | 0 | 27 |
### GLM-5.3

0/27 completed; 0 errors.


[GLM-5.3: individual verdicts and explanations](results/glm-5.3.json). [Criterion counts](results/glm-5.3-criterion-counts.csv).

| Category | Expected | Match | Mismatch | Pending |
| --- | --- | ---: | ---: | ---: |
| reported_hackable | fail | 0 | 0 | 27 |

Missing or invalid reviews are reported separately. Local inference has no API charge; GPU time is recorded with each model.
<!-- END LOCAL_JUDGES -->
