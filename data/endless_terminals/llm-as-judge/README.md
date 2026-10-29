# Endless Terminals LLM judge results

These exports review the prepared `EndlessTerminals_phase-3-4-5/tasks.parquet` snapshot (2,145 tasks), using the validation pipeline's LLM rubric review. They describe judge assessments, not teacher solve results or independently verified task defects.

| File | How obtained | Contents |
| --- | --- | --- |
| [comparison_100_tasks.json](comparison_100_tasks.json) | Custom and River classification reviews of the same 100 tasks, sampled with seed 42; `gpt-6.1-sol`, low reasoning. Copied unchanged from the comparison report reconciled with `retry-001`. | Per-task categories, explanations, statuses and findings for both rubrics, plus usage and agreement statistics. Custom completed 98 reviews; River completed 100. The two unresolved custom reviews remain in the file. |
| [per_criterion_findings_315.json](per_criterion_findings_315.json) | Custom rubric, `criteria` format; `openai/gpt-6-astra`, medium reasoning. Combined available shard `report/reviews.json` files and retained only status `findings`. | 315 unique tasks with criterion verdicts and explanations, review attempts, findings and usage. Error entries are excluded. |
| [classification_completed_183.json](classification_completed_183.json) | Custom rubric, `classification` format; `openai/gpt-6-astra`, medium reasoning. Combined available shard `report/reviews.json` files and retained statuses `findings` and `passed`. | 183 unique tasks: 157 with findings and 26 passed. Includes classification judgments and explanations, review attempts, findings and usage. Error entries are excluded. |

The custom rubric covers test coverage, instruction/verifier alignment, task quality and anti-cheat robustness. Criteria reviews assess these separately; classification reviews assign an overall category. Custom and River category names differ, so the comparison also reports agreement on clean versus defective assessments. Agreement is not accuracy.

The Astra files are filtered subsets of available results, not complete reviews of all 2,145 tasks. They should not be used to estimate full-dataset defect rates.