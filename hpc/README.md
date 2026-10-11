# Cluster execution

Run `validation/run.py`; it selects resources and submits the cluster launcher.
Choose stages and model settings on the command line rather than editing Slurm
scripts. Cluster software requirements live in `config/clusters.py`.

## Layout

The shared execution path is `validation_submit.py` → a cluster's
`validation.sbatch` → `validation.sh` → `validation_worker.py`.

| Shared file | Purpose |
| --- | --- |
| `validation_submit.py` | Choose resources, freeze code, submit jobs and archive snapshots |
| `validation.sh` | Load the site environment and bootstrap node-local Python |
| `validation_worker.py` | Stage inputs, manage services, run stages and save evidence |
| `local_runtime.py` | Build and relocate the Python runtime; bootstrap uses only the standard library |
| `local_assets.py` | Stage images, weights, dependency archives and TLS certificates |

Keep runtime bootstrapping separate from asset staging: it runs before the
prepared Python environment is available. Submission and execution also run in
different environments (login node and allocation). The small cluster wrappers
hold site-specific setup; pipeline behavior belongs in the shared files.

Certificate staging is available as
`python hpc/local_assets.py certificates DIRECTORY`. Frozen submissions copy the
whole `hpc/` tree, including maintenance launchers, while excluding Python caches.
Previously frozen submissions retain their own code and paths.

## Helma

| Entry point | Purpose |
| --- | --- |
| `helma/validation.sbatch` | All validation stages, including teacher generation |
| `helma/isolation.sbatch` | Separate cross-container isolation check |
| `helma/publish.sbatch` | Publish completed validation results |
| `helma/publish_worker.py` | Retry publication from saved reports without rerunning validation |
| `helma/proxy.sh` | Outbound proxy settings shared by validation and publication |
| `helma/archive_to_vault.py` | Archive, verify and restore completed run directories |
| `helma/diagnostics/` | Runtime diagnostic tools |

Without local model serving, validation requests CPUs. `--serve-model` adds GPUs;
external model APIs do not need local GPUs. The `auto` partition policy can use a
GPU partition if CPUs are unavailable; use `--partition cpu --cpus 48` to require CPUs. Helma CPU allocations require
multiples of 48 cores; a single GPU allows up to 32 cores.

Each run saves its exact submission command in `submissions/ID/submission.json`,
settings in `request.json`, and frozen code with its results. Keep experiment
outputs there, not as new one-off launchers in this directory.

Jobs stage Python, code, tasks, selected images, dependency archives and model
weights under node-local `$TMPDIR`. Model weights must fit locally; insufficient
space fails preparation. Runtime caches and task logs are local too. Bootstrap
reads, version checks, shared batch logs, and final result/archive writes still
access cluster storage; system executables and CUDA come from site modules.
See [runtime details](../validation/docs/runtime.md).

## ZIH

[ZIH](zih/README.md) uses `zih/validation.sbatch` with the same worker and staging
code. Site settings live in `config/clusters.py`. Local GPU serving requires
configuring the ZIH GPU partition and CUDA module; on-cluster verification is pending.

## Storage locations

`$OT_WORKSPACE` is the directory passed to `setup.sh`. On Helma, choose an allocated
workspace under `/hnvme/workspace/` (use `ws_list` to find yours). It is separate
from the cluster's `$WORK`, which GPU nodes cannot access. Setup does not allocate
a cluster workspace or move existing data.

`config/clusters.py` records available storage roots and quotas. After setup,
`env.sh` exports the following names. Paths below are for Helma user `y500bb12`;
quotas are soft / hard limits observed on 2026-10-06, not reserved space.

| Variable | Helma path | Purpose and access | Disk quota (GiB) | File quota |
| --- | --- | --- | --- | --- |
| `$OT_STORAGE_HOME` (`$HOME`) | `/home/hpc/y500bb/y500bb12` | Repo and configuration; all nodes | 100 / 200 | 395,594 / 495,594 |
| `$OT_STORAGE_WORKSPACES` | `/hnvme/workspace` | Parent of allocated workspaces for models, images and datasets; all nodes | No configured user limit | 81,920 / 102,400 |
| `$OT_STORAGE_WORK` (`$WORK`) | `/home/janus/y500bb/y500bb12` | Other shared work storage; login/CPU only | Unverified | Unverified |
| `$OT_STORAGE_ARCHIVE` (`$HPCVAULT`) | `/home/vault/y500bb/y500bb12` | Archives you explicitly save there; login/CPU only | 1,000 / 2,000 | 200,000 / 400,000 |
| `$OT_STORAGE_SCRATCH` (`$TMPDIR`) | Assigned per job | Temporary runtime and inputs on the compute node | Depends on node | Depends on node |

