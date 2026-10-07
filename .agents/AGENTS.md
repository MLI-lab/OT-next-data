# OT-next-data: notes for agents

Repository documentation is the source of truth; this folder only adds rules and
measurements that are not written down elsewhere.

## README changes requiring approval

Before changing the repository-root `README.md` or `validation/README.md`, show the user the complete
proposed additions and replacement passages, rendered as Markdown in the
conversation, and explicitly ask for approval. Apply the change only after
approval. Do not substitute a summary or file link for the proposed text.
This approval requirement applies to both named READMEs, not automatically to
other documentation files. Do not edit either README before the user approves
the displayed text.

| Topic | Where |
| --- | --- |
| Validation workflow, contracts, publishing | `validation/README.md` |
| Validation stages and checks | `validation/PROTOCOL.md` |
| Runtime, staging, bridge behavior | `validation/docs/runtime.md` |
| Cluster launchers | `hpc/README.md`, `hpc/zih/README.md` |
| Patch outputs and publication | `data/INVENTORY.md#reporting-patches-in-the-datasource-pr` |
| Helma mounts and quotas | `config/clusters.py`, `hpc/README.md#storage-locations` |
| Helma performance measurements | [Helma measurements](#helma-measurements) below |
| ZIH storage and job-local staging | `hpc/zih/README.md` |

Skills in `.agents/skills/`: `patch-dataset` (repair a dataset), `verify-dataset`
(stages 1, 3, 4, 5), `run-teachers` (stages 6 and 7, plotting),
[`write-dataset-pr`](skills/write-dataset-pr/SKILL.md) (HF PR wording and automatic template).

# Writing style

Do not use em dashes, en dashes, or standalone hyphens as punctuation in prose
or user-facing replies. Use commas, periods, parentheses, or rephrase the
sentence. Preserve hyphens required in commands, filenames, identifiers, and
Markdown syntax.

# Datasets and runs

Prepare Harbor task directories or TaskTrove Parquets with the patcher under
`data/<dataset>/`. Run checks and teacher generation through `validation/run.py`.
Results and code snapshots belong in the run output directory, not in new
launch scripts.

Keep generated datasource READMEs focused on the source, task content, domains,
capabilities and likely benchmark transfer. Put validation results, exclusions,
patch statistics, retries and runtime limitations in the Hugging Face PR and its
run JSON, not in the datasource README. Preserve detailed per-task evidence there.
Generate the datasource README with `data/annotate_dataset/prompt_template.txt`
and the existing renderer. Do not manually rewrite it or append validation notes.

# Cluster storage

On ZIH, keep large datasets, downloads, parquets, images, caches, environments,
build scratch, and run artifacts in an available workspace under `/data/horse`,
`/data/ws`, or `/data/cat`. Never download or generate them under `/home`.
Check available mounts and `ws_list`; do not assume a filesystem exists or
silently fall back to home. Source code and small configuration files may stay
in the repository.

ZIH uses the shared runtime bundle and `hpc/zih/validation.sbatch` to stage jobs
onto executable node-local scratch. The bundle includes the Python base and
packages; an environment path on data storage alone is not sufficient. Keep
canonical data and final outcomes on data storage. See `hpc/zih/README.md`.

On Helma, bulk job files go to `$TMPDIR`, shared data to `/hnvme`, archives to
`$HPCVAULT` (not mounted on GPU nodes), and `$HOME` holds code and small configuration.
`$WORK` is also unavailable on GPU nodes. Keep trees of many small files archived
on shared storage; unpack them in job-local scratch. See `hpc/README.md#storage-locations`
for mount availability, exported paths and current quotas.

On a login node or in a CPU job, `hpc/helma/archive_to_vault.py <dir>` packs a
finished run into the vault, verifies it, removes the original and leaves
`<dir>.archived.json`. Use `--restore <dir>` to restore it.

## Helma measurements

Measured 2026-10-01: one 1 GiB file written (`dd`, zeros, `fdatasync`) and read
back uncached, then 2,000 files of 4 KB created, read and deleted from Python.
These are historical measurements, not performance guarantees.

| Node | Location | 1 GiB write | 1 GiB read | Create a small file | Read a small file |
| --- | --- | --- | --- | --- | --- |
| login (helma1) | `$HOME` | 2.1 GB/s | 1.3 GB/s | 5.7 ms | 0.49 ms |
| login | `$HPCVAULT` | 1.6 GB/s | 1.1 GB/s | 3.3 ms | 0.53 ms |
| login | `$WORK` | 1.5 GB/s | 0.8 GB/s | 0.65 ms | 0.24 ms |
| login | `/hnvme` | 3.3 GB/s | 1.7 GB/s | 0.18 ms | 0.12 ms |
| login | `/tmp` | 2.2 GB/s | 3.4 GB/s | 0.01 ms | <0.01 ms |
| GPU (h24-07) | `$HOME` | 1.0 GB/s | 1.1 GB/s | 3.75 ms | 0.53 ms |
| GPU | `/hnvme` | 1.3 GB/s | 0.86 GB/s | 0.28 ms | 0.22 ms |
| GPU | `$TMPDIR` | 1.4 GB/s | 5.4 GB/s | 0.03 ms | 0.01 ms |
| cpu (h34-06) | `$HOME` | 1.4 GB/s | 1.1 GB/s | 3.3 ms | 0.47 ms |
| cpu | `$HPCVAULT` | 1.4 GB/s | 1.0 GB/s | 3.3 ms | 0.48 ms |
| cpu | `$WORK` | 1.4 GB/s | 0.8 GB/s | 0.56 ms | 0.22 ms |
| cpu | `/hnvme` | 2.3 GB/s | 0.9 GB/s | 0.19 ms | 0.14 ms |
| cpu | `$TMPDIR` | 0.67 GB/s | 5.9 GB/s | 0.01 ms | <0.01 ms |

Small-file creation was much slower in `$HOME` than on `/hnvme` or `$TMPDIR`.
The write tests used zeros, which can inflate throughput on compressing filesystems.

CrossCodeEval run on 2026-09-30 (job 917298): 6,710 tasks, 32 cores on an H200
node, concurrency 28, total 3 h 23 min:

- Tasks were unpacked into `$TMPDIR`; local results (538K entries) were packed
  into one `evidence.tar.gz`. Reading/unpacking the 128 MB source Parquet from
  `$HOME` took under a minute.
- Stage times: static checks 49 min, build 55 min, oracle 52 min, no-op 44 min.
  The run was limited mainly by cores and container starts after local staging.
- The 25 SIF images used 3.7 GB in `$OT_WORKSPACE/images`. An unpacked Python
  environment used about 20K files; unpacked task trees also consume file quotas.
  Jobs receive the environment as an archive in `$OT_WORKSPACE/runtime/`.

# Git branches

The only branch is `main`, locally and on `origin`
(https://github.com/MLI-lab/OT-next-data). There is no `master`. Commit and
push to `main`; never create or push a `master` branch. Before pushing, run
`git branch -vv` and check that the current branch is `main` and tracks
`origin/main`.
