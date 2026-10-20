# SETA review explanation clusters

199 reviewed tasks, one judge trial per task. These are the first 199 completed reviews of a seed-42 sample of 1,000 stage-1 survivors, not a separately random sample. The saved run is incomplete; unfinished tasks are excluded.

[Dataset](https://github.com/MLI-lab/OT-next-data/tree/main/data/seta/stage2-review-199) · [CSV assignments](assignments.csv) · [JSON assignments and rules](assignments.json)

## How to interpret these reports

Each failed explanation receives one primary theme and may have overlapping reason tags. Except for the preserved manual instruction-concision assignments, grouping uses explicit, ordered text patterns in `build_reports.py`; these are provisional lexical clusters, not independently verified task defects. Read the original explanation and expand the instructions when interpreting a cluster. Passes and not-applicable outcomes are preserved separately. Automatically skipped criteria are excluded from these reports and exports. Genuine N/A outcomes from evaluated criteria are retained.

The existing SETA manual instruction-concision clusters are preserved exactly. Other criteria use provisional text-pattern grouping; consult original explanations. No judge verdicts have been changed.

## Criteria

| Criterion | Description | Fail | Pass | N/A | Failure rate among applicable | Explanations |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| [verifiable](criteria.md#verifiable) | Verifier is reliable, deterministic, and efficient | 162 | 37 | 0 | 81.4% | [Clusters](verifiable_clusters.md) |
| [solvable](criteria.md#solvable) | Solution demonstrates the task is solvable within time and resource constraints | 1 | 198 | 0 | 0.5% | [Clusters](solvable_clusters.md) |
| [interesting](criteria.md#interesting) | Real-world value — someone could get paid to do this | 12 | 187 | 0 | 6.0% | [Clusters](interesting_clusters.md) |
| [outcome_verified](criteria.md#outcome_verified) | Tests grade outcomes, not process — instructions describe end state, not steps | 99 | 100 | 0 | 49.7% | [Clusters](outcome_verified_clusters.md) |
| [anti_cheat_robustness](criteria.md#anti_cheat_robustness) | Tests resist adversarial agent behavior | 132 | 67 | 0 | 66.3% | [Clusters](anti_cheat_robustness_clusters.md) |
| [task_security](criteria.md#task_security) | Task contains no malicious code, credential exfiltration, or exploitation of the agent or host | 1 | 198 | 0 | 0.5% | [Clusters](task_security_clusters.md) |
| [functional_verification](criteria.md#functional_verification) | Tests verify actual behavior through execution, not source code keywords or string patterns | 70 | 129 | 0 | 35.2% | [Clusters](functional_verification_clusters.md) |
| [deterministic_reproducible](criteria.md#deterministic_reproducible) | Consistent results across runs with pinned dependencies and no live service dependencies | 57 | 142 | 0 | 28.6% | [Clusters](deterministic_reproducible_clusters.md) |
| [essential_difficulty](criteria.md#essential_difficulty) | Difficulty stems from logical reasoning, not formatting minutiae or arbitrary precision | 122 | 77 | 0 | 61.3% | [Clusters](essential_difficulty_clusters.md) |
| [test_instruction_alignment](criteria.md#test_instruction_alignment) | All behavior checked in tests is described in the instruction, and all behavior described in the instruction is checked in tests | 147 | 52 | 0 | 73.9% | [Clusters](test_instruction_alignment_clusters.md) |
| [agentic](criteria.md#agentic) | Requires multi-step terminal interaction, not solvable by a single LLM call | 181 | 18 | 0 | 91.0% | [Clusters](agentic_clusters.md) |
| [reviewable](criteria.md#reviewable) | Non-specialist reviewers can verify task correctness, or sufficient explanation is provided | 3 | 196 | 0 | 1.5% | [Clusters](reviewable_clusters.md) |
| [instruction_concision](criteria.md#instruction_concision) | Human-written, concise, uses absolute paths, no unnecessary headings, no fluff | 196 | 3 | 0 | 98.5% | [Clusters](instruction_concision_clusters.md) |
| [solution_quality](criteria.md#solution_quality) | Solution demonstrates actual computation, not hardcoded answers | 101 | 98 | 0 | 50.8% | [Clusters](solution_quality_clusters.md) |
| [structured_data_schema](criteria.md#structured_data_schema) | If structured output is expected, exact schema documented in instruction or spec file | 21 | 89 | 89 | 19.1% | [Clusters](structured_data_schema_clusters.md) |
| [typos](criteria.md#typos) | No typos in filenames, paths, commands, or variable names | 2 | 197 | 0 | 1.0% | [Clusters](typos_clusters.md) |
| [resource_configuration](criteria.md#resource_configuration) | Timeouts and resource limits are appropriate for the task | 12 | 187 | 0 | 6.0% | [Clusters](resource_configuration_clusters.md) |
| [no_extraneous_files](criteria.md#no_extraneous_files) | Task directory contains only files needed to build, run, solve, or verify the task — no leftover or unused files | 33 | 166 | 0 | 16.6% | [Clusters](no_extraneous_files_clusters.md) |
| [artifact_efficiency](criteria.md#artifact_efficiency) | Artifacts ship only agent-produced content; build-time-fixed data is baked into the verifier image; collect hooks capture and fail safely | 0 | 0 | 199 | — | [Clusters](artifact_efficiency_clusters.md) |
| [verifier_execution_isolation](criteria.md#verifier_execution_isolation) | Verifiers that execute agent-produced code run it unprivileged and keep the reward channel out of its reach | 69 | 0 | 130 | 100.0% | [Clusters](verifier_execution_isolation_clusters.md) |
| [ctrf_reporting](criteria.md#ctrf_reporting) | Verifiers with discrete test cases emit a per-test CTRF report to /logs/verifier/ctrf.json | 106 | 93 | 0 | 53.3% | [Clusters](ctrf_reporting_clusters.md) |
| [do_not_modify_enforced](criteria.md#do_not_modify_enforced) | Instruction constraints that forbid modifying (or require preserving) a concrete artifact are actually enforced — an agent that violates one fails verification | 8 | 18 | 173 | 30.8% | [Clusters](do_not_modify_enforced_clusters.md) |

## Coverage and reproducibility

All 199 review IDs matched a task archive and have embedded instructions. Every criterion has 199 verdicts. Source review SHA-256: `4d0a81e1d60bcb062538e2e16bbe4dc053480e9f1e26c958ff66bed3416013a3`.

Instructions and rubric definitions were retrieved from the matching SETA stage2-review-199 reports on GitHub on 2026-10-19. Original task reports are saved under `tasks/`.

Rerun `python3 build_reports.py` using the saved reviews, instructions and original concision assignments in this directory. `prepare_reports.py` downloads inputs and refreshes the generator from the CalibForge report template.
