# InferredBugs on ZIH CPU nodes

For validation jobs, see [storage placement and automatic staging](storage.md).
New ZIH validation launchers should source `storage.sh` and use its Python paths;
a venv located on Horse can otherwise still point to a Python installation in home.

The CPU partition reported by this cluster is `barnard` (checked with `sinfo`
and `scontrol show partition barnard` on 2026-09-30). Apptainer 1.4.5 and `uv`
are installed. These scripts request no GPUs. The unrelated teacher launcher
is still a skeleton.

Julia is also available at `frwe188h@julia.hpc.tu-dresden.de` (no `login1.`
prefix). It sees the same repository and Horse workspace. To use it, SSH there
and run the submission commands below with `--partition=julia`, overriding the
scripts' Barnard default. Job IDs and queue commands belong to the cluster
where the job was submitted. Warmup requests 4 CPUs and 32 GiB on either cluster.

The next dataset milestone is verifier discrimination on the 5,877 tasks kept
under the 300-second filter: buggy code must be rejected for the original
warning, and the complete historical fix must pass. Then regenerate warning
keys and the parquet. See [the dataset status](../../data/inferredbugs/STATUS.md).

## 1. Warmup

Submit from the repository root. Defaults use the existing Horse workspace;
export `IB_ROOT` to use another shared workspace. Check its expiry with
`ws_list` and extend it or archive evidence before it expires.

```bash
cd /home/frwe188h/OT-next-data
export OT_DATA_REPO="$PWD"
export IB_ROOT=/data/horse/ws/frwe188h-trp-shared/inferredbugs
mkdir -p "$IB_ROOT/logs"
warmup=$(sbatch --parsable --output="$IB_ROOT/logs/%x-%j.out" hpc/zih/inferredbugs_warmup.sbatch)
echo "$warmup"
```

Large files must stay under `/data/horse`, `/data/ws`, or `/data/cat`, whichever
has an available workspace. The scripts reject home paths for large artifacts,
including overridden cache/output paths. `/data/horse` is available here;
`/data/ws` and `/data/cat` were absent when checked. Managed Python downloads,
uv/Hugging Face caches and temporary build storage also default beneath `IB_ROOT`.

Warmup downloads the original TaskTrove parquet to `upstream/tasks.parquet`
with a pinned revision and verified SHA-256. TaskTrove currently stores this
original under `deprecated/DCAgent__inferredbugs-sandboxes-verifier/`; the
download's revision, source URL and digest are saved alongside it. You can
download it independently with `python3 hpc/zih/download_inferredbugs.py`.

Warmup also creates a Python 3.12 venv with pyarrow, the pinned source checkout,
eight SIF images, analyzer downloads, bare repository caches, `ids.txt`, and
`pilot-ids.txt` (one task per retained analyzer version). It can be rerun;
existing images are reused. After image recipe changes, use a fresh image
directory via `CHECK_IMAGES`. Repository clone failures are printed by
`warmup.py` but do not fail that program: inspect the log for `failed:`.

CPU-node outbound access and Apptainer fakeroot/overlay support still need the
warmup and pilot to prove them. No Helma proxy is loaded. The reused Maven
helper gets public Maven Central explicitly; existing user proxy variables
are respected. You can override `INFERREDBUGS_CENTRAL_MIRROR` if needed.

## 2. Pilot, then all tasks

After warmup finishes successfully:

```bash
export CHECK_RUN="$IB_ROOT/runs/pilot-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$CHECK_RUN"
cp "$IB_ROOT/pilot-ids.txt" "$CHECK_RUN/ids.txt"
CHECK_WORKERS=2 sbatch --cpus-per-task=8 --mem=32G \
  --output="$IB_ROOT/logs/%x-%j.out" \
  hpc/zih/inferredbugs_verifier_check.sbatch
```

After the pilot completes, inspect the results:

```bash
"$IB_ROOT/venv/bin/python" hpc/helma/inferredbugs_verifier_check_summary.py "$CHECK_RUN"
```

Expected: buggy reward `0` with `original_warning_remains`, full reward `1`
with `passed`. A successful Slurm exit means the harness completed, **not**
that rewards were correct. Resolve container/build/network failures before
scaling. `full/new_warnings` needs review: the next key rebuild can allow
legitimate warnings introduced by the historical fix.

