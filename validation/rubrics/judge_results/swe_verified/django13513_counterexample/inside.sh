#!/bin/bash
set -euo pipefail
mode=$1
cd /testbed
git reset --hard 6599608c4d0befdcb820ddccce55f183f247ae4f
if [ "$mode" = instruction ]; then
 /opt/miniconda3/envs/testbed/bin/python /audit/instruction_fix.py
elif [ "$mode" = gold ]; then
 git apply /audit/gold.patch
fi
git diff > "/audit/$mode/applied.patch"
set +e
bash /tests/test.sh > "/audit/$mode/verifier.log" 2>&1
rc=$?
printf '%s\n' "$rc" > "/audit/$mode/exit-code.txt"
exit 0
