#!/bin/bash
set -euo pipefail
mode=$1
cd /testbed
git reset --hard 7b127bdf71a36d85216315f80c1b54d22b060818
git apply /audit/gold.patch
if [ "$mode" = equivalent ]; then
 /opt/miniconda3/envs/testbed/bin/python /audit/alternative.py
fi
git diff > "/audit/$mode/applied.patch"
/opt/miniconda3/envs/testbed/bin/python /audit/probe.py > "/audit/$mode/equivalence.txt" 2>&1
set +e
bash /tests/test.sh > "/audit/$mode/verifier.log" 2>&1
rc=$?
printf '%s\n' "$rc" > "/audit/$mode/exit-code.txt"
exit 0
