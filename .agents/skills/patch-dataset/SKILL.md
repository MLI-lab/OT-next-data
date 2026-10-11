---
name: patch-dataset
description: Repair an agent-task dataset (context, verifier, oracle, solvability filter) and produce Parquets ready for validation.
---

# Patch a dataset

Each dataset has its patcher in `data/<dataset>/` (usually `patch.py`) and, where
the repair needs explaining, a `README.md` saying what the patch changes inside
a task. Run the patcher on the pinned upstream source; see its `--help`.

## Rules

1. **Verifier, solvability filter and their audit stay in one module.** A filter
   that disagrees with the grader silently keeps unsolvable tasks, and a separate
   audit script drifts from the filter it audits.
2. **The filter asks exactly what the grader asks.** Derive what it checks from
   the *graded* text by calling the verifier's own functions, never by a second
   regex.
3. **Never modify the upstream checkout** (`external/`). Patches live here;
   upstream stays diffable.
4. **Report, don't silently drop.** The patcher calls
   `data.utils.patch_reporting.write_patch_report`, which writes the archive Parquet
   (original payloads of dropped tasks with category and reason) and the
   manifest beside the patched Parquet. See `data/INVENTORY.md#reporting-patches-in-the-datasource-pr`.
5. **Keep task IDs stable** across versions, with gaps where tasks were dropped;
   the report helper rejects additions and renames.

## After patching

```bash
python -m pytest tests -q
python validation/checks/dataset_checks.py reproduce --parquet NEW.parquet --reference PUBLISHED.parquet
```

Check the pytest output for `failed`, not only for `passed`. The reproduce check
applies once a version is published. Then run the `verify-dataset` skill; a
patched Parquet that has not passed stages 1, 3, 4 and 5 is not ready for
teacher generation.

## Publishing

`validation/publishing/publish.py` opens the pull request on the validated-tasks dataset
from a run's reports, with the archive and manifest attached
(`validation/README.md`, "Publishing results"). Never add assistant
attribution to a commit, PR title or description.
