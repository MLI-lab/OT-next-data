# Container warmup and storage pilot

`warmup.py` accepts patched **materialized task directories**. It inventories
every environment by the full build-context hash, seeds a private cache from
existing images, and starts one representative of each distinct environment.
Missing images/deferred installation layers are built through the real bridge.
It does not modify the production image cache or publish validation results.
The resulting durable `images/` directory can be reused as a future `--cache`.

Cross-file excerpts are already in `setup_files/context/`; this dataset does
not need a warmup Git clone. Harbor uploads those files before no-op/oracle.
The source task directories remain unchanged.

The default benchmark selects at least one task per language and environment,
then runs stages 3 -> 5 -> 4 sequentially per task. It compares:

| Layout | Image cache | Task inputs and writable container scratch |
|---|---|---|
| horse | Horse | Horse |
| local-images | Local | Horse |
| local-work | Horse | Local |
| local-both | Local | Local |

Two repetitions run in forward/reverse order. These measure **cached-image
startup**, not cold image builds. No system cache is flushed. The first warmup
may use existing cache hits; its duration must not be called a cold-build time.
Every no-op must return 0 and every oracle 1; a failure stops the pilot.
This initial pilot measures one active task, not production concurrency scaling.

`outcomes.jsonl` records stages, setup-file upload time, and container cleanup;
stage 3 also records start/inspection timings. Results and service logs stay
durable throughout. `staging.json` records the one-time image/task copy costs.
`complete.json` is written only after every comparison passes. Local replicas
are removed on normal exit or Python failure; Slurm termination can bypass
cleanup, so custom scratch mounts need the site's job cleanup policy.

## ZIH

Submit `hpc/zih/crosscodeeval_warmup.sbatch` from each cluster's login node with
`--partition=barnard`, `romeo`, or `julia`, respectively. For Barnard SSD tests,
request `--constraint=local_disk`; ordinary nodes test RAM instead. Request four
CPUs, 32 GiB and one hour (script defaults). Direct Slurm output to Horse.

The launcher uses `storage.sh` / `storage_paths.sh`:

- `ZIH_IMAGE_DIR`: existing canonical image cache.
- `ZIH_WARMUP_DIR`: durable pilot results and reusable prepared images.
- `ZIH_STATIC_TMPDIR/containers`: job-private local replicas, using the verified
  SSD/RAM mapping. Maximum estimated local footprint defaults to 8 GiB and must
  fit within a quarter of allocated memory on tmpfs.
- `CCE_WARMUP_LOCAL_ROOT`: optional approved scratch/NVMe location override.
- `CCE_WARMUP_TASKS`: materialized input override. Default is the preserved
  CrossCodeEval recovery tasks; input inventory records the source.

Example (run from the repository on Julia):

```bash
sbatch --partition=julia \
  --output=/data/horse/ws/frwe188h-trp-shared/crosscodeeval/logs/warmup-%j.out \
  hpc/zih/crosscodeeval_warmup.sbatch
```

Pass `--warmup-only` after the script name to prepare images without comparison.
Outside ZIH, invoke `warmup.py` with explicit `--tasks`, `--cache`, `--out` and
`--local-root` inside a Slurm allocation, with the project's Python environment,
Apptainer and bridge dependencies available. Set dependency caches to data
storage. No path discovery falls back to home.

This pilot does not change storage placement in production validation jobs.
Choose a production policy only after comparing copy cost, repeated startup,
setup upload, verifier correctness, and a subsequent concurrency test.
