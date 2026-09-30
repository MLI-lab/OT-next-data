#!/bin/bash
#SBATCH --job-name=inferredbugs-warnings
#SBATCH --partition=cpu
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=48
#SBATCH --time=24:00:00
#SBATCH --array=0-63%16
set -euo pipefail
repo=/home/hpc/y500bb/y500bb12/OT_next_data
source "$repo/hpc/helma/proxy.sh"
root=${AUDIT_RUN:-$repo/runs/inferredbugs-20260925/full-warning-audit}
export APPTAINER_NO_MOUNT=hostfs,bind-paths,cwd
scratch=${TMPDIR:?}/warning-audit
mkdir -p "$scratch/infer"
test -f "$root/smoke-passed"
"$root/venv/bin/python" - "$root" <<'PY'
import json,pathlib,sys
root=pathlib.Path(sys.argv[1]);r=json.loads((root/'runtime-manifest.json').read_text())
assert len(r)==8 and all('error' not in v for v in r.values()),r
PY
cp "$root/inputs.sqlite" "$scratch/inputs.sqlite"
tar -xJf "$root/infer-0.17.0.tar.xz" --strip-components=1 -C "$scratch/infer"
cp "$root/infersharp-engine.tar.gz" "$scratch/infersharp-engine.tar.gz"
extra=()
if [ -f "$root/retry-ids.txt" ]; then extra+=(--ids-file "$root/retry-ids.txt"); fi
if [ "${AUDIT_RETAIN_CACHE:-0}" = 1 ]; then extra+=(--dependency-cache "$root/dependency-cache"); fi
"$root/venv/bin/python" -u "$root/patcher.snapshot.py" --audit-warnings run \
 --inputs "$scratch/inputs.sqlite" --output "$root" --scratch "$scratch/tasks" \
 --images "$root/images" --infer "$scratch/infer" --infersharp-engine "$scratch/infersharp-engine.tar.gz" \
 --proxy-helper "$root/configure_maven_proxy.snapshot.py" \
 --vendor-cache "$root/vendor-cache" --workers "${AUDIT_WORKERS:-6}" --timeout "${AUDIT_TIMEOUT:-5400}" --shards "${AUDIT_SHARDS:-64}" --shard "${SLURM_ARRAY_TASK_ID:?}" "${extra[@]}"
