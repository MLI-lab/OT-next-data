#!/usr/bin/env bash
# Emits a multi-reward reward.json with 3 named rewards:
#   correctness      — graded answer matches gold (0/1)
#   submission       — a non-empty answer was submitted (0/1)
#   tool_efficiency  — fewer tool calls -> higher (0..1); null if count unknown
# Harbor reads /logs/verifier/reward.json (takes precedence over reward.txt).
set -u
mkdir -p /logs/verifier

answer_path="/workdir/answer.txt"
if [ ! -s "$answer_path" ]; then
  echo '{"correctness": 0.0, "submission": 0.0, "tool_efficiency": 0.0}' > /logs/verifier/reward.json
  echo "[grader] no answer at $answer_path -> all rewards 0" >&2
  exit 0
fi

python3 /tests/grader.py --json < "$answer_path" > /logs/verifier/reward.json
