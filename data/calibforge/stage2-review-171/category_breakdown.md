# Review failures by task category

171 reviewed tasks across 14 categories. Categories come from each task’s `task.toml` metadata, not inferred task names. Counts describe the reviewed sample only.

[All reports](README.md) · [Full category × criterion CSV](category_breakdown.csv) · [Task category assignments](task_categories.json)

Each task can fail multiple criteria, so criterion counts must not be added to obtain a task count. Rates below use applicable tasks (fail + pass); Genuine N/A outcomes are excluded from rate denominators; automatically skipped criteria are omitted entirely.

## Category totals

| Category | Reviewed tasks | Tasks with ≥1 failure | Median failed criteria per task |
| --- | ---: | ---: | ---: |
| [data-processing](#data-processing) | 10 | 10 | 4 |
| [data-querying](#data-querying) | 4 | 4 | 5.5 |
| [data-science](#data-science) | 8 | 8 | 4.5 |
| [debugging](#debugging) | 16 | 16 | 4.5 |
| [file-operations](#file-operations) | 11 | 10 | 4 |
| [machine-learning](#machine-learning) | 7 | 7 | 5 |
| [mathematics](#mathematics) | 7 | 7 | 7 |
| [model-training](#model-training) | 7 | 7 | 8 |
| [personal-assistant](#personal-assistant) | 5 | 5 | 5 |
| [scientific-computing](#scientific-computing) | 23 | 23 | 5 |
| [security](#security) | 10 | 10 | 3 |
| [software-engineering](#software-engineering) | 42 | 42 | 6 |
| [system-administration](#system-administration) | 19 | 19 | 6 |
| [video-processing](#video-processing) | 2 | 2 | 6 |

`solution_quality` is excluded because reference solutions are absent. Task failure totals and medians use only the retained criteria.

## Failure count matrix

Cells are failed task counts. This matrix includes every criterion with at least one failure anywhere in the sample. See each category below for applicable denominators and percentages.

| Criterion | data-processing (n=10) | data-querying (n=4) | data-science (n=8) | debugging (n=16) | file-operations (n=11) | machine-learning (n=7) | mathematics (n=7) | model-training (n=7) | personal-assistant (n=5) | scientific-computing (n=23) | security (n=10) | software-engineering (n=42) | system-administration (n=19) | video-processing (n=2) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| [verifiable](verifiable_clusters.md) | 7 | 3 | 0 | 10 | 3 | 0 | 7 | 7 | 1 | 15 | 3 | 24 | 13 | 1 |
| [interesting](interesting_clusters.md) | 0 | 0 | 1 | 0 | 5 | 1 | 2 | 1 | 0 | 0 | 0 | 0 | 1 | 1 |
| [outcome_verified](outcome_verified_clusters.md) | 1 | 1 | 1 | 3 | 0 | 3 | 0 | 3 | 0 | 7 | 2 | 7 | 10 | 0 |
| [anti_cheat_robustness](anti_cheat_robustness_clusters.md) | 5 | 3 | 4 | 13 | 3 | 4 | 6 | 7 | 3 | 16 | 6 | 29 | 14 | 0 |
| [functional_verification](functional_verification_clusters.md) | 1 | 0 | 0 | 4 | 0 | 0 | 0 | 2 | 0 | 5 | 3 | 6 | 7 | 0 |
| [deterministic_reproducible](deterministic_reproducible_clusters.md) | 0 | 0 | 2 | 2 | 0 | 3 | 4 | 4 | 0 | 12 | 2 | 9 | 5 | 2 |
| [essential_difficulty](essential_difficulty_clusters.md) | 1 | 0 | 3 | 2 | 1 | 0 | 3 | 4 | 2 | 7 | 2 | 11 | 6 | 1 |
| [test_instruction_alignment](test_instruction_alignment_clusters.md) | 10 | 3 | 5 | 11 | 4 | 5 | 1 | 6 | 5 | 17 | 6 | 29 | 16 | 1 |
| [agentic](agentic_clusters.md) | 0 | 0 | 2 | 2 | 1 | 3 | 4 | 1 | 0 | 10 | 0 | 13 | 1 | 1 |
| [reviewable](reviewable_clusters.md) | 6 | 4 | 5 | 10 | 9 | 4 | 3 | 5 | 2 | 16 | 5 | 23 | 15 | 0 |
| [instruction_concision](instruction_concision_clusters.md) | 7 | 3 | 3 | 6 | 5 | 4 | 5 | 3 | 3 | 17 | 5 | 17 | 10 | 2 |
| [structured_data_schema](structured_data_schema_clusters.md) | 4 | 0 | 3 | 0 | 2 | 2 | 0 | 1 | 2 | 5 | 3 | 3 | 1 | 0 |
| [typos](typos_clusters.md) | 0 | 0 | 0 | 1 | 0 | 0 | 1 | 0 | 0 | 1 | 0 | 3 | 0 | 0 |
| [resource_configuration](resource_configuration_clusters.md) | 1 | 0 | 5 | 0 | 2 | 0 | 2 | 1 | 0 | 2 | 2 | 6 | 5 | 1 |
| [no_extraneous_files](no_extraneous_files_clusters.md) | 0 | 1 | 3 | 4 | 2 | 2 | 0 | 1 | 0 | 1 | 1 | 3 | 1 | 0 |
| [verifier_execution_isolation](verifier_execution_isolation_clusters.md) | 0 | 3 | 2 | 14 | 1 | 4 | 5 | 3 | 0 | 9 | 4 | 41 | 7 | 2 |
| [do_not_modify_enforced](do_not_modify_enforced_clusters.md) | 1 | 0 | 1 | 3 | 0 | 0 | 0 | 0 | 3 | 2 | 0 | 6 | 1 | 0 |

<a id="data-processing"></a>
## data-processing

**10 tasks reviewed.**

| Criterion | Failed | Passed | N/A | Fail / applicable | Failure rate |
| --- | ---: | ---: | ---: | ---: | ---: |
| [test_instruction_alignment](test_instruction_alignment_clusters.md) | 10 | 0 | 0 | 10/10 | 100.0% |
| [instruction_concision](instruction_concision_clusters.md) | 7 | 3 | 0 | 7/10 | 70.0% |
| [verifiable](verifiable_clusters.md) | 7 | 3 | 0 | 7/10 | 70.0% |
| [reviewable](reviewable_clusters.md) | 6 | 4 | 0 | 6/10 | 60.0% |
| [anti_cheat_robustness](anti_cheat_robustness_clusters.md) | 5 | 5 | 0 | 5/10 | 50.0% |
| [structured_data_schema](structured_data_schema_clusters.md) | 4 | 6 | 0 | 4/10 | 40.0% |
| [do_not_modify_enforced](do_not_modify_enforced_clusters.md) | 1 | 1 | 8 | 1/2 | 50.0% |
| [essential_difficulty](essential_difficulty_clusters.md) | 1 | 9 | 0 | 1/10 | 10.0% |
| [functional_verification](functional_verification_clusters.md) | 1 | 9 | 0 | 1/10 | 10.0% |
| [outcome_verified](outcome_verified_clusters.md) | 1 | 9 | 0 | 1/10 | 10.0% |
| [resource_configuration](resource_configuration_clusters.md) | 1 | 9 | 0 | 1/10 | 10.0% |
| [agentic](agentic_clusters.md) | 0 | 10 | 0 | 0/10 | 0.0% |
| [artifact_efficiency](artifact_efficiency_clusters.md) | 0 | 0 | 10 | 0/0 | — |
| [ctrf_reporting](ctrf_reporting_clusters.md) | 0 | 10 | 0 | 0/10 | 0.0% |
| [deterministic_reproducible](deterministic_reproducible_clusters.md) | 0 | 10 | 0 | 0/10 | 0.0% |
| [interesting](interesting_clusters.md) | 0 | 10 | 0 | 0/10 | 0.0% |
| [no_extraneous_files](no_extraneous_files_clusters.md) | 0 | 10 | 0 | 0/10 | 0.0% |
| [task_security](task_security_clusters.md) | 0 | 10 | 0 | 0/10 | 0.0% |
| [typos](typos_clusters.md) | 0 | 10 | 0 | 0/10 | 0.0% |
| [verifier_execution_isolation](verifier_execution_isolation_clusters.md) | 0 | 0 | 10 | 0/0 | — |

<details>
<summary>Show tasks in this category and their failed criteria</summary>

| Task | Failed criteria |
| --- | --- |
| contrastive_solver-data-processing_20260420_020307_004 | [verifiable](verifiable_clusters.md), [test_instruction_alignment](test_instruction_alignment_clusters.md), [instruction_concision](instruction_concision_clusters.md) |
| contrastive_solver-data-processing_20260422_235124_001 | [verifiable](verifiable_clusters.md), [functional_verification](functional_verification_clusters.md), [test_instruction_alignment](test_instruction_alignment_clusters.md), [reviewable](reviewable_clusters.md) |
| contrastive_solver-data-processing_20260425_072438_002 | [verifiable](verifiable_clusters.md), [anti_cheat_robustness](anti_cheat_robustness_clusters.md), [test_instruction_alignment](test_instruction_alignment_clusters.md), [instruction_concision](instruction_concision_clusters.md) |
| contrastive_solver-data-processing_20260428_101637_004 | [verifiable](verifiable_clusters.md), [anti_cheat_robustness](anti_cheat_robustness_clusters.md), [essential_difficulty](essential_difficulty_clusters.md), [test_instruction_alignment](test_instruction_alignment_clusters.md), [reviewable](reviewable_clusters.md), [instruction_concision](instruction_concision_clusters.md), [structured_data_schema](structured_data_schema_clusters.md) |
| contrastive_solver-data-processing_20260501_000112_002 | [verifiable](verifiable_clusters.md), [test_instruction_alignment](test_instruction_alignment_clusters.md), [reviewable](reviewable_clusters.md), [instruction_concision](instruction_concision_clusters.md), [structured_data_schema](structured_data_schema_clusters.md) |
| contrastive_solver-data-processing_20260502_090008_003 | [verifiable](verifiable_clusters.md), [anti_cheat_robustness](anti_cheat_robustness_clusters.md), [test_instruction_alignment](test_instruction_alignment_clusters.md), [reviewable](reviewable_clusters.md) |
| contrastive_solver-data-processing_20260503_182123_001 | [verifiable](verifiable_clusters.md), [anti_cheat_robustness](anti_cheat_robustness_clusters.md), [test_instruction_alignment](test_instruction_alignment_clusters.md), [instruction_concision](instruction_concision_clusters.md), [structured_data_schema](structured_data_schema_clusters.md) |
| contrastive_solver-data-processing_20260617_210354_004 | [test_instruction_alignment](test_instruction_alignment_clusters.md), [reviewable](reviewable_clusters.md), [instruction_concision](instruction_concision_clusters.md), [structured_data_schema](structured_data_schema_clusters.md) |
| contrastive_solver-data-processing_20260618_093107_001 | [anti_cheat_robustness](anti_cheat_robustness_clusters.md), [test_instruction_alignment](test_instruction_alignment_clusters.md), [reviewable](reviewable_clusters.md) |
| contrastive_solver-data-processing_20260618_093107_002 | [outcome_verified](outcome_verified_clusters.md), [test_instruction_alignment](test_instruction_alignment_clusters.md), [instruction_concision](instruction_concision_clusters.md), [resource_configuration](resource_configuration_clusters.md), [do_not_modify_enforced](do_not_modify_enforced_clusters.md) |

</details>

<a id="data-querying"></a>
## data-querying

**4 tasks reviewed.**

| Criterion | Failed | Passed | N/A | Fail / applicable | Failure rate |
| --- | ---: | ---: | ---: | ---: | ---: |
| [reviewable](reviewable_clusters.md) | 4 | 0 | 0 | 4/4 | 100.0% |
| [anti_cheat_robustness](anti_cheat_robustness_clusters.md) | 3 | 1 | 0 | 3/4 | 75.0% |
| [instruction_concision](instruction_concision_clusters.md) | 3 | 1 | 0 | 3/4 | 75.0% |
| [test_instruction_alignment](test_instruction_alignment_clusters.md) | 3 | 1 | 0 | 3/4 | 75.0% |
| [verifiable](verifiable_clusters.md) | 3 | 1 | 0 | 3/4 | 75.0% |
| [verifier_execution_isolation](verifier_execution_isolation_clusters.md) | 3 | 0 | 1 | 3/3 | 100.0% |
| [no_extraneous_files](no_extraneous_files_clusters.md) | 1 | 3 | 0 | 1/4 | 25.0% |
| [outcome_verified](outcome_verified_clusters.md) | 1 | 3 | 0 | 1/4 | 25.0% |
| [agentic](agentic_clusters.md) | 0 | 4 | 0 | 0/4 | 0.0% |
| [artifact_efficiency](artifact_efficiency_clusters.md) | 0 | 0 | 4 | 0/0 | — |
| [ctrf_reporting](ctrf_reporting_clusters.md) | 0 | 4 | 0 | 0/4 | 0.0% |
| [deterministic_reproducible](deterministic_reproducible_clusters.md) | 0 | 4 | 0 | 0/4 | 0.0% |
| [do_not_modify_enforced](do_not_modify_enforced_clusters.md) | 0 | 0 | 4 | 0/0 | — |
| [essential_difficulty](essential_difficulty_clusters.md) | 0 | 4 | 0 | 0/4 | 0.0% |
| [functional_verification](functional_verification_clusters.md) | 0 | 4 | 0 | 0/4 | 0.0% |
| [interesting](interesting_clusters.md) | 0 | 4 | 0 | 0/4 | 0.0% |
| [resource_configuration](resource_configuration_clusters.md) | 0 | 4 | 0 | 0/4 | 0.0% |
| [structured_data_schema](structured_data_schema_clusters.md) | 0 | 4 | 0 | 0/4 | 0.0% |
| [task_security](task_security_clusters.md) | 0 | 4 | 0 | 0/4 | 0.0% |
| [typos](typos_clusters.md) | 0 | 4 | 0 | 0/4 | 0.0% |

<details>
<summary>Show tasks in this category and their failed criteria</summary>

| Task | Failed criteria |
| --- | --- |
| contrastive_solver-data-querying_20260502_090008_001 | [verifiable](verifiable_clusters.md), [anti_cheat_robustness](anti_cheat_robustness_clusters.md), [test_instruction_alignment](test_instruction_alignment_clusters.md), [reviewable](reviewable_clusters.md), [instruction_concision](instruction_concision_clusters.md) |
| contrastive_solver-data-querying_20260510_125534_001 | [verifiable](verifiable_clusters.md), [outcome_verified](outcome_verified_clusters.md), [anti_cheat_robustness](anti_cheat_robustness_clusters.md), [test_instruction_alignment](test_instruction_alignment_clusters.md), [reviewable](reviewable_clusters.md), [instruction_concision](instruction_concision_clusters.md), [verifier_execution_isolation](verifier_execution_isolation_clusters.md) |
| contrastive_solver-data-querying_20260601_053159_001 | [verifiable](verifiable_clusters.md), [anti_cheat_robustness](anti_cheat_robustness_clusters.md), [reviewable](reviewable_clusters.md), [instruction_concision](instruction_concision_clusters.md), [no_extraneous_files](no_extraneous_files_clusters.md), [verifier_execution_isolation](verifier_execution_isolation_clusters.md) |
| contrastive_solver-data-querying_20260604_003113_001 | [test_instruction_alignment](test_instruction_alignment_clusters.md), [reviewable](reviewable_clusters.md), [verifier_execution_isolation](verifier_execution_isolation_clusters.md) |

</details>

<a id="data-science"></a>
## data-science

**8 tasks reviewed.**

| Criterion | Failed | Passed | N/A | Fail / applicable | Failure rate |
| --- | ---: | ---: | ---: | ---: | ---: |
| [resource_configuration](resource_configuration_clusters.md) | 5 | 3 | 0 | 5/8 | 62.5% |
| [reviewable](reviewable_clusters.md) | 5 | 3 | 0 | 5/8 | 62.5% |
| [test_instruction_alignment](test_instruction_alignment_clusters.md) | 5 | 3 | 0 | 5/8 | 62.5% |
| [anti_cheat_robustness](anti_cheat_robustness_clusters.md) | 4 | 4 | 0 | 4/8 | 50.0% |
| [essential_difficulty](essential_difficulty_clusters.md) | 3 | 5 | 0 | 3/8 | 37.5% |
| [instruction_concision](instruction_concision_clusters.md) | 3 | 5 | 0 | 3/8 | 37.5% |
| [no_extraneous_files](no_extraneous_files_clusters.md) | 3 | 5 | 0 | 3/8 | 37.5% |
| [structured_data_schema](structured_data_schema_clusters.md) | 3 | 3 | 2 | 3/6 | 50.0% |
| [agentic](agentic_clusters.md) | 2 | 6 | 0 | 2/8 | 25.0% |
| [deterministic_reproducible](deterministic_reproducible_clusters.md) | 2 | 6 | 0 | 2/8 | 25.0% |
| [verifier_execution_isolation](verifier_execution_isolation_clusters.md) | 2 | 0 | 6 | 2/2 | 100.0% |
| [do_not_modify_enforced](do_not_modify_enforced_clusters.md) | 1 | 0 | 7 | 1/1 | 100.0% |
| [interesting](interesting_clusters.md) | 1 | 7 | 0 | 1/8 | 12.5% |
| [outcome_verified](outcome_verified_clusters.md) | 1 | 7 | 0 | 1/8 | 12.5% |
| [artifact_efficiency](artifact_efficiency_clusters.md) | 0 | 0 | 8 | 0/0 | — |
| [ctrf_reporting](ctrf_reporting_clusters.md) | 0 | 8 | 0 | 0/8 | 0.0% |
| [functional_verification](functional_verification_clusters.md) | 0 | 8 | 0 | 0/8 | 0.0% |
| [task_security](task_security_clusters.md) | 0 | 8 | 0 | 0/8 | 0.0% |
| [typos](typos_clusters.md) | 0 | 8 | 0 | 0/8 | 0.0% |
| [verifiable](verifiable_clusters.md) | 0 | 8 | 0 | 0/8 | 0.0% |

<details>
<summary>Show tasks in this category and their failed criteria</summary>

| Task | Failed criteria |
| --- | --- |
| contrastive_solver-data-science_20260604_193356_006 | [anti_cheat_robustness](anti_cheat_robustness_clusters.md), [test_instruction_alignment](test_instruction_alignment_clusters.md), [instruction_concision](instruction_concision_clusters.md), [no_extraneous_files](no_extraneous_files_clusters.md), [verifier_execution_isolation](verifier_execution_isolation_clusters.md) |
| contrastive_solver-data-science_20260604_194036_001 | [anti_cheat_robustness](anti_cheat_robustness_clusters.md), [deterministic_reproducible](deterministic_reproducible_clusters.md), [essential_difficulty](essential_difficulty_clusters.md), [test_instruction_alignment](test_instruction_alignment_clusters.md), [reviewable](reviewable_clusters.md), [instruction_concision](instruction_concision_clusters.md), [structured_data_schema](structured_data_schema_clusters.md), [resource_configuration](resource_configuration_clusters.md) |
| contrastive_solver-data-science_20260604_194036_007 | [test_instruction_alignment](test_instruction_alignment_clusters.md), [reviewable](reviewable_clusters.md), [resource_configuration](resource_configuration_clusters.md), [no_extraneous_files](no_extraneous_files_clusters.md) |
| contrastive_solver-data-science_20260605_015435_004 | [reviewable](reviewable_clusters.md), [resource_configuration](resource_configuration_clusters.md), [verifier_execution_isolation](verifier_execution_isolation_clusters.md) |
| contrastive_solver-data-science_20260605_015435_005 | [anti_cheat_robustness](anti_cheat_robustness_clusters.md), [reviewable](reviewable_clusters.md) |
| contrastive_solver-data-science_20260605_015435_007 | [test_instruction_alignment](test_instruction_alignment_clusters.md), [reviewable](reviewable_clusters.md), [structured_data_schema](structured_data_schema_clusters.md), [resource_configuration](resource_configuration_clusters.md) |
| contrastive_solver-data-science_20260606_153537_005 | [deterministic_reproducible](deterministic_reproducible_clusters.md), [essential_difficulty](essential_difficulty_clusters.md), [test_instruction_alignment](test_instruction_alignment_clusters.md), [agentic](agentic_clusters.md), [structured_data_schema](structured_data_schema_clusters.md), [no_extraneous_files](no_extraneous_files_clusters.md), [do_not_modify_enforced](do_not_modify_enforced_clusters.md) |
| contrastive_solver-data-science_20260617_210354_004 | [interesting](interesting_clusters.md), [outcome_verified](outcome_verified_clusters.md), [anti_cheat_robustness](anti_cheat_robustness_clusters.md), [essential_difficulty](essential_difficulty_clusters.md), [agentic](agentic_clusters.md), [instruction_concision](instruction_concision_clusters.md), [resource_configuration](resource_configuration_clusters.md) |

</details>

<a id="debugging"></a>
## debugging

**16 tasks reviewed.**

| Criterion | Failed | Passed | N/A | Fail / applicable | Failure rate |
| --- | ---: | ---: | ---: | ---: | ---: |
| [verifier_execution_isolation](verifier_execution_isolation_clusters.md) | 14 | 0 | 2 | 14/14 | 100.0% |
| [anti_cheat_robustness](anti_cheat_robustness_clusters.md) | 13 | 3 | 0 | 13/16 | 81.2% |
| [test_instruction_alignment](test_instruction_alignment_clusters.md) | 11 | 5 | 0 | 11/16 | 68.8% |
| [reviewable](reviewable_clusters.md) | 10 | 6 | 0 | 10/16 | 62.5% |
| [verifiable](verifiable_clusters.md) | 10 | 6 | 0 | 10/16 | 62.5% |
| [instruction_concision](instruction_concision_clusters.md) | 6 | 10 | 0 | 6/16 | 37.5% |
| [functional_verification](functional_verification_clusters.md) | 4 | 12 | 0 | 4/16 | 25.0% |
| [no_extraneous_files](no_extraneous_files_clusters.md) | 4 | 12 | 0 | 4/16 | 25.0% |
| [do_not_modify_enforced](do_not_modify_enforced_clusters.md) | 3 | 1 | 12 | 3/4 | 75.0% |
| [outcome_verified](outcome_verified_clusters.md) | 3 | 13 | 0 | 3/16 | 18.8% |
| [agentic](agentic_clusters.md) | 2 | 14 | 0 | 2/16 | 12.5% |
| [deterministic_reproducible](deterministic_reproducible_clusters.md) | 2 | 14 | 0 | 2/16 | 12.5% |
| [essential_difficulty](essential_difficulty_clusters.md) | 2 | 14 | 0 | 2/16 | 12.5% |
| [typos](typos_clusters.md) | 1 | 15 | 0 | 1/16 | 6.2% |
| [artifact_efficiency](artifact_efficiency_clusters.md) | 0 | 0 | 16 | 0/0 | — |
| [ctrf_reporting](ctrf_reporting_clusters.md) | 0 | 16 | 0 | 0/16 | 0.0% |
| [interesting](interesting_clusters.md) | 0 | 16 | 0 | 0/16 | 0.0% |
| [resource_configuration](resource_configuration_clusters.md) | 0 | 16 | 0 | 0/16 | 0.0% |
| [structured_data_schema](structured_data_schema_clusters.md) | 0 | 4 | 12 | 0/4 | 0.0% |
| [task_security](task_security_clusters.md) | 0 | 16 | 0 | 0/16 | 0.0% |

<details>
<summary>Show tasks in this category and their failed criteria</summary>

| Task | Failed criteria |
| --- | --- |
| contrastive_solver-debugging_20260422_235124_002 | [verifiable](verifiable_clusters.md), [outcome_verified](outcome_verified_clusters.md), [anti_cheat_robustness](anti_cheat_robustness_clusters.md), [test_instruction_alignment](test_instruction_alignment_clusters.md), [instruction_concision](instruction_concision_clusters.md), [verifier_execution_isolation](verifier_execution_isolation_clusters.md), [do_not_modify_enforced](do_not_modify_enforced_clusters.md) |
| contrastive_solver-debugging_20260501_172213_003 | [verifiable](verifiable_clusters.md), [anti_cheat_robustness](anti_cheat_robustness_clusters.md), [instruction_concision](instruction_concision_clusters.md), [verifier_execution_isolation](verifier_execution_isolation_clusters.md) |
| contrastive_solver-debugging_20260503_182123_001 | [verifiable](verifiable_clusters.md), [anti_cheat_robustness](anti_cheat_robustness_clusters.md), [test_instruction_alignment](test_instruction_alignment_clusters.md), [verifier_execution_isolation](verifier_execution_isolation_clusters.md) |
| contrastive_solver-debugging_20260503_182123_004 | [anti_cheat_robustness](anti_cheat_robustness_clusters.md), [functional_verification](functional_verification_clusters.md), [test_instruction_alignment](test_instruction_alignment_clusters.md), [reviewable](reviewable_clusters.md), [no_extraneous_files](no_extraneous_files_clusters.md), [verifier_execution_isolation](verifier_execution_isolation_clusters.md) |
| contrastive_solver-debugging_20260504_125558_001 | [verifiable](verifiable_clusters.md), [anti_cheat_robustness](anti_cheat_robustness_clusters.md), [functional_verification](functional_verification_clusters.md), [deterministic_reproducible](deterministic_reproducible_clusters.md), [test_instruction_alignment](test_instruction_alignment_clusters.md), [reviewable](reviewable_clusters.md), [instruction_concision](instruction_concision_clusters.md), [no_extraneous_files](no_extraneous_files_clusters.md), [verifier_execution_isolation](verifier_execution_isolation_clusters.md) |
| contrastive_solver-debugging_20260617_210354_001 | [verifiable](verifiable_clusters.md), [outcome_verified](outcome_verified_clusters.md), [anti_cheat_robustness](anti_cheat_robustness_clusters.md), [functional_verification](functional_verification_clusters.md), [test_instruction_alignment](test_instruction_alignment_clusters.md), [reviewable](reviewable_clusters.md), [verifier_execution_isolation](verifier_execution_isolation_clusters.md) |
| contrastive_solver-debugging_20260617_210354_003 | [essential_difficulty](essential_difficulty_clusters.md), [test_instruction_alignment](test_instruction_alignment_clusters.md), [reviewable](reviewable_clusters.md), [verifier_execution_isolation](verifier_execution_isolation_clusters.md) |
| contrastive_solver-debugging_20260617_210354_004 | [anti_cheat_robustness](anti_cheat_robustness_clusters.md), [reviewable](reviewable_clusters.md), [verifier_execution_isolation](verifier_execution_isolation_clusters.md) |
| contrastive_solver-debugging_20260618_093107_002 | [anti_cheat_robustness](anti_cheat_robustness_clusters.md), [reviewable](reviewable_clusters.md), [no_extraneous_files](no_extraneous_files_clusters.md), [verifier_execution_isolation](verifier_execution_isolation_clusters.md) |
| multi_solver-debugging_20260414_211453_004 | [verifiable](verifiable_clusters.md), [test_instruction_alignment](test_instruction_alignment_clusters.md) |
| multi_solver-debugging_20260414_211532_003 | [outcome_verified](outcome_verified_clusters.md), [anti_cheat_robustness](anti_cheat_robustness_clusters.md), [functional_verification](functional_verification_clusters.md), [deterministic_reproducible](deterministic_reproducible_clusters.md), [essential_difficulty](essential_difficulty_clusters.md), [test_instruction_alignment](test_instruction_alignment_clusters.md), [agentic](agentic_clusters.md), [reviewable](reviewable_clusters.md), [instruction_concision](instruction_concision_clusters.md), [typos](typos_clusters.md), [verifier_execution_isolation](verifier_execution_isolation_clusters.md), [do_not_modify_enforced](do_not_modify_enforced_clusters.md) |
| multi_solver-debugging_20260415_094424_005 | [anti_cheat_robustness](anti_cheat_robustness_clusters.md), [test_instruction_alignment](test_instruction_alignment_clusters.md), [reviewable](reviewable_clusters.md), [instruction_concision](instruction_concision_clusters.md), [no_extraneous_files](no_extraneous_files_clusters.md), [verifier_execution_isolation](verifier_execution_isolation_clusters.md), [do_not_modify_enforced](do_not_modify_enforced_clusters.md) |
| multi_solver-debugging_20260415_123352_002 | [verifiable](verifiable_clusters.md), [anti_cheat_robustness](anti_cheat_robustness_clusters.md), [test_instruction_alignment](test_instruction_alignment_clusters.md), [reviewable](reviewable_clusters.md), [instruction_concision](instruction_concision_clusters.md), [verifier_execution_isolation](verifier_execution_isolation_clusters.md) |
| multi_solver-debugging_20260416_172928_002 | [verifiable](verifiable_clusters.md), [anti_cheat_robustness](anti_cheat_robustness_clusters.md), [test_instruction_alignment](test_instruction_alignment_clusters.md), [agentic](agentic_clusters.md), [verifier_execution_isolation](verifier_execution_isolation_clusters.md) |
| multi_solver-debugging_20260417_145736_001 | [verifiable](verifiable_clusters.md), [anti_cheat_robustness](anti_cheat_robustness_clusters.md), [verifier_execution_isolation](verifier_execution_isolation_clusters.md) |
| multi_solver-debugging_20260419_023954_005 | [verifiable](verifiable_clusters.md), [reviewable](reviewable_clusters.md) |

</details>

<a id="file-operations"></a>
## file-operations

**11 tasks reviewed.**

| Criterion | Failed | Passed | N/A | Fail / applicable | Failure rate |
| --- | ---: | ---: | ---: | ---: | ---: |
| [reviewable](reviewable_clusters.md) | 9 | 2 | 0 | 9/11 | 81.8% |
| [instruction_concision](instruction_concision_clusters.md) | 5 | 6 | 0 | 5/11 | 45.5% |
| [interesting](interesting_clusters.md) | 5 | 6 | 0 | 5/11 | 45.5% |
| [test_instruction_alignment](test_instruction_alignment_clusters.md) | 4 | 7 | 0 | 4/11 | 36.4% |
| [anti_cheat_robustness](anti_cheat_robustness_clusters.md) | 3 | 8 | 0 | 3/11 | 27.3% |
| [verifiable](verifiable_clusters.md) | 3 | 8 | 0 | 3/11 | 27.3% |
| [no_extraneous_files](no_extraneous_files_clusters.md) | 2 | 9 | 0 | 2/11 | 18.2% |
| [resource_configuration](resource_configuration_clusters.md) | 2 | 9 | 0 | 2/11 | 18.2% |
| [structured_data_schema](structured_data_schema_clusters.md) | 2 | 0 | 9 | 2/2 | 100.0% |
| [agentic](agentic_clusters.md) | 1 | 10 | 0 | 1/11 | 9.1% |
| [essential_difficulty](essential_difficulty_clusters.md) | 1 | 10 | 0 | 1/11 | 9.1% |
| [verifier_execution_isolation](verifier_execution_isolation_clusters.md) | 1 | 0 | 10 | 1/1 | 100.0% |
| [artifact_efficiency](artifact_efficiency_clusters.md) | 0 | 0 | 11 | 0/0 | — |
| [ctrf_reporting](ctrf_reporting_clusters.md) | 0 | 11 | 0 | 0/11 | 0.0% |
| [deterministic_reproducible](deterministic_reproducible_clusters.md) | 0 | 11 | 0 | 0/11 | 0.0% |
| [do_not_modify_enforced](do_not_modify_enforced_clusters.md) | 0 | 0 | 11 | 0/0 | — |
| [functional_verification](functional_verification_clusters.md) | 0 | 11 | 0 | 0/11 | 0.0% |
| [outcome_verified](outcome_verified_clusters.md) | 0 | 11 | 0 | 0/11 | 0.0% |
| [task_security](task_security_clusters.md) | 0 | 11 | 0 | 0/11 | 0.0% |
| [typos](typos_clusters.md) | 0 | 11 | 0 | 0/11 | 0.0% |

<details>
<summary>Show tasks in this category and their failed criteria</summary>

| Task | Failed criteria |
| --- | --- |
| contrastive_solver-file-operations_20260420_020307_001 | [verifiable](verifiable_clusters.md), [reviewable](reviewable_clusters.md), [instruction_concision](instruction_concision_clusters.md) |
| contrastive_solver-file-operations_20260424_090344_004 | [verifiable](verifiable_clusters.md), [interesting](interesting_clusters.md), [reviewable](reviewable_clusters.md), [instruction_concision](instruction_concision_clusters.md) |
| contrastive_solver-file-operations_20260429_103257_001 | [verifiable](verifiable_clusters.md), [reviewable](reviewable_clusters.md), [structured_data_schema](structured_data_schema_clusters.md) |
| contrastive_solver-file-operations_20260604_003814_004 | [interesting](interesting_clusters.md), [reviewable](reviewable_clusters.md), [instruction_concision](instruction_concision_clusters.md) |
| contrastive_solver-file-operations_20260604_003814_005 |  |
| contrastive_solver-file-operations_20260604_144210_002 | [test_instruction_alignment](test_instruction_alignment_clusters.md), [reviewable](reviewable_clusters.md), [structured_data_schema](structured_data_schema_clusters.md), [verifier_execution_isolation](verifier_execution_isolation_clusters.md) |
| contrastive_solver-file-operations_20260605_015435_003 | [interesting](interesting_clusters.md), [test_instruction_alignment](test_instruction_alignment_clusters.md), [reviewable](reviewable_clusters.md), [resource_configuration](resource_configuration_clusters.md) |
| contrastive_solver-file-operations_20260605_015435_004 | [interesting](interesting_clusters.md), [test_instruction_alignment](test_instruction_alignment_clusters.md), [reviewable](reviewable_clusters.md), [instruction_concision](instruction_concision_clusters.md) |
| contrastive_solver-file-operations_20260605_015435_005 | [anti_cheat_robustness](anti_cheat_robustness_clusters.md), [reviewable](reviewable_clusters.md), [resource_configuration](resource_configuration_clusters.md) |
| contrastive_solver-file-operations_20260606_172554_005 | [anti_cheat_robustness](anti_cheat_robustness_clusters.md), [test_instruction_alignment](test_instruction_alignment_clusters.md), [instruction_concision](instruction_concision_clusters.md), [no_extraneous_files](no_extraneous_files_clusters.md) |
| contrastive_solver-software-engineering_20260606_172606_026 | [interesting](interesting_clusters.md), [anti_cheat_robustness](anti_cheat_robustness_clusters.md), [essential_difficulty](essential_difficulty_clusters.md), [agentic](agentic_clusters.md), [reviewable](reviewable_clusters.md), [no_extraneous_files](no_extraneous_files_clusters.md) |

</details>

<a id="machine-learning"></a>
## machine-learning

**7 tasks reviewed.**

| Criterion | Failed | Passed | N/A | Fail / applicable | Failure rate |
| --- | ---: | ---: | ---: | ---: | ---: |
| [test_instruction_alignment](test_instruction_alignment_clusters.md) | 5 | 2 | 0 | 5/7 | 71.4% |
| [anti_cheat_robustness](anti_cheat_robustness_clusters.md) | 4 | 3 | 0 | 4/7 | 57.1% |
| [instruction_concision](instruction_concision_clusters.md) | 4 | 3 | 0 | 4/7 | 57.1% |
| [reviewable](reviewable_clusters.md) | 4 | 3 | 0 | 4/7 | 57.1% |
| [verifier_execution_isolation](verifier_execution_isolation_clusters.md) | 4 | 0 | 3 | 4/4 | 100.0% |
| [agentic](agentic_clusters.md) | 3 | 4 | 0 | 3/7 | 42.9% |
| [deterministic_reproducible](deterministic_reproducible_clusters.md) | 3 | 4 | 0 | 3/7 | 42.9% |
| [outcome_verified](outcome_verified_clusters.md) | 3 | 4 | 0 | 3/7 | 42.9% |
| [no_extraneous_files](no_extraneous_files_clusters.md) | 2 | 5 | 0 | 2/7 | 28.6% |
| [structured_data_schema](structured_data_schema_clusters.md) | 2 | 3 | 2 | 2/5 | 40.0% |
| [interesting](interesting_clusters.md) | 1 | 6 | 0 | 1/7 | 14.3% |
| [artifact_efficiency](artifact_efficiency_clusters.md) | 0 | 0 | 7 | 0/0 | — |
| [ctrf_reporting](ctrf_reporting_clusters.md) | 0 | 7 | 0 | 0/7 | 0.0% |
| [do_not_modify_enforced](do_not_modify_enforced_clusters.md) | 0 | 3 | 4 | 0/3 | 0.0% |
| [essential_difficulty](essential_difficulty_clusters.md) | 0 | 7 | 0 | 0/7 | 0.0% |
| [functional_verification](functional_verification_clusters.md) | 0 | 7 | 0 | 0/7 | 0.0% |
| [resource_configuration](resource_configuration_clusters.md) | 0 | 7 | 0 | 0/7 | 0.0% |
| [task_security](task_security_clusters.md) | 0 | 7 | 0 | 0/7 | 0.0% |
| [typos](typos_clusters.md) | 0 | 7 | 0 | 0/7 | 0.0% |
| [verifiable](verifiable_clusters.md) | 0 | 7 | 0 | 0/7 | 0.0% |

<details>
<summary>Show tasks in this category and their failed criteria</summary>

| Task | Failed criteria |
| --- | --- |
| contrastive_solver-machine-learning_20260604_193356_002 | [agentic](agentic_clusters.md), [reviewable](reviewable_clusters.md) |
| contrastive_solver-machine-learning_20260604_194036_003 | [interesting](interesting_clusters.md), [outcome_verified](outcome_verified_clusters.md), [anti_cheat_robustness](anti_cheat_robustness_clusters.md), [test_instruction_alignment](test_instruction_alignment_clusters.md), [agentic](agentic_clusters.md), [reviewable](reviewable_clusters.md), [instruction_concision](instruction_concision_clusters.md), [structured_data_schema](structured_data_schema_clusters.md) |
| contrastive_solver-machine-learning_20260605_015435_003 | [deterministic_reproducible](deterministic_reproducible_clusters.md), [test_instruction_alignment](test_instruction_alignment_clusters.md), [reviewable](reviewable_clusters.md), [structured_data_schema](structured_data_schema_clusters.md), [verifier_execution_isolation](verifier_execution_isolation_clusters.md) |
| contrastive_solver-machine-learning_20260606_153537_002 | [anti_cheat_robustness](anti_cheat_robustness_clusters.md), [test_instruction_alignment](test_instruction_alignment_clusters.md), [instruction_concision](instruction_concision_clusters.md), [no_extraneous_files](no_extraneous_files_clusters.md), [verifier_execution_isolation](verifier_execution_isolation_clusters.md) |
| contrastive_solver-machine-learning_20260606_172622_002 | [outcome_verified](outcome_verified_clusters.md), [anti_cheat_robustness](anti_cheat_robustness_clusters.md), [deterministic_reproducible](deterministic_reproducible_clusters.md), [test_instruction_alignment](test_instruction_alignment_clusters.md), [instruction_concision](instruction_concision_clusters.md), [verifier_execution_isolation](verifier_execution_isolation_clusters.md) |
| contrastive_solver-machine-learning_20260607_104421_001 | [outcome_verified](outcome_verified_clusters.md), [anti_cheat_robustness](anti_cheat_robustness_clusters.md), [deterministic_reproducible](deterministic_reproducible_clusters.md), [test_instruction_alignment](test_instruction_alignment_clusters.md), [reviewable](reviewable_clusters.md), [instruction_concision](instruction_concision_clusters.md), [verifier_execution_isolation](verifier_execution_isolation_clusters.md) |
| contrastive_solver-machine-learning_20260610_111012_002 | [agentic](agentic_clusters.md), [no_extraneous_files](no_extraneous_files_clusters.md) |

</details>

<a id="mathematics"></a>
## mathematics

**7 tasks reviewed.**

| Criterion | Failed | Passed | N/A | Fail / applicable | Failure rate |
| --- | ---: | ---: | ---: | ---: | ---: |
| [verifiable](verifiable_clusters.md) | 7 | 0 | 0 | 7/7 | 100.0% |
| [anti_cheat_robustness](anti_cheat_robustness_clusters.md) | 6 | 1 | 0 | 6/7 | 85.7% |
| [instruction_concision](instruction_concision_clusters.md) | 5 | 2 | 0 | 5/7 | 71.4% |
| [verifier_execution_isolation](verifier_execution_isolation_clusters.md) | 5 | 0 | 2 | 5/5 | 100.0% |
| [agentic](agentic_clusters.md) | 4 | 3 | 0 | 4/7 | 57.1% |
| [deterministic_reproducible](deterministic_reproducible_clusters.md) | 4 | 3 | 0 | 4/7 | 57.1% |
| [essential_difficulty](essential_difficulty_clusters.md) | 3 | 4 | 0 | 3/7 | 42.9% |
| [reviewable](reviewable_clusters.md) | 3 | 4 | 0 | 3/7 | 42.9% |
| [interesting](interesting_clusters.md) | 2 | 5 | 0 | 2/7 | 28.6% |
| [resource_configuration](resource_configuration_clusters.md) | 2 | 5 | 0 | 2/7 | 28.6% |
| [test_instruction_alignment](test_instruction_alignment_clusters.md) | 1 | 6 | 0 | 1/7 | 14.3% |
| [typos](typos_clusters.md) | 1 | 6 | 0 | 1/7 | 14.3% |
| [artifact_efficiency](artifact_efficiency_clusters.md) | 0 | 0 | 7 | 0/0 | — |
| [ctrf_reporting](ctrf_reporting_clusters.md) | 0 | 7 | 0 | 0/7 | 0.0% |
| [do_not_modify_enforced](do_not_modify_enforced_clusters.md) | 0 | 0 | 7 | 0/0 | — |
| [functional_verification](functional_verification_clusters.md) | 0 | 7 | 0 | 0/7 | 0.0% |
| [no_extraneous_files](no_extraneous_files_clusters.md) | 0 | 7 | 0 | 0/7 | 0.0% |
| [outcome_verified](outcome_verified_clusters.md) | 0 | 7 | 0 | 0/7 | 0.0% |
| [structured_data_schema](structured_data_schema_clusters.md) | 0 | 3 | 4 | 0/3 | 0.0% |
| [task_security](task_security_clusters.md) | 0 | 7 | 0 | 0/7 | 0.0% |

<details>
<summary>Show tasks in this category and their failed criteria</summary>

| Task | Failed criteria |
| --- | --- |
| multi_solver-mathematics_20260414_211453_003 | [verifiable](verifiable_clusters.md), [anti_cheat_robustness](anti_cheat_robustness_clusters.md), [verifier_execution_isolation](verifier_execution_isolation_clusters.md) |
| multi_solver-mathematics_20260414_211532_001 | [verifiable](verifiable_clusters.md), [deterministic_reproducible](deterministic_reproducible_clusters.md), [test_instruction_alignment](test_instruction_alignment_clusters.md), [reviewable](reviewable_clusters.md), [instruction_concision](instruction_concision_clusters.md), [typos](typos_clusters.md), [verifier_execution_isolation](verifier_execution_isolation_clusters.md) |
| multi_solver-mathematics_20260415_123352_002 | [verifiable](verifiable_clusters.md), [anti_cheat_robustness](anti_cheat_robustness_clusters.md), [deterministic_reproducible](deterministic_reproducible_clusters.md), [essential_difficulty](essential_difficulty_clusters.md), [agentic](agentic_clusters.md), [reviewable](reviewable_clusters.md), [instruction_concision](instruction_concision_clusters.md), [resource_configuration](resource_configuration_clusters.md) |
| multi_solver-mathematics_20260416_184513_002 | [verifiable](verifiable_clusters.md), [anti_cheat_robustness](anti_cheat_robustness_clusters.md), [instruction_concision](instruction_concision_clusters.md) |
| multi_solver-mathematics_20260419_023954_004 | [verifiable](verifiable_clusters.md), [interesting](interesting_clusters.md), [anti_cheat_robustness](anti_cheat_robustness_clusters.md), [deterministic_reproducible](deterministic_reproducible_clusters.md), [essential_difficulty](essential_difficulty_clusters.md), [agentic](agentic_clusters.md), [reviewable](reviewable_clusters.md), [instruction_concision](instruction_concision_clusters.md), [resource_configuration](resource_configuration_clusters.md), [verifier_execution_isolation](verifier_execution_isolation_clusters.md) |
| multi_solver-mathematics_20260419_065313_004 | [verifiable](verifiable_clusters.md), [anti_cheat_robustness](anti_cheat_robustness_clusters.md), [agentic](agentic_clusters.md), [instruction_concision](instruction_concision_clusters.md), [verifier_execution_isolation](verifier_execution_isolation_clusters.md) |
| multi_solver-mathematics_20260419_182351_001 | [verifiable](verifiable_clusters.md), [interesting](interesting_clusters.md), [anti_cheat_robustness](anti_cheat_robustness_clusters.md), [deterministic_reproducible](deterministic_reproducible_clusters.md), [essential_difficulty](essential_difficulty_clusters.md), [agentic](agentic_clusters.md), [verifier_execution_isolation](verifier_execution_isolation_clusters.md) |

</details>

<a id="model-training"></a>
## model-training

**7 tasks reviewed.**

| Criterion | Failed | Passed | N/A | Fail / applicable | Failure rate |
| --- | ---: | ---: | ---: | ---: | ---: |
| [anti_cheat_robustness](anti_cheat_robustness_clusters.md) | 7 | 0 | 0 | 7/7 | 100.0% |
| [verifiable](verifiable_clusters.md) | 7 | 0 | 0 | 7/7 | 100.0% |
| [test_instruction_alignment](test_instruction_alignment_clusters.md) | 6 | 1 | 0 | 6/7 | 85.7% |
| [reviewable](reviewable_clusters.md) | 5 | 2 | 0 | 5/7 | 71.4% |
| [deterministic_reproducible](deterministic_reproducible_clusters.md) | 4 | 3 | 0 | 4/7 | 57.1% |
| [essential_difficulty](essential_difficulty_clusters.md) | 4 | 3 | 0 | 4/7 | 57.1% |
| [instruction_concision](instruction_concision_clusters.md) | 3 | 4 | 0 | 3/7 | 42.9% |
| [outcome_verified](outcome_verified_clusters.md) | 3 | 4 | 0 | 3/7 | 42.9% |
| [verifier_execution_isolation](verifier_execution_isolation_clusters.md) | 3 | 0 | 4 | 3/3 | 100.0% |
| [functional_verification](functional_verification_clusters.md) | 2 | 5 | 0 | 2/7 | 28.6% |
| [agentic](agentic_clusters.md) | 1 | 6 | 0 | 1/7 | 14.3% |
| [interesting](interesting_clusters.md) | 1 | 6 | 0 | 1/7 | 14.3% |
| [no_extraneous_files](no_extraneous_files_clusters.md) | 1 | 6 | 0 | 1/7 | 14.3% |
| [resource_configuration](resource_configuration_clusters.md) | 1 | 6 | 0 | 1/7 | 14.3% |
| [structured_data_schema](structured_data_schema_clusters.md) | 1 | 1 | 5 | 1/2 | 50.0% |
| [artifact_efficiency](artifact_efficiency_clusters.md) | 0 | 0 | 7 | 0/0 | — |
| [ctrf_reporting](ctrf_reporting_clusters.md) | 0 | 7 | 0 | 0/7 | 0.0% |
| [do_not_modify_enforced](do_not_modify_enforced_clusters.md) | 0 | 0 | 7 | 0/0 | — |
| [task_security](task_security_clusters.md) | 0 | 7 | 0 | 0/7 | 0.0% |
| [typos](typos_clusters.md) | 0 | 7 | 0 | 0/7 | 0.0% |

<details>
<summary>Show tasks in this category and their failed criteria</summary>

| Task | Failed criteria |
| --- | --- |
| contrastive_solver-model-training_20260509_111805_002 | [verifiable](verifiable_clusters.md), [anti_cheat_robustness](anti_cheat_robustness_clusters.md), [test_instruction_alignment](test_instruction_alignment_clusters.md) |
| contrastive_solver-model-training_20260509_111805_004 | [verifiable](verifiable_clusters.md), [outcome_verified](outcome_verified_clusters.md), [anti_cheat_robustness](anti_cheat_robustness_clusters.md), [essential_difficulty](essential_difficulty_clusters.md), [test_instruction_alignment](test_instruction_alignment_clusters.md), [reviewable](reviewable_clusters.md), [instruction_concision](instruction_concision_clusters.md), [verifier_execution_isolation](verifier_execution_isolation_clusters.md) |
| contrastive_solver-model-training_20260509_154924_001 | [verifiable](verifiable_clusters.md), [outcome_verified](outcome_verified_clusters.md), [anti_cheat_robustness](anti_cheat_robustness_clusters.md), [functional_verification](functional_verification_clusters.md), [deterministic_reproducible](deterministic_reproducible_clusters.md), [essential_difficulty](essential_difficulty_clusters.md), [test_instruction_alignment](test_instruction_alignment_clusters.md), [reviewable](reviewable_clusters.md), [no_extraneous_files](no_extraneous_files_clusters.md), [verifier_execution_isolation](verifier_execution_isolation_clusters.md) |
| contrastive_solver-model-training_20260509_205636_002 | [verifiable](verifiable_clusters.md), [anti_cheat_robustness](anti_cheat_robustness_clusters.md), [instruction_concision](instruction_concision_clusters.md), [verifier_execution_isolation](verifier_execution_isolation_clusters.md) |
| contrastive_solver-model-training_20260510_053611_004 | [verifiable](verifiable_clusters.md), [anti_cheat_robustness](anti_cheat_robustness_clusters.md), [deterministic_reproducible](deterministic_reproducible_clusters.md), [test_instruction_alignment](test_instruction_alignment_clusters.md), [reviewable](reviewable_clusters.md), [resource_configuration](resource_configuration_clusters.md) |
| contrastive_solver-model-training_20260510_142854_001 | [verifiable](verifiable_clusters.md), [interesting](interesting_clusters.md), [anti_cheat_robustness](anti_cheat_robustness_clusters.md), [deterministic_reproducible](deterministic_reproducible_clusters.md), [essential_difficulty](essential_difficulty_clusters.md), [test_instruction_alignment](test_instruction_alignment_clusters.md), [agentic](agentic_clusters.md), [reviewable](reviewable_clusters.md), [instruction_concision](instruction_concision_clusters.md) |
| contrastive_solver-model-training_20260511_015840_001 | [verifiable](verifiable_clusters.md), [outcome_verified](outcome_verified_clusters.md), [anti_cheat_robustness](anti_cheat_robustness_clusters.md), [functional_verification](functional_verification_clusters.md), [deterministic_reproducible](deterministic_reproducible_clusters.md), [essential_difficulty](essential_difficulty_clusters.md), [test_instruction_alignment](test_instruction_alignment_clusters.md), [reviewable](reviewable_clusters.md), [structured_data_schema](structured_data_schema_clusters.md) |

</details>

<a id="personal-assistant"></a>
## personal-assistant

**5 tasks reviewed.**

| Criterion | Failed | Passed | N/A | Fail / applicable | Failure rate |
| --- | ---: | ---: | ---: | ---: | ---: |
| [test_instruction_alignment](test_instruction_alignment_clusters.md) | 5 | 0 | 0 | 5/5 | 100.0% |
| [anti_cheat_robustness](anti_cheat_robustness_clusters.md) | 3 | 2 | 0 | 3/5 | 60.0% |
| [do_not_modify_enforced](do_not_modify_enforced_clusters.md) | 3 | 0 | 2 | 3/3 | 100.0% |
| [instruction_concision](instruction_concision_clusters.md) | 3 | 2 | 0 | 3/5 | 60.0% |
| [essential_difficulty](essential_difficulty_clusters.md) | 2 | 3 | 0 | 2/5 | 40.0% |
| [reviewable](reviewable_clusters.md) | 2 | 3 | 0 | 2/5 | 40.0% |
| [structured_data_schema](structured_data_schema_clusters.md) | 2 | 3 | 0 | 2/5 | 40.0% |
| [verifiable](verifiable_clusters.md) | 1 | 4 | 0 | 1/5 | 20.0% |
| [agentic](agentic_clusters.md) | 0 | 5 | 0 | 0/5 | 0.0% |
| [artifact_efficiency](artifact_efficiency_clusters.md) | 0 | 0 | 5 | 0/0 | — |
| [ctrf_reporting](ctrf_reporting_clusters.md) | 0 | 5 | 0 | 0/5 | 0.0% |
| [deterministic_reproducible](deterministic_reproducible_clusters.md) | 0 | 5 | 0 | 0/5 | 0.0% |
| [functional_verification](functional_verification_clusters.md) | 0 | 5 | 0 | 0/5 | 0.0% |
| [interesting](interesting_clusters.md) | 0 | 5 | 0 | 0/5 | 0.0% |
| [no_extraneous_files](no_extraneous_files_clusters.md) | 0 | 5 | 0 | 0/5 | 0.0% |
| [outcome_verified](outcome_verified_clusters.md) | 0 | 5 | 0 | 0/5 | 0.0% |
| [resource_configuration](resource_configuration_clusters.md) | 0 | 5 | 0 | 0/5 | 0.0% |
| [task_security](task_security_clusters.md) | 0 | 5 | 0 | 0/5 | 0.0% |
| [typos](typos_clusters.md) | 0 | 5 | 0 | 0/5 | 0.0% |
| [verifier_execution_isolation](verifier_execution_isolation_clusters.md) | 0 | 0 | 5 | 0/0 | — |

<details>
<summary>Show tasks in this category and their failed criteria</summary>

| Task | Failed criteria |
| --- | --- |
| contrastive_solver-personal-assistant_20260604_194036_001 | [test_instruction_alignment](test_instruction_alignment_clusters.md), [instruction_concision](instruction_concision_clusters.md) |
| contrastive_solver-personal-assistant_20260611_210349_001 | [anti_cheat_robustness](anti_cheat_robustness_clusters.md), [essential_difficulty](essential_difficulty_clusters.md), [test_instruction_alignment](test_instruction_alignment_clusters.md), [reviewable](reviewable_clusters.md), [instruction_concision](instruction_concision_clusters.md), [structured_data_schema](structured_data_schema_clusters.md), [do_not_modify_enforced](do_not_modify_enforced_clusters.md) |
| contrastive_solver-personal-assistant_20260613_152233_001 | [anti_cheat_robustness](anti_cheat_robustness_clusters.md), [essential_difficulty](essential_difficulty_clusters.md), [test_instruction_alignment](test_instruction_alignment_clusters.md), [structured_data_schema](structured_data_schema_clusters.md), [do_not_modify_enforced](do_not_modify_enforced_clusters.md) |
| contrastive_solver-personal-assistant_20260617_210354_001 | [verifiable](verifiable_clusters.md), [anti_cheat_robustness](anti_cheat_robustness_clusters.md), [test_instruction_alignment](test_instruction_alignment_clusters.md), [reviewable](reviewable_clusters.md), [instruction_concision](instruction_concision_clusters.md), [do_not_modify_enforced](do_not_modify_enforced_clusters.md) |
| contrastive_solver-personal-assistant_20260618_093107_001 | [test_instruction_alignment](test_instruction_alignment_clusters.md) |

</details>

<a id="scientific-computing"></a>
## scientific-computing

**23 tasks reviewed.**

| Criterion | Failed | Passed | N/A | Fail / applicable | Failure rate |
| --- | ---: | ---: | ---: | ---: | ---: |
| [instruction_concision](instruction_concision_clusters.md) | 17 | 6 | 0 | 17/23 | 73.9% |
| [test_instruction_alignment](test_instruction_alignment_clusters.md) | 17 | 6 | 0 | 17/23 | 73.9% |
| [anti_cheat_robustness](anti_cheat_robustness_clusters.md) | 16 | 7 | 0 | 16/23 | 69.6% |
| [reviewable](reviewable_clusters.md) | 16 | 7 | 0 | 16/23 | 69.6% |
| [verifiable](verifiable_clusters.md) | 15 | 8 | 0 | 15/23 | 65.2% |
| [deterministic_reproducible](deterministic_reproducible_clusters.md) | 12 | 11 | 0 | 12/23 | 52.2% |
| [agentic](agentic_clusters.md) | 10 | 13 | 0 | 10/23 | 43.5% |
| [verifier_execution_isolation](verifier_execution_isolation_clusters.md) | 9 | 0 | 14 | 9/9 | 100.0% |
| [essential_difficulty](essential_difficulty_clusters.md) | 7 | 16 | 0 | 7/23 | 30.4% |
| [outcome_verified](outcome_verified_clusters.md) | 7 | 16 | 0 | 7/23 | 30.4% |
| [functional_verification](functional_verification_clusters.md) | 5 | 18 | 0 | 5/23 | 21.7% |
| [structured_data_schema](structured_data_schema_clusters.md) | 5 | 17 | 1 | 5/22 | 22.7% |
| [do_not_modify_enforced](do_not_modify_enforced_clusters.md) | 2 | 2 | 19 | 2/4 | 50.0% |
| [resource_configuration](resource_configuration_clusters.md) | 2 | 21 | 0 | 2/23 | 8.7% |
| [no_extraneous_files](no_extraneous_files_clusters.md) | 1 | 22 | 0 | 1/23 | 4.3% |
| [typos](typos_clusters.md) | 1 | 22 | 0 | 1/23 | 4.3% |
| [artifact_efficiency](artifact_efficiency_clusters.md) | 0 | 0 | 23 | 0/0 | — |
| [ctrf_reporting](ctrf_reporting_clusters.md) | 0 | 23 | 0 | 0/23 | 0.0% |
| [interesting](interesting_clusters.md) | 0 | 23 | 0 | 0/23 | 0.0% |
| [task_security](task_security_clusters.md) | 0 | 23 | 0 | 0/23 | 0.0% |

<details>
<summary>Show tasks in this category and their failed criteria</summary>

| Task | Failed criteria |
| --- | --- |
| contrastive_solver-scientific-computing_20260420_020307_007 | [verifiable](verifiable_clusters.md), [anti_cheat_robustness](anti_cheat_robustness_clusters.md), [deterministic_reproducible](deterministic_reproducible_clusters.md), [test_instruction_alignment](test_instruction_alignment_clusters.md), [instruction_concision](instruction_concision_clusters.md), [structured_data_schema](structured_data_schema_clusters.md) |
| contrastive_solver-scientific-computing_20260422_235114_001 | [verifiable](verifiable_clusters.md), [outcome_verified](outcome_verified_clusters.md), [anti_cheat_robustness](anti_cheat_robustness_clusters.md), [deterministic_reproducible](deterministic_reproducible_clusters.md), [test_instruction_alignment](test_instruction_alignment_clusters.md), [agentic](agentic_clusters.md), [reviewable](reviewable_clusters.md), [instruction_concision](instruction_concision_clusters.md), [verifier_execution_isolation](verifier_execution_isolation_clusters.md) |
| contrastive_solver-scientific-computing_20260601_053159_007 | [verifiable](verifiable_clusters.md), [anti_cheat_robustness](anti_cheat_robustness_clusters.md), [functional_verification](functional_verification_clusters.md), [deterministic_reproducible](deterministic_reproducible_clusters.md), [test_instruction_alignment](test_instruction_alignment_clusters.md), [reviewable](reviewable_clusters.md), [instruction_concision](instruction_concision_clusters.md) |
| contrastive_solver-scientific-computing_20260602_105139_002 | [anti_cheat_robustness](anti_cheat_robustness_clusters.md), [deterministic_reproducible](deterministic_reproducible_clusters.md), [reviewable](reviewable_clusters.md), [instruction_concision](instruction_concision_clusters.md), [verifier_execution_isolation](verifier_execution_isolation_clusters.md) |
| contrastive_solver-scientific-computing_20260602_105139_003 | [essential_difficulty](essential_difficulty_clusters.md), [test_instruction_alignment](test_instruction_alignment_clusters.md), [reviewable](reviewable_clusters.md), [instruction_concision](instruction_concision_clusters.md) |
| contrastive_solver-scientific-computing_20260602_105139_004 | [verifiable](verifiable_clusters.md), [outcome_verified](outcome_verified_clusters.md), [anti_cheat_robustness](anti_cheat_robustness_clusters.md), [deterministic_reproducible](deterministic_reproducible_clusters.md), [test_instruction_alignment](test_instruction_alignment_clusters.md), [agentic](agentic_clusters.md), [reviewable](reviewable_clusters.md), [instruction_concision](instruction_concision_clusters.md) |
| contrastive_solver-scientific-computing_20260602_105139_007 | [agentic](agentic_clusters.md), [reviewable](reviewable_clusters.md), [no_extraneous_files](no_extraneous_files_clusters.md) |
| contrastive_solver-scientific-computing_20260603_181444_004 | [outcome_verified](outcome_verified_clusters.md), [deterministic_reproducible](deterministic_reproducible_clusters.md), [test_instruction_alignment](test_instruction_alignment_clusters.md), [reviewable](reviewable_clusters.md), [structured_data_schema](structured_data_schema_clusters.md) |
| contrastive_solver-scientific-computing_20260604_002443_001 | [verifiable](verifiable_clusters.md), [outcome_verified](outcome_verified_clusters.md), [anti_cheat_robustness](anti_cheat_robustness_clusters.md), [functional_verification](functional_verification_clusters.md), [deterministic_reproducible](deterministic_reproducible_clusters.md), [essential_difficulty](essential_difficulty_clusters.md), [test_instruction_alignment](test_instruction_alignment_clusters.md), [agentic](agentic_clusters.md), [instruction_concision](instruction_concision_clusters.md), [verifier_execution_isolation](verifier_execution_isolation_clusters.md), [do_not_modify_enforced](do_not_modify_enforced_clusters.md) |
| contrastive_solver-scientific-computing_20260615_143956_004 | [deterministic_reproducible](deterministic_reproducible_clusters.md), [test_instruction_alignment](test_instruction_alignment_clusters.md), [do_not_modify_enforced](do_not_modify_enforced_clusters.md) |
| contrastive_solver-scientific-computing_20260616_094815_003 | [agentic](agentic_clusters.md), [reviewable](reviewable_clusters.md) |
| contrastive_solver-scientific-computing_20260616_102700_003 | [verifiable](verifiable_clusters.md), [anti_cheat_robustness](anti_cheat_robustness_clusters.md), [deterministic_reproducible](deterministic_reproducible_clusters.md), [essential_difficulty](essential_difficulty_clusters.md), [test_instruction_alignment](test_instruction_alignment_clusters.md), [reviewable](reviewable_clusters.md), [instruction_concision](instruction_concision_clusters.md), [typos](typos_clusters.md) |
| contrastive_solver-scientific-computing_20260617_051646_002 | [deterministic_reproducible](deterministic_reproducible_clusters.md), [test_instruction_alignment](test_instruction_alignment_clusters.md), [agentic](agentic_clusters.md), [reviewable](reviewable_clusters.md), [instruction_concision](instruction_concision_clusters.md) |
| contrastive_solver-scientific-computing_20260617_104707_001 | [verifiable](verifiable_clusters.md), [outcome_verified](outcome_verified_clusters.md), [anti_cheat_robustness](anti_cheat_robustness_clusters.md), [functional_verification](functional_verification_clusters.md), [essential_difficulty](essential_difficulty_clusters.md), [test_instruction_alignment](test_instruction_alignment_clusters.md), [reviewable](reviewable_clusters.md), [instruction_concision](instruction_concision_clusters.md), [structured_data_schema](structured_data_schema_clusters.md), [resource_configuration](resource_configuration_clusters.md), [verifier_execution_isolation](verifier_execution_isolation_clusters.md) |
| contrastive_solver-scientific-computing_20260617_104707_005 | [verifiable](verifiable_clusters.md), [anti_cheat_robustness](anti_cheat_robustness_clusters.md), [reviewable](reviewable_clusters.md), [instruction_concision](instruction_concision_clusters.md), [verifier_execution_isolation](verifier_execution_isolation_clusters.md) |
| contrastive_solver-scientific-computing_20260617_104707_006 | [anti_cheat_robustness](anti_cheat_robustness_clusters.md), [test_instruction_alignment](test_instruction_alignment_clusters.md), [reviewable](reviewable_clusters.md), [instruction_concision](instruction_concision_clusters.md) |
| multi_solver-scientific-computing_20260416_031414_007 | [verifiable](verifiable_clusters.md), [deterministic_reproducible](deterministic_reproducible_clusters.md), [essential_difficulty](essential_difficulty_clusters.md), [test_instruction_alignment](test_instruction_alignment_clusters.md), [reviewable](reviewable_clusters.md), [structured_data_schema](structured_data_schema_clusters.md) |
| multi_solver-scientific-computing_20260416_172928_005 | [verifiable](verifiable_clusters.md), [anti_cheat_robustness](anti_cheat_robustness_clusters.md), [agentic](agentic_clusters.md), [instruction_concision](instruction_concision_clusters.md), [verifier_execution_isolation](verifier_execution_isolation_clusters.md) |
| multi_solver-scientific-computing_20260416_184513_002 | [verifiable](verifiable_clusters.md), [anti_cheat_robustness](anti_cheat_robustness_clusters.md), [deterministic_reproducible](deterministic_reproducible_clusters.md), [agentic](agentic_clusters.md), [instruction_concision](instruction_concision_clusters.md), [resource_configuration](resource_configuration_clusters.md) |
| multi_solver-scientific-computing_20260416_184513_006 | [verifiable](verifiable_clusters.md), [outcome_verified](outcome_verified_clusters.md), [anti_cheat_robustness](anti_cheat_robustness_clusters.md), [functional_verification](functional_verification_clusters.md), [essential_difficulty](essential_difficulty_clusters.md), [test_instruction_alignment](test_instruction_alignment_clusters.md), [reviewable](reviewable_clusters.md), [instruction_concision](instruction_concision_clusters.md), [structured_data_schema](structured_data_schema_clusters.md), [verifier_execution_isolation](verifier_execution_isolation_clusters.md) |
| multi_solver-scientific-computing_20260417_145736_006 | [verifiable](verifiable_clusters.md), [anti_cheat_robustness](anti_cheat_robustness_clusters.md), [test_instruction_alignment](test_instruction_alignment_clusters.md), [reviewable](reviewable_clusters.md), [instruction_concision](instruction_concision_clusters.md) |
| multi_solver-scientific-computing_20260418_190236_003 | [verifiable](verifiable_clusters.md), [anti_cheat_robustness](anti_cheat_robustness_clusters.md), [test_instruction_alignment](test_instruction_alignment_clusters.md), [agentic](agentic_clusters.md), [verifier_execution_isolation](verifier_execution_isolation_clusters.md) |
| multi_solver-scientific-computing_20260419_112101_005 | [verifiable](verifiable_clusters.md), [outcome_verified](outcome_verified_clusters.md), [anti_cheat_robustness](anti_cheat_robustness_clusters.md), [functional_verification](functional_verification_clusters.md), [essential_difficulty](essential_difficulty_clusters.md), [test_instruction_alignment](test_instruction_alignment_clusters.md), [agentic](agentic_clusters.md), [instruction_concision](instruction_concision_clusters.md), [verifier_execution_isolation](verifier_execution_isolation_clusters.md) |

</details>

<a id="security"></a>
## security

**10 tasks reviewed.**

| Criterion | Failed | Passed | N/A | Fail / applicable | Failure rate |
| --- | ---: | ---: | ---: | ---: | ---: |
| [anti_cheat_robustness](anti_cheat_robustness_clusters.md) | 6 | 4 | 0 | 6/10 | 60.0% |
| [test_instruction_alignment](test_instruction_alignment_clusters.md) | 6 | 4 | 0 | 6/10 | 60.0% |
| [instruction_concision](instruction_concision_clusters.md) | 5 | 5 | 0 | 5/10 | 50.0% |
| [reviewable](reviewable_clusters.md) | 5 | 5 | 0 | 5/10 | 50.0% |
| [verifier_execution_isolation](verifier_execution_isolation_clusters.md) | 4 | 0 | 6 | 4/4 | 100.0% |
| [functional_verification](functional_verification_clusters.md) | 3 | 7 | 0 | 3/10 | 30.0% |
| [structured_data_schema](structured_data_schema_clusters.md) | 3 | 3 | 4 | 3/6 | 50.0% |
| [verifiable](verifiable_clusters.md) | 3 | 7 | 0 | 3/10 | 30.0% |
| [deterministic_reproducible](deterministic_reproducible_clusters.md) | 2 | 8 | 0 | 2/10 | 20.0% |
| [essential_difficulty](essential_difficulty_clusters.md) | 2 | 8 | 0 | 2/10 | 20.0% |
| [outcome_verified](outcome_verified_clusters.md) | 2 | 8 | 0 | 2/10 | 20.0% |
| [resource_configuration](resource_configuration_clusters.md) | 2 | 8 | 0 | 2/10 | 20.0% |
| [no_extraneous_files](no_extraneous_files_clusters.md) | 1 | 9 | 0 | 1/10 | 10.0% |
| [agentic](agentic_clusters.md) | 0 | 10 | 0 | 0/10 | 0.0% |
| [artifact_efficiency](artifact_efficiency_clusters.md) | 0 | 0 | 10 | 0/0 | — |
| [ctrf_reporting](ctrf_reporting_clusters.md) | 0 | 10 | 0 | 0/10 | 0.0% |
| [do_not_modify_enforced](do_not_modify_enforced_clusters.md) | 0 | 3 | 7 | 0/3 | 0.0% |
| [interesting](interesting_clusters.md) | 0 | 10 | 0 | 0/10 | 0.0% |
| [task_security](task_security_clusters.md) | 0 | 10 | 0 | 0/10 | 0.0% |
| [typos](typos_clusters.md) | 0 | 10 | 0 | 0/10 | 0.0% |

<details>
<summary>Show tasks in this category and their failed criteria</summary>

| Task | Failed criteria |
| --- | --- |
| contrastive_solver-security_20260509_154924_001 | [anti_cheat_robustness](anti_cheat_robustness_clusters.md) |
| contrastive_solver-security_20260509_154924_007 | [verifiable](verifiable_clusters.md), [test_instruction_alignment](test_instruction_alignment_clusters.md), [instruction_concision](instruction_concision_clusters.md) |
| contrastive_solver-security_20260509_202829_006 | [verifiable](verifiable_clusters.md), [anti_cheat_robustness](anti_cheat_robustness_clusters.md), [deterministic_reproducible](deterministic_reproducible_clusters.md), [reviewable](reviewable_clusters.md), [resource_configuration](resource_configuration_clusters.md) |
| contrastive_solver-security_20260510_062946_003 | [reviewable](reviewable_clusters.md), [instruction_concision](instruction_concision_clusters.md) |
| contrastive_solver-security_20260510_125534_007 | [verifiable](verifiable_clusters.md), [anti_cheat_robustness](anti_cheat_robustness_clusters.md), [functional_verification](functional_verification_clusters.md), [deterministic_reproducible](deterministic_reproducible_clusters.md), [test_instruction_alignment](test_instruction_alignment_clusters.md), [reviewable](reviewable_clusters.md), [instruction_concision](instruction_concision_clusters.md), [structured_data_schema](structured_data_schema_clusters.md) |
| contrastive_solver-security_20260614_141608_004 | [outcome_verified](outcome_verified_clusters.md), [anti_cheat_robustness](anti_cheat_robustness_clusters.md), [functional_verification](functional_verification_clusters.md), [essential_difficulty](essential_difficulty_clusters.md), [test_instruction_alignment](test_instruction_alignment_clusters.md), [reviewable](reviewable_clusters.md), [instruction_concision](instruction_concision_clusters.md), [structured_data_schema](structured_data_schema_clusters.md), [no_extraneous_files](no_extraneous_files_clusters.md), [verifier_execution_isolation](verifier_execution_isolation_clusters.md) |
| contrastive_solver-security_20260615_105532_001 | [anti_cheat_robustness](anti_cheat_robustness_clusters.md), [test_instruction_alignment](test_instruction_alignment_clusters.md), [verifier_execution_isolation](verifier_execution_isolation_clusters.md) |
| contrastive_solver-security_20260616_094815_003 | [test_instruction_alignment](test_instruction_alignment_clusters.md), [verifier_execution_isolation](verifier_execution_isolation_clusters.md) |
| contrastive_solver-security_20260617_104707_005 | [resource_configuration](resource_configuration_clusters.md) |
| contrastive_solver-security_20260617_210354_004 | [outcome_verified](outcome_verified_clusters.md), [anti_cheat_robustness](anti_cheat_robustness_clusters.md), [functional_verification](functional_verification_clusters.md), [essential_difficulty](essential_difficulty_clusters.md), [test_instruction_alignment](test_instruction_alignment_clusters.md), [reviewable](reviewable_clusters.md), [instruction_concision](instruction_concision_clusters.md), [structured_data_schema](structured_data_schema_clusters.md), [verifier_execution_isolation](verifier_execution_isolation_clusters.md) |

</details>

<a id="software-engineering"></a>
## software-engineering

**42 tasks reviewed.**

| Criterion | Failed | Passed | N/A | Fail / applicable | Failure rate |
| --- | ---: | ---: | ---: | ---: | ---: |
| [verifier_execution_isolation](verifier_execution_isolation_clusters.md) | 41 | 0 | 1 | 41/41 | 100.0% |
| [anti_cheat_robustness](anti_cheat_robustness_clusters.md) | 29 | 13 | 0 | 29/42 | 69.0% |
| [test_instruction_alignment](test_instruction_alignment_clusters.md) | 29 | 13 | 0 | 29/42 | 69.0% |
| [verifiable](verifiable_clusters.md) | 24 | 18 | 0 | 24/42 | 57.1% |
| [reviewable](reviewable_clusters.md) | 23 | 19 | 0 | 23/42 | 54.8% |
| [instruction_concision](instruction_concision_clusters.md) | 17 | 25 | 0 | 17/42 | 40.5% |
| [agentic](agentic_clusters.md) | 13 | 29 | 0 | 13/42 | 31.0% |
| [essential_difficulty](essential_difficulty_clusters.md) | 11 | 31 | 0 | 11/42 | 26.2% |
| [deterministic_reproducible](deterministic_reproducible_clusters.md) | 9 | 33 | 0 | 9/42 | 21.4% |
| [outcome_verified](outcome_verified_clusters.md) | 7 | 35 | 0 | 7/42 | 16.7% |
| [do_not_modify_enforced](do_not_modify_enforced_clusters.md) | 6 | 1 | 35 | 6/7 | 85.7% |
| [functional_verification](functional_verification_clusters.md) | 6 | 36 | 0 | 6/42 | 14.3% |
| [resource_configuration](resource_configuration_clusters.md) | 6 | 36 | 0 | 6/42 | 14.3% |
| [no_extraneous_files](no_extraneous_files_clusters.md) | 3 | 39 | 0 | 3/42 | 7.1% |
| [structured_data_schema](structured_data_schema_clusters.md) | 3 | 7 | 32 | 3/10 | 30.0% |
| [typos](typos_clusters.md) | 3 | 39 | 0 | 3/42 | 7.1% |
| [artifact_efficiency](artifact_efficiency_clusters.md) | 0 | 0 | 42 | 0/0 | — |
| [ctrf_reporting](ctrf_reporting_clusters.md) | 0 | 42 | 0 | 0/42 | 0.0% |
| [interesting](interesting_clusters.md) | 0 | 42 | 0 | 0/42 | 0.0% |
| [task_security](task_security_clusters.md) | 0 | 42 | 0 | 0/42 | 0.0% |

<details>
<summary>Show tasks in this category and their failed criteria</summary>

| Task | Failed criteria |
| --- | --- |
| contrastive_solver-software-engineering_20260420_020307_009 | [verifiable](verifiable_clusters.md), [agentic](agentic_clusters.md), [instruction_concision](instruction_concision_clusters.md), [verifier_execution_isolation](verifier_execution_isolation_clusters.md) |
| contrastive_solver-software-engineering_20260508_045800_002 | [verifiable](verifiable_clusters.md), [outcome_verified](outcome_verified_clusters.md), [anti_cheat_robustness](anti_cheat_robustness_clusters.md), [test_instruction_alignment](test_instruction_alignment_clusters.md), [verifier_execution_isolation](verifier_execution_isolation_clusters.md), [do_not_modify_enforced](do_not_modify_enforced_clusters.md) |
| contrastive_solver-software-engineering_20260508_045800_003 | [verifiable](verifiable_clusters.md), [reviewable](reviewable_clusters.md), [verifier_execution_isolation](verifier_execution_isolation_clusters.md) |
| contrastive_solver-software-engineering_20260508_045800_015 | [verifiable](verifiable_clusters.md), [anti_cheat_robustness](anti_cheat_robustness_clusters.md), [test_instruction_alignment](test_instruction_alignment_clusters.md), [reviewable](reviewable_clusters.md), [verifier_execution_isolation](verifier_execution_isolation_clusters.md), [do_not_modify_enforced](do_not_modify_enforced_clusters.md) |
| contrastive_solver-software-engineering_20260508_045920_016 | [verifiable](verifiable_clusters.md), [outcome_verified](outcome_verified_clusters.md), [anti_cheat_robustness](anti_cheat_robustness_clusters.md), [functional_verification](functional_verification_clusters.md), [deterministic_reproducible](deterministic_reproducible_clusters.md), [essential_difficulty](essential_difficulty_clusters.md), [test_instruction_alignment](test_instruction_alignment_clusters.md), [reviewable](reviewable_clusters.md), [typos](typos_clusters.md), [verifier_execution_isolation](verifier_execution_isolation_clusters.md) |
| contrastive_solver-software-engineering_20260508_045920_021 | [verifiable](verifiable_clusters.md), [test_instruction_alignment](test_instruction_alignment_clusters.md), [agentic](agentic_clusters.md), [instruction_concision](instruction_concision_clusters.md), [verifier_execution_isolation](verifier_execution_isolation_clusters.md) |
| contrastive_solver-software-engineering_20260508_045920_025 | [verifiable](verifiable_clusters.md), [anti_cheat_robustness](anti_cheat_robustness_clusters.md), [deterministic_reproducible](deterministic_reproducible_clusters.md), [reviewable](reviewable_clusters.md), [typos](typos_clusters.md), [verifier_execution_isolation](verifier_execution_isolation_clusters.md), [do_not_modify_enforced](do_not_modify_enforced_clusters.md) |
| contrastive_solver-software-engineering_20260508_153434_014 | [verifiable](verifiable_clusters.md), [anti_cheat_robustness](anti_cheat_robustness_clusters.md), [essential_difficulty](essential_difficulty_clusters.md), [test_instruction_alignment](test_instruction_alignment_clusters.md), [agentic](agentic_clusters.md), [reviewable](reviewable_clusters.md), [instruction_concision](instruction_concision_clusters.md), [verifier_execution_isolation](verifier_execution_isolation_clusters.md) |
| contrastive_solver-software-engineering_20260601_053159_011 | [anti_cheat_robustness](anti_cheat_robustness_clusters.md), [verifier_execution_isolation](verifier_execution_isolation_clusters.md) |
| contrastive_solver-software-engineering_20260601_053159_021 | [test_instruction_alignment](test_instruction_alignment_clusters.md), [agentic](agentic_clusters.md), [reviewable](reviewable_clusters.md), [instruction_concision](instruction_concision_clusters.md), [verifier_execution_isolation](verifier_execution_isolation_clusters.md) |
| contrastive_solver-software-engineering_20260602_105139_012 | [anti_cheat_robustness](anti_cheat_robustness_clusters.md), [agentic](agentic_clusters.md), [no_extraneous_files](no_extraneous_files_clusters.md), [verifier_execution_isolation](verifier_execution_isolation_clusters.md) |
| contrastive_solver-software-engineering_20260602_105139_019 | [verifiable](verifiable_clusters.md), [anti_cheat_robustness](anti_cheat_robustness_clusters.md), [essential_difficulty](essential_difficulty_clusters.md), [agentic](agentic_clusters.md), [resource_configuration](resource_configuration_clusters.md), [verifier_execution_isolation](verifier_execution_isolation_clusters.md) |
| contrastive_solver-software-engineering_20260602_105139_020 | [anti_cheat_robustness](anti_cheat_robustness_clusters.md), [verifier_execution_isolation](verifier_execution_isolation_clusters.md) |
| contrastive_solver-software-engineering_20260602_105139_022 | [anti_cheat_robustness](anti_cheat_robustness_clusters.md), [test_instruction_alignment](test_instruction_alignment_clusters.md), [agentic](agentic_clusters.md), [structured_data_schema](structured_data_schema_clusters.md), [resource_configuration](resource_configuration_clusters.md), [verifier_execution_isolation](verifier_execution_isolation_clusters.md) |
| contrastive_solver-software-engineering_20260602_105139_024 | [anti_cheat_robustness](anti_cheat_robustness_clusters.md), [test_instruction_alignment](test_instruction_alignment_clusters.md), [agentic](agentic_clusters.md), [instruction_concision](instruction_concision_clusters.md), [verifier_execution_isolation](verifier_execution_isolation_clusters.md) |
| contrastive_solver-software-engineering_20260606_153537_004 | [anti_cheat_robustness](anti_cheat_robustness_clusters.md), [essential_difficulty](essential_difficulty_clusters.md), [test_instruction_alignment](test_instruction_alignment_clusters.md), [reviewable](reviewable_clusters.md), [instruction_concision](instruction_concision_clusters.md), [verifier_execution_isolation](verifier_execution_isolation_clusters.md) |
| contrastive_solver-software-engineering_20260606_172554_010 | [essential_difficulty](essential_difficulty_clusters.md), [test_instruction_alignment](test_instruction_alignment_clusters.md), [reviewable](reviewable_clusters.md), [verifier_execution_isolation](verifier_execution_isolation_clusters.md) |
| contrastive_solver-software-engineering_20260606_172554_025 | [anti_cheat_robustness](anti_cheat_robustness_clusters.md), [no_extraneous_files](no_extraneous_files_clusters.md), [verifier_execution_isolation](verifier_execution_isolation_clusters.md) |
| contrastive_solver-software-engineering_20260606_172606_001 | [anti_cheat_robustness](anti_cheat_robustness_clusters.md), [verifier_execution_isolation](verifier_execution_isolation_clusters.md) |
| contrastive_solver-software-engineering_20260606_172606_009 | [reviewable](reviewable_clusters.md), [verifier_execution_isolation](verifier_execution_isolation_clusters.md) |
| contrastive_solver-software-engineering_20260606_172606_018 | [essential_difficulty](essential_difficulty_clusters.md), [test_instruction_alignment](test_instruction_alignment_clusters.md), [agentic](agentic_clusters.md), [instruction_concision](instruction_concision_clusters.md), [structured_data_schema](structured_data_schema_clusters.md), [verifier_execution_isolation](verifier_execution_isolation_clusters.md) |
| contrastive_solver-software-engineering_20260613_152233_009 | [anti_cheat_robustness](anti_cheat_robustness_clusters.md), [verifier_execution_isolation](verifier_execution_isolation_clusters.md) |
| contrastive_solver-software-engineering_20260613_152233_011 | [verifiable](verifiable_clusters.md), [functional_verification](functional_verification_clusters.md), [deterministic_reproducible](deterministic_reproducible_clusters.md), [essential_difficulty](essential_difficulty_clusters.md), [test_instruction_alignment](test_instruction_alignment_clusters.md), [reviewable](reviewable_clusters.md), [instruction_concision](instruction_concision_clusters.md), [verifier_execution_isolation](verifier_execution_isolation_clusters.md) |
| contrastive_solver-software-engineering_20260613_152233_013 | [anti_cheat_robustness](anti_cheat_robustness_clusters.md), [test_instruction_alignment](test_instruction_alignment_clusters.md), [structured_data_schema](structured_data_schema_clusters.md), [verifier_execution_isolation](verifier_execution_isolation_clusters.md) |
| contrastive_solver-software-engineering_20260613_152233_020 | [test_instruction_alignment](test_instruction_alignment_clusters.md), [verifier_execution_isolation](verifier_execution_isolation_clusters.md) |
| contrastive_solver-software-engineering_20260613_152233_023 | [anti_cheat_robustness](anti_cheat_robustness_clusters.md), [test_instruction_alignment](test_instruction_alignment_clusters.md), [agentic](agentic_clusters.md), [instruction_concision](instruction_concision_clusters.md), [resource_configuration](resource_configuration_clusters.md), [verifier_execution_isolation](verifier_execution_isolation_clusters.md), [do_not_modify_enforced](do_not_modify_enforced_clusters.md) |
| contrastive_solver-software-engineering_20260614_141608_009 | [reviewable](reviewable_clusters.md), [verifier_execution_isolation](verifier_execution_isolation_clusters.md) |
| contrastive_solver-software-engineering_20260614_141608_014 | [outcome_verified](outcome_verified_clusters.md), [anti_cheat_robustness](anti_cheat_robustness_clusters.md), [test_instruction_alignment](test_instruction_alignment_clusters.md), [reviewable](reviewable_clusters.md), [verifier_execution_isolation](verifier_execution_isolation_clusters.md) |
| multi_solver-software-engineering_20260413_174325_025 | [verifiable](verifiable_clusters.md), [anti_cheat_robustness](anti_cheat_robustness_clusters.md), [deterministic_reproducible](deterministic_reproducible_clusters.md), [essential_difficulty](essential_difficulty_clusters.md), [test_instruction_alignment](test_instruction_alignment_clusters.md), [agentic](agentic_clusters.md), [reviewable](reviewable_clusters.md), [instruction_concision](instruction_concision_clusters.md), [verifier_execution_isolation](verifier_execution_isolation_clusters.md) |
| multi_solver-software-engineering_20260414_080620_014 | [verifiable](verifiable_clusters.md), [outcome_verified](outcome_verified_clusters.md), [anti_cheat_robustness](anti_cheat_robustness_clusters.md), [functional_verification](functional_verification_clusters.md), [test_instruction_alignment](test_instruction_alignment_clusters.md), [agentic](agentic_clusters.md), [reviewable](reviewable_clusters.md), [instruction_concision](instruction_concision_clusters.md), [verifier_execution_isolation](verifier_execution_isolation_clusters.md) |
| multi_solver-software-engineering_20260414_080620_021 | [verifiable](verifiable_clusters.md), [outcome_verified](outcome_verified_clusters.md), [anti_cheat_robustness](anti_cheat_robustness_clusters.md), [test_instruction_alignment](test_instruction_alignment_clusters.md), [agentic](agentic_clusters.md), [reviewable](reviewable_clusters.md), [instruction_concision](instruction_concision_clusters.md), [verifier_execution_isolation](verifier_execution_isolation_clusters.md) |
| multi_solver-software-engineering_20260414_091921_001 | [verifiable](verifiable_clusters.md), [anti_cheat_robustness](anti_cheat_robustness_clusters.md), [functional_verification](functional_verification_clusters.md), [deterministic_reproducible](deterministic_reproducible_clusters.md), [test_instruction_alignment](test_instruction_alignment_clusters.md), [reviewable](reviewable_clusters.md), [no_extraneous_files](no_extraneous_files_clusters.md) |
| multi_solver-software-engineering_20260414_091921_008 | [verifiable](verifiable_clusters.md), [anti_cheat_robustness](anti_cheat_robustness_clusters.md), [test_instruction_alignment](test_instruction_alignment_clusters.md), [reviewable](reviewable_clusters.md), [verifier_execution_isolation](verifier_execution_isolation_clusters.md), [do_not_modify_enforced](do_not_modify_enforced_clusters.md) |
| multi_solver-software-engineering_20260414_091921_014 | [verifiable](verifiable_clusters.md), [anti_cheat_robustness](anti_cheat_robustness_clusters.md), [functional_verification](functional_verification_clusters.md), [deterministic_reproducible](deterministic_reproducible_clusters.md), [essential_difficulty](essential_difficulty_clusters.md), [test_instruction_alignment](test_instruction_alignment_clusters.md), [reviewable](reviewable_clusters.md), [instruction_concision](instruction_concision_clusters.md), [resource_configuration](resource_configuration_clusters.md), [verifier_execution_isolation](verifier_execution_isolation_clusters.md) |
| multi_solver-software-engineering_20260414_091921_017 | [verifiable](verifiable_clusters.md), [anti_cheat_robustness](anti_cheat_robustness_clusters.md), [test_instruction_alignment](test_instruction_alignment_clusters.md), [reviewable](reviewable_clusters.md), [verifier_execution_isolation](verifier_execution_isolation_clusters.md), [do_not_modify_enforced](do_not_modify_enforced_clusters.md) |
| multi_solver-software-engineering_20260418_190236_012 | [verifiable](verifiable_clusters.md), [outcome_verified](outcome_verified_clusters.md), [anti_cheat_robustness](anti_cheat_robustness_clusters.md), [functional_verification](functional_verification_clusters.md), [deterministic_reproducible](deterministic_reproducible_clusters.md), [essential_difficulty](essential_difficulty_clusters.md), [test_instruction_alignment](test_instruction_alignment_clusters.md), [reviewable](reviewable_clusters.md), [resource_configuration](resource_configuration_clusters.md), [verifier_execution_isolation](verifier_execution_isolation_clusters.md) |
| multi_solver-software-engineering_20260418_190236_018 | [verifiable](verifiable_clusters.md), [anti_cheat_robustness](anti_cheat_robustness_clusters.md), [test_instruction_alignment](test_instruction_alignment_clusters.md), [verifier_execution_isolation](verifier_execution_isolation_clusters.md) |
| multi_solver-software-engineering_20260418_190236_023 | [verifiable](verifiable_clusters.md), [outcome_verified](outcome_verified_clusters.md), [essential_difficulty](essential_difficulty_clusters.md), [test_instruction_alignment](test_instruction_alignment_clusters.md), [reviewable](reviewable_clusters.md), [instruction_concision](instruction_concision_clusters.md), [verifier_execution_isolation](verifier_execution_isolation_clusters.md) |
| multi_solver-software-engineering_20260418_190236_024 | [verifiable](verifiable_clusters.md), [anti_cheat_robustness](anti_cheat_robustness_clusters.md), [deterministic_reproducible](deterministic_reproducible_clusters.md), [test_instruction_alignment](test_instruction_alignment_clusters.md), [reviewable](reviewable_clusters.md), [instruction_concision](instruction_concision_clusters.md), [verifier_execution_isolation](verifier_execution_isolation_clusters.md) |
| multi_solver-software-engineering_20260419_023954_020 | [verifiable](verifiable_clusters.md), [anti_cheat_robustness](anti_cheat_robustness_clusters.md), [deterministic_reproducible](deterministic_reproducible_clusters.md), [test_instruction_alignment](test_instruction_alignment_clusters.md), [instruction_concision](instruction_concision_clusters.md), [typos](typos_clusters.md), [verifier_execution_isolation](verifier_execution_isolation_clusters.md) |
| multi_solver-software-engineering_20260419_023954_022 | [verifiable](verifiable_clusters.md), [test_instruction_alignment](test_instruction_alignment_clusters.md), [resource_configuration](resource_configuration_clusters.md), [verifier_execution_isolation](verifier_execution_isolation_clusters.md) |
| multi_solver-software-engineering_20260419_023954_023 | [verifiable](verifiable_clusters.md), [reviewable](reviewable_clusters.md), [instruction_concision](instruction_concision_clusters.md), [verifier_execution_isolation](verifier_execution_isolation_clusters.md) |

</details>

<a id="system-administration"></a>
## system-administration

**19 tasks reviewed.**

| Criterion | Failed | Passed | N/A | Fail / applicable | Failure rate |
| --- | ---: | ---: | ---: | ---: | ---: |
| [test_instruction_alignment](test_instruction_alignment_clusters.md) | 16 | 3 | 0 | 16/19 | 84.2% |
| [reviewable](reviewable_clusters.md) | 15 | 4 | 0 | 15/19 | 78.9% |
| [anti_cheat_robustness](anti_cheat_robustness_clusters.md) | 14 | 5 | 0 | 14/19 | 73.7% |
| [verifiable](verifiable_clusters.md) | 13 | 6 | 0 | 13/19 | 68.4% |
| [instruction_concision](instruction_concision_clusters.md) | 10 | 9 | 0 | 10/19 | 52.6% |
| [outcome_verified](outcome_verified_clusters.md) | 10 | 9 | 0 | 10/19 | 52.6% |
| [functional_verification](functional_verification_clusters.md) | 7 | 12 | 0 | 7/19 | 36.8% |
| [verifier_execution_isolation](verifier_execution_isolation_clusters.md) | 7 | 0 | 12 | 7/7 | 100.0% |
| [essential_difficulty](essential_difficulty_clusters.md) | 6 | 13 | 0 | 6/19 | 31.6% |
| [deterministic_reproducible](deterministic_reproducible_clusters.md) | 5 | 14 | 0 | 5/19 | 26.3% |
| [resource_configuration](resource_configuration_clusters.md) | 5 | 14 | 0 | 5/19 | 26.3% |
| [agentic](agentic_clusters.md) | 1 | 18 | 0 | 1/19 | 5.3% |
| [do_not_modify_enforced](do_not_modify_enforced_clusters.md) | 1 | 0 | 18 | 1/1 | 100.0% |
| [interesting](interesting_clusters.md) | 1 | 18 | 0 | 1/19 | 5.3% |
| [no_extraneous_files](no_extraneous_files_clusters.md) | 1 | 18 | 0 | 1/19 | 5.3% |
| [structured_data_schema](structured_data_schema_clusters.md) | 1 | 3 | 15 | 1/4 | 25.0% |
| [artifact_efficiency](artifact_efficiency_clusters.md) | 0 | 0 | 19 | 0/0 | — |
| [ctrf_reporting](ctrf_reporting_clusters.md) | 0 | 19 | 0 | 0/19 | 0.0% |
| [task_security](task_security_clusters.md) | 0 | 19 | 0 | 0/19 | 0.0% |
| [typos](typos_clusters.md) | 0 | 19 | 0 | 0/19 | 0.0% |

<details>
<summary>Show tasks in this category and their failed criteria</summary>

| Task | Failed criteria |
| --- | --- |
| contrastive_solver-system-administration_20260430_081156_009 | [verifiable](verifiable_clusters.md), [verifier_execution_isolation](verifier_execution_isolation_clusters.md) |
| contrastive_solver-system-administration_20260501_000112_002 | [verifiable](verifiable_clusters.md), [anti_cheat_robustness](anti_cheat_robustness_clusters.md), [functional_verification](functional_verification_clusters.md), [deterministic_reproducible](deterministic_reproducible_clusters.md), [essential_difficulty](essential_difficulty_clusters.md), [test_instruction_alignment](test_instruction_alignment_clusters.md), [reviewable](reviewable_clusters.md), [instruction_concision](instruction_concision_clusters.md), [verifier_execution_isolation](verifier_execution_isolation_clusters.md) |
| contrastive_solver-system-administration_20260501_051538_005 | [verifiable](verifiable_clusters.md), [outcome_verified](outcome_verified_clusters.md), [anti_cheat_robustness](anti_cheat_robustness_clusters.md), [deterministic_reproducible](deterministic_reproducible_clusters.md), [test_instruction_alignment](test_instruction_alignment_clusters.md), [reviewable](reviewable_clusters.md), [instruction_concision](instruction_concision_clusters.md), [resource_configuration](resource_configuration_clusters.md), [do_not_modify_enforced](do_not_modify_enforced_clusters.md) |
| contrastive_solver-system-administration_20260501_051538_006 | [verifiable](verifiable_clusters.md), [outcome_verified](outcome_verified_clusters.md), [functional_verification](functional_verification_clusters.md), [essential_difficulty](essential_difficulty_clusters.md), [test_instruction_alignment](test_instruction_alignment_clusters.md), [instruction_concision](instruction_concision_clusters.md) |
| contrastive_solver-system-administration_20260501_172213_002 | [verifiable](verifiable_clusters.md), [anti_cheat_robustness](anti_cheat_robustness_clusters.md), [reviewable](reviewable_clusters.md) |
| contrastive_solver-system-administration_20260505_093950_006 | [verifiable](verifiable_clusters.md), [outcome_verified](outcome_verified_clusters.md), [essential_difficulty](essential_difficulty_clusters.md), [test_instruction_alignment](test_instruction_alignment_clusters.md), [reviewable](reviewable_clusters.md) |
| contrastive_solver-system-administration_20260507_093408_006 | [verifiable](verifiable_clusters.md), [outcome_verified](outcome_verified_clusters.md), [functional_verification](functional_verification_clusters.md), [test_instruction_alignment](test_instruction_alignment_clusters.md), [reviewable](reviewable_clusters.md), [instruction_concision](instruction_concision_clusters.md), [verifier_execution_isolation](verifier_execution_isolation_clusters.md) |
| contrastive_solver-system-administration_20260603_181444_005 | [verifiable](verifiable_clusters.md), [anti_cheat_robustness](anti_cheat_robustness_clusters.md), [test_instruction_alignment](test_instruction_alignment_clusters.md), [reviewable](reviewable_clusters.md), [resource_configuration](resource_configuration_clusters.md) |
| contrastive_solver-system-administration_20260604_002443_005 | [verifiable](verifiable_clusters.md), [anti_cheat_robustness](anti_cheat_robustness_clusters.md), [test_instruction_alignment](test_instruction_alignment_clusters.md), [reviewable](reviewable_clusters.md), [instruction_concision](instruction_concision_clusters.md), [resource_configuration](resource_configuration_clusters.md) |
| contrastive_solver-system-administration_20260604_002443_008 | [interesting](interesting_clusters.md), [outcome_verified](outcome_verified_clusters.md), [anti_cheat_robustness](anti_cheat_robustness_clusters.md), [essential_difficulty](essential_difficulty_clusters.md), [test_instruction_alignment](test_instruction_alignment_clusters.md), [agentic](agentic_clusters.md), [instruction_concision](instruction_concision_clusters.md) |
| contrastive_solver-system-administration_20260604_003113_006 | [outcome_verified](outcome_verified_clusters.md), [anti_cheat_robustness](anti_cheat_robustness_clusters.md), [functional_verification](functional_verification_clusters.md), [test_instruction_alignment](test_instruction_alignment_clusters.md), [reviewable](reviewable_clusters.md), [instruction_concision](instruction_concision_clusters.md) |
| contrastive_solver-system-administration_20260604_003814_002 | [anti_cheat_robustness](anti_cheat_robustness_clusters.md), [functional_verification](functional_verification_clusters.md) |
| contrastive_solver-system-administration_20260604_003814_008 | [verifiable](verifiable_clusters.md), [outcome_verified](outcome_verified_clusters.md), [anti_cheat_robustness](anti_cheat_robustness_clusters.md), [deterministic_reproducible](deterministic_reproducible_clusters.md), [test_instruction_alignment](test_instruction_alignment_clusters.md), [reviewable](reviewable_clusters.md), [verifier_execution_isolation](verifier_execution_isolation_clusters.md) |
| contrastive_solver-system-administration_20260604_003814_009 | [anti_cheat_robustness](anti_cheat_robustness_clusters.md), [test_instruction_alignment](test_instruction_alignment_clusters.md), [reviewable](reviewable_clusters.md), [resource_configuration](resource_configuration_clusters.md), [verifier_execution_isolation](verifier_execution_isolation_clusters.md) |
| contrastive_solver-system-administration_20260616_094815_002 | [test_instruction_alignment](test_instruction_alignment_clusters.md), [reviewable](reviewable_clusters.md), [verifier_execution_isolation](verifier_execution_isolation_clusters.md) |
| contrastive_solver-system-administration_20260616_094815_006 | [anti_cheat_robustness](anti_cheat_robustness_clusters.md), [test_instruction_alignment](test_instruction_alignment_clusters.md), [reviewable](reviewable_clusters.md), [instruction_concision](instruction_concision_clusters.md) |
| contrastive_solver-system-administration_20260617_051646_002 | [verifiable](verifiable_clusters.md), [outcome_verified](outcome_verified_clusters.md), [anti_cheat_robustness](anti_cheat_robustness_clusters.md), [deterministic_reproducible](deterministic_reproducible_clusters.md), [test_instruction_alignment](test_instruction_alignment_clusters.md), [reviewable](reviewable_clusters.md), [instruction_concision](instruction_concision_clusters.md), [resource_configuration](resource_configuration_clusters.md), [no_extraneous_files](no_extraneous_files_clusters.md), [verifier_execution_isolation](verifier_execution_isolation_clusters.md) |
| contrastive_solver-system-administration_20260617_051646_006 | [verifiable](verifiable_clusters.md), [outcome_verified](outcome_verified_clusters.md), [anti_cheat_robustness](anti_cheat_robustness_clusters.md), [functional_verification](functional_verification_clusters.md), [deterministic_reproducible](deterministic_reproducible_clusters.md), [essential_difficulty](essential_difficulty_clusters.md), [test_instruction_alignment](test_instruction_alignment_clusters.md), [reviewable](reviewable_clusters.md), [instruction_concision](instruction_concision_clusters.md), [structured_data_schema](structured_data_schema_clusters.md) |
| contrastive_solver-system-administration_20260617_104707_006 | [verifiable](verifiable_clusters.md), [outcome_verified](outcome_verified_clusters.md), [anti_cheat_robustness](anti_cheat_robustness_clusters.md), [functional_verification](functional_verification_clusters.md), [essential_difficulty](essential_difficulty_clusters.md), [test_instruction_alignment](test_instruction_alignment_clusters.md), [reviewable](reviewable_clusters.md) |

</details>

<a id="video-processing"></a>
## video-processing

**2 tasks reviewed.**

| Criterion | Failed | Passed | N/A | Fail / applicable | Failure rate |
| --- | ---: | ---: | ---: | ---: | ---: |
| [deterministic_reproducible](deterministic_reproducible_clusters.md) | 2 | 0 | 0 | 2/2 | 100.0% |
| [instruction_concision](instruction_concision_clusters.md) | 2 | 0 | 0 | 2/2 | 100.0% |
| [verifier_execution_isolation](verifier_execution_isolation_clusters.md) | 2 | 0 | 0 | 2/2 | 100.0% |
| [agentic](agentic_clusters.md) | 1 | 1 | 0 | 1/2 | 50.0% |
| [essential_difficulty](essential_difficulty_clusters.md) | 1 | 1 | 0 | 1/2 | 50.0% |
| [interesting](interesting_clusters.md) | 1 | 1 | 0 | 1/2 | 50.0% |
| [resource_configuration](resource_configuration_clusters.md) | 1 | 1 | 0 | 1/2 | 50.0% |
| [test_instruction_alignment](test_instruction_alignment_clusters.md) | 1 | 1 | 0 | 1/2 | 50.0% |
| [verifiable](verifiable_clusters.md) | 1 | 1 | 0 | 1/2 | 50.0% |
| [anti_cheat_robustness](anti_cheat_robustness_clusters.md) | 0 | 2 | 0 | 0/2 | 0.0% |
| [artifact_efficiency](artifact_efficiency_clusters.md) | 0 | 0 | 2 | 0/0 | — |
| [ctrf_reporting](ctrf_reporting_clusters.md) | 0 | 2 | 0 | 0/2 | 0.0% |
| [do_not_modify_enforced](do_not_modify_enforced_clusters.md) | 0 | 1 | 1 | 0/1 | 0.0% |
| [functional_verification](functional_verification_clusters.md) | 0 | 2 | 0 | 0/2 | 0.0% |
| [no_extraneous_files](no_extraneous_files_clusters.md) | 0 | 2 | 0 | 0/2 | 0.0% |
| [outcome_verified](outcome_verified_clusters.md) | 0 | 2 | 0 | 0/2 | 0.0% |
| [reviewable](reviewable_clusters.md) | 0 | 2 | 0 | 0/2 | 0.0% |
| [structured_data_schema](structured_data_schema_clusters.md) | 0 | 2 | 0 | 0/2 | 0.0% |
| [task_security](task_security_clusters.md) | 0 | 2 | 0 | 0/2 | 0.0% |
| [typos](typos_clusters.md) | 0 | 2 | 0 | 0/2 | 0.0% |

<details>
<summary>Show tasks in this category and their failed criteria</summary>

| Task | Failed criteria |
| --- | --- |
| contrastive_solver-video-processing_20260425_072438_001 | [verifiable](verifiable_clusters.md), [deterministic_reproducible](deterministic_reproducible_clusters.md), [instruction_concision](instruction_concision_clusters.md), [verifier_execution_isolation](verifier_execution_isolation_clusters.md) |
| contrastive_solver-video-processing_20260503_030115_001 | [interesting](interesting_clusters.md), [deterministic_reproducible](deterministic_reproducible_clusters.md), [essential_difficulty](essential_difficulty_clusters.md), [test_instruction_alignment](test_instruction_alignment_clusters.md), [agentic](agentic_clusters.md), [instruction_concision](instruction_concision_clusters.md), [resource_configuration](resource_configuration_clusters.md), [verifier_execution_isolation](verifier_execution_isolation_clusters.md) |

</details>
