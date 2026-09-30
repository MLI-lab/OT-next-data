# InferredBugs: where the dataset stands, and what is left — 2026-09-30

Three documents live here. This one is the plan and the current numbers. `README.md` describes
what the patch script produces and how the verifier grades. `FAILURE_REPAIRS.md` is the history:
every round of the audit, what went wrong and what was decided, from 2026-09-25 on; read it for
the reasons, not for the state.

## Where we stand

| | |
|---|---|
| Original TaskTrove tasks | 9,659, all with a proven build recipe |
| Kept after the warning audit | 6,086 |
| Kept after the 300 s limit on build plus analysis | 5,877 (`task-provenance.csv` has every task with its reason) |
| Verifier | multi-file, grades by Infer's issue keys; checked on 97 tasks buggy/fixed-file and on 48 tasks with the complete historical fix |
| Analyzer install, images, instruction, timeouts (agent 1800 s, verifier 900 s) | done |
| Keys the verifier compares with | still the audit's; to be replaced by the verifier's own (step 2) |
| Parquet | not regenerated |

## What is left, in order

The runs happen on another cluster; nothing here needs Helma.

1. **Warm the caches** (`warmup.py`): build the eight images, download the analyzer releases,
   clone the repositories. Optional, but it removes GitHub and download failures from the runs.

2. **Run every kept task twice through its own verifier**, with the buggy file and with the
   historical fix (`solution/solve.sh`). Either the check harness
   (`hpc/helma/inferredbugs_verifier_check.sbatch` with `CHECK_VARIANTS=buggy,full`) or the
   validation pipeline's stages 4 and 5 (oracle, NOP) do this. About 12,000 verifications: with
   8 verifications per node in parallel, roughly 4 to 5 hours on 8 nodes.

   This answers, per task: does doing nothing fail, does the reference pass, and it produces the
   keys as the verifier itself computes them. The buggy result is the second observation of that
   side (the first was the run on Helma, under Infer's time limit); a task whose warning is
   missing in either run is unstable and goes.

3. **Rebuild the keys** from those results: `warning_identity/verifier_keys.py --write`, then
   `recovery_tables.py`, then `provenance.py`. This fills `allowed_elsewhere` (the fix's
   warnings in other files) and drops the tasks where the buggy file passed or the fix failed.

4. **Package**: run the patch script over the original parquet (`--trust-recipes
   --no-verify-package`, minutes). The report next to the output and `task-provenance.csv` list
   every dropped task with its reason.

5. **Validate** the packaged tasks with `validation/`: stage 1 (static checks), 3 (the images
   build and start), 4 and 5 (oracle gets 1, NOP gets 0; on the freshly packaged tasks this
   repeats step 2 as a check of the final artefact, so a sample is enough), then the agent
   trials and trajectory stages as for the other datasets.

Open, in case the runs show it: the Java build time with Python 2.7 in the image (if the Infer
download still dominates, bake Infer 0.17 into the Java images); tasks whose historical fix
introduces warnings in files it changed are fine after step 3, but a fix that does not build
under the verifier drops the task.

Not done, by decision: guards against deleting or emptying the method with the warning; runtime
tests.
