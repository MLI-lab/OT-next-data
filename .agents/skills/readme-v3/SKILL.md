---
name: readme-v3
description: Write or revise v3 dataset release READMEs and trace-statistics overviews focused on the new evaluation, without repeating v2 results.
---

# V3 dataset README

Use the evidence and count-first conventions in
[`v2_readme`](../v2_readme/SKILL.md), with the presentation scope below.
This records the user's presentation preference from 2026-10-20 and incremental
release preference from 2026-10-26.

## Report the new evaluation

Use the five-section format in `v2_readme`: Dataset stats, Evaluation settings,
Results, Files and usage, Trace statistics. No arrows, separate provenance
section, provenance/reproduction links, raw hashes or operational histories.

- First stats bullet: how many v2 tasks had no successful attempt, how many
  were evaluated for v3, how many are newly kept, and how many remain without
  success. Keep previously unattempted tasks separate if they enter the cohort.
  Do not lead with the original source count or cumulative v2+v3 total.
- Second bullet: full new model ID, thinking mode, attempts per task and pass@k
  budget. Further bullets give recorded attempts and saved conversations.
- Include **Network access:** in Dataset stats for this new evaluation, following
  `v2_readme`: offline, online, mixed, or unverified based on effective execution
  evidence. Separate offline attempts and successes from online retry coverage
  and recoveries; do not inherit a network label from v2 or infer it from caches.
- State that `tasks.parquet` contains only newly kept tasks absent from v2.
  Earlier kept tasks remain separately in v2. Put combined counts in Files and
  usage, if useful, rather than opening stats.
- The settings table has one row per new model/configuration with all recorded
  hyperparameters and numeric token budgets. Put time limits and the concise
  intended-versus-enforced output-cap caveat below it.
- Results cover all tasks in the new evaluation. For two attempts, show rows
  `0/2`, `1/2`, `2/2` and a plot with pass@1 and pass@2. For other budgets,
  follow the common powers-of-two plot convention through the available k.
  Do not reproduce v2 scores, success distributions or runtime statistics.
- When the same model has thinking-off and thinking-on evaluations, keep modes
  distinct in opening stats, settings rows, success distributions, pass@k plots
  and trace statistics. Report cohort counts explicitly. For SETA v3, show
  thinking off: 221/434 solved; thinking on: 38/213 earlier failures solved
  (including one retry), plus 238/427 previously untested tasks. These are this
  release's measured counts, not defaults. Fixed pass@2 results use 37/213 main
  successes; disclose the additional retry success separately and never count
  it as a two-attempt result. Preserve additional recheck cohorts separately.
- Apply the same new-evaluation scope to `trace_stats/README.md`, its settings,
  measurement columns and plots. Do not mix historical attempts into v3 stats.
- Keep important unresolved validation, networking, payload or evidence caveats
  beside the affected content. Do not claim checks were rerun without evidence.

## Preserve the release data

V3 task files contain only newly solved tasks absent from v2. Verify disjoint
task IDs and preserve the evaluated payloads and success-count columns. Explain
how to combine v2 and v3 for the full solved set, allowing for differing columns
and preserving each source's image manifest. Describe v3 as harder relative to
the earlier evaluation, not intrinsically harder for every model.

Keep all attempts from the new evaluation, including unsuccessful attempts and
unsolved tasks, with explicit task-membership flags. Historical traces stay in
v2 rather than being duplicated in v3. For an authorized cumulative-to-incremental
migration, verify that historical conversations and result records are preserved
in v2 before removing duplicate v3 copies. Update task files, metadata, trace
manifests, reproduction scripts and READMEs together. Preserve pinned provenance
and access to the earlier cumulative revision. A README-only request does not
authorize a data migration.

For example, BugsInPy v3 keeps 406 newly solved tasks; combining it with the
65 v2 tasks gives 471 unique tasks. Its new evaluation covers 424 tasks and
848 attempts, with 18 tasks at 0/2, 41 at 1/2, and 365 at 2/2. These numbers
are an example, not defaults for other releases.

## Required evaluation settings

Apply the required evaluation-settings and intended-versus-enforced budget
rules in `v2_readme` to every v3 README and trace-statistics README. The settings
table covers the new evaluation only; earlier runs remain documented in v2. Always
state context and per-call output limits numerically when evidence exists, even
when sampling follows model-card defaults. Document historical forwarding bugs
without presenting the current fix as having changed already-recorded results.
