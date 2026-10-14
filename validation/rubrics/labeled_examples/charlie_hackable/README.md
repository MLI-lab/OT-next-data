# Charlie's MiMo tasks: negative examples for anti_cheat_robustness

We review the same [27 externally reported hackable tasks](labels.json),
using only **`anti_cheat_robustness`** and expecting `fail`.

Sonnet reviews each task with shell tools in two settings:

- **Task files only:** the complete task directory in a neutral Apptainer container.
- **Task files + task image:** the task directory plus inspection inside the actual task image.

Both use the [upstream Terminal-Bench review prompt](https://github.com/harbor-framework/terminal-bench/blob/1dcda8716784493721921c23e4bc7f7d988b4494/scripts/rubric-regression/templates/instruction.md)
with only the unchanged `anti_cheat_robustness` criterion. The judge receives
neither the expected labels nor Charlie's findings. Image tags are current;
their contents have not been matched to the historical audit.

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
