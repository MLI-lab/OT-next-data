# CalibForge review explanation clusters

171 reviewed tasks, one judge trial per task. These reports describe this sample only, not all 5,317 kept tasks.

[Dataset](https://huggingface.co/datasets/FWeindel/validated-tasks/tree/main/calibforge_v1) · [CSV assignments](assignments.csv) · [JSON assignments and rules](assignments.json)

## How to interpret these reports

Each failed explanation receives one primary theme and may have overlapping reason tags. Grouping uses explicit, ordered text patterns in `build_reports.py`; these are provisional lexical clusters, not independently verified task defects. Read the original explanation and expand the instructions when interpreting a cluster. Passes and not-applicable outcomes are preserved separately. Automatically skipped criteria are excluded from these reports and exports. Genuine N/A outcomes from evaluated criteria are retained.

The judge marks all 171 tasks as failing solution quality for missing reference solutions, while automatically skipping solvability for that same datasource-wide absence. Treat this as a rubric-policy issue to review, rather than 171 demonstrated unsolvable tasks. Several other explanations invoke benchmark difficulty expectations or separate-verifier assumptions; their applicability to RL training deserves review. No verdicts have been changed.

[Breakdown by task category](category_breakdown.md)

## Criteria

| Criterion | Description | Fail | Pass | N/A | Failure rate among applicable | Explanations |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| [verifiable](criteria.md#verifiable) | Verifier is reliable, deterministic, and efficient | 94 | 77 | 0 | 55.0% | [Clusters](verifiable_clusters.md) |
| [interesting](criteria.md#interesting) | Real-world value — someone could get paid to do this | 12 | 159 | 0 | 7.0% | [Clusters](interesting_clusters.md) |
| [outcome_verified](criteria.md#outcome_verified) | Tests grade outcomes, not process — instructions describe end state, not steps | 38 | 133 | 0 | 22.2% | [Clusters](outcome_verified_clusters.md) |
| [anti_cheat_robustness](criteria.md#anti_cheat_robustness) | Tests resist adversarial agent behavior | 113 | 58 | 0 | 66.1% | [Clusters](anti_cheat_robustness_clusters.md) |
| [task_security](criteria.md#task_security) | Task contains no malicious code, credential exfiltration, or exploitation of the agent or host | 0 | 171 | 0 | 0.0% | [Clusters](task_security_clusters.md) |
| [functional_verification](criteria.md#functional_verification) | Tests verify actual behavior through execution, not source code keywords or string patterns | 28 | 143 | 0 | 16.4% | [Clusters](functional_verification_clusters.md) |
| [deterministic_reproducible](criteria.md#deterministic_reproducible) | Consistent results across runs with pinned dependencies and no live service dependencies | 45 | 126 | 0 | 26.3% | [Clusters](deterministic_reproducible_clusters.md) |
| [essential_difficulty](criteria.md#essential_difficulty) | Difficulty stems from logical reasoning, not formatting minutiae or arbitrary precision | 43 | 128 | 0 | 25.1% | [Clusters](essential_difficulty_clusters.md) |
| [test_instruction_alignment](criteria.md#test_instruction_alignment) | All behavior checked in tests is described in the instruction, and all behavior described in the instruction is checked in tests | 119 | 52 | 0 | 69.6% | [Clusters](test_instruction_alignment_clusters.md) |
| [agentic](criteria.md#agentic) | Requires multi-step terminal interaction, not solvable by a single LLM call | 38 | 133 | 0 | 22.2% | [Clusters](agentic_clusters.md) |
| [reviewable](criteria.md#reviewable) | Non-specialist reviewers can verify task correctness, or sufficient explanation is provided | 107 | 64 | 0 | 62.6% | [Clusters](reviewable_clusters.md) |
| [instruction_concision](criteria.md#instruction_concision) | Human-written, concise, uses absolute paths, no unnecessary headings, no fluff | 90 | 81 | 0 | 52.6% | [Clusters](instruction_concision_clusters.md) |
| [solution_quality](criteria.md#solution_quality) | Solution demonstrates actual computation, not hardcoded answers | 171 | 0 | 0 | 100.0% | [Clusters](solution_quality_clusters.md) |
| [structured_data_schema](criteria.md#structured_data_schema) | If structured output is expected, exact schema documented in instruction or spec file | 26 | 59 | 86 | 30.6% | [Clusters](structured_data_schema_clusters.md) |
| [typos](criteria.md#typos) | No typos in filenames, paths, commands, or variable names | 6 | 165 | 0 | 3.5% | [Clusters](typos_clusters.md) |
| [resource_configuration](criteria.md#resource_configuration) | Timeouts and resource limits are appropriate for the task | 27 | 144 | 0 | 15.8% | [Clusters](resource_configuration_clusters.md) |
| [no_extraneous_files](criteria.md#no_extraneous_files) | Task directory contains only files needed to build, run, solve, or verify the task — no leftover or unused files | 19 | 152 | 0 | 11.1% | [Clusters](no_extraneous_files_clusters.md) |
| [artifact_efficiency](criteria.md#artifact_efficiency) | Artifacts ship only agent-produced content; build-time-fixed data is baked into the verifier image; collect hooks capture and fail safely | 0 | 0 | 171 | — | [Clusters](artifact_efficiency_clusters.md) |
| [verifier_execution_isolation](criteria.md#verifier_execution_isolation) | Verifiers that execute agent-produced code run it unprivileged and keep the reward channel out of its reach | 95 | 0 | 76 | 100.0% | [Clusters](verifier_execution_isolation_clusters.md) |
| [ctrf_reporting](criteria.md#ctrf_reporting) | Verifiers with discrete test cases emit a per-test CTRF report to /logs/verifier/ctrf.json | 0 | 171 | 0 | 0.0% | [Clusters](ctrf_reporting_clusters.md) |
| [do_not_modify_enforced](criteria.md#do_not_modify_enforced) | Instruction constraints that forbid modifying (or require preserving) a concrete artifact are actually enforced — an agent that violates one fails verification | 17 | 12 | 142 | 58.6% | [Clusters](do_not_modify_enforced_clusters.md) |

## Coverage and reproducibility

All 171 review IDs matched a task archive and have embedded instructions. Every criterion has 171 verdicts. Source review SHA-256: `1f7aecc731516666f7cc0bb6b6d0c030d49bb4764d9822cc098ffddb37c972d0`.

Instructions were retrieved from the current Hugging Face `main` snapshot on 2026-10-19; the review JSON does not identify its dataset revision, so exact revision correspondence cannot be guaranteed. Task archives are read in memory without executing or extracting their code.

Rerun `python3 build_reports.py` with PyArrow installed and the downloaded parquet files at the paths in the script.
