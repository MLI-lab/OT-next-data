#!/bin/bash
# Source explicitly from a ZIH sbatch script, then call zih_storage_init ROOT VENV.
# Persistent outputs/containers stay on data storage. Only bounded static-check
# scratch and the Python base are staged locally. No changes to running jobs.

zih_data_path() {
    case "$(realpath -m -- "$1")" in
        /data/horse/*|/data/ws/*|/data/cat/*) ;;
        *) echo "ZIH persistent data must not use home: $1" >&2; return 2 ;;
    esac
}

zih_storage_init() {
    local root=$1 venv=$2 helper cluster candidate='' fs options free_kb memory_mb
    helper=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
    : "${SLURM_JOB_ID:?ZIH storage setup requires a Slurm allocation}"
    zih_data_path "$root" && zih_data_path "$venv" || return
    cluster=${SLURM_CLUSTER_NAME:?Missing Slurm cluster name}
    case "$cluster" in barnard|romeo|julia) ;; *) echo "Unsupported ZIH cluster: $cluster" >&2; return 2 ;; esac
    mkdir -p "$root/scratch" "$root/runtimes" "$root/storage-records"
    # Keep full pipeline evidence durable. Static tasks use a separate local dir.
    export TMPDIR
    TMPDIR=$(mktemp -d "$root/scratch/job-${SLURM_JOB_ID}-XXXXXX")
    export ZIH_DURABLE_SCRATCH=$TMPDIR
    export UV_CACHE_DIR="$root/cache/uv" UV_PYTHON_INSTALL_DIR="$root/python"
    export PIP_CACHE_DIR="$root/cache/pip" HF_HOME="$root/cache/huggingface"
    export XDG_CACHE_HOME="$root/cache/xdg" PYTHONDONTWRITEBYTECODE=1
    export PYTHONNOUSERSITE=1
    # Prefer actual SSD /tmp on Romeo/Barnard. Never mistake tmpfs or root for SSD.
    fs=$(findmnt -T /tmp -no FSTYPE)
    if [[ "$cluster" != julia && "$fs" =~ ^(xfs|ext4)$ && $(findmnt -T /tmp -no TARGET) == /tmp ]]; then
        candidate=/tmp
    else
        # RAM is bounded by the allocation, not the node's much larger free RAM.
        memory_mb=${SLURM_MEM_PER_NODE:-0}
        if [[ "$memory_mb" == 0 && ${SLURM_MEM_PER_CPU:-0} != 0 ]]; then
            memory_mb=$((SLURM_MEM_PER_CPU * SLURM_CPUS_PER_TASK))
        fi
        if (( memory_mb >= 8192 )); then candidate=/dev/shm; fi
    fi
    # A caller may deliberately request shared-only operation.
    if [[ ${ZIH_STATIC_STORAGE:-auto} == shared ]]; then candidate='';
    elif [[ ${ZIH_STATIC_STORAGE:-auto} != auto ]]; then
        echo 'ZIH_STATIC_STORAGE must be auto or shared' >&2; return 2
    fi
    if [[ -n "$candidate" ]]; then
        options=$(findmnt -T "$candidate" -no OPTIONS)
        free_kb=$(df -Pk "$candidate" | awk 'END {print $4}')
        if [[ ",$options," == *,noexec,* || ",$options," == *,ro,* ]] || (( free_kb < 2097152 )); then
            echo "Local staging unavailable at $candidate; using durable scratch" >&2
            candidate=''
        fi
    fi
    if [[ -n "$candidate" ]]; then
        export ZIH_STATIC_TMPDIR
        ZIH_STATIC_TMPDIR=$(mktemp -d "$candidate/ot-static-${USER}-${SLURM_JOB_ID}-XXXXXX")
        chmod 700 "$ZIH_STATIC_TMPDIR"
    else
        export ZIH_STATIC_TMPDIR="$TMPDIR/static"
        mkdir -p "$ZIH_STATIC_TMPDIR"
    fi
    # Conservative cap for copied static task data; runtime uses about 120 MB.
    export ZIH_STATIC_MAX_BYTES=${ZIH_STATIC_MAX_BYTES:-1073741824}
    local record="$root/storage-records/${SLURM_JOB_ID}.json"
    /usr/bin/python3 -B "$helper/runtime_storage.py" "$venv" "$root/runtimes" \
        --local "$ZIH_STATIC_TMPDIR" > "$record" || { zih_storage_cleanup; return 1; }
    export ZIH_PYTHON ZIH_STATIC_PYTHON
    ZIH_PYTHON=$(/usr/bin/python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["python"])' "$record")
    ZIH_STATIC_PYTHON=$(/usr/bin/python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["static_python"])' "$record")
    export PATH="$(dirname "$ZIH_PYTHON"):$PATH"
    echo "ZIH $cluster: durable=$TMPDIR static=$ZIH_STATIC_TMPDIR Python=$ZIH_PYTHON"
}

zih_storage_cleanup() {
    # Never delete persistent results or canonical input data.
    if [[ ${ZIH_STATIC_TMPDIR:-} == /tmp/ot-static-* || ${ZIH_STATIC_TMPDIR:-} == /dev/shm/ot-static-* ]]; then
        rm -rf -- "$ZIH_STATIC_TMPDIR"
    fi
}
