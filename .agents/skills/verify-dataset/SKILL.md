---
name: verify-dataset
description: Validate patched tasks with the shared pipeline before teacher generation.
---

# Verify a dataset

Follow `validation/README.md`. Run the relevant repository tests, then prepare a
contract for stages 1, 3, 4 and 5 on a diverse ten-task pilot. Fix dataset issues
in `data/<dataset>/patch.py` and rerun the pilot before validating the full set.

Check that reference solutions score 1 and no-op agents score 0. Inspect verifier
failures; a successful process exit alone is not evidence that a task is correct.
Use `validation/checks/dataset_checks.py` for reproduction, image counts and isolation.

Generate teachers through stages 6 and 7 only after the task checks pass. Inspect
recorded trajectories, missing rewards, timeouts and infrastructure failures when
interpreting pass@k. Use stages 2 and 8 for rubric-based LLM review.

Record dataset source revisions and patch provenance. See
`data/INVENTORY.md#reporting-patches-in-the-datasource-pr` for output and publication conventions.
