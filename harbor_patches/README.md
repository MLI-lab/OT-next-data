# Harbor patches

Runtime adaptations for the Harbor version pinned in `requirements.txt`.
`validation/stages/harbor.py` installs the agent-side patches;
`hpc/validation_worker.py` launches the patched bridge. New jobs freeze both in
code snapshots. Editing this checkout does not change running jobs.

```text
Harbor → bridge server → protected controller → task executor → Apptainer/tmux
                        outside task limit    inside task Slurm step
```

Each task gets its own execution step with its effective CPU/memory limits.
The controller retains lifecycle state and disk-backed storage. Model serving
and the agent conversation run separately. A small executor, Apptainer and tmux
still consume part of the task memory budget.

## Shared pass@k infrastructure fixes and remaining limits

| Failure | Fix | Boundary |
| --- | --- | --- |
| Truncated tmux responses | Bound visible/full captures before Harbor adds protocol markers | Keep the global output cap; never replay commands |
| Failed test uploads | Clean shell/PATH for upload bookkeeping; allow 600s RPC and 630s result polling | Enclosing phase timeout still applies; accepted uploads are not resubmitted |
| Transient bridge HTTP failures | Bounded retries with request IDs, deduplication and retained results | Does not repair model API failures, task downloads or a dead bridge |
| `/tmp` cleanup deletes tmux socket | Put sockets in the container's private `/run/ot-harbor-tmux` | Does not prevent explicit tmux removal or shell exits |
| Task OOM kills executor/terminal | Keep controller outside task limit; reopen preserved filesystem and restart terminal | Requires confirmed task OOM; process state is lost |
| Failed startup keeps a Slurm slot | Remember early stop requests and check them while waiting for an executor | Cleanup remains scoped to that environment |

### Terminal output and uploads

