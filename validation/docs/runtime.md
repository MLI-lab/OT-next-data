# Runtime behavior

## Local storage

Helma stages the shared Python environment, code, upstream checkouts, certificates,
tasks, selected cached images and overlays, dependency archives, and model weights
under `$TMPDIR`. Runtime caches are local. Weight staging must succeed on every
serving node before the model starts; there is no shared-weight fallback.
Bootstrap and input verification still read shared storage. Reports, batch logs
and final evidence are durable outputs on shared storage. Site executables, CUDA
modules, network services and user-specified external mounts are not copied.

ZIH permits the site's Apptainer 1.5.0 and 1.5.2 compute installations. The
launcher warns when using the 1.5.0 alternative and records the actual version
in `execution.json`; other versions still fail the version check. Allowing a
site version is not evidence that every benchmark environment has passed on it.

Model-server readiness has a 600-second cutoff by default. Set
`OT_MODEL_STARTUP_TIMEOUT` to a positive number of seconds to override it, or
zero to disable it explicitly. Service process exits still fail startup, Slurm's walltime
still applies, and the launcher prints a waiting message every minute. A living
but stuck service can wait until the allocation ends when the cutoff is disabled. Task execution
begins only after readiness. The configured startup limit and successful
readiness wait duration are recorded under `execution.json`'s `serving` field.
Existing frozen submissions retain their captured launcher and configuration.

Model startup permits three retries after the initial attempt by default
(`OT_MODEL_STARTUP_RETRIES=3`). Each attempt has its own readiness budget and
log files. Before retrying, the launcher terminates the attempt's serving
processes and confirms that its uniquely named Slurm steps have disappeared.
Remaining matching steps are killed; unconfirmed cleanup aborts the job instead
of launching another server. Other steps and other allocations are untouched.
Four failed attempts exhaust the default retry budget, then the worker records
the failure, preserves evidence and exits. Slurm releases the allocation on
exit. Attempt errors, log names, elapsed times and cleanup outcomes are recorded
under `execution.json`'s `serving.startup_attempts` field.

After changing Python packages, rebuild the transport archive:

```bash
"$OT_PREP_ENV/bin/python" hpc/local_runtime.py build "$OT_PREP_ENV" "$OT_WORKSPACE/runtime/prep.tar"
```

Setup does this automatically. Jobs verify the archive checksum and relocate the
Python interpreter before starting services. This is a copy of the same environment.

### Per-environment Slurm resources

The outer `.sbatch` allocation starts the shared bridge. On Slurm, the bridge
launches one disposable execution step per task environment using
`srun --exact -n1 -c<cpus> --mem=<memory> --gres=none`. CPU and memory requests
come from the effective Harbor environment config (including task.toml and
CLI overrides); missing values fall back to `OT_TRIAL_CPUS=1` and
`OT_TRIAL_MEM=4G`.

[`bridge_worker.py`](../../harbor_patches/bridge_worker.py) installs a protected
controller from [`protected_step.py`](../../harbor_patches/protected_step.py) in
the shared bridge process. It retains lifecycle state outside the task memory
limit. A small executor inside the task step launches Apptainer subprocesses,
including the persistent tmux owner, and returns results over a private
node-local socket. Container commands inherit the task limits. A separately
configured verifier environment gets its own execution step, and concurrency
accounting reserves both environments to avoid allocation deadlocks.

