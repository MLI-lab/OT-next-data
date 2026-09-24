#!/bin/bash
# Install the two pinned dependencies and the host tooling for this repo.
#
#   ./setup.sh /path/to/workspace
#
# The workspace holds what must not live in the repo: the OpenThoughts-Agent
# checkout, the python env, model weights, task archives and run outputs.
# Writes env.sh next to this script; source it before using run/ or verify/.
set -euo pipefail

# Pinned upstreams. Both are dependencies, not vendored code, so this repo stays
# easy to compare with them. Update deliberately and re-run the checks in
# verify_pipeline.py afterwards.
OTAGENT_REPO=${OTAGENT_REPO:-https://github.com/open-thoughts/OpenThoughts-Agent.git}
OTAGENT_PIN=${OTAGENT_PIN:-3bd1917e62c9d03d73063b433f5c442c279c0563}
HARBOR_PIN=${HARBOR_PIN:-7faf878c14b6d72737579ec936ebf5f74ba3194d}

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ws=${1:?Usage: setup.sh <workspace dir>}
mkdir -p "$ws"
ws=$(cd "$ws" && pwd)

# 1. Official OpenThoughts-Agent provides data/local/run_tracegen.py.
#    The experiment wrapper lives here in teacher_traces/.
if [[ ! -d "$ws/OpenThoughts-Agent/.git" ]]; then
    git clone "$OTAGENT_REPO" "$ws/OpenThoughts-Agent"
fi
# Preserve existing checkouts: an old private clone needs a separate workspace.
actual_repo=$(git -C "$ws/OpenThoughts-Agent" remote get-url origin)
if [[ "${actual_repo%.git}" != "${OTAGENT_REPO%.git}" ]]; then
    echo "Existing checkout uses $actual_repo; expected $OTAGENT_REPO. Use a fresh workspace or set OTAGENT_REPO explicitly." >&2
    exit 1
fi
git -C "$ws/OpenThoughts-Agent" fetch --quiet origin
git -C "$ws/OpenThoughts-Agent" checkout --quiet "$OTAGENT_PIN"

# 2. Host env: the patcher, the checks and the analysis run here; Harbor is
#    needed because harbor_patches/bridge_worker.py wraps its apptainer worker.
python3 -m venv "$ws/envs/prep"
"$ws/envs/prep/bin/pip" install --quiet --upgrade pip
"$ws/envs/prep/bin/pip" install --quiet -r "$here/requirements.txt"
"$ws/envs/prep/bin/pip" install --quiet \
    "harbor[daytona] @ https://github.com/marin-community/harbor/archive/$HARBOR_PIN.zip"

cat > "$here/env.sh" <<EOF
# Written by setup.sh on $(date -Is). Source before running anything here.
export PILOT_ROOT=$ws
export OTAGENT_ROOT=$ws/OpenThoughts-Agent
export OT_NEXT_DATA=$here
export PATH=$ws/envs/prep/bin:\$PATH
export PYTHONPATH=$here\${PYTHONPATH:+:\$PYTHONPATH}
EOF

echo "Installed. Workspace: $ws"
echo "  OpenThoughts-Agent @ $OTAGENT_PIN"
echo "  harbor             @ $HARBOR_PIN"
echo "Next: source $here/env.sh"
echo "Then: python $here/verify_pipeline.py tests   # 43 tests, no cluster needed"
echo "The GPU runtime image is built separately: hpc/<cluster>/build_runtime.sh (see README)."
