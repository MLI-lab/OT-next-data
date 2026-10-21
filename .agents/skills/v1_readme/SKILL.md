---
name: v1_readme
description: Write or revise v1 datasource READMEs and matching Hugging Face PR descriptions, with dataset stats, validation changes, archive reasons and measured timings.
---

# V1 dataset README and PR descriptions

Use `validation/publishing/publish.py::description` for the automatic PR text.
Apply these rules to manually assembled descriptions too. Update the template
when a recurring writing preference can be implemented there; a skill alone
does not change generated output.

## Agreed writing rules

- Keep the whole README brief, concise and natural. Use plain language and
  avoid repeated caveats or implementation detail that belongs in the evidence.
- Lead with the datasource and concrete kept/archived counts.
- Always use “kept”, never “retained”, for tasks included in a dataset.
- For manually approved no-op checker false positives, prefer:
  “Manually approved X false positives from the no-op checker, which could not
  confirm verifier execution; the corresponding reference solutions passed.”
  Use this wording only when the reviewed evidence supports it. A passing
  reference alone does not prove the no-op verifier completed successfully.
  Keep individual parser errors and missing-score details in the linked review
  evidence unless they affect the reader's interpretation of the summary.
  Preserve missing numeric scores in the evidence; manual approval does not
  turn them into measured reward 0.
- Report validation outcomes within the per-stage breakdown only. Do not add
  a separate stage overview/results table or a “Why tasks are kept” section.
- Describe repairs and archive reasons in plain language, with relevant counts.
- In per-stage README breakdowns, group tasks by change or archive reason and
  lead each explanation with the count: “Changed X tasks: …” or “Archived X
  tasks because …”. Omit individual task names; keep those in the evidence.
  Explain the concrete behavior repaired, not just the component name:
  “Changed 1 task so a missing line break no longer hides a passing test result”
  is clearer than “fixed parser handling”.
  Group resource changes by measured old/new limits, for example “Changed X
  tasks from 4 to 8 GiB”. Report final counts after results are reconciled;
  describe ongoing retries separately without counting them as successful
  repairs. Use actual checker-supported sizes, not illustrative intermediate
  values. Avoid repeating that pending tasks are not final exclusions.
- Keep internal patch version labels, run IDs, contract hashes, payload-hash
  audits and instruction-suffix normalization details in the linked run JSON
  or manifests. Do not explain those bookkeeping mechanisms in PR prose.
- If results combine a full run and reruns, one sentence is sufficient:
  “Includes the full validation run and reruns of repaired tasks.” Match the
  wording to what actually ran; do not imply a fresh full run.
- Do not add boilerplate listing stages that were not acceptance gates or
  disclaiming unmeasured teacher pass@k and LLM-rubric results. Label the stages
  actually reported and describe any skipped tasks that affect the counts.
- Preserve material unresolved limitations and evidence. Brevity changes the
  presentation, not validation requirements, task outcomes or recorded provenance.

## Datasource README

Use this order for a v1 README:

1. **Dataset stats**, immediately below the title: original, kept, changed,
   archived and (if applicable) pending task counts. For Docker images, say only
   “X images before, Y images now.” The current count covers kept tasks only.
   Verify the counting basis in the evidence; omit image explanations from
   this stats line.
2. **Dataset characteristics**, using the automatically generated description:
   source and revision, target benchmark or intended benchmark transfer,
   domains and capabilities. Preserve the generated content unless corrections
   are requested or supported by evidence. Intended transfer is not a measured
   benchmark result.
