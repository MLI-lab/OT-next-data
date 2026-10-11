# Harbor patches

These fix or adapt the Harbor version pinned in `requirements.txt`. The pipeline
applies them automatically when running tasks:

- `validation/stages/harbor.py` changes Harbor's behavior in the running Python
  process, including request retries and handling model reasoning output.
- `hpc/validation_worker.py` starts the patched bridge server and bridge worker
  inside the Slurm job. The server receives and queues requests; the worker
  carries them out in Apptainer containers.

These changes apply at runtime; they do not reinstall Harbor or edit its installed files.

Separate processes communicate to control each task container:

```text
Harbor → bridge server → bridge worker → task process → container
```

The bridge worker retains container lifecycle state outside the task memory
limit. A disposable executor runs Apptainer and the terminal inside a dedicated
Slurm step with the task limits. It returns command output or errors to the
bridge worker and server. Harbor polls the server to retrieve them.

| File | Why it exists |
| --- | --- |
| `bridge_client.py` | Retry interrupted communication with the bridge (task-container commands, startup/shutdown and file transfers) without running commands twice |
| `bridge_server.py` | Recognize repeated requests, keep results for retrieval after connection failures, and clean up unused task containers |
| `bridge_timeouts.py` | Give the task process time to return a command's result to the worker, and the worker time to send it back before Harbor stops waiting; see [timeouts below](#command-timeouts) |
| `bridge_worker.py` | Rebuild cached images when task files change, copy files correctly, and make task files, saved dependencies and networking available inside containers |
| `trial_step.py` | Launch task Slurm steps and carry local RPC requests; also retain the optional legacy lifecycle-worker pool |
| `protected_step.py` | Keep lifecycle control outside task memory limits and restore the same disk-backed container after a confirmed task OOM |
| `oom_recovery.py` | Give Terminus OOM feedback in its existing conversation, restart its terminal without replaying commands, and record recovery counts |
| `fakeroot_ipc.py` | Remove leftover communication resources from a task's fakeroot helper (used to simulate root permissions), without disrupting other tasks |
| `reasoning_field.py` | Save reasoning text returned by vLLM in the agent trace; Harbor otherwise misses it because it expects a different response field name |
| `tmux_diagnostics.py` | Log the attempted commands and container state when the agent's terminal session unexpectedly closes, so the failure can be investigated |
| `tmux_runtime.py` | Require working tmux before starting its persistent session; disable Harbor's task-startup tmux installer |
| `tmux_capture.py` | Bound terminal captures before Harbor's batch markers are added, so worker output truncation cannot remove the protocol framing |
| `tmux_socket.py` | Keep each container's tmux socket under `/run/ot-harbor-tmux`, outside task scratch cleanup in `/tmp` |
| `upload_runtime.py` | Run upload bookkeeping independently of task login profiles, PATH changes and shell startup hooks |
| `local_image_base.py` | Reuse explicitly selected, SHA-256-verified local base SIFs for native and deferred image builds without registry pulls |

## Shared pass@k infrastructure fixes and remaining limits

These patches are shared across datasets. Newly submitted validation jobs freeze
them into their code snapshots; editing this checkout does not update an existing
teacher job. Passing regression tests establishes the reproduced fixes below,
not that every historical terminal, upload or connection error has been resolved.

### Truncated tmux batch responses

