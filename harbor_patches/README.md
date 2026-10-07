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

The task process runs inside a Slurm step and manages the Apptainer container.
It returns command output or errors to the bridge worker, which sends the result
to the bridge server. Harbor polls the server to retrieve it.

| File | Why it exists |
| --- | --- |
| `bridge_client.py` | Retry interrupted communication with the bridge (task-container commands, startup/shutdown and file transfers) without running commands twice |
| `bridge_server.py` | Recognize repeated requests, keep results for retrieval after connection failures, and clean up unused task containers |
| `bridge_timeouts.py` | Give the task process time to return a command's result to the worker, and the worker time to send it back before Harbor stops waiting; see [timeouts below](#command-timeouts) |
| `bridge_worker.py` | Rebuild cached images when task files change, copy files correctly, and make task files, saved dependencies and networking available inside containers |
| `trial_step.py` | Apply each task's CPU and memory limits through Slurm; reuse idle Slurm steps to reduce startup overhead while giving each task a fresh container |
| `fakeroot_ipc.py` | Remove leftover communication resources from a task's fakeroot helper (used to simulate root permissions), without disrupting other tasks |
| `reasoning_field.py` | Save reasoning text returned by vLLM in the agent trace; Harbor otherwise misses it because it expects a different response field name |
| `tmux_diagnostics.py` | Log the attempted commands and container state when the agent's terminal session unexpectedly closes, so the failure can be investigated |
| `tmux_runtime.py` | Require working tmux before starting its persistent session; disable Harbor's task-startup tmux installer |
| `local_image_base.py` | Reuse explicitly selected, SHA-256-verified local base SIFs for native and deferred image builds without registry pulls |

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
