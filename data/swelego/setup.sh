#!/bin/bash
set -euo pipefail
marker=/setup_files/.swelego-@IDENTITY@
if [ -f "$marker" ]; then exit 0; fi
@PREPARED@
export PATH=/opt/conda/envs/testbed/bin:/opt/conda/bin:$PATH
set +u
source /opt/conda/bin/activate testbed
set -u
export PIP_DISABLE_PIP_VERSION_CHECK=1
export PIP_DEFAULT_TIMEOUT=60
export PIP_PROGRESS_BAR=off
export PIP_ROOT_USER_ACTION=ignore
export GIT_TERMINAL_PROMPT=0
@EXPORTS@
started=$SECONDS
phase=checkout
timings=/setup_files/setup-phases.tsv
: > "$timings"
last=$SECONDS
finish_phase() {
    printf '%s\t%s\n' "$phase" "$((SECONDS-last))" >> "$timings"
    last=$SECONDS
    phase=$1
}
finish() {
    status=$?
    trap - EXIT
    finish_phase done
    /opt/conda/bin/python - "$started" "$SECONDS" "$status" <<'PY'
import json, pathlib, sys
phases = {}
for line in pathlib.Path('/setup_files/setup-phases.tsv').read_text().splitlines():
    name, seconds = line.split('\t')
    phases[name] = int(seconds)
result = {'seconds': int(sys.argv[2])-int(sys.argv[1]), 'exit_code': int(sys.argv[3]), 'phases': phases}
pathlib.Path('/setup_files/setup-timing.json').write_text(json.dumps(result, indent=2)+'\n')
print('SWELEGO_SETUP_TIMING=' + json.dumps(result), flush=True)
PY
    exit "$status"
}
trap finish EXIT
mkdir -p /testbed
cd /testbed
git init
git remote remove origin 2>/dev/null || true
git remote add origin @REPO@
git fetch --depth @DEPTH@ origin @COMMIT@
git checkout --detach --force @COMMIT@
test "$(git rev-parse HEAD)" = @COMMIT@
@VERSION_TAG@
git remote remove origin
finish_phase conda
@CONDA@
finish_phase preinstall
@PREINSTALL@
finish_phase dependencies
python -m pip install --no-deps@PIP_CACHE@ -r /setup_files/requirements.txt
finish_phase install
# Frozen dependencies are already restored. The local checkout must not resolve
# newer versions through extras or an isolated build environment.
export PIP_NO_DEPS=1
# pip maps this negative-option environment variable to build_isolation itself.
export PIP_NO_BUILD_ISOLATION=0
@INSTALL@
test "$(git rev-parse HEAD)" = @COMMIT@
touch "$marker"