The pinned Marin Harbor fork combines two changes: [PR 136](https://github.com/marin-community/harbor/pull/136)
retains only the last 500,000 characters of an Apptainer exec stream, while
[PR 137](https://github.com/marin-community/harbor/pull/137) adds Terminus batched
terminal calls with status markers embedded in their output. Both were introduced
by Ben Feuer, in commits
`c52e5d30` and `06139137`. A long terminal capture can push the leading markers
out of the retained response, causing `TmuxBatchProtocolError` even when the
terminal command ran. This is specific to the Apptainer/Terminus path.

`tmux_capture.py` limits the visible and full terminal captures separately before
the surrounding batch script adds its framing. The lifecycle worker derives each
capture's byte budget from its actual per-stream character limit:
`(max_exec_output_chars - 1024) // 2`. The reserve leaves room for the markers.
A sentinel and whole-line trimming preserve small captures and avoid cutting a
UTF-8 character; `pipefail` preserves capture errors. The global output limit
remains in place, other backends are unchanged, and no terminal command is replayed.

`validation/stages/harbor.py` installs the agent-side script patch;
`bridge_worker.configure_worker()` installs the budget adapter in the lifecycle
worker. Both sides must be present in a fresh job snapshot. Regression coverage:
`tests/test_tmux_capture.py` executes the generated shell script, applies worker
truncation, and checks Harbor's parser with long ASCII/Unicode output, different
limits, small captures and failing commands.

### Failed test uploads

Observed test uploads failed when Harbor's upload verification used `bash -lc`:
task or agent changes to `/root/.bash_profile` broke PATH, leaving `find`, `sort`
and `sed` unavailable. `upload_runtime.py` confines upload mkdir, extraction and
verification to `/bin/bash --noprofile --norc`, with a standard system PATH,
`BASH_ENV`/`ENV` removed, and working directory `/`. It does not change the shell
configuration used by agent commands or task tests.

A separate failure occurred when Harbor stopped polling an accepted upload after
120 seconds while it was still running. Directory upload includes mkdir,
extraction and verification, whose individual deadlines already total 210 seconds
before staging overhead or safe mkdir retries. `bridge_timeouts.py` gives uploads
a 600-second lifecycle RPC budget and a 630-second result-wait budget, measured
from acceptance. The enclosing preparation/verifier timeout still applies.
The client waits for the same accepted operation; it does not submit a second
upload or rerun the agent. These repairs do not cover arbitrary extraction errors,
missing files, disk failures or permanent worker failures.

Regression coverage: `tests/test_upload_runtime.py` reproduces poisoned PATH and
startup hooks and checks scope restoration after errors. Upload result-wait and
submission-deduplication tests are in `tests/test_bridge_transport.py`.

### Connection failures

The existing `bridge_client.py` / `bridge_server.py` transport retries transient
bridge HTTP 408/429/502/503/504 responses, connection resets, temporary DNS
failures and interrupted responses. Requests use bounded, jittered backoff within
a 60-second retry deadline. Submission retries retain the same request ID and
payload; server receipts prevent accepted work from executing twice. Polling can
recover a lost response without replaying the operation. A bridge restart changes
its epoch, so the client fails instead of risking duplicate execution.

This protects Harbor-to-bridge communication. It is not a general fix for model
API/vLLM connection failures, a dead server, authentication failures, or network
downloads inside task scripts. Those need their own evidence and diagnosis.
These transport safeguards predate the truncation and upload repairs above.

### Lost tmux sessions and tmux command errors

Existing safeguards keep a persistent tmux owner in the task's Slurm lifecycle
step, require working image-provided tmux, and clean up only fakeroot IPC resources
owned by the stopped lifecycle worker. `tmux_diagnostics.py` records attempted
keystrokes, sessions, sockets, processes, memory events and fakeroot information
when a session ends.

The validation runner no longer automatically retries `TmuxSessionEndedError`.
The previous one-retry policy selected only the exception type and could give an
agent a new attempt after its own command closed the shell. Both session-end and
command errors remain recorded exceptions pending cause review; this change
does not automatically relabel them as scored agent failures. Agent timeouts
remain excluded from retries. `tmux_diagnostics.py` now records failed command
batches for both exception types, preserving the original error even if its
diagnostic probe fails.

The 2026-10-08 audit of saved SETA teacher batch reports found 28 final
`TmuxCommandError` results: 25 missing `/tmp/tmux-0/default` sockets, one missing
tmux executable, one memory allocation failure and one unexpected server exit.
The missing-socket group includes disk-cleanup trajectories using
`rm -rf /tmp/* /var/tmp/*`. The missing-executable trajectory contains an
autoremove command, but the saved successful operation reports zero removals;
it does not establish that the agent removed tmux. The cause remains unconfirmed.
These are different failure mechanisms from output truncation.

`tmux_socket.py` now sets `TMUX_TMPDIR=/run/ot-harbor-tmux` for worker execs,
and the persistent anchor uses the same location. The directory belongs to the
container's private writable filesystem. New containers prepare both fakeroot
and host-uid socket directories there. Task `/tmp` cleanup therefore does not
delete the terminal socket. This does not prevent an agent from explicitly
killing tmux or removing its executable; it does not reinstall tools, recreate
a lost shell or replay agent commands. A real Apptainer fakeroot smoke test
confirmed `/tmp` cleanup preserves the terminal session, and deleting that
session leaves the anchor alive. Local tmux regressions also verify independent
server roots and reproduce the old missing-socket failure.

The same audit found 42 final `TmuxSessionEndedError` results. Across 245
session-loss diagnostics in the root job logs (including retries and additional
attempts, not 245 final failed trials), 242 still show the `_pilot_anchor`
session. Observed commands include `exec`, `tmux kill-server`, and nested
heredocs reusing `EOF`, which can execute script tails and `exit` in the
interactive shell. An intact anchor establishes that the server survived; it
does not by itself establish why the agent shell exited. Historical attempts
need outcome-classification review; existing frozen runs keep their old policy.

Four final report errors contain local bridge connection reset/refused messages;
none of the scanned final exceptions is a model API connection exception.
Root job logs also show local connection refusals around Slurm lifecycle-step
OOM kills. `trial_step.StepSlot.request` uses an AF_UNIX socket on the same node,
so these errors must not be described as external network or vLLM failures.
One reset was followed by a successful download from the same environment;
not every reset proves a dead worker. Local RPC socket errors now include the
operation, connect/send/receive phase, worker return code and bounded worker log.
A failed connection is identified as not submitted; an error during send or
receive leaves execution unknown. Neither case automatically replays a command.
This diagnostic change does not prevent resource exhaustion. The separate tmux
allocation failure followed repeatedly killed simulation processes, consistent
with memory pressure, but the saved evidence does not identify its exact limit.
The unexpected server exit likewise lacks a proven root cause. Audit evidence
is in the Helma experiment directory
`shared-infra-audit-20261008` beside `teachers-20261007`.

Follow-up Slurm accounting identifies 2 GiB allocations for the affected
lifecycle steps. The allocation-failure step (`953165.13`) and unexpected-server-
exit step (`953166.3`) are marked `OUT_OF_MEMORY`, but each was reused across
multiple tasks; a step-wide peak or final state cannot attribute an OOM event
to one particular attempt. Repeatedly killed simulations make memory exhaustion
likely for the allocation failure. It remains only a plausible explanation for
the unexpected server exit. The missing-executable step (`953265.2`) completed;
the archive lacks both the failed command batch and a post-failure filesystem
snapshot, so its exact cause cannot be reconstructed from saved evidence.

These historical runs included the Python lifecycle worker and terminal in
the task limit. Connection errors alone do not establish agent memory exhaustion.
New jobs use the recovery and reporting policy below; this audit does not
rewrite historical outcomes. See `assessment.json` for per-case evidence.

In the historical implementation (now available with `OT_OOM_RECOVERY=0`),
step reuse is sequential, not concurrent sharing by different task containers.
The pool reuses a live worker only after its previous container stops cleanly;
dead workers are discarded. This amortizes Slurm step startup, but also retains
step-wide resource counters and log history across tasks. An OOM state for a
reused step therefore does not mean all tasks previously using it failed, and
the audit found no evidence that these OOMs killed other concurrently running
tasks. `OT_STEP_REUSE=0` already provides one lifecycle step per environment for
diagnosis; it does not separate the worker from that environment's memory limit.

Matching the audited final exceptions to the actual environment/step IDs and
timestamped Slurm OOM messages identifies **8 interrupted attempts across 7
distinct tasks**, with an OOM message within seconds of the worker connection
failure. Three further connection resets lack this contemporaneous evidence.
This counts OOM-related worker interruptions, not all command-level OOMs or
proof that the agent alone consumed the budget. Exact matches are recorded in
`worker-oom-correlations.json` in the audit directory. Neither historical
trajectories nor rewards were rewritten.

Shared-patch regression check on 2026-10-08: **94 passed** across
`test_tmux_socket.py`, `test_tmux_diagnostics.py`,
`test_tmux_capture.py`, `test_tmux_runtime.py`, `test_bridge_transport.py`,
`test_upload_runtime.py`, `test_bridge_runtime_safety.py` and `test_trial_step.py`.
This check does not close unresolved historical tmux failures or establish
anything about model-API failure recovery.


### Continuing an agent after a confirmed task OOM

Enabled by default for new Slurm jobs, `protected_step.py` keeps the lifecycle
controller in the shared bridge process, outside the task's memory cgroup.
Apptainer, tmux and a small subprocess executor run in a dedicated task step.
Steps are no longer reused across tasks on this path, so memory counters and
failure logs belong to one environment and execution generation. The small
executor, terminal and container runtime still consume part of the task budget;
this is not a claim that every byte was allocated by agent code. Model serving
and the Harbor conversation stay outside this task step.

Recovery requires an increase in that step's cgroup v2 `oom_kill` counter or an
explicit Slurm `oom_kill event` in its own log. Exit 137, a missing shell and a
connection reset alone do not qualify. Only marked Terminus terminal operations
can recover. Setup and verifier OOMs remain failures of their respective phases.

On confirmed OOM, the controller reaps the entire old execution step, cleans up
its recorded fakeroot IPC resources, and creates a new step with the same limits.
It reopens the original disk-backed writable overlay and bind mounts using the
saved successful container-start command. Missing storage or a volatile overlay
causes recovery to fail. It never seeds a fresh filesystem, reruns setup, repeats
an upload, or replays the interrupted command. This also handles an OOM that
kills the executor itself. Killing all old processes makes the reset explicit
and prevents two instances from writing the same overlay concurrently.

`oom_recovery.py` then creates a new tmux terminal and returns `TASK_MEMORY_LIMIT`
feedback in the current interaction. The feedback explains that disk-backed
files survived, but processes, shell variables, working directory and
memory-backed files did not. The interrupted command may have partially
completed. The same conversation and original agent timeout continue; recovery
does not grant a new attempt or reset the time budget. Agent-caused shell exits
without confirmed OOM are not automatically recovered.

Stage 7 trace metrics record `oom_recoveries` per trajectory and in the aggregate,
plus `trajectories_with_oom_recovery`. A recovered trajectory can still succeed or
finish with another outcome. An unrecoverable confirmed agent OOM raises
`TaskMemoryLimitError`, reported as `task_memory_limit`, rather than an inference
error. Historical trajectories and rewards are unchanged.

Both the bridge and agent patches must be included in a fresh job snapshot.
`OT_OOM_RECOVERY=0` restores the legacy lifecycle-worker path for diagnosis;
`OT_TRIAL_SRUN=0` bypasses task-step isolation and this recovery mechanism.
The protected path requires readable, enforcing cgroup v2 memory limits and
fails startup if they cannot be established. It does not recover loss of the
shared bridge, node, job allocation or its storage.

Validation on Helma, 2026-10-08: job **954666** used a 512 MiB task step and
triggered an actual Slurm OOM termination. The controller survived in the batch
cgroup, reopened the filesystem in a replacement step, returned OOM feedback
through the real Terminus terminal wrapper, preserved files under `/opt`, `/etc`,
`/root`, `/workspace` and `/tmp`, and successfully executed the next terminal
command. Evidence: `/hnvme/workspace/y500bb12-seta-validation/experiments/oom-recovery-20261008/result-954666.json`.
Regression tests in `test_protected_step.py` and `test_oom_recovery.py` cover the
executor socket, no command replay, unconfirmed failures, missing preserved
storage, cancellation, the original timeout, and outcome reporting.

A task container counts as unused when it is ready, has no queued or running
commands, and has been idle for more than `BRIDGE_STALE_READY_SEC` (default:
3,600 seconds). The server asks the worker to stop and delete it.

Task startup checks `tmux -V` and rejects missing or broken tmux with an image
rebuild instruction. It neither installs tmux nor restores libraries. Image
preparation owns installation; datasource Dockerfiles must provide terminal
tooling. The InferredBugs patcher explicitly installs and checks tmux. Newly
built images must pass `tmux -V` and, when available, `dpkg --audit` before the
shared builder publishes them. Existing cache bundles are not retroactively
certified by this gate; the startup check still rejects broken tmux in them.

The earlier runtime library repair is removed. Historical job 948835 passed all
42 InferredBugs retries using that workaround; it does not validate newly
generated image definitions. The InferredBugs patch now records a dpkg ownership
override for libutempter's amd64 helper before installing tmux: root:root, mode
0755. These tasks do not need privileged utmp login accounting. This avoids the
unmapped group in single-user builds while allowing normal package installation.
Cold-install probes on Debian 12 and Ubuntu 24 completed with an empty dpkg audit
and a working tmux session (job 949467, repeated with the exact generated
Dockerfile command in job 949478).

Image preparation runs each Dockerfile RUN in its own checked shell so an
earlier failure in an `&&` chain cannot be hidden by a later successful RUN.
`OT_IMAGE_BASE_MANIFEST` can select a JSON mapping under `bases`, keyed by OCI
reference, with absolute `path` and `sha256` fields for each raw base SIF.
These must be base images, not completed task images or deferred overlays.
Missing references or changed content fail preparation rather than contacting
the registry. After a separate preparation gate passes, validation jobs can set
`OT_REQUIRE_PREBUILT_IMAGES=1` to reject cache misses without rebuilding.

## Command timeouts

For a command with execution limit `T`, the timeouts are:

| Who waits for whom | Limit | Where it comes from |
| --- | --- | --- |
| Task process waits for the command inside the container | `T` | Upstream Harbor enforces the supplied command timeout; its fallback is 600 seconds |
| Bridge worker waits for the task process to return output or an error | `T + 60 seconds` | Our `trial_step.py` uses `bridge_timeouts.py`, allowing time for command termination and the response |
| Harbor waits to retrieve the result from the bridge server | `T + 60 + 30 seconds` | Our `bridge_client.py` uses `bridge_timeouts.py`; upstream waits only `T + 30` |

These waits overlap; the extra time does not extend the command's execution limit.
Our adapter in `validation/stages/harbor.py` uses a 7-day command limit when no
explicit timeout is supplied, replacing upstream's 600-second fallback. Separate
agent/verifier limits still apply. The 60/30-second allowances in
`bridge_timeouts.py` apply to command execution, not to every startup, shutdown
or file-transfer request.

## After upgrading Harbor

Before removing a patch after a Harbor upgrade, run its regression tests under
`tests/test_bridge_*`, `test_trial_step.py` and `test_fakeroot_ipc.py`, then run
container smoke tests. Dataset verifier behavior does not belong in these patches.

## Image workspace preservation

Before mounting a fresh task directory at `/workspace`, the worker copies the
image's existing `/workspace` into it, including hidden files, `.git`, executable
bits and symlinks. The copy reads the base image plus any read-only deferred-build
overlay, without the trial's workspace bind. Uploaded task files and emulated
Dockerfile `COPY` files take precedence at colliding paths; directories merge
without following symlinks. Each container receives its own writable copy.

Missing or empty image workspaces still accept task uploads. A copy error or
300-second copy timeout aborts startup instead of hiding an incomplete repository.
Fakeroot fallback attempts reuse the already seeded directory. This adds one
workspace copy per fresh container and requires node-local space for that copy.

The preservation probe uses a temporary writable upper layer so Apptainer
applies deferred-layer whiteouts before copying the merged filesystem. On Helma,
a read-only-only mount exposed `.wh..wh..opq` to the copy command in the
InferredBugs retry. The probe now copies through a temporary tar archive,
preserving file modes, timestamps and links without filesystem-specific ACLs or
extended attributes. This also avoids the `Operation not supported` error from
`cp -a` on that mounted filesystem. Whiteouts are interpreted by the filesystem,
not filtered out of raw layer contents. Source images and build overlays remain
read-only. The temporary archive requires space in the probe's temporary directory.

Verified on 2026-10-07 with Scale-SWE's ten-task path-resolution pilot (Helma job
946901): 10/10 builds, 10/10 oracle rewards of 1, and 10/10 no-op rewards of 0.
The resolver found 9 unique paths and 4 missing paths, matching direct image
inspection; before this fix all 13 paths were hidden by the workspace mount.
Reports: `/hnvme/workspace/y500bb12-optiagent/runs/scaleswe-workspace-fix-20261006/submissions/00a5d2fcf689/report/`.
Repository tests: `pytest -q tests` — 702 passed.

## Image build resource limits and retries

The worker also supplies a job-local, read-only `/etc/hosts` with standard IPv4
and IPv6 localhost mappings. Disabling site bind paths can otherwise leave the
image's empty hosts file active, causing local socket tests to fail with
`socket.gaierror`. An explicitly supplied hosts mount takes precedence. This
does not expose the host's hosts file or change network namespace policy.
Live reproduction and repair evidence:
`/hnvme/workspace/y500bb12-optiagent/runs/scaleswe-972-path-resolution-20261007/reference-review/loopback-probe.json`.

Slurm validation prepares missing images with `hpc/image_cache.py` before task
timers and containers start. Each build has separate memory, CPU and timeout
settings; defaults are 8192 MB, four CPUs, two simultaneous builds, and 3600
seconds per image. Configure these with `--image-build-memory-mb`,
`--image-build-cpus`, `--image-build-concurrency`, and `--image-build-timeout-sec`.
Compression uses one quarter of this build memory allowance and its assigned
CPUs, rather than physical node memory. There is no global 256 MB override.
The total job wall time still bounds preparation and validation together.

A cache miss logs a warning. Successful SIFs and any required deferred metadata
and overlays are published together as immutable, checksum-verified bundles in
`$OT_WORKSPACE/images/bundles-v1`. Subsequent jobs stage matching bundles by
environment content hash. Partial builds and agent-modified workspaces are never
published. Concurrent publishers keep the first completed bundle. `--force-build`
rebuilds locally but does not overwrite an existing immutable shared bundle.

Preparation failures are recorded in `image-preparation.json` and build logs.
Timed trials refuse to rebuild a missing image under task limits. Task startup,
setup scripts, healthchecks, agent and verifier budgets otherwise remain intact.
This preparation is in the shared Slurm validation runner, not the path resolver;
direct standalone bridge launches must arrange their own image preparation.

Registry imports into temporary `.tmp` images retry at most twice for truncated
JSON, connection resets, or TLS handshake timeouts reported by the OCI conveyor.
Retries share the original timeout and overwrite only the temporary image.
Dockerfile commands and permanent registry failures are not retried.

The earlier 256 MB mitigation was tested in job 947035; that frozen job retains
the earlier behavior. New jobs use separate preparation.

Verified 2026-10-07: job 947056 built and published two Scale-SWE images with
8 GB build limits; both tasks passed stage 3 and scored oracle reward 1. Job
947076 restored both bundles in a fresh job, recorded two cache hits and no
builds, and again passed both stages for both tasks. Repository tests: 733 passed.
Evidence: `/hnvme/workspace/y500bb12-optiagent/runs/scaleswe-image-cache-20261007/verification.json`.
