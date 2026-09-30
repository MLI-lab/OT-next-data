# SETA CPU validation on ZIH

Input: TaskTrove [PR #4](https://huggingface.co/datasets/open-thoughts/TaskTrove/discussions/4),
commit `9262d5628e13ec20ac75b7d897f94b93f6be0594`,
`camel-ai__SETA-Env/tasks.parquet` (3,153 tasks).
The downloader verifies the pinned size and SHA-256 before reusing the file.

Storage defaults to `/data/horse/ws/frwe188h-trp-shared/seta` (`SETA_ROOT`).
Check `ws_list` before a new run. The Python environment is `venv/`, installed
with Python 3.12, Harbor at the repository's pinned `7faf878c14b6` commit
with its Daytona extra, pyarrow, pyyaml and pytest. All caches and temporary
build storage must also be redirected into this workspace during installation.

From the repository root:

```bash
python3 hpc/zih/download_seta.py
PYTHONDONTWRITEBYTECODE=1 LITELLM_LOCAL_MODEL_COST_MAP=True \
  /data/horse/ws/frwe188h-trp-shared/seta/venv/bin/python hpc/zih/submit_seta.py --smoke --cluster julia
```

The smoke run selects ten tasks (two per source, preferring distinct Dockerfiles
and synth/evolve lineages), requests 8 CPUs and 32 GB for four hours, and runs
four tasks concurrently. Its selection is saved in `smoke-input/selection.json`.
Review all four stage reports and fix issues before submitting a full run.
There is no automatic full-run dependency. Omit `--smoke` for the full run;
use `--cluster barnard` for local Barnard submission instead of SSH to Julia.

Full submission snapshots the validation code and requests 32 CPUs, 128 GB RAM,
24 hours, and no GPUs. The job prepares a frozen contract for
all 3,153 tasks and runs stages **1, 3, 4, 5**, with one oracle/NOP attempt per
task and up to 24 concurrent tasks. It uses the standard training static
profile without extra exclusions and normalizes instruction suffixes in a
saved validation copy. The downloaded parquet is preserved. Host networking
allows the packaged setup scripts and verifiers to install dependencies.
GPTZero is disabled; no LLM stages are selected.

Each run has `submission.json`, `source.json`, the contract and its task
manifest, then `execution.json`, `resources.json`, `report/summary.json` and
`evidence.tar.gz` when the worker finishes. Slurm output is in
`logs/seta-validation-JOBID.out`. Scratch is retained under `scratch/` so
partial results survive interrupted runs. A nonzero job exit can mean dataset
findings: inspect `execution.json` and the report to distinguish findings from
infrastructure errors. Submission alone does not establish validation success.

## Split/resumed validation

`submit_seta.py` accepts `--stages`, `--cpus`, `--concurrency`, `--time`,
`--task-id-range FIRST LAST`, `--static-resume CHECKPOINT`,
`--reuse-validation-containers`, and `--dependency afterok:JOBID`.
Run stage 1 separately from runtime shards. Use non-overlapping task-ID ranges
whose union covers the dataset. In runtime shards, select `--stages 3,4,5`
and `--reuse-validation-containers`: execution is build → NOP → oracle
(3 → 5 → 4), with a single same-task container lifetime and logs cleared
between phases. Verifier side effects can persist into the oracle phase.
Container starts are capped at eight concurrently per job.

Before replacing a static run, `recover_static_checkpoint.py` preserves its
checkpoint, normalized task inputs, hashes and check logs. Resume imports only
successful unchanged checks; failed or changed checks run again. Do not enable
`--static-resume-accept-previous-path-check` when the new path-check logic should
be applied. Completed evidence from a different execution profile is retained,
not silently relabeled as a result from the new profile.

The replacement of job `137314` is recorded in the workspace at
`runs/resume-137314.json`; its static checkpoint is
`checkpoints/137314-preserved`. Scheduler allocations may differ from original
requests; each executing job records the effective concurrency in `resources.json`.
