#!/usr/bin/env bash
set -u
mkdir -p /logs/verifier

answer_path="/workdir/answer.txt"
if [ ! -s "$answer_path" ]; then
  echo "0.0" > /logs/verifier/reward.txt
  echo "[grader] no answer at $answer_path" >&2
  exit 0
fi

python3 /tests/grader.py < "$answer_path" > /logs/verifier/reward.txt
