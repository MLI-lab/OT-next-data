# Terminal-Bench 3 and 4: positive examples

We sampled **10 tasks from each release**, seed 42, to check whether Sonnet passes
accepted benchmark tasks under our full patched rubric. Release acceptance is
an assumed positive, so flagged failures need inspection.

Sonnet reviews the complete task files in an Apptainer review container,
without the task images. We use the [upstream Terminal-Bench review prompt](https://github.com/harbor-framework/terminal-bench/blob/1dcda8716784493721921c23e4bc7f7d988b4494/scripts/rubric-regression/templates/instruction.md)
with [our patched rubric](../../rubric_terminal_bench/patches/).

<!-- BEGIN TB34_RESULTS -->
| Release | Reviewed | All applicable criteria pass | At least one fail | Pending or invalid | Reported cost | Mean per task |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| terminal-bench-3 | 10/10 | 5 | 5 | 0 | $8.4679 | $0.8468 |
| terminal-bench-4 | 10/10 | 3 | 7 | 0 | $8.5090 | $0.8509 |

Reported cost: **$16.9769 total**, **$0.8488 per task** across 20 tasks with costs.

[Sonnet-generated labels and explanations](results/tb3-tb4-summary.json).
<!-- END TB34_RESULTS -->

<!-- BEGIN MODEL_RESULTS -->
**Additional judges**

| Model | Release | Reviewed | All applicable criteria pass | At least one fail | Pending or invalid |
| --- | --- | ---: | ---: | ---: | ---: |
| Opus 5.5 | terminal-bench-3 | 10/10 | 3 | 7 | 0 |
| Opus 5.5 | terminal-bench-4 | 10/10 | 0 | 10 | 0 |
| Fable 5.1 | terminal-bench-3 | 10/10 | 4 | 6 | 0 |
| Fable 5.1 | terminal-bench-4 | 10/10 | 2 | 8 | 0 |
| GPT-6 Astra (medium) | terminal-bench-3 | 10/10 | 1 | 9 | 0 |
| GPT-6 Astra (medium) | terminal-bench-4 | 10/10 | 0 | 10 | 0 |

| Model | Status | Total reported cost | Mean per task with cost | Labels and explanations |
| --- | --- | ---: | ---: | --- |
| Opus 5.5 | complete | $11.6093 | $0.5805 | [Opus 5.5-generated labels and explanations](results/opus-5.5.json) |
| Fable 5.1 | complete | $44.2612 | $2.2131 | [Fable 5.1-generated labels and explanations](results/fable-5.1.json) |
| GPT-6 Astra (medium) | complete | $22.4762 | $1.1238 | [GPT-6 Astra (medium)-generated labels and explanations](results/astra-medium.json) |

Claude costs are CLI-reported token costs; Astra costs are [API-equivalent estimates](https://developers.openai.com/api/docs/models/gpt-6-astra) from CLI token usage at standard-context rates. Both exclude container compute and subscription charges.

Fable's remaining reviews were resumed after reauthentication and completed: **20/20 reviews**, with no errors. The $44.2612 total includes reported costs from earlier attempts; the mean is across all 20 task records.
Each linked JSON includes task-level labels, explanations, and reported costs.
<!-- END MODEL_RESULTS -->
