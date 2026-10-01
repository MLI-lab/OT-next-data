# Storage pilot for static validation (2026-10-01)

Use Horse for persistent inputs, runtime bundles, checkpoints and results.
Stage the Python runtime, check scripts and active task shard into a unique
job-local directory before static validation. A venv on Horse is insufficient
if its Python symlink still resolves into home.

## Site guidance and verified mounts

| Cluster | Candidate staging storage | Caveat |
| --- | --- | --- |
| Barnard | `/dev/shm` for a small static-check working set; `/tmp` with `--constraint=local_disk` for larger sets | Ordinary nodes have only a small RAM-backed `/tmp`. SSD constraint may add queue time. |
| Romeo | SSD `/tmp` | Capacity is shared with other jobs on that node; check free space. |
| Julia | `/dev/shm` for a small working set | `/data/nvme/p_agents_finetuning` did not exist when checked. Project NVMe requires support access. Do not use the root filesystem's `/tmp` for large staging. |

RAM-backed files count toward job memory usage: reserve space for both staging
and executing processes. Check mount type, execution permission and capacity in
the allocation, not on the login node. None of these temporary locations is a
durable checkpoint store.

Official references, checked 2026-10-01:

- [Working filesystems](https://compendium.hpc.tu-dresden.de/data_lifecycle/working/): avoid many small-file reads; bundle files; keep checkpoints in Horse workspaces.
- [Barnard](https://compendium.hpc.tu-dresden.de/jobs_and_resources/barnard/): `local_disk` SSD nodes, ordinary `/tmp` limits, `/dev/shm` alternative.
- [Romeo](https://compendium.hpc.tu-dresden.de/jobs_and_resources/romeo/): SSD `/tmp`, high-IOPS Quokka access. This pilot did not allocate a Quokka workspace.
- [Julia](https://compendium.hpc.tu-dresden.de/jobs_and_resources/julia/): no general-purpose local scratch disk; project NVMe by request. The generic filesystem table lists Julia `/tmp`, but its dedicated hardware page and the observed root mount make it unsuitable for large scratch.

## Reproducible pilot

`storage_probe.py` and `storage_probe.sbatch` compare eight tasks selected across
the 879 TypeScript tasks affected by job 137324's timeout errors. Four unchanged
checks per task cover instruction suffix, nproc, resource sizes and task timeout.
These are performance measurements, not a replacement for full validation.

Each pilot requests 8 CPUs, 32 GiB and a 20-minute upper limit. It records mounts,
Python startup, archive transfer/extraction, and check outcomes. Serial runs use
two tasks; parallel runs use eight. Repeats reverse case order. Probe commands
have a 20-second diagnostic timeout, with process-group cleanup; this does not
change the validation pipeline's 300-second timeout.

The comparison separates task placement from runtime/check placement. The final
Julia diagnostic also isolates Python-on-home and scripts-on-home. No global
filesystem caches are flushed. Results are warm-cache, small-sample measurements;
they do not establish performance at 128 workers or explain the precise cause
of yesterday's transient five-minute stalls.

Artifacts: `/data/horse/ws/frwe188h-trp-shared/storage-probe-20261001/`.
Original scripts, repeated scripts and the isolation script are saved separately
there, alongside input hashes and per-command measurements. Test jobs:

- Barnard SSD: 38931764; Barnard RAM/reversed repeat: 38931840.
- Romeo SSD: 8997771; reversed repeat: 8997833.
- Julia RAM: 137359; reversed repeat: 137360; isolation: 137362.

Initial parallel measurements (32 check executions, seconds):

| Cluster | Home Python/scripts, Horse tasks | Everything on Horse | Everything staged locally |
| --- | ---: | ---: | ---: |
| Barnard SSD | 3.692 | 0.346 | 0.232 |
| Romeo SSD | 8.437 | 0.402 | 0.267 |
| Julia RAM | 4.702 | 0.342 | 0.223 |

Reverse-order repeats preserve the advantage. Barnard's additional RAM pilot
measured 0.123 seconds for the all-local case, but used a different node, so it
does not isolate SSD versus RAM hardware performance.

The final Julia isolation run measured 3.017–3.788 seconds with only Python on
home, versus 0.436–0.470 seconds with only scripts on home, 0.389–0.392 seconds
with everything on Horse, and 0.223–0.233 seconds fully local. Python on home is
the dominant observed overhead in this sample. All seven pilots completed;
all 2,360 check executions returned zero. Compact measurements are saved in
[`storage-pilot-20261001.json`](../../data/crosscodeeval/diagnostics/storage-pilot-20261001.json).

The prepared bundle is 116 MB as tar, 41 MB as gzip (decimal units). One-time
preparation took 94 seconds copying, 2.75 seconds creating tar, 1.56 seconds
compressing. Initial per-job copy plus extraction took 0.34–0.70 seconds for tar
and 1.04–1.24 seconds for gzip. Uncompressed tar won for this bundle and network;
larger payloads may behave differently. The archive includes a real Python
installation, not just its venv symlink. Its libraries are needed for relocation.

## Proposed production staging

1. Prepare a versioned, hashed runtime/check bundle once on Horse. Bundle task
   shards separately so each worker job reads only its assigned tasks.
2. At job startup, copy the selected archives to private local scratch and
   extract there. Set `TMPDIR` and check subprocess `PATH` to the staged paths;
   verify Python's resolved executable and library prefix are local.
3. Run a measured concurrency sweep before increasing to 128 workers. Staging
   cannot fix unbounded subprocess trees or container startup bottlenecks.
4. Keep incremental outcome journals on Horse, or copy durable checkpoints there
   throughout execution. Do not rely solely on an exit trap or final tar creation:
   time limits and SIGKILL can prevent those operations.
5. Copy final evidence back, verify it, and clean only that job's private staging
   directory. Keep canonical inputs and successful checkpoints intact.

## Implemented launch support

`storage.sh` now provides `zih_storage_init DATA_ROOT VENV` and
`zih_storage_cleanup`. CrossCodeEval's normal/resume jobs, SETA validation and
CalibForge validation use it. SETA/CalibForge submission snapshots include both
helper files. Manually frozen CrossCodeEval submissions must include these too.
Existing snapshots and running jobs are not changed.

`runtime_storage.py` copies the standalone Python base into a versioned data
directory, creates a replacement venv with corrected launchers, and reuses the
original data-resident site-packages. It does not alter the original venv; retain
that package directory. Editable dependencies may still refer to source trees.
Concurrent preparations use a file lock; an incomplete preparation fails closed.
Jobs copy the hashed Python tar to local staging and verify its checksum.

CrossCodeEval's corrected durable interpreter is:
`/data/horse/ws/frwe188h-trp-shared/crosscodeeval/runtimes/308018c0ee5bce7b/venv/bin/python`.
The helper selects it automatically; changing `UV_PYTHON_INSTALL_DIR` alone
would not repair the old venv's interpreter reference.

The helper chooses verified SSD `/tmp` on Barnard/Romeo, otherwise executable
`/dev/shm` when at least 8 GiB was requested and 2 GiB is available. It falls back
to Horse if these conditions fail. `ZIH_STATIC_STORAGE=shared` explicitly uses
Horse. `ZIH_STATIC_MAX_BYTES` defaults to 1 GiB, split across concurrent tasks
with room for adapted copies; larger individual tasks fall back to Horse.
Static scripts and active task copies are staged; each task copy is removed
after use. The reports, combined log and incremental journal remain on Horse.

The main pipeline `TMPDIR`, materialized canonical task set, container overlays
and full pipeline evidence remain on Horse. This change speeds repeated static
checks; it does not eliminate the full pipeline's initial materialization copy.
Container stages need a separate capacity/performance test for image extraction
and writable overlays before using local staging. No full dataset resubmission
is part of this change.

Verification of the implemented helper: 38 static/resume tests plus two runtime
cache/path tests passed. Live integration smokes passed all eight tasks on
Barnard (38932538, `/dev/shm`), Romeo (8997898, SSD `/tmp`) and Julia (137373,
`/dev/shm`), each using the full selected 14 executable static checks plus the
optional AI skip. Reports are under `crosscodeeval/storage-smoke/JOB/` on Horse.
The first Julia attempt (137370) exposed a 60-second timeout inspecting the old
interpreter. Prepared-runtime metadata is now cached, so a ready runtime does
not execute or require the original home Python. The successful retry used it.
