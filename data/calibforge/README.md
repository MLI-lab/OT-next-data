# CalibForge pilot

Original source: [AweAI-Team/CalibForge](https://huggingface.co/datasets/AweAI-Team/CalibForge/tree/fb1e75441a94b8bb0ced08acd6b59e711704d70a),
commit `fb1e75441a94b8bb0ced08acd6b59e711704d70a`.
The release contains 5,431 Harbor-style tasks, with environments and verifiers,
but **no `solution/solve.sh` files**. Verifier tests exist; reference solutions
are absent. Stage 4 will be skipped until real solutions are supplied, which
does not satisfy the four-stage pilot gate.

Storage: `/data/horse/ws/frwe188h-trp-shared/calibforge`.
`upstream/repo` is the original Git checkout, including all 2,591 LFS files
(1,634,481,128 bytes). `upstream/download.json` records download completion.
Do not edit the original checkout. Repository metadata and original metadata
JSONL files are also retained under `upstream/`.

`pilot.py` selects ten tasks deterministically with seed 42: one from each of
ten CPU-oriented categories, alternating the two calibration subsets. It does
not select on validation outcomes. The sample is not representative of all
sixteen categories. Each run records task IDs, metadata, and original file
hashes in `selection.json`, and freezes validation code before submission.

```bash
PYTHONDONTWRITEBYTECODE=1 LITELLM_LOCAL_MODEL_COST_MAP=True \
  /data/horse/ws/frwe188h-trp-shared/seta/venv/bin/python \
  data/calibforge/pilot.py --cluster romeo --submit
```

The allocation requests 4 CPUs, 16 GB RAM, four hours, and no GPUs. One task
runs at a time; static concurrency is two. Stages 1, 3, 4 and 5 use the default
training profile, host networking, and fresh containers between stages. No
model stages or publishing are enabled. The environment is shared read-only
with SETA's existing workspace virtualenv; caches/results go under CalibForge.

Keep any verified dataset repairs in `data/calibforge/patch.py`, with their
source revision and per-task change reports. No dataset repairs have been
implemented yet. Do not weaken verifiers or manufacture oracle passes to
clear the missing-solution gate. A future patched rerun can use
`pilot.py --input /data/horse/.../patched/tasks --cluster romeo --submit`.

## Current baseline (2026-09-30)

Run: `runs/20260930T140821090694Z` under the storage root above.
Romeo job **8996349**, replacing cancelled Barnard job **38906695**.
The original submission used individually downloaded pilot files at the same
pinned revision while the full LFS download completed. The run's input copy
and file hashes are frozen.

Romeo started the job at **16:18:22 local time on September 30**, despite its
initial 23:17 estimate. Barnard had estimated October 3 around 01:23. Julia
closed SSH connections during the capacity check. The worker recorded a
four-CPU budget, concurrency one, and Apptainer 1.5.2. Slurm displays eight
allocated logical CPUs. No container-stage results are available yet.
A separate single-worker login static contract was prepared under
`login-static/`, then its runner was stopped when Romeo started; it is not
completed validation evidence.
Inspect `submission.json`, `slurm-romeo-8996349.out`, then
`execution.json` and `report/summary.json` once the job runs. A queued job is
not a passed pilot, and missing oracle solutions remain unresolved.
