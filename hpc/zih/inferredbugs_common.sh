#!/bin/bash
# Sourced by the CPU jobs. Submit from the repository root, or export OT_DATA_REPO.
set -euo pipefail
: "${SLURM_JOB_ID:?Submit through sbatch}"
repo=${OT_DATA_REPO:-${SLURM_SUBMIT_DIR:?}}
[[ -f "$repo/data/inferredbugs/warmup.py" ]] || {
  echo 'Submit from the OT-next-data root or export OT_DATA_REPO.' >&2; exit 2;
}
export IB_ROOT=${IB_ROOT:-/data/horse/ws/frwe188h-trp-shared/inferredbugs}
[[ "$IB_ROOT" = /* ]] || { echo 'IB_ROOT must be an absolute shared-storage path.' >&2; exit 2; }
require_data_path() {
  case "$(realpath -m -- "$1")" in
    /data/horse/*|/data/ws/*|/data/cat/*) ;;
    *) echo "Large artifacts must live in ZIH data space, never home: $1" >&2; exit 2 ;;
  esac
}
require_data_path "$IB_ROOT"
export CHECK_SOURCE=${CHECK_SOURCE:-$IB_ROOT/source}
export CHECK_IMAGES=${CHECK_IMAGES:-$IB_ROOT/images}
export IB_PYTHON=${IB_PYTHON:-$IB_ROOT/venv/bin/python}
export INFERREDBUGS_DOWNLOAD_CACHE=${INFERREDBUGS_DOWNLOAD_CACHE:-$IB_ROOT/downloads}
export INFERREDBUGS_REPOSITORY_CACHE=${INFERREDBUGS_REPOSITORY_CACHE:-$IB_ROOT/repositories}
# Use public Central, including in the reusable Maven helper under hpc/helma.
# No Helma proxy is sourced. Explicit user proxy variables are respected.
export INFERREDBUGS_CENTRAL_MIRROR=${INFERREDBUGS_CENTRAL_MIRROR:-https://repo.maven.apache.org/maven2}
export APPTAINER_NO_MOUNT=hostfs,bind-paths,cwd
export APPTAINER_CACHEDIR=${APPTAINER_CACHEDIR:-$IB_ROOT/apptainer-cache}
export UV_CACHE_DIR=${UV_CACHE_DIR:-$IB_ROOT/uv-cache}
export UV_PYTHON_INSTALL_DIR=${UV_PYTHON_INSTALL_DIR:-$IB_ROOT/uv-python}
export HF_HOME=${HF_HOME:-$IB_ROOT/huggingface}
export HF_HUB_CACHE=${HF_HUB_CACHE:-$HF_HOME/hub}
export HF_DATASETS_CACHE=${HF_DATASETS_CACHE:-$HF_HOME/datasets}
export XDG_CACHE_HOME=${XDG_CACHE_HOME:-$IB_ROOT/cache}
scratch_base=${IB_SCRATCH:-$IB_ROOT/scratch}
for path in "$CHECK_SOURCE" "$CHECK_IMAGES" "$IB_PYTHON" \
  "$INFERREDBUGS_DOWNLOAD_CACHE" "$INFERREDBUGS_REPOSITORY_CACHE" \
  "$APPTAINER_CACHEDIR" "$UV_CACHE_DIR" "$UV_PYTHON_INSTALL_DIR" \
  "$HF_HOME" "$HF_HUB_CACHE" "$HF_DATASETS_CACHE" "$XDG_CACHE_HOME" "$scratch_base"; do
  require_data_path "$path"
done
mkdir -p "$IB_ROOT" "$APPTAINER_CACHEDIR" "$scratch_base"
# Keep even temporary large artifacts in data space, as requested for ZIH.
scratch=$(mktemp -d "$scratch_base/inferredbugs-${SLURM_JOB_ID}-XXXXXX")
trap 'rm -rf -- "$scratch"' EXIT
export TMPDIR=$scratch
export APPTAINER_TMPDIR=$scratch
export PYTHONUNBUFFERED=1
cd "$repo"