Marin [PR 136](https://github.com/marin-community/harbor/pull/136) retains the last
500,000 characters of an exec stream; [PR 137](https://github.com/marin-community/harbor/pull/137)
adds batched terminal status markers. Ben Feuer introduced both in `c52e5d30`
and `06139137`. Long Apptainer/Terminus output could discard the leading markers
and raise `TmuxBatchProtocolError` after the command had run.

`tmux_capture.py` budgets each capture to `(max_exec_output_chars - 1024) // 2`
bytes before framing. Whole-line trimming preserves UTF-8, small captures remain
unchanged, and `pipefail` preserves errors. Both agent and worker patches are required.

`upload_runtime.py` uses `/bin/bash --noprofile --norc`, a standard PATH, no
`BASH_ENV`/`ENV`, and cwd `/` for upload mkdir/extraction/verification. Agent and
test shells retain their configuration. Longer polling accommodates the upload's
multiple operations and safe mkdir retries; it cannot fix missing files, broken
archives, disk failures or a permanently failed worker.

### Transport and terminal failures

`bridge_client.py` and `bridge_server.py` retry transient HTTP 408/429/502/503/504,
resets, temporary DNS failures and interrupted responses with jittered backoff
within 60s. Submission retries preserve the request ID and payload. Receipts
prevent duplicate work; polling retrieves accepted results. A changed bridge
epoch fails safely. Receipts and polled results expire after five minutes;
acknowledgement removes completed results sooner. State is in memory.

Local executor RPC errors report the operation, connect/send/receive phase,
process status and log tail. Connect failure means not submitted; send/receive
failure leaves execution unknown. Neither automatically replays the command.

A persistent `_pilot_anchor` keeps tmux's owner alive. `tmux_diagnostics.py`
records failed batches, sessions, sockets, processes and memory/fakeroot details
without replacing the original error. Generic session loss is not retried:
`exit`, `exec`, `tmux kill-server` and malformed heredocs can be agent-caused.
Agent timeouts likewise receive no fresh attempt. Unexplained terminal errors
still need cause review; an intact anchor does not explain why a shell exited.

### Continuing an agent after a confirmed task OOM

`protected_step.py` requires an increase in the task step's cgroup v2 `oom_kill`
counter or an explicit Slurm OOM event in that step's log. A reset, missing shell
or exit 137 alone is insufficient. Execution steps are never shared or reused
between tasks on this path.

For a marked Terminus terminal operation, recovery:

1. Reaps the old execution step and cleans only its recorded fakeroot IPC.
2. Reopens the original writable disk overlay and bind mounts in a new step with
   the same limits. Missing storage or a volatile overlay causes failure.
3. Creates a terminal and returns `TASK_MEMORY_LIMIT` feedback in the existing
   conversation, within the original agent timeout.

Disk-backed files survive. Processes, shell variables, cwd and memory-backed
files do not. Interrupted commands may have partially completed. Recovery never
replays them, reruns setup, repeats uploads or grants a fresh attempt. Setup and
verifier OOMs remain failures of their own phases.

Stage 7 reports per-trajectory and aggregate `oom_recoveries`, plus
`trajectories_with_oom_recovery`. Recovered trajectories retain their eventual
outcome; unrecoverable confirmed agent OOM is `task_memory_limit`. Historical
results are unchanged. These counters establish task-budget exhaustion, not
that agent code alone consumed every byte.

Enabled for new Slurm jobs; readable enforcing cgroup v2 limits are required.
The legacy worker pool and its recovery/reuse switches have been removed.
`OT_TRIAL_SRUN=0` bypasses task-step isolation/recovery. Recovery cannot survive
loss of the shared bridge, node, allocation or its storage.

## Images and lifecycle

- Execute instance commands with `--cleanenv`, as for container startup. Otherwise
  host runtime variables such as `UV_CACHE_DIR` override image caches and break
  offline verifier dependencies. Explicit task `--env` values still apply.
- Prepare images before timed trials. Separate build defaults: 8192 MB, 4 CPUs,
  concurrency 2, 3600s per image. Use `--image-build-memory-mb`,
  `--image-build-cpus`, `--image-build-concurrency`, `--image-build-timeout-sec`.
  Compression gets one quarter of build memory. Allocation wall time still applies.
- Publish immutable, checksum-verified bundles under `$OT_WORKSPACE/images/bundles-v1`,
  including deferred overlays/metadata. Stage by environment content hash.
  `--force-build` does not overwrite published bundles. Failures go to
  `image-preparation.json`; timed trials refuse cache misses. Build commands stream
  full output to per-image logs so downstream dpkg errors do not hide the first
  package failure. Task command output limits are unchanged.
- Before definition builds, create empty targets for the configured CA binds.
  Apptainer's writable build root cannot create missing bind destinations as a
  runtime overlay can. Without these targets, certificate mounting aborts `%post`
  and forces the less capable deferred builder. Only empty placeholders enter
  the image; host certificate contents remain read-only mounts.
- `OT_IMAGE_BASE_MANIFEST` maps OCI references under `bases` to absolute raw-base
  SIF `path` and `sha256`. Missing/changed bases fail instead of pulling.
  `OT_REQUIRE_PREBUILT_IMAGES=1` rejects missing prepared task images.
- Dockerfile RUN instructions use separate checked shells. Temporary OCI imports
  retry at most twice for conveyor truncated JSON, connection resets or TLS
  handshake timeouts, within the original deadline. Dockerfile commands and
  permanent registry errors are not retried.
- Images must provide working tmux. Startup checks `tmux -V`; newly built images
  also run `dpkg --audit` where available. No runtime installation/library repair.
  Existing cached images are not retroactively certified.
- Seed `/workspace` from the merged image before mounting task storage. Preserve
  hidden files, links and modes; uploaded/COPY task files win collisions. A
  disposable upper layer applies deferred whiteouts; tar avoids unsupported
  ACL/xattr copying. Copy failures or the 300s timeout abort startup. Budget local
  disk for both the workspace copy and temporary archive.
- Bind a job-local read-only `/etc/hosts` with loopback names unless explicitly
  overridden. This does not change network isolation. Host-network fallback is
  logged and cannot be described as certified offline.
- Reap ready environments only when idle, without queued/running commands, for
  `BRIDGE_STALE_READY_SEC` (default 3600s). Fakeroot cleanup removes only resources
  positively attributed to the stopped step, never node-wide orphans.

## Command timeouts

| Wait | Budget |
| --- | --- |
| Container command | Supplied `T` |
| Executor RPC | `T + 60s` |
| Harbor result retrieval | `T + 90s` |
| Upload RPC / result retrieval | `600s / 630s` |

Waits overlap; grace periods do not extend command execution. The validation
adapter supplies seven days when no command timeout is specified, replacing
Harbor's 600s fallback. Enclosing preparation, agent and verifier timeouts still
apply. OOM recovery subprocesses also share the interrupted call's remaining
budget. Grace periods do not apply indiscriminately to startup/stop/transfers.

## Code and validation

| Modules | Responsibility |
| --- | --- |
| `bridge_client`, `bridge_server`, `bridge_timeouts` | Transport, deduplication, polling, idle cleanup |
| `bridge_worker`, `protected_step`, `trial_step` | Container adaptations, isolated execution, OOM recovery |
| `tmux_capture`, `tmux_socket`, `tmux_runtime`, `tmux_diagnostics`, `oom_recovery` | Terminal protocol, lifecycle, diagnostics and feedback |
| `upload_runtime`, `fakeroot_ipc` | Upload shell isolation and scoped cleanup |
| `image_build`, `local_image_base` | Image preparation and verified local bases |
| `fresh_verifier`, `verifier_setup` | Separate verifier preparation and optional `/tests/setup.sh` |
| `reasoning_field` | Preserve vLLM reasoning in traces |

Before removing patches after an upgrade, run their `tests/test_*` regressions
and real container smoke tests. Dataset-specific verifier logic belongs outside
these patches. Passing tests verifies reproduced cases, not every historical
failure or readiness at full concurrency.

See the [execution review](docs/execution-review-20261008.md) for current findings
and rollout recommendations. The [detailed evidence archive](docs/runtime-evidence-20261008.md)
preserves all previous explanations, audit counts, PR links, job IDs, paths,
regression inventories and dataset-specific probes. It is a historical snapshot;
this README describes current behavior.
