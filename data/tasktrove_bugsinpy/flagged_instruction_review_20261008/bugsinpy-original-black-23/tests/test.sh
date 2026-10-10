#!/bin/bash
set -euo pipefail
bash /setup_files/setup.sh
mkdir -p /logs/verifier
echo '7e247141351d5ab9a452f199cbfeb0cc96c1308f0753db9d6531a7cae03e023b  /tests/fixed-tests.tar.gz' | sha256sum -c -
tar -xzf /tests/fixed-tests.tar.gz -C /app
echo '27e210f77ce6822b5a0a656bd7012ed3ffd0aa702a2dd3f2102b830dd935fb89  /tests/requirements.lock' | sha256sum -c -
venv_start=$SECONDS
rm -rf /tests/.venv
python3 -m venv /tests/.venv
env -u PYTHONPATH /tests/.venv/bin/python -m pip install --no-deps -r /tests/requirements.lock
echo "verifier venv ready in $((SECONDS - venv_start))s"
cd /app
export PYTHONNOUSERSITE=1
export PYTHONPATH=/app:/app
export PATH=/tests/.venv/bin:$PATH
set +e
bash /tests/run_test.sh > /logs/verifier/test_output.txt 2>&1
status=$?
set -e
cat /logs/verifier/test_output.txt
python3 -I - <<'PY'
from pathlib import Path
import re
output = Path('/logs/verifier/test_output.txt').read_text(errors='replace')
if not re.search(r'^Ran [1-9][0-9]* tests? in ', output, re.M) or '_FailedTest' in output:
    raise SystemExit('Test infrastructure failure: unittest did not execute the selected tests')
PY
if [ "$status" -eq 0 ]; then
  printf '1\n' > /logs/verifier/reward.txt
  exit 0
fi
if [ "$status" -eq 1 ]; then
  printf '0\n' > /logs/verifier/reward.txt
  exit 0
fi
echo "Test infrastructure failure: $status" >&2
exit "$status"
