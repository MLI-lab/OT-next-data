#!/bin/bash
set -euo pipefail
mode=$1
cd /testbed
sh /task/setup_files/setup.sh
git status --short | head
if [ "$mode" != baseline ]; then bash /task/solution/solve.sh > "/audit/$mode/solve.log" 2>&1; fi
git diff > "/audit/$mode/applied.patch"
if [ "$mode" = gold-rA ]; then export PYTEST_ADDOPTS="-rA"; fi
set +e
bash /tests/test.sh > "/audit/$mode/verifier.log" 2>&1
printf '%s\n' "$?" > "/audit/$mode/exit-code.txt"
cp /logs/verifier/reward.txt "/audit/$mode/reward.txt" 2>/dev/null
exit 0