Confirmed task OOM during a Terminus terminal operation reaps the old step and
reopens the same disk-backed overlay and mounts in a replacement step. Harbor
receives explicit OOM feedback and creates a new terminal in the same agent
conversation and time budget. Setup and the interrupted command are not replayed.
Recovery counts and terminal `task_memory_limit` outcomes appear in stage 7 trace
metrics. See [OOM recovery details](../../harbor_patches/README.md#continuing-an-agent-after-a-confirmed-task-oom)
for evidence requirements, lost process state, reporting and validation.
The legacy lifecycle-worker pool has been removed.

Each task executor temporarily writes `config.json`, `rpc.sock` and `step.log`
under node-local `$TMPDIR/trial-*`. After the process exits, including startup
and stop failures, the proxy copies at most the last 8 KiB of its log into the
shared worker log and deletes that control directory. Task steps redirect their
output there rather than creating individual `slurm-*.out` files. On Helma,
bridge server and worker output (including workers on other nodes) inherits the
batch job's output stream, producing one combined batch log instead of separate
`bridge.log`, `worker.log` and `worker-other-nodes.log` files. Older runs may still
have those separate files. Structured validation reports and packaged evidence
remain separate; model-serving and metrics logs also retain their own files.

### Terminal capture limits

[`tmux_capture.py`](../../harbor_patches/tmux_capture.py) bounds Apptainer
Terminus batch captures before Harbor adds its status markers. The lifecycle
worker reserves 1,024 characters for framing and splits the remaining exec
output allowance between the visible screen and scrollback. Captures are
limited in bytes to retain recent complete lines without splitting UTF-8;
small captures stay unchanged. The worker's ordinary output limit remains in
force. Capture failures still propagate, and the patch never replays agent
commands. Other backends keep Harbor's existing capture behavior.

Both the agent runtime and lifecycle worker install this patch. Existing frozen
submissions retain their original code; new submissions include it in their
code snapshot and contract implementation hashes.

### Bridge communication retries

Upload bookkeeping uses a non-login shell with a standard system PATH and no
`BASH_ENV` or `ENV` startup hooks. This applies to transport extraction and
verification only. Agent commands and task verifiers retain their task shell
configuration. An accepted upload has a 600-second lifecycle RPC budget and a
630-second result-wait budget, covering extraction, verification and bounded
directory-creation retries. The enclosing preparation or verifier deadline still
limits the complete phase. Polling retries keep waiting for the accepted upload;
they do not rerun the agent or submit duplicate uploads.

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

Apptainer instances use a private IPC namespace (`--ipc`, or the equivalent
isolation already supplied by `--containall`). This applies in the shared bridge
on every cluster. Persistent fakeroot clients can leave SysV message queues and
semaphores behind when stopped; in the host IPC namespace these objects retain
user namespace references even after all container processes exit. Private IPC
makes those objects disappear when the last process in the IPC namespace exits.
Container commands must join the existing instance IPC namespace, rather than
creating a separate IPC namespace for every exec.

The instance isolation does not remove historical objects left in the host IPC
namespace. Keep affected nodes excluded until an ownership-verified cleanup or
administrator cleanup has completed. Never sweep all IPC resources for a shared
UID while other jobs may use them. Frozen submissions keep their original
runtime; submit fresh snapshots to apply this change to pending work.

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
The protected path gives each environment its own execution step and never
reuses it for another task. There is no cross-task executor reuse. `OT_TRIAL_SRUN=0` explicitly opts out, and
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
  `exclude` (tar patterns left out).

By default, stage 4 writes the archives: an oracle trial starts without an archive, so its build downloads
everything, and when the verifier gives reward 1 the worker saves the container's `folder` as
a local `<task>.tar` before the container stops; the launcher saves new archives
back to `DIR` after task execution. Every other trial (stage 5, agent stages) of a task
that has an archive gets its staged local copy mounted read-only at `target`. Unpacking is the task's own business;
the bridge only mounts the file. Only oracle trials can write an archive, so an agent cannot put
anything into it. A dataset layout can set `"reuse_for_oracle": true` to let oracle retries
also mount an existing archive read-only. A successful oracle can replace its archive atomically;
failed trials and non-oracle agents cannot publish one. Missing archives still start cold.
InferredBugs enables this option to avoid downloading the same historical build dependencies
on every oracle retry. The contract records the selected policy.
The options need a fresh container per stage and are refused together with
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

### Instruction normalization and checkpoint identity

When instruction normalization is enabled, contract preparation applies it before
hashing for every validation stage selection, including stage 3 alone. Image-only
prebuilds do not normalize instructions. Original inputs remain unchanged. Use the
same normalization settings and evidence for the pilot and final validation.
Changed task content still invalidates stage-3 reuse and requires fresh evidence;
a hash mismatch is not downgraded to a warning or used to silently skip edits.

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

### Setup review

Optional `--review-setup` runs each task five times in fresh containers using
prepared images. Use `--review-setup 3` to change the repetition count. Each run
performs task preparation, then verifier preparation through `tests/setup.sh`.
The review never calls `test.sh` or grading.
Task preparation must succeed every time, have mean duration at most 30 seconds
and no run above 60 seconds. Override task limits with
`--preparation-mean-target-seconds` and `--preparation-max-seconds`; the legacy
`--preparation-median-target-seconds` remains an optional additional constraint.
Verifier preparation must succeed every time, have mean duration at most 5% of
its `task.toml` verifier timeout, and no run above 60 seconds. For multiple steps,
each verifier is evaluated separately against that step's timeout. A task may
be flagged in both categories.

Both preparation timers start after their container is ready. The **task timer**
includes uploading setup files and running task setup. The **verifier timer**
includes uploading setup/test files, running submission collect hooks, transferring submitted files,
running `tests/setup.sh` and cleaning up temporary
transfer files. These operations count even when performed by the runner outside
a setup script.

Task setup is not replayed in a separate verifier. Any initialization the verifier
needs belongs in `tests/setup.sh`; its image is declared by the task's verifier
configuration or `tests/Dockerfile`.

Container startup is recorded separately and must finish within the environment’s
`build_timeout_sec`. Startup failures still fail stage 3.

Image builds, runtime inspection,
teardown and verification checks are excluded. All work performed by a setup
command counts, including any compilation it performs. Slurm prepares
images before measurement; direct runs must have their images prepared already.
Host filesystem caches are not cleared. Apptainer reviews explicitly disable
per-task dependency archives from earlier trials. Configured external cache
mounts on other backends remain the caller's responsibility.

Use the standard scripts:

- `tests/setup.sh`: verifier preparation.
- `tests/test.sh`: verification checks and grading.

Stage 3 runs only `setup.sh`, after task setup, collect hooks and the required uploads/transfers.
Normal verification uploads the tests, runs `setup.sh`, then runs `test.sh` in the
same environment and within the existing verifier timeout. Stale rewards are cleared
before setup. An explicit new reward 0 from failed setup is returned without running
the tests; a failure without that reward is an infrastructure error. Setup failure or
timeout prevents checks from running. Setup output is saved separately from test
output. Omit `setup.sh` if no preparation is needed; no declaration file is required.

Some existing `test.sh` scripts mix setup and verification. Separate them first;
the runner cannot identify arbitrary installation commands inside a test script.
Do not call `setup.sh` again from `test.sh`, since the runner now calls it.
For multi-step tasks, a step's tests overlay the shared tests, including `setup.sh`.
Every verifier receives the task's test files, even if its image already contains
tests. Apptainer keeps the `/tests` upload mount for all images. Store image-only
dependencies or generated assets outside `/tests` so this mount does not hide them.

Submission transfers use the existing artifact declarations in `task.toml`.
Stage 3 runs declared verifier collect hooks before transfer and includes their
execution in verifier preparation timing; a failed hook fails setup review.
Review runs transfer the files available after task setup, before an agent has
produced a solution. The measured transfer size can therefore differ from a real
submission.

With `environment_mode = "separate"` under `[verifier]`, a task without a custom
verifier image uses the original task image in a fresh container. Submitted
artifacts are imported, tests are uploaded, and `tests/setup.sh` runs before
`tests/test.sh`. Any required initialization belongs explicitly in `tests/setup.sh`;
the runtime does not infer or replay task setup in the verifier.
No saved copy of the agent's modified environment is used. A custom verifier
image (a verifier environment definition or Dockerfile in its tests directory)
uses the same upload and setup sequence. Existing shared-mode tasks remain shared
until migrated with the correct artifact declarations.

Failed or slow tasks fail stage 3. The two review indexes are
`review/task-setup-needs-review/index.json` and
`review/verifier-setup-needs-review/index.json`. Both reference portable task
archives and timing/failure evidence under `review/setup-speed/`, without storing
duplicate task archives. Review mode uses exactly the requested repetitions
instead of the ordinary failure retry policy below. Five clean runs measure
preparation speed; they do not execute the verifier checks.

### Verifier execution evidence

Reference and no-op validation use one structured execution check. Console
messages such as `no tests ran` are diagnostic text, not execution evidence:
pytest plugin tests can legitimately capture those messages from an inner run.
The shared shell-verifier wrapper installs `ot_pytest_execution` for grading
only, preserving existing `PYTHONPATH` and `PYTEST_PLUGINS` values. The plugin
records each outer pytest invocation and ignores in-process and subprocess
pytest runs nested inside it. Setup and agent commands are not instrumented.

The wrapper writes `verifier/execution-context.json` with a fresh version-1
context token. The grading environment receives `OT_VERIFIER_EXECUTION_TOKEN`.
A runner adapter emits separate lines in the verifier log using
`OT_VERIFIER_EXECUTION:<token>:BEGIN:<json>` and
`OT_VERIFIER_EXECUTION:<token>:END:<json>`. Both records include `version: 1`
and the same unique invocation `id`. END also includes `finished: true`, integer
`executed`, `skipped`, `setup_errors`, `collection_errors`, and `exitstatus`.
The counts describe the outer invocation, not captured child output. Each
required top-level invocation needs its own record, including failed or empty
invocations. A producer must not report completion before the runner finishes.

The check rejects incomplete/malformed records, collection/internal/usage
failures, and zero executed tests (subject to the existing explicitly declared
missing-output setup rule). Ordinary assertion failures are execution evidence;
reward checking still requires reference reward 1 and no-op reward 0. This does
not change the task's test selection, assertions, or scoring implementation.

There is no legacy text-summary fallback. Historical logs without a matching
context cannot be certified by this check and need a fresh run. Non-pytest
runners, Windows batch verifiers, and scripts that replace the plugin environment
need an adapter producing the same records before their execution can be
certified. Missing instrumentation is reported as missing evidence, not as a
proven task defect. The current automatic recorder covers shell verifiers that
load pytest's explicit plugins and, through a `sitecustomize` module uploaded on
the same path, outer Twisted `trial` runs (counts come from the finished trial
reporter; loader `ErrorHolder` entries are reported as `collection_errors`). It
is not universal framework coverage. The plugin captures the token when pytest
configures, so suites that clear `os.environ` still produce a matching END record.
Verifier commands that assign `PYTHONPATH=` outright discard the recorder path and
abort pytest; the one SWE-Lego task doing so is patched to append the inherited
value instead.

One no-op exception is accepted deliberately. When the no-op run has collection
errors and executed no tests, and every `ModuleNotFoundError`/`ImportError`
names a module or symbol that the reference patch (`solution/*.patch`) adds,
removes or renames, the zero reward is a genuine result: the unpatched tree
cannot import the code the tests target. Any other collection error, and any
collection error in a reference run, remains a runner problem. Setup (fixture)
errors that leave no test executed are treated the same way. When the verifier's
one-line traceback mode hides where an error comes from, the no-op stage falls
back on the oracle report of the same submission: if the reference run executed
this suite and scored 1 in the same environment, a no-op that fails before any
test runs is accepted as a genuine zero, and the stage report notes the strict
finding it would otherwise have raised. A pytest usage error for an option the
reference patch removes from the project configuration is accepted on the same
basis.

### Container PATH and interpreter selection

For Apptainer definition builds, `definition_build_home` changes `HOME=/.root`
to `/root` only when that synthetic home directory is absent. It runs before
Dockerfile ENV exports, so an explicit image HOME keeps precedence. This repairs
builder-generated state without changing task commands. TerminalLego task_10513
passed CLAIX canary 4998502 with this adapter: the normal image build completed
in 25.046 seconds and all five preparations passed. The previous missing-home
failure had triggered a deferred root-mapped build whose package installation
failed on `chgrp`; this canary avoided that fallback rather than claiming to fix
all fallback UID/GID limitations.

`harbor_patches/exec_environment.py` preserves the image's `PATH` for Harbor
container exec calls. The precedence is:

1. An explicit per-command `env_vars["PATH"]` overrides the image and is not
   augmented with runtime tool directories.
2. Otherwise, use the image PATH, including deferred Dockerfile ENV supplied
   on each exec. Append `/root/.local/bin`, `/testbed/.venv/bin`, and available
   bound agent-tool directories after it, without shadowing image commands.
3. If PATH is unset at the helper-shell boundary, use the adapter's default
   search path. An explicitly empty PATH is not treated as unset.

A task script can subsequently export PATH or activate an environment. An
absolute interpreter path bypasses PATH lookup. The runtime does not choose a
universal Python version or discover arbitrary virtual environments. A venv
that exists but is absent from PATH is not automatically activated. In
particular, appending `/testbed/.venv/bin` does not make its Python win over
`/usr/bin/python` already present earlier. Tasks requiring that environment must
declare it in image ENV, activate it in the script, or invoke it explicitly.
`PYTHONPATH` controls Python module imports and is distinct from this executable
selection rule.

The prior pinned Harbor worker injected a fixed PATH on instance start and
exec, then modified PATH again in its proxy/tool prefix. This could hide image
venvs from command lookup without deleting their files. Its base-SIF helper
documents the need to expose tools under `/root/.local/bin` when HOME is
redirected, and bound agent tools. That explains retaining tool availability,
but does not justify overriding every task's interpreter. The local adapter
retains these directories as fallbacks. Upload bookkeeping separately uses
`upload_runtime.py` and a clean system PATH for mkdir/tar/checks; that isolation
does not select the interpreter for task setup or grading.

The backend's helper shell uses `bash -c`, which avoids sourcing task login
profiles on every command. Tasks that explicitly request a login shell still
receive one. Environment activation needed for both setup and grading must be
explicit in both processes; shell exports do not persist between exec calls.

Compatibility audit on 2026-10-14 inspected datasource patchers and shared
converters, rather than assuming every dataset uses CalibForge's layout:

| Datasource | Interpreter selection in current patch/conversion code |
| --- | --- |
| SWE-Lego and Scale-SWE | Converter test scripts prepend Conda environment directories; SWE-Lego also uses image ENV and absolute Conda Python for some dependency operations. |
| Multi-SWE | Grader invokes `/opt/multiswe-python/bin/python3 -I` explicitly. |
| BugsInPy | Verifier uses `/tests/.venv/bin/python` for installation and prepends that venv in grading. |
| SETA | Some tasks use explicit verifier environments; uv-related scripts explicitly prepend `$HOME/.local/bin`. |
| FACET | Patcher preserves environment activation in both setup and grading scripts. |
| InferredBugs | Explicit Python 2/analyzer paths and a JDK PATH recovery workaround remain in task scripts. |
| MIMO | No task Python/PATH override found in its patcher. |

These explicit choices remain effective under image-preserving exec. The audit
found no patcher evidence that a global forced Python was required for these
datasets. This is a static compatibility finding, not fresh validation of every
datasource or unmodified upstream task. Existing frozen jobs retain their old
runtime. New runtime rollouts still require dataset setup and verifier checks;
do not remove task-specific activation or absolute interpreter choices merely
because the shared PATH defect is fixed.

CalibForge evidence: all seven former PATH failures passed five fresh setup
runs each in job `1012674` on 2026-10-14. Together with three login-profile tasks
and one historical setup timeout, the canary passed 11 tasks and 55 setups.
This validates setup behavior, not complete grading across all datasets.
Evidence is retained at
`/hnvme/workspace/y500bb12-optiagent/calibforge-infra-20261014/investigation.json`.

### Cancelling timed-out agent and validation work

Validation executes container commands too: task setup, verifier setup, collect
hooks, runtime checks, reference solutions and grading. Cancelling a local
async wait does not by itself stop the remote command. An agent may also leave
work running in tmux while its local coroutine is waiting on a model response.

`command_cancellation.py` connects cancelled/timed-out bridge operations and
agent/shared-verifier phase deadlines to `/env/cancel`. A dedicated worker
control thread handles cancellation independently of busy execution threads.
It interrupts and reaps only that environment's protected Slurm step, including
detached descendants. The bridge acknowledges completion only after worker
cleanup succeeds. Errors or missing acknowledgements fail visibly. Command
generations reject both queued operations and delayed HTTP submissions from
before cancellation; unrelated environments are unaffected.

If interruption does not release the command lock within 15 seconds, the
control thread sends KILL to the numeric Slurm job/step identity previously
returned by that executor. This does not require its RPC or operation lock.
It confirms that the step has disappeared before reaping the launcher and
retrying the lock. Missing identity, failed scheduler queries, a surviving step
or a lock that still cannot be acquired are reported as cleanup failures.
Final teardown uses the same forced-stop fallback after its own interruption
grace period. Control files and task files are not deleted by the forced stop.

An isolated Helma Slurm smoke test (job `1013764`, 2026-10-14) exercised the
fallback with a blocked command and detached TERM-ignoring descendant. Both
were stopped, partial command output and the solution file were preserved,
and a sibling executor remained responsive. This verifies the Slurm teardown
boundary; it is not a complete benchmark run or a ZIH deployment.

Cancellation retains the disk-backed writable overlay and bind mounts. If
grading or artifact transfer subsequently needs that environment, it reopens
the same files without replaying setup or the interrupted command. Process
state, shell state and memory-backed files are lost. Reopening fails if the
original writable overlay or bind sources are unavailable; it never substitutes
a fresh task. Normal final teardown still deletes the environment as configured.
This mechanism requires the protected Slurm executor. It does not claim support
for an unpatched bridge or a backend that cannot reap its task process tree.

Captured command output is spooled in the step's control directory. A command's
own timeout kills its process group and returns captured output. When the whole
step is cancelled, teardown records the last 8192 bytes of each interrupted
stdout/stderr stream in the worker log before removing control files. Output
still buffered inside the task process cannot be recovered. Timeout thresholds
are unchanged; cleanup acknowledgement can add wall time after the execution
deadline, and setup-review timings may include that cleanup time.

Deploy the client, server and worker changes together in a new frozen job
snapshot. Existing frozen jobs do not acquire cancellation support by editing
the controller checkout.

Report export publishes an incomplete summary first, writes stage files by
atomic rename, and publishes the final summary last. Repair-loop collection
also verifies the actual stage files and permits a bounded visibility grace
after scheduler completion for older workers. A missing report remains an
infrastructure failure; it is never converted into a pass.
