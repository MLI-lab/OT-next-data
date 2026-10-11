# Historical CalibForge baseline

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
