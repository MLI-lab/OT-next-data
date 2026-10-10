#!/bin/bash
set -euo pipefail
bash /setup_files/setup.sh
mkdir -p /logs/verifier
echo '1f7bd1c1274f7a5c641128dbf608a4526afe22ef61636107f457bb47ebbeb72b  /tests/fixed-tests.tar.gz' | sha256sum -c -
tar -xzf /tests/fixed-tests.tar.gz -C /app
echo 'c102ec70b29bdf9a9b31cd1e1652339f430ed884bc56757928fbb54f95548891  /tests/requirements.lock' | sha256sum -c -
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
