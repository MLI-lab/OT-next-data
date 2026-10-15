# SETA stage-2 review: 199 completed tasks

Saved snapshot of 199 completed reviews from the seed-42 sample of 1,000 stage-1 survivors. These are the first completed tasks, not a separately random sample. Model: `claude-opus-5-5`; evidence: container inspection.

**0/199 pass every active criterion.** Errors and unfinished reviews are excluded from these counts. These are judge findings, not independently confirmed task defects.

CLI-reported cost for these 199 completed reviews: **$72.35**, averaging **$0.364/task**. This is API-equivalent usage cost, not necessarily your subscription bill. Failed attempts and other reviews are not included.

[Criterion definitions and examples](criteria.md) | [All task reviews](tasks.md) | [Counts as CSV](criteria.csv) | [Raw reviews](reviews.json) | [Token usage and cost](usage.json)

| Criterion | Pass | Fail | N/A |
| --- | ---: | ---: | ---: |
| [instruction_concision](criteria.md#instruction-concision) | 3 | 196 | 0 |
| [agentic](criteria.md#agentic) | 18 | 181 | 0 |
| [verifiable](criteria.md#verifiable) | 37 | 162 | 0 |
| [test_instruction_alignment](criteria.md#test-instruction-alignment) | 52 | 147 | 0 |
| [anti_cheat_robustness](criteria.md#anti-cheat-robustness) | 67 | 132 | 0 |
| [essential_difficulty](criteria.md#essential-difficulty) | 77 | 122 | 0 |
| [ctrf_reporting](criteria.md#ctrf-reporting) | 93 | 106 | 0 |
| [solution_quality](criteria.md#solution-quality) | 98 | 101 | 0 |
| [outcome_verified](criteria.md#outcome-verified) | 100 | 99 | 0 |
| [functional_verification](criteria.md#functional-verification) | 129 | 70 | 0 |
| [verifier_execution_isolation](criteria.md#verifier-execution-isolation) | 0 | 69 | 130 |
| [deterministic_reproducible](criteria.md#deterministic-reproducible) | 142 | 57 | 0 |
| [no_extraneous_files](criteria.md#no-extraneous-files) | 166 | 33 | 0 |
| [structured_data_schema](criteria.md#structured-data-schema) | 89 | 21 | 89 |
| [interesting](criteria.md#interesting) | 187 | 12 | 0 |
| [resource_configuration](criteria.md#resource-configuration) | 187 | 12 | 0 |
| [do_not_modify_enforced](criteria.md#do-not-modify-enforced) | 18 | 8 | 173 |
| [reviewable](criteria.md#reviewable) | 196 | 3 | 0 |
| [typos](criteria.md#typos) | 197 | 2 | 0 |
| [solvable](criteria.md#solvable) | 198 | 1 | 0 |
| [task_security](criteria.md#task-security) | 198 | 1 | 0 |
| [artifact_efficiency](criteria.md#artifact-efficiency) | 0 | 0 | 199 |

13 criteria were automatically skipped for every task; see the CSV and criterion report.

Start by inspecting instruction concision, agentic complexity and verifier findings before using an all-criteria-must-pass filter.
