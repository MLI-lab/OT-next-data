# Goal

Test whether progressively fixing and filtering RL environments improves downstream RL performance enough to outweigh having fewer training tasks. Start with SETA.

- **A, functional:** Apptainer compatibility, reproducible build/setup including dependency pinning, reference reward 1 and no-op reward 0 (stages 3, 4 and 5). Keep all dependency pins, including those originally suggested by static checks.
- **B, task quality:** A plus fixes/exclusions from static checks and LLM task review (stages 1–2).
- **C, trajectory quality:** B plus fixes/exclusions informed by teacher trials, trace metrics and LLM trajectory review (stages 6–8).

Sample one fixed pool of 1,000 tasks from A before quality filtering to limit judge costs. Derive B and C from that pool without replacing discarded tasks. Compare RL using the same starting checkpoint, training recipe, compute budget and held-out evaluation; dataset sizes may differ.

First stage-2 experiment: sample 1,000 tasks after stage 1 with seed 42, review with Claude Opus 5.5, and retain matching task IDs from A for comparison. This tests additional filtering within stage-1 survivors; it does not measure the effect of stage-1 exclusions.

Record each fix/exclusion and its reason so A does not inherit quality-only edits. Reuse validation or trajectories only when the task version and execution settings match. Current teachers are Qwen3-30B-A3B-Instruct-2507 and Qwen3-30B-A3B; a switch to Qwen3.5 is not yet decided.

Dependency pinning is build preparation, not a task-quality intervention. Use the same pinned dependencies across A, B and C, and validate the exact payloads that will be published. The existing unpinned baseline must be updated before it represents this revised definition.