Once the pilot is reviewed, submit 32 shards with at most eight running at
once; each shard runs up to eight verifications concurrently:

```bash
export CHECK_RUN="$IB_ROOT/runs/full-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$CHECK_RUN"
cp "$IB_ROOT/ids.txt" "$CHECK_RUN/ids.txt"
export CHECK_SHARDS=32
sbatch --array=0-31%8 --output="$IB_ROOT/logs/%x-%A_%a.out" \
  hpc/zih/inferredbugs_verifier_check.sbatch
```

Each array element requests 32 CPUs and 128 GiB for 12 hours. Tune resources
and concurrency from pilot measurements. Override account/QOS with normal
`sbatch --account=... --qos=...` options if needed; the scripts use your default
account. `CHECK_SHARDS` must match a contiguous array starting at zero. Unset
it before another non-array pilot.

`CHECK_SOURCE`, `CHECK_IMAGES`, `IB_PYTHON`, `CHECK_WORKERS`,
`CHECK_STEP_TIMEOUT` (2400 seconds), and `CHECK_TIMEOUT` (7200 seconds) are
overridable. These are diagnostic check budgets; final packaged tasks retain
their own agent/verifier limits. Scratch uses a private directory beneath
`IB_SCRATCH` or `$IB_ROOT/scratch`, and is cleaned on exit. Scratch must also be
in data space. The pilot needs to confirm Apptainer directory overlay support
on the selected filesystem. Persistent data stays in `IB_ROOT`.

To test an already generated artifact, export `CHECK_PARQUET` pointing to its
parquet before submitting the check. The harness loads the selected task
archives from that file instead of generating their verifier in memory. In
this mode the verifier's step budget comes from the parquet; changing
`CHECK_STEP_TIMEOUT` does not rewrite it. A preliminary parquet can be built
before the pilot; regenerate the final parquet after rebuilding warning keys.

Every variant retains `TASK/{buggy,full}/summary.json` and `logs.tar.gz`,
including its verifier `result.json` when produced. Do not enable the harness's
compact `--summary-file` mode: it drops the successful evidence needed below.
Re-submitting the same array resumes from existing summaries, including failed
ones. To retry failures, use a new run directory with the selected IDs. Keep
the IDs, shard count, code, and settings fixed while a run is active or resumed;
use a new run directory after changing them.

## 3. Rebuild keys and package

Summarize the full run with the same summary command. Before changing tables,
check that every selected ID has both summaries and a verifier `result.json`
inside both archives. A missing result is an infrastructure failure to retry,
not evidence that a warning was removed. `verifier_keys.py` skips missing
pairs, so its dry-run `not run` count must be explained: with this selection,
209 of the 6,086 audited tasks were deliberately excluded by the time filter.

```bash
python="$IB_ROOT/venv/bin/python"
"$python" data/inferredbugs/warning_identity/verifier_keys.py \
  --buggy "$CHECK_RUN/*/buggy/logs.tar.gz" --fix "$CHECK_RUN/*/full/logs.tar.gz"
```

After reviewing completeness, failures and proposed discards, write the keys
and regenerate dependent tables. These commands modify tracked dataset files;
run them only after the array has finished and review the diff.

```bash
"$python" data/inferredbugs/warning_identity/verifier_keys.py \
  --buggy "$CHECK_RUN/*/buggy/logs.tar.gz" --fix "$CHECK_RUN/*/full/logs.tar.gz" --write
"$python" data/inferredbugs/warning_identity/recovery_tables.py
"$python" data/inferredbugs/warning_identity/provenance.py

export IB_INPUT="$IB_ROOT/upstream/tasks.parquet"
export IB_OUTPUT="$IB_ROOT/tasks.patched.parquet"
sbatch --output="$IB_ROOT/logs/%x-%j.out" hpc/zih/inferredbugs_package.sbatch
```

The input is the original TaskTrove
`DCAgent__inferredbugs-sandboxes-verifier` parquet downloaded by warmup.
The package job refuses to overwrite an existing output. It trusts the proven
recipes and updated warning tables; it does not replace final validation.
Next run validation stages 1 (static), 3 (images), and 4/5 (oracle/NOP on a
sample of the regenerated artifact), then agent trials and trajectories as
described in [validation](../../validation/README.md).
