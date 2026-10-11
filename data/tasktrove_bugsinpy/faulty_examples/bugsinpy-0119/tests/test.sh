#!/bin/bash
set -euo pipefail
mkdir -p /logs/verifier
reward=/logs/verifier/reward.txt
output=/logs/verifier/test_output.txt
ratio=/logs/verifier/pass_ratio.txt
rm -f "${reward}" "${ratio}"
cd /app
export PYTHONPATH="/app:${PYTHONPATH:-}"

score_zero() {
  printf '0.0000
' > "${ratio}"
  printf '0
' > "${reward}"
}

score_one() {
  printf '1.0000
' > "${ratio}"
  printf '1
' > "${reward}"
}

if [[ ! -f /app/solution.py ]]; then
  printf '[verify] solution.py missing
' > "${output}"
  score_zero
  exit 0
fi
baseline=$(tr -d '[:space:]' < /tests/baseline_sha256.txt)
if [[ $(sha256sum /app/solution.py | cut -d' ' -f1) == "${baseline}" ]]; then
  printf '[verify] solution unchanged from baseline
' > "${output}"
  score_zero
  exit 0
fi
if ! python3 -m py_compile /app/solution.py 2> "${output}"; then
  printf '[verify] solution does not compile
' >> "${output}"
  score_zero
  exit 0
fi
python3 -c 'import solution' 2>> "${output}"

set +e
timeout 300 python3 -m pytest /tests/test_solution.py -q --tb=line   -p no:cacheprovider > "${output}" 2>&1
status=$?
set -e
if [[ ${status} -eq 1 ]]; then
  printf '
[verify] pytest failed with status %s
' "${status}" >> "${output}"
  score_zero
  exit 0
fi
if [[ ${status} -ne 0 ]]; then
  printf '
[verify] pytest infrastructure failure with status %s
' "${status}" >> "${output}"
  exit "${status}"
fi

passed=$(python3 - <<'PY'
import re
from pathlib import Path
text = Path('/logs/verifier/test_output.txt').read_text(errors='replace')
matches = re.findall(r'(\d+) passed', text)
print(matches[-1] if matches else '0')
PY
)
if [[ ${passed} -le 0 ]]; then
  printf '
[verify] zero tests ran
' >> "${output}"
  exit 2
fi
score_one
