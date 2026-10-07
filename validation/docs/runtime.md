# Runtime behavior

## Local storage

Helma stages the shared Python environment, code, upstream checkouts, certificates,
tasks, selected cached images and overlays, dependency archives, and model weights
under `$TMPDIR`. Runtime caches are local. Weight staging must succeed on every
serving node before the model starts; there is no shared-weight fallback.
Bootstrap and input verification still read shared storage. Reports, batch logs
and final evidence are durable outputs on shared storage. Site executables, CUDA
modules, network services and user-specified external mounts are not copied.

After changing Python packages, rebuild the transport archive:

```bash
"$OT_PREP_ENV/bin/python" hpc/local_runtime.py build "$OT_PREP_ENV" "$OT_WORKSPACE/runtime/prep.tar"
```

Setup does this automatically. Jobs verify the archive checksum and relocate the
Python interpreter before starting services. This is a copy of the same environment.

### Per-environment Slurm resources

The outer `.sbatch` allocation starts the shared bridge. On Slurm, the bridge
launches one persistent lifecycle worker per task environment using
`srun --exact -n1 -c<cpus> --mem=<memory> --gres=none`. CPU and memory requests
come from the effective Harbor environment config (including task.toml and
CLI overrides); missing values fall back to `OT_TRIAL_CPUS=1` and
`OT_TRIAL_MEM=4G`.

[`bridge_worker.py`](../../harbor_patches/bridge_worker.py) routes the environment
to [`trial_step.py`](../../harbor_patches/trial_step.py). This worker owns image
building/pulling, container startup, agent and direct verifier commands, file
transfers, dependency saving and teardown. The bridge sends requests over a
private node-local socket. Every container command inherits this worker's Slurm
step; the persistent tmux session does not request another step. A separately
configured verifier environment gets its own lifecycle step, and concurrency
accounting reserves both environments to avoid allocation deadlocks.

Each lifecycle worker temporarily writes `config.json`, `rpc.sock` and `step.log`
under node-local `$TMPDIR/trial-*`. After the process exits, including startup
and stop failures, the proxy copies at most the last 8 KiB of its log into the
shared worker log and deletes that control directory. Task steps redirect their
output there rather than creating individual `slurm-*.out` files. On Helma,
bridge server and worker output (including workers on other nodes) inherits the
batch job's output stream, producing one combined batch log instead of separate
`bridge.log`, `worker.log` and `worker-other-nodes.log` files. Older runs may still
have those separate files. Structured validation reports and packaged evidence
remain separate; model-serving and metrics logs also retain their own files.

### Bridge communication retries

`harbor_patches/bridge_client.py` is installed by validation's runtime adapter
and the Helma validation launcher. Status/result requests retry transient connection
failures and HTTP 408/429/502/503/504 responses with jittered exponential backoff
(0.5–1 s initially, capped at 5–10 s). Attempts have a 10 s socket timeout;
the total request retry window is at most 60 s and never exceeds the caller's
remaining deadline. Cancellation and permanent HTTP errors are not retried.

Exec deadlines are nested: the container command keeps its requested timeout,
the lifecycle RPC allows an additional 60 s, and the caller allows another 30 s
for dispatch/result delivery. Thus a 15 s inspection command has a 75 s RPC
budget and a 105 s result-wait budget. The result deadline is fixed when the
submission is accepted, and an expired wait never resubmits the command.
Timeout diagnostics include the last observed job state.

Fakeroot IPC cleanup is scoped to a dedicated lifecycle step. The worker records
message-queue and semaphore identities only when both queues identify the same
live `faked` process in that step's cgroup. After teardown, it reclaims only those
unchanged identities with no live owner or recorded client. There is no automatic
node-wide orphan sweep at worker startup or container stop: missing queues alone
cannot establish semaphore ownership. Untracked historical orphans are left
alone, as are resources of other jobs and resources outside dedicated Slurm steps.

Submissions use one request ID and identical payload across retries. The patched
server atomically records the returned environment/job ID before sending its
response; concurrent duplicates return that receipt without queuing another job.
Completed results remain available until the client acknowledges receipt, so a
lost polling response is also recoverable. Retry receipts and unacknowledged
polled results expire after five minutes, in memory only. Expired request IDs
are rejected rather than submitted again. Receipts are capped at 20,000 entries;
when full, the server rejects new submissions with 503 before queuing work.

On an environment-not-ready 409, the client checks its state and waits only for
pending/starting environments (or retries if startup completed meanwhile).
Stopping, stopped, missing environments and other 409 conflicts fail clearly.
The same request deadline bounds this wait; it does not restart the trial.

Both client and server must be updated. A server epoch binds each submission to
one server lifetime: after a bridge restart, retries fail rather than duplicate
an operation whose receipt was lost. This handles transient communication loss,
not recovery of in-flight work after a server crash. The protocol assumes cluster
clocks agree within 30 s. Worker-to-server dispatch/result delivery is unchanged.

Previously only the tmux anchor ran under `srun`; image creation and direct
`apptainer exec` calls could inherit the outer job's resources. Joining an
Apptainer instance alone does not put a command in the tmux server's Slurm step.
Instance pooling is disabled for lifecycle workers so different tasks cannot
reuse an old resource allocation. `OT_TRIAL_SRUN=0` explicitly opts out, and
outside Slurm the worker continues to run containers directly.

