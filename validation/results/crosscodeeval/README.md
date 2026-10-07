# Current CrossCodeEval results

The 6,710-task full validation was resubmitted as Helma job `923204` after
job `922945` stopped before all stages on a CPU-worker `gpus=0` argument
error. The new contract, submission record, and eventual reports are under
`/hnvme/workspace/y500bb12-crosscodeeval-pilot/validation-smoke/full-6710-dual-nop-20261002/`.
The new task manifest matches the old one. Job `923204` is a submission,
not a validation pass.

This README is a manually maintained index. Contracts and reports are generated
automatically by the pipeline. Folder names describe the model/test and task count.
Older completed local attempts were deleted. Generated contracts live in each run
folder; `validation/contracts/` contains only the editable example.

In these names, `claude-login-5-v1` means Claude Code login on five tasks,
first attempt; `local-qwen38-1-v4` means local Qwen3.8-27B on one task,
fourth attempt. The earlier local attempts used the same model: v1 hit a
local-server configuration error; v2 exhausted the reviewer turn limit; v3
repeatedly summarized its 32K context and its implementation review failed.
The fourth attempt used 40 turns and 64K context and completed both reviews.

| Folder | What it contains | Start here |
| --- | --- | --- |
| `non-llm-10/` | **10 tasks**, stages **1 (static), 3 (build/runtime), 4 (oracle), 5 (NOP)**. Static findings; builds/oracle/NOP passed. Uses its original historical settings. | [summary.json](non-llm-10/summary.json), [protocol.md](non-llm-10/protocol.md) |
| `claude-login-5-v1/` | **5 tasks**, **stage 2 only**: Claude Code login review completed. All five produced implementation findings and proposal rejections; no review execution errors. Job 915546. | [task reviews](claude-login-5-v1/submissions/84b49e17e33b/report/reviews.json), [run summary](claude-login-5-v1/submissions/84b49e17e33b/report/summary.json) |
| `local-qwen38-1-v4/` | **1 task**, **stage 2 only**: local Qwen3.8-27B completed both reviews with 40 turns and 64K context. The implementation review found failing criteria; the proposal decision was Reject. No review execution error. Job 915589. | [task review](local-qwen38-1-v4/submissions/01acd8dec5d2/report/reviews.json), [run summary](local-qwen38-1-v4/submissions/01acd8dec5d2/report/summary.json) |

The Claude-login and local-Qwen runs both test stage 2. Their reports are saved in
`submissions/ID/report/`.
The ten-task run already has its reports directly in `non-llm-10/`.

## Files to read

| File | Meaning |
| --- | --- |
| `contract.md` | Readable plan: tasks, checks, expected rewards and execution profile. |
| `contract.json` | Exact frozen plan used by the program. |
| `contract.tasks.json` / `*.tasks.json` | Exact task IDs and content hashes. |
| `reviews.json` | One entry per task: implementation criterion outcomes and explanations, plus the proposal decision and full written review. Present when stage 2 ran. |
| `summary.json` | Run completion, stage counts, task groups, short failure reasons, contract/network status and pipeline errors. |
| `stage-N-ID.json` | Detailed task results for stages other than 2. The older `non-llm-10/` run has one for each of stages 1, 3, 4 and 5. |
| `failures.csv`, `pipeline-ID.json` | Extra files from the older `non-llm-10/` report. New reports include this information in `summary.json`. |

## Supporting files

- `prepare.log`, `submit.log`: contract-generation and submission output.
- `submissions/ID/submission.json`: job ID, Slurm command and actual CPU/GPU choice.
- `request.json`: settings passed to the worker; `resources.json`: resource/concurrency calculation.
- `execution.json`: job completion/error status; `slurm-JOB.out`: live job log.
- `code.tar.gz`: exact code snapshot used by that job, packed after the job finishes.
- `evidence.tar.gz`: compressed detailed trial files, including prompts, model output,
  verifier output and each `proposal-review.json` (decision plus reasoning). This
  keeps shared-filesystem file counts low. It is evidence for this run, not a folder
  of older attempts.
- `report/`: `reviews.json` and `summary.json` for a stage-2-only run; other
  stages can also have detailed `stage-N-ID.json` files
  for convenient inspection. The contract, protocol and task manifest are in the
  run folder and the full trial files remain in `evidence.tar.gz`.
- `non-llm-10/evidence.json`: location of that older run's archive in workspace
  storage; its compact report is directly in `non-llm-10/`.

To choose a results location, set `--out validation/results/crosscodeeval/NAME`
when preparing the contract and save that contract as `NAME/contract.json`.
Explicit relative paths resolve from your working directory; the default results
root is this repository’s `validation/results/`.
