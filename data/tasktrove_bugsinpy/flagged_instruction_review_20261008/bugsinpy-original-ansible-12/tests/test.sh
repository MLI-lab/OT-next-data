#!/bin/bash
set -euo pipefail
bash /setup_files/setup.sh
mkdir -p /logs/verifier
echo '5ffe83188ed8d13be254173cab2e8ee05e4fcc30aecbb64c35a2e482cf79ece4  /tests/fixed-tests.tar.gz' | sha256sum -c -
tar -xzf /tests/fixed-tests.tar.gz -C /app
echo '9fb101ea81a7b5b1e72ceae187a3aecd8e38add8c1035f33abf0b6efeecbdc5c  /tests/requirements.lock' | sha256sum -c -
venv_start=$SECONDS
rm -rf /tests/.venv
python3 -m venv /tests/.venv
env -u PYTHONPATH /tests/.venv/bin/python -m pip install --no-deps -r /tests/requirements.lock
echo "verifier venv ready in $((SECONDS - venv_start))s"
cd /app
export PYTHONNOUSERSITE=1
export PYTHONPATH=/app:/app/lib:/app
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
