# BugsInPy offline audit status (2026-11-01)

Goal: evaluate the validation pipeline on the patch without repairs, with offline
runtimes. Tasks may fail; fix only what our conversion or infrastructure caused,
never the upstream task. A fix goes into `patch.py`, then only the changed tasks
are rerun.

Root: `/hnvme/workspace/y500bb12-rule-based-checks/bugsinpy-no-repairs-20261101`

## Runs

All use stages 0-4, `--outcome-retries 0`, `--network-mode simulated`, Helma.

| Job | Tasks | State when written | Folder |
|---|---|---|---|
| 1195453 (h200) | 498 from `offline-v1` | Running, stage 2 | `run-h200` |
| 1196246 (h200) / 1196247 (preempt) | 171: pandas + 2 spaCy, with headers | Pending; tmux `bugsinpy-audit` cancels the copy that does not start first | `run-r1-h200`, `run-r1-preempt` |
| 1195462 | httpie-4, thefuck-3 | thefuck-3 passed all; httpie-4 superseded below | removed |
| 1195559 | httpie-4 | Reference 0 on DNS failure: real network dependency | `run-httpie` |
| 1196053 | httpie-2, 3, 5 | Passed all | `run-httpie235` |

Online test 1195617: httpie-4 passes with `allow_internet = true`. Decided: stays
false and is archived at upload with the network reason.

## To do

1. Check 1195453 when it ends. Do not use its results for tasks changed since:
   pandas, 2 spaCy, httpie-2/3/5 (use the reruns). Confirm the `-X utf8=0` fix
   (black, cookiecutter, pysnooper no-op) and the 3.7 leak probe worked, neither
   was rerun in a pilot before the full run. Read the verifier_tamper results.
2. Sort every failure: ours (fix in `patch.py`, update `offline-recipes.json`,
   rerun those tasks) or the task's (leave, the filter archives it). Evidence of
   a running job can be read with `srun --jobid=N --overlap`.
3. Check 1196246/7 the same way; pandas-51, 65, 99, 146, 150, 153 should now pass
   reference. Check whether the 2 spaCy tasks were the ones missing headers.
4. Merge results of all runs and fill the four `reference_*` columns of the 498
   and earlier runs from their evidence (their frozen code predates the columns).
5. Copy `bugsinpy-offline-artifacts-20261101.tar` (13.7 GB, built before later
   fixes, rebuild it if recipes change) somewhere permanent, then upload. The
   runtimes still live in `runtime-agents-20261028T140014Z/repos/runtimes`.
6. Publish: no merge or upload was done. Confirm with the user first.
7. After the audit: merge verifier and agent runtimes into one artifact where the
   locks match (499 of 500; 2026-10-29 decision). No full rerun: run the 17-task
   pilot (one per project), and if rewards match carry the results over with a
   recorded reason, because task hashes change.
8. `offline-recipes.json` is a saved result. Moving how recipes are generated into
   `patch.py`, and checking patch plus repairs byte for byte, are still open.

## Commands

```bash
python data/tasktrove_bugsinpy/patch.py --no-repairs --output CONVERT
python data/tasktrove_bugsinpy/patch.py prepare-offline --tasks CONVERT --output OUT \
  --images IMAGES --runtimes RUNTIMES --source-revision REV
```

Environment for runs: `OT_WORKSPACE=<root>`, `OT_RUNTIME_CACHE=<repos>/runtimes`,
`OT_REQUIRE_PREBUILT_IMAGES=1`, `OT_RUNTIME_BUNDLE` as in `.agents/RUNTIME.md`.
AI detection is excluded by user policy. Helma `cpu` is drained, use `h200` and
`preempt`, submitting to both is fine. CLAIX and LRZ notes: `.agents/RUNTIME.md`.
Nothing here is committed yet.
