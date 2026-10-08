# Harbor/Apptainer execution review, 2026-10-08

The shared fixes address reproduced failures, but a full SETA pass@k rerun should
wait for preparation review and a representative concurrency pilot. The live
OOM smoke test establishes recovery for one environment, not throughput or
reliability across a full allocation.

## Review scope and fixes

Reviewed controller/executor lifecycle, terminal patches, bridge retries and
cleanup, image/startup adapters, task/verifier preparation, and regression tests.
This was a source review with focused regression execution, not an exhaustive
fault-injection audit of Harbor or Apptainer.

| Finding | Change | Regression |
| --- | --- | --- |
| Protected startup ignored the registry's early stop while waiting for Slurm | Pass an abandonment callback; check while waiting and immediately after readiness | Both queued and just-ready abandoned starts fail promptly |
| Remote anchor lacked Popen-compatible `returncode`; stop diagnostics could raise before normal teardown | Retain its polled exit status, including a dead executor | Dead anchor polling, waiting and returncode |
| Workspace-copy and fakeroot compatibility probes called raw subprocess.run outside task limits | Route those probes through the active executor without recursively applying startup adapters | Both probes and final instance start stay in the task step |
| Recovery stop/start/anchor subprocesses could each start with a fresh timeout | Cap each subprocess by the interrupted call's remaining budget and honor stop requests | Remaining budget is forwarded; expired budget submits nothing |
| Protected controller retained an unused start semaphore argument/field | Remove it; upstream container startup already owns admission | Default dispatcher and protected controller tests |

215 tests passed across controller/OOM, tmux, uploads, bridge transport/runtime
and cleanup, Slurm steps, fakeroot IPC, fresh verifier, setup review, preparation
budgets, image builds/cache, local runtime staging and runtime configuration.
The previous real Slurm smoke test, job 954666, confirmed step OOM, controller
survival, disk-file preservation and continued terminal execution. That job
predates the review fixes above; those fixes have regression coverage but have
were subsequently exercised by the preparation pilots below.

## Complexity worth retaining or removing later

- Following review, the legacy `SlurmInstance`/`StepPool` path and its switches
  were removed at the user's request. Dedicated protected execution is the only
  Slurm path. The earlier test counts below include the now-removed pool tests.
- Request receipts, epoch checks and result acknowledgements are necessary to
  distinguish transport retries from command replay. Do not replace them with
  generic retry decorators.
- The anchor and scoped fakeroot IPC cleanup address process-lifetime and
  ownership problems. A node-wide cleanup would be simpler but incorrect.
- Startup adapters still patch global subprocess.run and depend on wrapper order.
  They are tied to the pinned Harbor version. A later cleanup should introduce
  an explicit command runner upstream; attempting that broad refactor before
  the rerun would need its own compatibility and live-container validation.

## Remaining limits

- One step per task removes ambiguous shared OOM history but increases Slurm
  startup frequency. Measure queue/start latency at intended concurrency before
  scaling; a successful single-task test cannot establish scheduler throughput.
- The executor still uses captured subprocess output before upstream truncation.
  Large non-terminal stdout/stderr can consume task memory; bounded tmux capture
  fixes the known protocol failure, not every output-memory case. A future
  disk-spooled output transport needs explicit byte/text and timeout semantics.
- The shared controller and node/job storage remain common failure domains.
  Protecting it from a task cgroup does not protect it from allocation/node OOM,
  full scratch, job termination or node failure.
- Generic shell loss is not automatically classified as agent-caused. Saved
  historical missing-tmux/unexpected-exit cases remain partly unexplained.
- OOM recovery restores disk state, not transactions or running services. Agent
  feedback must continue to disclose partial execution and lost process state.

## Recommended SETA order

1. Check the exact regenerated task payloads for verifier installation/downloads
   embedded in `test.sh`. Before v57, the SETA patcher retained such commands;
   `--review-setup` invokes only optional `/tests/setup.sh` and deliberately never
   runs grading. Move reusable dependencies into images or explicit verifier
   setup without moving grading into the preparation phase.
2. Prepare/cache images, then review every selected SETA task using stage 3
   `--review-setup 5`, fresh containers and representative resource limits.
   Current defaults are **mean task preparation <=30s**, every run <=60s;
   verifier preparation mean <=5% of its timeout and every run <=60s.
   The earlier requested median rule is different. Adding
   `--preparation-median-target-seconds 30` enforces median too, while retaining
   the default mean gate. Do not describe the defaults as median-only.
3. Inspect per-phase timings and review buckets. Fix downloads/rate limits and
   reusable installation/compilation in images, then repeat all five runs for
   affected tasks. Cached images must not imply warmed task containers.
4. Run oracle/no-op checks for changed task/image/verifier semantics. Run a small
   teacher pilot with this code snapshot at intended concurrency, including
   historically problematic tasks. Inspect leaks, startup delays, uploads,
   terminal errors and resource outcomes before scaling.
5. Resume missing/infrastructure-invalid attempts under the chosen sampling
   policy. Do not grant extra samples to valid agent timeouts or silently mix
   old and new execution policies. Preserve code/config provenance per attempt.

The initial review submitted no new jobs. Follow-up preparation work below did
not rewrite historical results. See [historical evidence](runtime-evidence-20261008.md)
for original counts, logs, PRs and live probes.

## Follow-up preparation pilots

- SETA v57/v58 separates verifier preparation for all 3,134 retained tasks.
  V59 through v61 bake measured dependencies for three tasks and repair the video image.
  The latest four task repairs leave instructions, solutions, limits and Python
  grading unchanged; see `semantic-file-comparison-v60.json`.
- Full build logs exposed a CA bind failure before `%post`: writable definition
  builds could not create `/run/ot-certificates/...`. Empty `%files` placeholders
  fix the mount without embedding host certificates. Job 955011 built the three
  affected images in 64.128, 59.622 and 24.277 seconds with clean package audits.
- Instance exec lacked `--cleanenv`, importing host `UV_CACHE_DIR` instead of
  using image caches. Clean execution preserves explicit task environment values.
  Job 955050 passed the offline verifier checks after this repair, but four
  initial starts exceeded the 60-second preparation gate; their subsequent
  starts took 3.5 to 9.6 seconds. No task setup ran before those initial timeouts.
  The cause of that startup spike remains unproven. Slow Apptainer operations
  now log elapsed time without logging the command body.
- The current full repository check passed 1,132 tests. Later timing diagnostics
  passed 61 focused tests. These do not certify all historical failure causes.

Pilot contracts, reports and archived job evidence are under
`/hnvme/workspace/y500bb12-seta-validation/experiments/setup-review-20261008/`.
`progress.json` records the active gate; failed gates are preserved.
