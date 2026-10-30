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
| Runtime, staging, bridge behavior | `.agents/RUNTIME.md` |
| Cluster launchers | `hpc/README.md`, `hpc/zih/README.md` |
| SSH access to CLAIX, LRZ and ZIH; agent and Mac VPN tunnel | [Cluster access](cluster-access.md) |
| Patch outputs and publication | `data/INVENTORY.md#reporting-patches-in-the-datasource-pr` |
| Helma mounts and quotas | `config/clusters.py`, `hpc/README.md#storage-locations` |
| Helma performance measurements | [Helma measurements](#helma-measurements) below |
| ZIH storage and job-local staging | `hpc/zih/README.md` |

Skills in `.agents/skills/`: `patch-dataset` (repair a dataset), `verify-dataset`
(stages 1, 2, 3, 4), `run-teachers` (stages 6 and 7, plotting),
[`v1_readme`](skills/v1_readme/SKILL.md) (v1 dataset README and HF PR wording), and
[`v2_readme`](skills/v2_readme/SKILL.md) (v2 evaluation results and trajectory statistics),
and [`readme-v3`](skills/readme-v3/SKILL.md) (v3 READMEs focused on new results,
without repeating v2 evaluation tables).

## Patch explanations in automatic PRs

For per-stage dataset README breakdowns, attribute a repair to the validation
rule it satisfies, not to the stage that happens to execute the repair.
Relative-path normalization belongs under stage 4 (rule-based checks), even
when it executes during stage 1. Do not count it again as a build/setup repair.
Use verified affected-task counts; if consolidation is incomplete, mark the
count pending rather than reporting zero or inventing a count. Integrate new
results into existing totals and reason summaries instead of appending rerun
history. Describe manual approvals plainly, while distinguishing an inspected
result from an automatic pass and avoiding claims unsupported by its evidence.

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

## Pass@k execution concurrency

For teacher generation, explicitly choose multiple concurrent trials per GPU
when the allocation and a throughput pilot support it. Do not default full
runs to one trial at a time without a measured reason. Preserve every task's
requested CPU and memory resources, sampling settings, attempts and timeouts.

Distinguish the Slurm array throttle (simultaneous shard jobs), pipeline
`--concurrency` (simultaneous trials within each job), and vLLM
`--max-num-seqs` (serving sequence slots). Increasing the array throttle alone
does not improve batching within a GPU job. Trials can be attempts of the same
task, so concurrency is not necessarily the number of distinct tasks.

Budget serving CPUs first, including all serving replicas. The worker's
`trial_capacity` reserves each task's agent CPUs plus the largest separate
verifier CPU request that can overlap it, then uses the largest per-trial
reservation in the shard. The CPU ceiling is
`floor((job_cpus - serving_cpus) / reserved_cpus_per_trial)`.
Also check aggregate memory, GPU KV-cache capacity and node-local scratch.
Never lower task CPU requests just to increase concurrency. Increase the job
allocation if needed. Inspect the frozen worker because budget behavior can
vary by snapshot.

Check `submission/resources.json` for effective concurrency, not only the
requested value or the current checkout. The worker can cap concurrency at
startup. Validate higher concurrency with real task attempts, saved trajectories,
verifier results, infrastructure errors, throughput and GPU utilization before
expanding. Keep GPU-free agent/verifier steps on `srun --exact --gres=none`.

For existing submissions, inspect frozen requests/contracts and live Slurm
throttles. Do not silently edit frozen contracts or cancel running jobs.
Replacements must preserve completed work and have disjoint task/attempt
ownership; reconcile any existing acceleration controller before submitting.
See [the 2026-10-14 audit](passk-concurrency-audit-20261014.md) for a dated
snapshot, not a substitute for querying current queues.

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

Before rebuilding a task image, check both the target cluster's cache and the
dataset's pinned Hugging Face image manifest. Prefer a compatible published
bundle over a rebuild: verify its task/environment identity, architecture,
provenance, file sizes and checksums, then stage it into the target cluster's
cache. A published image does not need rebuilding merely because its local
cache entry is missing. Prepare missing cache entries in a separate image-only
job, verify them before submitting validation, and enforce
`OT_REQUIRE_PREBUILT_IMAGES=1` for validation jobs. An image available only on
Hugging Face or another cluster is not yet a verified target-cluster cache
entry. Build only when no compatible cached or published image is available.

Helma node-local storage is shared by jobs placed on the same node. On
2026-10-10, three Scale SWE jobs on `h35-04` staged roughly 600 images
(about 300 GB) plus trial workspace copies and exhausted `/tmp`. The Claude
session recorded `TmpDisk=0`, so scheduler disk accounting did not prevent
overcommit. The shard launcher used `--exclusive` to prevent co-location.
This was a scratch-capacity precaution, not a dataset validation requirement.

On 2026-10-11, the Scale SWE session removed exclusivity after estimating that
five 24-task shards would use about 70 GB together on a node whose disk had
filled at roughly 400 GB. These are historical estimates, not guarantees for
other datasets. `validation/shards.py` now makes `--exclusive` opt-in, but
already-submitted jobs retain their original request. CalibForge array
`978995` inherited the old exclusive default.

Before sharing a node, budget aggregate scratch for all co-located jobs:
runtime extraction, staged images, unpacking/copying peaks, build directories,
overlays, and trial workspaces. Image-only prebuild releases each image's
scratch after publishing, so its peak depends on concurrent image operations;
ordinary validation can retain the whole shard's image set. Check the frozen
worker implementation and actual image sizes before transferring an estimate
between these modes or changing a queued allocation.

History: Claude session
`/home/hpc/y500bb/y500bb12/.claude/projects/-home-hpc-y500bb-y500bb12/c67e8930-5973-4121-934e-f74c9dc03717.jsonl`,
messages at `2026-10-10T12:26:09Z` (disk exhaustion) and
`2026-10-11T14:38:47Z` (sharing small shards after checking disk usage).

For dependency-heavy image builds, run deferred Dockerfile RUN steps in a native
directory overlay on node-local scratch, then package the completed tree into
the cached ext3 overlay. Keep dependencies inside the resulting image; do not
replace them with a runtime cache-restore step. Avoid installing many small files
directly into a writable ext3 image when the native-directory path is available.

`hpc/offline_prep/image_cache.py` enables this path by default for both Helma and ZIH validation
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

**Co-located shards and Apptainer contention (2026-10-17).** Slurm pins CPUs and
memory per job, but node-local `/tmp`, image mounts (squashfuse, fuse-overlayfs)
and Apptainer container starts are shared by all jobs on a node. Each shard's
start limit (`BRIDGE_START_CONCURRENCY`) applies only within that shard.
Multi-SWE pilot 1039663: three shards and three other jobs started on h31-61
within 3 minutes. Workdir copies and `instance start` took 90-300 s there,
against under 20 s on an uncontended node; 14 copies hit the 120 s limit, and
Slurm drained the node ("Kill task failed"). We accept co-location for now:
whole-node exclusive shards would lengthen queue waits. Watch for this pattern:
start or copy timeouts clustered on one node, while the same tasks pass
elsewhere. Rerun those tasks as infrastructure failures, never archive them, and
record the node in the report.

## Hugging Face commit authors

Do not use `Codex` or another assistant identity as the author or committer of
Hugging Face commits, including uploads and merge-conflict resolutions. Use the
authenticated user's Hugging Face identity for API uploads and preserve the
repository's existing user identity for Git commits. Do not invent author names
or email addresses.

# Git branches

The only branch is `main`, locally and on `origin`
(https://github.com/MLI-lab/OT-next-data). There is no `master`. Commit and
push to `main`; never create or push a `master` branch. Before pushing, run
`git branch -vv` and check that the current branch is `main` and tracks
`origin/main`.