Quotas apply across your files on each filesystem, not separately to each
workspace. No configured user limit does not mean unlimited disk capacity.
These are disk limits, not job RAM. ZIH's roots (`$OT_STORAGE_HORSE`,
`$OT_STORAGE_WORKSPACES`, `$OT_STORAGE_CAT`) and quotas still need site verification.
Names depending on unset site variables remain unset; scratch is refreshed inside
each job. These names identify locations; they do not automatically route files.

| What | Location |
| --- | --- |
| Repo and pinned upstream checkouts | This repo; upstreams in `external/` |
| Source Python environment | `/path/to/workspace/envs/prep`; override with `OT_PREP_ENV` |
| Environment transport archive | `/path/to/workspace/runtime/`; `prep.json` identifies the archive |
| Model weights | `/path/to/workspace/models/`, or the directory set by `OT_MODELS` |
| Reusable task images | `/path/to/workspace/images/`; selected images are copied into the job |
| Downloaded/prepared datasets | Destination chosen by each dataset script; SETA defaults to `$OT_WORKSPACE/seta/upstream/tasks.parquet`, while pilot preparation scripts require an output path |
| Reports, frozen code and evidence | The `--out` directory, with Slurm runs under `submissions/ID/`; default is this repo's `validation/results/` |
| Running job's Python and code | `$TMPDIR/validation-runtime/` |
| Running job's tasks, images, weights, dependency archives and results | `$TMPDIR/validation-ID/`; required inputs are copied before trials start |
| Runtime caches | Under `$TMPDIR`; temporary and disposable |

Slurm validation prepares missing task images before starting timed trials. The
shared helper `hpc/image_cache.py` logs cache misses and runs each build in its
own step. Defaults: `--image-build-memory-mb 8192 --image-build-cpus 4
--image-build-concurrency 2 --image-build-timeout-sec 3600`. Concurrent builds are
capped to fit allocated CPUs and memory; preparation runs before model serving
and task steps. These limits do not change `task.toml`. The allocation wall time
includes image preparation, cache transfers, and validation.

Successful images are saved automatically under
`$OT_WORKSPACE/images/bundles-v1/`, keyed by environment content. Each atomic
bundle includes checksums, the SIF, and any deferred-build metadata and sparse
overlay. Future jobs verify and stage matching bundles. Cache hits skip building;
task startup and setup still run under their original limits. Incomplete builds
are excluded. Build failures and logs are preserved in the run evidence as
`image-preparation.json` and `image-build-logs/`; cache publication failures warn
without discarding a usable local build. Shared images are immutable: a forced
local rebuild does not replace an already published bundle. Unpinned registry
tags retain the existing cache semantics; use pinned image digests for exact
base-image provenance.

For a consistent layout, pass `$OT_WORKSPACE/datasets/<dataset>` to preparation
scripts that accept an output directory, and use `--out "$OT_WORKSPACE/runs/my-check"`
for validation results. These are explicit choices, not current defaults.
Keep downloaded Parquets and unpacked task directories there; the repo's `data/`
is for preparation code, patches and small metadata. Parquet uses few files but
can consume substantial disk space; unpacked tasks also consume the file quota.
Hugging Face's download cache is separate; set `HF_HOME` if it should also live
in the workspace. Dependency archives use the directory passed with
`--dependency-archives`; jobs stage selected archives locally and save new ones
back after execution. Temporary storage is not a permanent copy of the results.

Helma workspaces normally live on `/hnvme`; ZIH workspaces on an available
`/data/horse`, `/data/ws` or `/data/cat` filesystem. Choose the actual workspace
path when running setup. The detailed staging behavior is in the
[runtime reference](../validation/docs/runtime.md#local-storage).
