# CrossCodeEval: where validation stands, and what is left — 2026-09-30

## ZIH continuation (2026-09-30)

Julia job **137299** passed stages 1, 3, 4 and 5 on all ten smoke tasks.
The pending Barnard job **38901549** was cancelled. After the smoke passed,
the 32-CPU Julia allocation was stopped and replaced with Julia job **137304**
for the full 6,710 tasks: **128 CPUs, 512 GB RAM, 112 concurrent tasks and an
eight-hour limit**, with no GPUs. Static checks remain capped at eight and
container startups retain the existing throttle. A roughly one-hour run is
a target, not a measured estimate or guarantee.

The existing Horse workspace is `/data/horse/ws/frwe188h-trp-shared` (expires
2026-11-18). This run uses its `crosscodeeval/` subdirectory. Published inputs
were downloaded at revision `12df4483fe99c79ccbb4c923d76ab5a2b042e64a` and
the patcher's pip-only update applied; hashes are in `parquets-pinned/source.json`.
The smoke evidence is saved in `runs/137299/smoke/`. The replacement code
snapshot is under `submissions/20260930-115202-wide/code`; full-run reports
will be under `runs/137304/full/`, and the log is `logs/cce-validation-137304.out`.
No automatic publication is configured. See [ZIH commands](../../hpc/zih/crosscodeeval.md).

Local checks: 158 tests passed and two skipped on the full run; the remaining
plotting test passed after installing its missing matplotlib dependency.
Bridge imports and ten-task contract preparation/reload also passed.

## Prior Helma handoff

`README.md` describes what the patch script produces and how the verifier grades. This file
is the plan. The runs move to another cluster; the Helma job that is still queued (917298,
stages 1, 3, 4, 5 with an automatic pull request) can be cancelled with `scancel 917298` or
left as a cross-check.

## Where we stand

