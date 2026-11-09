#!/bin/bash
set -euo pipefail
cd /testbed
git reset --hard 58c1acb1d6054dfec29d0f30b1033bae6ef62aec
git apply /audit/partial.patch
git diff > /audit/applied.patch
/opt/miniconda3/envs/testbed/bin/python /audit/probe.py > /audit/remaining-bug.txt
set +e
bash /tests/test.sh > /audit/verifier.log 2>&1
rc=$?
cp /tmp/tmp.* /audit/ 2>/dev/null || true
printf '%s
' "$rc" > /audit/exit-code.txt
exit "$rc"
