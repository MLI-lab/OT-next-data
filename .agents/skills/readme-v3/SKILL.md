---
name: readme-v3
description: Write or revise v3 dataset release READMEs and trace-statistics overviews focused on the new evaluation, without repeating v2 results.
---

# V3 dataset README

Use the evidence and count-first conventions in
[`v2_readme`](../v2_readme/SKILL.md), with the presentation scope below.
This records the user's v3 preference from 2026-10-20.

## Report the new evaluation

- Lead with total kept tasks, the number carried forward from v2, newly solved
  tasks, and remaining excluded or pending tasks. State the new evaluation's
  task, attempt, and saved-trajectory counts.
- Evaluation tables, success distributions, runtime tables, and highlighted
  trace statistics cover only the new v3 evaluation. Link to the v2 README
  for earlier results instead of reproducing its tables.
- For a new pass@2 run, show one success-distribution table with 0/2, 1/2,
  and 2/2. Do not repeat the older 0/16 through 16/16 distribution or old
  models' pass rates, token statistics, and runtime rows.
- Identify the new model, evaluated subset, attempt budget, and relevant
  sampling and runtime settings. Do not use the full cumulative kept-task
  count as the denominator for a model evaluated only on previously
  unsolved tasks.
- Apply the same scope to `trace_stats/README.md`: show the new evaluation's
  model columns and plots. If detailed data includes earlier evaluations,
  label that scope and link to it without repeating historical statistics.
- A short carry-forward sentence is sufficient. Do not add a separate
  explanation of inherited validation versus new stages by default.
  Preserve material unresolved limitations already documented, and do not
  claim that checks were rerun without evidence.

## Preserve the release data

The README scope does not change dataset membership. For a cumulative v3
release, preserve every v2 task and add the newly solved tasks, deduplicated
by task ID. Preserve existing success-count columns, traces, and provenance.
An older task not evaluated by the new model has zero new attempts and a
null new success count, not a measured failure.

Keep new and historical trace membership identifiable. Changing the README
does not authorize deleting old artifacts or silently relabeling aggregate
statistics as new-run statistics.

For example, BugsInPy v3 keeps 65 v2 tasks plus 406 newly solved tasks, for
471 total. Its new evaluation covers 424 tasks and 848 attempts; the new
success distribution is 18 with 0/2, 41 with 1/2, and 365 with 2/2. These
numbers are an example, not defaults for other releases.