On other nodes of a multi-node job, the shared coordinator step uses
`--overlap` so it can coexist with task steps. Task steps themselves do **not**
use `--overlap`: they request distinct CPU sets. They are pinned to the
coordinator's node with `--cpu-bind=none` to avoid inheriting its binding mask.

### Command time limits on the bridge

The pinned bridge client runs a command that names no limit of its own for at most 600 s, and
Harbor's verifier names none: every verifier that needed more than ten minutes was cut off with
"Command timed out after 600s" and reward 0 (InferredBugs, 2026-10-02: 178 of 28,977 trials).
`validation/stages/harbor.py` therefore sends such commands with a limit of a week, so that the
limits that count are Harbor's own: the task's `agent.timeout_sec` and `verifier.timeout_sec`,
which Harbor enforces around the whole agent run and the whole verifier run.

### Dependency archives

A task whose build downloads its dependencies (Maven, NuGet, ...) can keep them in one archive
per task, so that later trials build without downloading. Two options switch this on, and both
are frozen in the contract:

- `--dependency-archives DIR`: a folder on shared storage with one `<task>.tar` per task.
- `--dependency-layout FILE`: a JSON file of the dataset with `target` (the file the task's own
  scripts read the archive from), `folder` (the container folder an archive is made of) and
  `exclude` (tar patterns left out). InferredBugs: `data/inferredbugs/dependency_archives.json`.

Stage 4 writes the archives: an oracle trial starts without an archive, so its build downloads
everything, and when the verifier gives reward 1 the worker saves the container's `folder` as
a local `<task>.tar` before the container stops; the launcher saves new archives
back to `DIR` after task execution. Every other trial (stage 5, agent stages) of a task
that has an archive gets its staged local copy mounted read-only at `target`. Unpacking is the task's own business;
the bridge only mounts the file. Only oracle trials can write an archive, so an agent cannot put
anything into it. The options need a fresh container per stage and are refused together with
`--reuse-validation-containers`.

A worker started outside this runner (teacher or RL runs) mounts the same archives when it is
started with `OT_DEPENDENCY_ARCHIVES` set to the JSON object
`{"directory": DIR, "target": ..., "folder": ..., "exclude": [...]}`.

### Container validation throughput

Stage 3 appends each finished task to `outcomes.jsonl` and writes its full stage
summary only at completion (an initial empty summary records that it started).
Its environment results include start, inspection, and stop durations.
Stages 4 and 5 retain Harbor's individual trial results and use fresh containers
unless the option below is enabled.
Harbor's upstream aggregate progress snapshots are unchanged.

For allocation-worker jobs, `--container-start-concurrency` controls simultaneous
Apptainer starts (default 8), separately from active task concurrency.
`--container-start-interval 0.25` spaces admissions by at least a quarter-second;
the default is 0 (no added spacing). These controls apply to container starts,
not cold image compilation. Benchmark changes on a pilot before raising the cap;
staggering is not proof that a higher cap is safe or faster. Controls are frozen
in the contract and passed to the bridge by the worker. Directly managed bridges
use `BRIDGE_START_CONCURRENCY` and `BRIDGE_START_INTERVAL` instead.

With `--reuse-validation-containers`, selecting both stages 4 and 5
groups them per task in order **5 → 4**. Stage 3 runs separately first when selected
before them, including its retry passes and build gate. Each surviving task
keeps its Apptainer instances until its selected phases finish. `--concurrency`
bounds concurrent task groups. A standalone stage starts normally, using the
existing image cache or building the image if needed. Incompatible environment
contexts/configurations also start separate instances.

The grouped path appends stage outcomes to `outcomes.jsonl` and writes full stage
summaries initially and at completion. Harbor's trial artifacts and upstream
progress snapshots remain unchanged. Reports record actual starts and reuses.

Between phases, downloaded trial artifacts are retained and container agent and
verifier logs are cleared. Other filesystem changes persist, including changes
made by the no-op verifier. The contract records this execution profile; it does
not establish fresh-container oracle parity. This option requires Apptainer,
one attempt, and no `--force-build`. Existing submitted code snapshots are
unaffected. The orchestration has mock-based tests; live-cluster performance and
parity still need a pilot.

### Resuming static checks

`--static-resume CHECKPOINT_DIR` imports successful checks from a preserved
checkpoint. It verifies the checkpoint files, original contract, task hashes,
upstream revision and input transformations. Failed or missing checks run again;
changed adaptations run again by default. The explicit
`--static-resume-accept-previous-path-check` exception retains earlier successful
path checks and records their original adaptation in the new report. The new
contract hashes the checkpoint; imported checks retain their evidence references.

`validation/checkpoints/recover_checkpoint.py` preserves normalized tasks, the old static
summary, original contract and manifest, checker source, and compressed check
logs before an old allocation is cancelled. Pass `--static-resume CHECKPOINT_DIR`
when preparing a new contract, together with the stages and task selection to
run. The runner imports reusable stage-1 outcomes; it does not automatically
choose a smoke sample or add stages. Use `--publish-require-complete` with
automatic publication to require outcomes for stages 1, 3, 4 and 5. Validation
findings can archive tasks; incomplete execution prevents that publication.
