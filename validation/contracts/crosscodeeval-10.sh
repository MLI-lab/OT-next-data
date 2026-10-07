#!/usr/bin/env bash
# Editable example: PREPARE a ten-task contract; do not submit or execute checks.
set -euo pipefail
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$repo_root"
workspace="${OT_WORKSPACE:-/hnvme/workspace/y500bb12-crosscodeeval-pilot/validation-smoke}"
python_bin="${VALIDATION_PYTHON:-$workspace/envs/prep/bin/python}"
run_name="non-llm-$(date +%Y%m%d-%H%M%S)"
contract_path="${1:-$repo_root/validation/results/crosscodeeval/$run_name/contract.json}"
result_dir="$(dirname "$contract_path")"

# This saved sample contains 3 C#, 3 Java, 2 Python and 2 TypeScript tasks.
# Its source.json records the deterministic source-row selection and input hashes.
"$python_bin" validation/run.py "$workspace/ten-task-sample/tasks.parquet" \
  --dataset-source open-thoughts/TaskTrove \
  --dataset-revision 12df4483fe99c79ccbb4c923d76ab5a2b042e64a \
  --stages 1,3,4,5 --static-profile training --min-tasks 10 \
  --exclude "separate-verifier=CrossCodeEval grades in the agent's container; the verifier reads one file and its reference is uploaded after the agent has finished" \
  --exclude "test-sh-sanity=applies to shared verifiers that install test tools; this verifier uses only the Python standard library" \
  --backend apptainer --submit helma --network-mode host \
  --time 00:30:00 --cpus 32 --gpus 1 --concurrency 4 --attempts 1 \
  --out "$result_dir" \
  --prepare-contract "$contract_path"
