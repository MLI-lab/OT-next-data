**Updates from today’s data breakout meeting**

**V1**

- 42,166 patched tasks across 8 data sources + cached Apptainer images, are on https://huggingface.co/datasets/FWeindel/validated-tasks/tree/main.
- V1 includes the following checks and repairs: Apptainer/Marin Harbor compatibility, shared images where possible, setup repairs, no-op checks, reference checks where solutions exist, and rule-based repairs/exclusions.
- Each README lists original, kept and patched task counts.

**V2**

- 7,629 tasks across the same 8 data sources, filtered using pass@16/8/4: keep tasks solved at least once by any evaluated model. Evaluated models: Qwen3-30B-A3B-Instruct-2507, Qwen3-30B-A3B (Thinking), and Qwen3-Coder-30B-A3B-Instruct, depending on the dataset.

**Findings so far**

- Getting the environments to run with Apptainer required many repairs and is slow because some builds take a long time.
- Most rule checks catch minor issues. The most useful, test-file-references, flags required paths missing from instructions. We repaired some cases and archived others for now.

**LLM-as-judge filtering**

- Tried Terminal-Bench/Harbor rubrics with Opus 5.5: 199 completed reviews for SETA and 171 for CalibForge. No reviewed task passed every criterion.

SETA judgments and explanations (JSON): https://github.com/MLI-lab/OT-next-data/blob/main/data/seta/stage2-review-199/reviews.json
CalibForge judgments and explanations (JSON): https://github.com/MLI-lab/OT-next-data/blob/main/data/calibforge/stage2-review-171/reviews.json

- The rubrics need more refinement. Some checks could become rules or pre-filters to make review scale better, as Opus 5.5 costs roughly $0.40/task.
- We discussed prioritizing pass@k results before refining the rubrics, as filtering based on trajectories seems easier than judging task definitions alone.

**Next steps / V3**

We started auditing failed trajectories, but this does not scale well. Instead, next try a stronger model on unsolved tasks, then review the remaining failed trajectories to distinguish task defects from agent failures and check successful trajectories for reward hacking.
