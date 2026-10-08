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

## Patch explanations in automatic PRs

Follow [`validation/README.md`, Automatic PR text](../validation/README.md#automatic-pr-text).
Put explanations in the patcher's reporting inputs, not in a custom PR template
or renderer field. `write_patch_report` accepts `change_labels` and
`change_reasons` keyed by task ID; these become manifest `labels` and `reason`.
Native-source converters use `write_conversion_report`, which assigns
`converted-to-harbor` and applicable adaptation labels by default. It also accepts
explicit per-task `change_labels` overrides and `change_reasons`. Use one label
for a single combined change explanation, as BugsInPy does with
`upstream-to-harbor`, instead of repeating that explanation under several labels. Exclusions require a category, individual reason and preserved payload.
Check the manifest and render the automatic PR table to verify labels, counts
and explanations. A task's reason appears under each of its change labels.
Do not claim new wording is published until the relevant artifacts are regenerated
and published; existing frozen manifests are unchanged by patcher edits.

When a checker failure is the main reason for excluding a task, reuse that
checker's existing reporting label instead of inventing a synonymous category
or `failure_label`. For example, `check-test-file-references.sh` is reported as
`static:test-file-references`. Put task-specific details in the explanation,
while keeping the checker label. Use a separate category only when the evidence
establishes a distinct exclusion reason that the checker label does not describe.
Do not attribute patcher filters or manual review exclusions to a checker that
did not cause them; CrossCodeEval's identifier-context filter is separate from
the static file-reference checker.

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

## Image build scratch and packaging

For dependency-heavy image builds, run deferred Dockerfile RUN steps in a native
directory overlay on node-local scratch, then package the completed tree into
the cached ext3 overlay. Keep dependencies inside the resulting image; do not
replace them with a runtime cache-restore step. Avoid installing many small files
directly into a writable ext3 image when the native-directory path is available.

`hpc/image_cache.py` enables this path by default for both Helma and ZIH validation
launchers. `OT_IMAGE_DIRECTORY_BUILD=0` explicitly selects the old deferred-build
path for compatibility diagnosis. Use the shared builder rather than adding
dataset-specific build launchers. Its image output directory must be on node-local
scratch; the directory overlay is created beside the output. Keep host home and
site-wide mounts isolated during installation, retaining explicit certificate and
DNS binds. Publish only the completed image artifacts to shared storage.
Normal Apptainer definition builds are unchanged; this optimization applies to
the deferred RUN fallback. Existing frozen submissions retain their captured code
and settings, and direct external Apptainer commands do not use this adapter.

Measured on Helma on 2026-10-08: a React Router baseline installation in an isolated
native directory overlay took 43 seconds including checkout, followed by 114
seconds to package an 8 GiB sparse overlay. Reopening that overlay verified the
baseline commit and installed dependencies. The earlier image build failed after
about 29 minutes with a Yarn download timeout. Download retries and filesystem
overhead both warrant investigation; this comparison does not isolate their
individual contributions or establish timings for every dataset. Preserve full
build logs and distinguish installation time, packaging time and complete build
time. Larger images and other dependency managers still require validation.

# Git branches

The only branch is `main`, locally and on `origin`
(https://github.com/MLI-lab/OT-next-data). There is no `master`. Commit and
push to `main`; never create or push a `master` branch. Before pushing, run
`git branch -vv` and check that the current branch is `main` and tracks
`origin/main`.
