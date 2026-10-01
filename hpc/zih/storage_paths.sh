#!/bin/bash
# Single mapping of ZIH cluster/partition policies and persistent path names.
# Source storage.sh and call zih_storage_init DATA_ROOT VENV to resolve these
# plus job-specific local paths. No eval or automatic home fallback.

zih_storage_paths() {
    export ZIH_CLUSTER=${SLURM_CLUSTER_NAME:?Missing Slurm cluster name}
    export ZIH_PARTITION=${SLURM_JOB_PARTITION:-$ZIH_CLUSTER}
    # Cluster : partition -> local static-check storage preference.
    # /tmp is used only when it is a separate executable SSD filesystem.
    case "$ZIH_CLUSTER:$ZIH_PARTITION" in
        barnard:barnard) ZIH_STATIC_POLICY=ssd_then_ram ;;
        romeo:romeo)     ZIH_STATIC_POLICY=ssd_then_ram ;;
        julia:julia)     ZIH_STATIC_POLICY=ram ;;
        *) echo "No ZIH storage mapping for $ZIH_CLUSTER:$ZIH_PARTITION" >&2; return 2 ;;
    esac
    export ZIH_STATIC_POLICY
    export ZIH_DATA_ROOT
    ZIH_DATA_ROOT=$(realpath -m -- "${1:?Pass the persistent dataset workspace directory}")
    case "$ZIH_DATA_ROOT" in
        /data/horse/*|/data/ws/*|/data/cat/*) ;;
        *) echo "ZIH_DATA_ROOT must be a data workspace: $ZIH_DATA_ROOT" >&2; return 2 ;;
    esac
    export ZIH_RUNTIME_DIR="$ZIH_DATA_ROOT/runtimes"
    export ZIH_CACHE_DIR="$ZIH_DATA_ROOT/cache"
    # Warmup/benchmark outputs are durable; local replicas are job-private.
    export ZIH_IMAGE_DIR="$ZIH_DATA_ROOT/images"
    export ZIH_WARMUP_DIR="$ZIH_DATA_ROOT/warmup"
    export ZIH_RUNS_DIR="$ZIH_DATA_ROOT/runs"
    export ZIH_LOG_DIR="$ZIH_DATA_ROOT/logs"
    export ZIH_SCRATCH_DIR="$ZIH_DATA_ROOT/scratch"
    export ZIH_STORAGE_RECORD_DIR="$ZIH_DATA_ROOT/storage-records"
    export ZIH_PYTHON_INSTALL_DIR="$ZIH_DATA_ROOT/python"
    # No input subdirectory is imposed: existing datasets use upstream/,
    # parquets-pinned/, etc. Keep their input paths beneath ZIH_DATA_ROOT.
}
