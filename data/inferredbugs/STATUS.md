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
| Parquet | preliminary 5,877-task parquet generated on Julia; final regeneration follows verifier-key rebuilding |

### ZIH run — 2026-09-30

Data root: `/data/horse/ws/frwe188h-trp-shared/inferredbugs`.
Warmup job 137301 completed on Julia: all eight images and analyzer downloads
are ready; one repository (`ant-design-blazor/ant-design-blazor`, retained task
`inferredbugs-1676`) could not be cached. Its task will attempt the normal fetch
path; any fetch failure needs investigation separately from warning outcomes.

Package job 137305 produced `tasks.preliminary.parquet` (5,877 tasks). Pilot
137306 read that artifact and finished all 16 checks in 11m27s: all eight buggy
versions rejected for the original warning, seven references passed, and
`inferredbugs-0001`'s reference removed the original warning but was rejected for
two warnings in another changed file. The preliminary artifact has no
`allowed_elsewhere` entries for those reference warnings; rebuilding the keys
must populate them before final validation.

Full evidence-collection array **137315** was submitted on Julia: 32 shards,
initially eight active; the limit was raised to 13 so spare CPUs can be used as
other jobs finish. Eight verifications per shard means 64 checks with eight
active shards, up to 104 with thirteen. Resources are
32 CPUs and 128 GiB per shard, 12-hour shard limit. It runs buggy/full on all
5,877 retained tasks using `tasks.preliminary.parquet`. Results, the exact task
list, code snapshot, and checksums are under `runs/full-20260930/` in the data
root. This run supplies step 3's warning tables; it is not final artifact
acceptance. A reference rejected only for missing allowed warnings is not
automatically discarded. Review complete evidence, rebuild keys, regenerate
the parquet, and repeat final oracle/NOP checks afterward.

### Why 3,782 tasks are excluded

These are the previous audit's exclusions, applied by the preliminary packaging
run. The new Julia pilot does not determine these counts.

| Reporting category | Tasks |
|---|---:|
| Warning remains in historical fix: exact match or same Infer issue key | 1,897 |
| Original warning could not be reproduced on buggy code | 1,600 |
| Build plus analysis exceeds 300 seconds | 209 |
| Buggy file or project could not be compiled or analyzed | 70 |
| Historical fix could not be analyzed with Infer | 5 |
| Packaged verifier accepted buggy code | 1 |
| **Total excluded** | **3,782** |

`task-provenance.csv` uses these grouped categories in `reason` and `reason_code`.
Its `detail_reason` and `detail_reason_code` preserve the finer audit outcomes:
1,897 combines 446 exact matches and 1,451 same-key cases; 70 combines 47
compilation/snapshot failures and 23 other build or analysis failures. Compilation
failure means no tested recovery succeeded, not that compilation is impossible.
Same-key cases do not establish an exact warning match; they still prevent the
hash-based verifier from distinguishing the reference from the buggy code.
Packaging reports and the patcher's internal discard tables retain those detailed
codes. This reporting change does not alter task selection or verifier behavior.

## Setting up on another cluster

Everything comes from public sources; nothing has to be copied from Helma.

For ZIH CPU jobs, use the warmup, verifier-check array, and packaging
scripts in [`hpc/zih/`](../../hpc/zih/README.md). They preserve every variant's
verifier evidence for step 3. Execution was proven by the Julia pilot above;
Barnard defaults can be overridden with `--partition=julia` when submitting
from Julia.

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