| | |
|---|---|
| Tasks | 6,710 in four TaskTrove sources (C# 1,353, Java 1,895, Python 432, TypeScript 3,030), revision `12df4483fe99` |
| Change since publication | `pip install pytest==9.1.1 pytest-timeout==2.4.0` pinned in every Dockerfile; nothing else |
| Last complete result | run 916252 (2026-09-29): stage 1 passed on every task it could check, stage 3 built 6,703 of 6,710, oracle 1 on all 3,638 tasks it reached; then the node ran out of message queues (fixed since, see below) |
| Kept or archived | nothing archived yet; pull request 1 on the dataset repository is from that partial run and should be closed once a complete run replaces it |
| Static checks excluded, with reasons | `separate-verifier`, `test-sh-sanity` (shared verifier by design) |
| Older teacher runs (2026-09-17, 1,000-task selection, unpinned) | Qwen3-Coder-30B-A3B-Instruct 16 attempts: pass@1 7.0%, pass@4 14.3%, pass@16 22.3%; Qwen3.5-122B-A10B: pass@1 20.0%. Reward summaries only, trajectories not kept |

## What to carry over

From Helma, `/hnvme/workspace/y500bb12-crosscodeeval-pilot/validation-smoke/`:

| Path | Content | Size |
|---|---|---|
| `parquets-pinned/` | the four pinned Parquets plus `source.json` (provenance and hashes) | 56 MB |
| `ten-task-sample-pinned/tasks.parquet` | ten tasks for smoke tests | small |
| `upstream-cceval/crosscodeeval_data.tar.xz` | the benchmark's data archive, needed only to run the patcher again | 41 MB |

Everything else comes from the repository (`main`) and from `python -m validation.upstream setup`.
The container images are rebuilt from the Dockerfiles on the new cluster: three distinct images
of about 84 MB.

## What is left, in order

The stage commands are in `validation/README.md`; the ones below are the exact invocations.
Replace `$TASKS` with the directory that holds the four Parquets.

1. **Set up** the validation environment and the Apptainer bridge on the new cluster. Without a
   launcher for that cluster, run the bridge inside your own allocation and use `--submit never`:
   the bridge server (`python -m harbor.environments.apptainer.server --host 127.0.0.1 --port P`)
   and this repository's worker (`python harbor_patches/bridge_worker.py --bridge-url
   http://127.0.0.1:P --sif-cache IMAGES --staging-base SCRATCH --num-workers 2N`), with
   `APPTAINER_BRIDGE_URL`, `HARBOR_SIF_CACHE`, `BRIDGE_USE_FAKEROOT=1` and
   `APPTAINER_NO_MOUNT=hostfs,bind-paths,cwd` exported, as `hpc/helma/validation_worker.py`
   does. Extract the Parquets to node-local storage first
   (`python validation/data/materialize.py $TASKS TASKDIR`) and point the contract at `TASKDIR`;
   the pull request script packs task directories back into `task_binary`.
   Log in to Hugging Face (`hf auth login`, write token).

2. **Smoke test** on the ten-task sample, stages 1, 3, 4, 5. Expect all ten to pass every stage
   and `worker.log` to contain "Removed N leaked fakeroot message queues" lines: that is the
   bridge freeing the queues that `--fakeroot` leaves behind (about two per container start;
   a full run makes 20,000 starts).

3. **Stages 1, 3, 4, 5 on all 6,710 tasks**, with the two exclusions and the automatic pull
   request:

   ```bash
   python validation/run.py TASKDIR --stages 1,3,4,5 --static-profile training \
     --exclude "separate-verifier=CrossCodeEval grades in the agent's container; the verifier reads one file and its reference is uploaded after the agent has finished" \
     --exclude "test-sh-sanity=applies to shared verifiers that install test tools; this verifier uses only the Python standard library" \
     --backend apptainer --submit never --network-mode host --concurrency 28 --attempts 1 \
     --publish-repo FWeindel/validated-tasks --publish-analysis \
     --publish-folder crosscodeeval-csharp=crosscodeeval-csharp-v5 --publish-folder crosscodeeval-java=crosscodeeval-java-v4 \
     --publish-folder crosscodeeval-python=crosscodeeval-python-v3 --publish-folder crosscodeeval-typescript=crosscodeeval-typescript-v3 \
     --out RESULTS --prepare-contract RESULTS/contract.json
   python validation/run.py --contract RESULTS/contract.json
   ```

   On Helma this took 1 h 47 min up to the middle of oracle at 28 trials in parallel; expect
   3 to 4 hours for all four stages. If the job is not on Helma, open the pull request by hand
   afterwards: `python validation/publish.py RESULTS/stage_*/ --contract RESULTS/contract.json
   --analysis --folder ...` (the same four `--folder` mappings). Review the pull request: every
   task that was reached should pass; a task archived at stage 3, 4 or 5 is either a real
   defect or a node problem, and the advisory analysis in the description groups them.
   Merge, and close pull request 1.

4. **Stages 6 and 7**, agent trials and trace metrics, on the kept tasks, with the trajectories
   kept this time. Models: Qwen3-Coder-30B-A3B-Instruct with 8 attempts and Qwen3.5-122B-A10B
   with 1 attempt, as before, plus the SFT'd Snowball model once it has an entry in
   `config/models.py` (weights, sampling, GPUs, reasoning parser). One contract per model:

   ```bash
   python validation/run.py TASKDIR --stages 6,7 --serve-model MODEL --attempts 8 \
     --backend apptainer --submit never --network-mode host --concurrency 28 \
     --out RESULTS-MODEL --prepare-contract RESULTS-MODEL/contract.json
   ```

   Stage 6 writes `agent-run.json` (how the agent was run) and the reward metrics per task,
   family and overall; stage 7 writes `trace-metrics.json` (turns, tokens, context, latency,
   throughput, tool calls, terminations, errors, GPU use). The trajectories are in the Harbor
   job directory under `RESULTS-MODEL/stage_6_agent_trials/`. Archive that directory; it is the
   input of stage 8 and the material to publish.

5. **Publish the trajectories** to Hugging Face: a second dataset repository, one folder per
   model and run, with the trajectories, `agent-run.json`, `trace-metrics.json` and the
   contract. Not built yet; `validation/publish.py` handles tasks only.

6. **Stage 8, LLM trajectory analysis, on a pilot sample.** Stage 8 takes a Harbor job
   directory (`--trials`) and reviews every trial in it, so the sample is made by copying the
   selected trial directories into a job directory of their own. How to pick them is still to be
   decided; the natural buckets come from the stage 6 and 7 outputs: the variance group
   (`all_solved`, `all_zero`, `constant_partial`, `varying`), the family (language), and the
   termination reason (`task_complete`, `turn_limit`, `task_timeout`, `error`). A first cut:
   a few trials per language from `all_zero` and from `varying`, plus every `turn_limit` and
   `task_timeout` trial. Stage 8 needs a review model (Claude Code login or
   `--review-model`), and each trial costs one model call.

7. **Stage 2, LLM rubric review, on a sample** of tasks, to see what the reviewer objects to
   in this dataset before spending a full run on it.

## Fixed on 2026-09-30, relevant to the runs

- The bridge removes the SysV message queues that `--fakeroot` leaks after every container
  stop; without it a node fails after about 10,000 container starts with "creating message
  channels: No space left on device". Confirmed on a node with a ten-task job.
- Static checks get 300 s and one retry, and run at most 8 in parallel; the first full run lost
  121 tasks to check timeouts under load.
- Tasks whose context file names contain spaces or `[]` (65 tasks) are checked on a renamed
  copy; the tasks themselves keep the original names.
- The Helma worker picks a free port; nodes are shared.

### Julia checkpoint restart (2026-09-30)

Job **137304** was cancelled after preserving and verifying its last complete
static checkpoint: **5,450 / 6,710 tasks passed**, leaving **1,260** without saved
complete outcomes. Checkpoint inputs, original contract/manifest, checker source,
static summary and compressed check logs are in:
`/data/horse/ws/frwe188h-trp-shared/crosscodeeval/recovery/137304-stage1`.
All 6,710 copied task hashes match the old contract.

Replacement job **137324** uses the frozen code under
`submissions/20260930-150335-resume-137304/code` in the same workspace. It requests
128 CPUs, 512 GB, 12 hours; static concurrency 128, container-task concurrency 112,
start concurrency 8, start spacing 0.25 seconds. A fresh ten-task smoke gate runs
before the full resumed stages 1 → 3 → 5 → 4. Completed static checks retain their
original path-check adaptation, explicitly accepted by the user; unfinished
checks use the current wrapper. No task contents were changed for this restart.

After complete validation outcomes, it is configured to open a PR on
`FWeindel/validated-tasks` with the four language/version folders. It does not
merge. Hugging Face credentials are loaded at runtime from the user-specified
secret file; no token is stored in the snapshot/contract. Model-generated README
creation is disabled for this submission because the Claude OAuth session could
not refresh. Log: `logs/cce-resume-137324.out`; results: `runs/137324/{smoke,full}`.

Resume/reuse/publishing-gate tests passed (28 targeted tests); static/resume
regressions passed (36 tests). Queue submission alone is not a passing smoke or
full validation result; consult the execution and stage reports.
