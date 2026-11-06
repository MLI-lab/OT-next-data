#!/bin/bash
set -euo pipefail
mode=$1
cd /testbed
git reset --hard b9cf764be62e77b4777b3a75ec256f6209a57671
if [ "$mode" = gold ]; then git apply /audit/gold.patch; fi
git diff > "/audit/$mode/applied.patch"
PYTHONPATH=/testbed /opt/miniconda3/envs/testbed/bin/python /audit/probe.py > "/audit/$mode/url-probe.json"
set +e
bash /tests/test.sh > "/audit/$mode/verifier.log" 2>&1
rc=$?
printf '%s\n' "$rc" > "/audit/$mode/exit-code.txt"
exit 0
