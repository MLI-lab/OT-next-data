#!/bin/bash
#SBATCH --job-name=validation-publish
set -euo pipefail
umask 077
validation_secrets_file="${OT_SECRETS_FILE:-$HOME/.env}"
if [[ -r "$validation_secrets_file" ]]; then
    set -a
    source "$validation_secrets_file" >/dev/null 2>&1
    set +a
fi
: "${OT_WORKSPACE:?Source env.sh before submitting}"
: "${TMPDIR:?Helma must provide node-local TMPDIR}"
: "${VALIDATION_REPO:?Set the frozen code snapshot path}"
/usr/bin/python3 "$VALIDATION_REPO/hpc/local_runtime.py" stage \
    "${OT_RUNTIME_BUNDLE:-$OT_WORKSPACE/runtime/prep.json}" "$VALIDATION_REPO" "$TMPDIR/publish-runtime"
source "$TMPDIR/publish-runtime/env.sh"
source "$VALIDATION_REPO/hpc/helma/proxy.sh"
export PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
exec "$HARBOR_BRIDGE_VENV/bin/python" -u "$VALIDATION_REPO/hpc/helma/publish_worker.py" "$1" "$2"
