# Custom task-quality rubric

Our four criteria combine checks from [Terminal-Bench](../rubric_terminal_bench/upstream/task-implementation.toml), [Harbor](../rubric_harbor/upstream/default-rubric.toml), and [River, Appendix C.1](https://arxiv.org/pdf/2608.22631#page=26).

| Custom criterion | Sources |
| --- | --- |
| `test_coverage` | Terminal-Bench: `test_instruction_alignment`, `do_not_modify_enforced`; Harbor: `behavior_in_tests`; River: `VERIFIER-TOO-WEAK` |
| `instruction_verifier_alignment` | Terminal-Bench: `test_instruction_alignment`, `structured_data_schema`, `outcome_verified`; Harbor: `behavior_in_tests`, `file_reference_mentioned`; River: `INSTR-VERIFIER-MISMATCH` |
| `task_quality` | River: `INSTR-AMBIGUOUS`, `TASK-TRIVIAL`; Terminal-Bench: `agentic`, `solvable`, essential difficulty, `verifiable` |
| `anti_cheat_robustness` | Terminal-Bench: `anti_cheat_robustness`, `functional_verification`, `solution_quality`, `hardcoded_solution`; Harbor: `anti_cheating_measures`; River: `ANSWER-LEAK`, `VERIFIER-TOO-WEAK` |

The examples in `rubric.toml` draw on true-positive and false-positive findings from Opus 5.5 pilot reviews of [SETA](../../../data/seta/stage2-review-199/README.md) and [CalibForge](../../../data/calibforge/stage2-review-171/README.md) using the Terminal-Bench rubric.

`classification.toml` uses the guidance before the examples, retains the anti-cheat note, and adds River's exact `CLEAN` and `OTHER` definitions.
