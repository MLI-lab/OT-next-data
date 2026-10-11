#!/bin/bash
set -euo pipefail
bash /setup_files/setup.sh
mkdir -p /logs/verifier
echo '1d37e4ee4811a3eebe175220dad85af090dee35e99c729ba56ad190f77e45d11  /tests/fixed-tests.tar.gz' | sha256sum -c -
tar -xzf /tests/fixed-tests.tar.gz -C /app
echo '996f6cd7ea783c95bda06a0d010407861f0440b9450438e266906746f946d0d5  /tests/requirements.lock' | sha256sum -c -
venv_start=$SECONDS
rm -rf /tests/.venv
python3 -m venv /tests/.venv
env -u PYTHONPATH /tests/.venv/bin/python -m pip install --no-deps -r /tests/requirements.lock
echo "verifier venv ready in $((SECONDS - venv_start))s"
cd /app
export PYTHONNOUSERSITE=1
export PYTHONPATH=/app:/app
export PATH=/tests/.venv/bin:$PATH
unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy
set +e
bash /tests/run_test.sh > /logs/verifier/test_output.txt 2>&1
status=$?
set -e
cat /logs/verifier/test_output.txt
python3 -I - <<'PY'
import json
from pathlib import Path
report = Path('/logs/verifier/unittest.jsonl')
rows = [json.loads(line) for line in report.read_text().splitlines()] if report.is_file() else []
if len(rows) != 2 or any(row['invalid'] or not row['tests_run'] for row in rows):
    raise SystemExit('Test infrastructure failure: missing or invalid unittest execution reports')
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