3. **Per-stage breakdown**, with one bold lead-in per stage:
   - **1. Build and setup: X changed, Y archived.**
   - **2. Reference solution (reward 1): X changed, Y archived.**
   - **3. No-op baseline (reward 0): X changed, Y archived.**
   - **4. Rule-based checks: X changed, Y archived.**

   Follow each lead-in with short, count-first explanations of what changed
   and why tasks were archived. Add pending counts only where applicable.
   Include stage 4's verified affected-task counts for Git-history cleanup,
   timestamp cleanup, path repairs and other rule-driven changes. Count unique
   changed tasks within each stage, not the number of edits; overlapping repair
   groups must not inflate the stage total. The same task may change in more
   than one stage, so stage totals need not sum to the dataset changed count.

   Stage 4 must summarize the outcomes of rule-driven repairs, not just say
   checks passed:
   - Path warnings: use the concise sequence “Flagged X tasks for relative
     paths. Automatic repair cleared warnings for Y tasks. Z tasks still have
     warnings. Inspected T: all sampled warnings were harmless. Archived A
     tasks for [verified reason].” Adapt the review outcome to the evidence:
     if some inspected tasks were unsafe, give their count and actual repair,
     archive or pending outcome. Never imply an unreviewed remainder is safe
     or that a task was archived unless it actually was. When the remaining
     warning count and inspected sample size are given, omit the implied
     unreviewed count. Omit zero-archive statements already covered by the
     stage heading; include path-specific archives when nonzero.
     Use task counts only in this summary: flagged, warnings cleared by the
     automatic resolver, still warned, and inspected. Omit numbers of path
     references or instruction edits and repair implementation details.
     Keep the stage heading's changed-task count accurate: an edited task may
     still have a warning, so it can differ from the count cleared by the
     resolver. Keep that distinction in the evidence without explaining it
     in the paragraph unless needed. Omit zero-result mechanism details such
     as “none became warning-free through evidence alone”.
   - Git-history cleanup: how many tasks had accessible later/unrelated
     history, how many were successfully cleaned, and how many remain
     incomplete or were archived for this reason.
   - Timestamp normalization: how many tasks had timestamps changed, how many
     passed the subsequent check, and how many remain incomplete or were
     archived for this reason. Installing or running a normalization step does
     not by itself establish that timestamps changed.

   Attribute these results to stage 4 even when discovery, cleanup or
   normalization executes during stage 1 build/setup. Do not count the same
   repair again as a stage 1 change. Report task counts separately from numbers
   of paths, Git refs or files; use concise grouped wording in the README and
   keep detailed evidence in linked reports. Mark unmeasured counts as such.
4. Immediately below the per-stage breakdown, add a brief natural-language
   note only for incomplete checks/evidence, checks not used, or checks that
   were not applicable. Omit features intentionally off by default, such as
   AI detection, and intentional upstream-profile exclusions. Do not list
   enabled features or completed checks. Distinguish missing evidence from
   checks that did not run; give a short reason when needed to understand the
   limitation. Omit the note when there is nothing relevant to report.
5. **Per-task resource requirements**: use a table grouping kept tasks by CPU
   and memory requirements, with the task count for each combination. Include
   differing timeouts as columns when useful; state shared timeouts briefly
   below. Do not mix task limits with cluster/job allocations.
6. **Runtime and validation time**: give runtime/backend versions,
   implementation (framework commit and relevant runtime patches), cluster/job
   resources and concurrency. Follow with a compact table of measured stage
   durations. Omit a separate elapsed-validation-window paragraph unless
   requested:

   | Stage | Measured tasks | Total stage time | Mean per task | Median per task |
   | --- | --- | --- | --- | --- |

   Keep timing results in the table; do not append a long methodology
   paragraph. Include measured task counts and whether they cover kept tasks
   in the table. If coverage is partial, briefly state how many tasks have
   measurements, including differing coverage by metric. Otherwise omit a
   separate coverage note. Label summed shard wall time in the column header
   so it is not confused with elapsed calendar time.
   Verify measurement definitions in the evidence: use actual per-task means
   and medians, never shard averages or concurrent stage time divided by task
   count. Keep detailed inclusion/exclusion and measurement methodology in
   the evidence. Mark unavailable statistics rather than inventing them.

Attribute repairs to the check they satisfy. Git-history cleanup and path
normalization belong under rule-based checks even when performed during setup.
Do not equate accessible future Git history with confirmed exact solution leaks.
Keep selected, executed, passed, flagged and incomplete check counts distinct.

Use `data/annotate_dataset/prompt_template.txt` and its renderer when generating
an initial characteristics section. For requested README revisions, edit the
existing document directly and preserve unrelated user edits. Reconcile counts
against saved evidence. Repository-root `README.md` and `validation/README.md`
retain the separate approval rule in `.agents/AGENTS.md`.

## Datasource folder review

Inspect `data/<datasource>/` before finishing. It should contain only `patch.py`
and `README.md`, plus at most one supporting folder if the patcher genuinely
needs it. Move reusable helpers to the shared utilities location and generated
artifacts to run storage; preserve required dependencies and update references
when moving files. Do not delete evidence or unrelated work just to tidy the
folder.

## Refining the format

When the user reviews a PR section by section, apply the preferences already
stated and extend this skill as decisions are made. Do not redesign unreviewed
sections or treat suggested wording as an approved final template.
