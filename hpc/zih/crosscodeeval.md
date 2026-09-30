# CrossCodeEval CPU validation on ZIH

Use the existing Horse workspace, currently
`/data/horse/ws/frwe188h-trp-shared/crosscodeeval`. Check its expiry with `ws_list`.
The merged TaskTrove repair is pinned at `12df4483fe99c79ccbb4c923d76ab5a2b042e64a`;
there is no need to rerun context reconstruction against the original benchmark.

From the repository root, set up a dedicated environment and prepare inputs:

```bash
export CCE_ROOT=/data/horse/ws/frwe188h-trp-shared/crosscodeeval
export UV_CACHE_DIR="$CCE_ROOT/cache/uv"
export UV_PYTHON_INSTALL_DIR="$CCE_ROOT/python"
export HF_HOME="$CCE_ROOT/cache/huggingface"
export TMPDIR="$CCE_ROOT/scratch/setup"
mkdir -p "$TMPDIR" "$CCE_ROOT/logs"
python3 -m validation.upstream setup
uv venv --python 3.12 "$CCE_ROOT/venv"
uv pip install --python "$CCE_ROOT/venv/bin/python" ./external/harbor-validation -r requirements.txt
"$CCE_ROOT/venv/bin/python" hpc/zih/crosscodeeval_prepare.py "$CCE_ROOT"
sbatch --output="$CCE_ROOT/logs/%x-%j.out" hpc/zih/crosscodeeval_validation.sbatch
```

Preparation keeps the published parquets, applies only the existing patcher's
`pin_parquet` operation, and records input/output hashes in
`parquets-pinned/source.json`. The sample has 3 C#, 3 Java, 2 Python and 2
TypeScript tasks. Full coverage is 6,710 tasks.

One Barnard allocation requests 32 CPUs, 128 GB RAM, eight hours and no GPUs.
It runs stages 1, 3, 4 and 5 on the sample, then on the entire dataset only if
all smoke stages pass. Contract preparation applies the standard instruction
suffix normalization to a validation copy. The two documented shared-verifier
static-check exclusions are recorded in each contract.

The existing allocation worker starts the Apptainer bridge, uses the fixed
fakeroot message-queue cleanup, and saves contracts, reports, logs and task
evidence under `runs/JOB_ID/{smoke,full}/`. Images and scratch remain in Horse
storage. The job does not publish to Hugging Face. Inspect `execution.json`
and `report/summary.json` after completion; queue acceptance is not a passing
validation result.

For a larger Julia allocation, submit through `julia.hpc.tu-dresden.de` with
`--partition=julia --cpus-per-task=128 --mem=512G --time=08:00:00` and export
`CCE_CONCURRENCY=112`. Set `CCE_SMOKE_FROM` to a completed smoke result directory
to skip repeating it: the launcher checks its successful exit, contract hash,
ten-task coverage, stages and dataset revision before starting the full run.
Without this variable the smoke test runs first as usual. Runtime scales only
as far as container startup and filesystem throughput allow.


Future submissions use the configured task concurrency for stage 1, capped by
allocated CPUs, instead of the previous eight-task limit. Static output uses one shared log and an append-only task-outcome journal;
the summary is assembled at completion or reconstructed after interruption. Job 137304
continues with its original frozen code and settings. Horse remains the scratch
location under the project's storage policy; increasing CPU parallelism does
not remove shared-filesystem latency or contract preparation costs.

### Checkpoint restart

The restart launcher `crosscodeeval_resume.sbatch` requests 128 CPUs, 512 GB and
12 hours on Julia. It requires `CCE_STATIC_RESUME` and `OT_DATA_REPO`; set
`CCE_CONCURRENCY=112`. It preserves completed static successes under their
original path-check adaptation, then runs pending checks and container phases
3 → 5 → 4 with live per-task reuse. A fresh ten-task smoke run gates the full run.
The full run opens a PR on `FWeindel/validated-tasks` only after complete gate
outcomes. `CCE_PUBLISH_README=1` additionally requires a working Claude login.
On failure, this launcher retains scratch for recovery; on normal success it
removes scratch after archiving evidence and publishing. The checkpoint and
completed-run archives are retained.
