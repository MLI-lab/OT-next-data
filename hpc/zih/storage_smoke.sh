#!/bin/bash
set -euo pipefail
repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
source "$repo/hpc/zih/storage.sh"
root=/data/horse/ws/frwe188h-trp-shared/crosscodeeval
zih_storage_init "$root" "$root/venv"
trap zih_storage_cleanup EXIT
"$ZIH_PYTHON" "$repo/hpc/zih/storage_smoke.py"
