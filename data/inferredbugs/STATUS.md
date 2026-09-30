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

## Setting up on another cluster

Everything comes from public sources; nothing has to be copied from Helma.

| Need | How to get it |
|---|---|
| The repository | `git clone https://github.com/MLI-lab/OT-next-data.git` (branch `main`) |
| Python with `pyarrow` | any venv, or `uv run --with pyarrow python ...` |
| `git`, `apptainer` (or Docker), outbound network to GitHub, Maven Central, NuGet, python.org | cluster setup; a proxy goes into the Slurm script like `hpc/helma/proxy.sh` |
| The buggy and fixed files of the tasks | `python3 data/inferredbugs/warmup.py --source DIR`: microsoft/InferredBugs at the pinned commit, about 1 GB. The harness takes it as `--inferredbugs-root DIR` (`CHECK_SOURCE=DIR` in the Slurm script); Helma used its `inputs.sqlite` instead |
| The eight images | `warmup.py --images DIR` (`CHECK_IMAGES=DIR`); the Java ones include Python 2.7 |
| Optional caches | `warmup.py --downloads DIR` (analyzer releases, 1.5 GB) and `--repositories DIR` (bare clones, tens of GB); export `INFERREDBUGS_DOWNLOAD_CACHE` and `INFERREDBUGS_REPOSITORY_CACHE` for the Slurm job |
| The Slurm script | `hpc/helma/inferredbugs_verifier_check.sbatch`: set partition, proxy, `CHECK_RUN` (output directory with `ids.txt`), `CHECK_SOURCE`, `CHECK_IMAGES`; `CHECK_VARIANTS=buggy,full`; as an array job with `CHECK_SHARDS` |

The task ids for the run (`ids.txt`): the `kept = yes` rows of `task-provenance.csv`, 5,877 tasks
with the 300 s limit already applied, or all 6,086 of `EMBEDDED_WARNINGS` to measure the slow ones
again: `awk -F, '$4 == "yes" {print $1}' data/inferredbugs/task-provenance.csv > ids.txt`.

## What is left, in order

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
