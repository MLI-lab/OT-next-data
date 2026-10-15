# Cluster access from Helma

Configured on `helma1.nhr.fau.de` for `y500bb12`, 2026-10-11 and 2026-10-12.
The connection settings live in `/home/hpc/y500bb/y500bb12/.ssh/config`.
Use these aliases rather than reconstructing SSH commands with raw hostnames.
Connectivity is conditional on the agent and tunnels below; check it at the
start of a new session. SSH access does not establish launcher compatibility.

| Alias | Destination and user | Route | Status established in this session |
| --- | --- | --- | --- |
| `rwth` | `login23-1.hpc.itc.rwth-aachen.de`, `lva77460` | TUM jump host (`login01.msv.ei.tum.de:3022`) | Direct route timed out on 2026-10-14; TUM jump route verified |
| `tum` | `login01.msv.ei.tum.de:3022`, `ge74met` | Direct from Helma | Key login verified; jump host for LRZ |
| `lrz` | `login.ai.lrz.de`, `di38yur` | `ProxyJump tum` | Key login and Slurm access verified; includes the user's LRZ/MCML access, subject to scheduler entitlements |
| `barnard` | `login1.barnard.hpc.tu-dresden.de`, `frwe188h` | Helma loopback port `22231` through the user's Mac VPN tunnel | Key login and Slurm access verified |
| `alpha` | `login1.alpha.hpc.tu-dresden.de`, `frwe188h` | `ProxyJump barnard` | Key login and Slurm access verified; GPU cluster |
| `capella` | `login1.capella.hpc.tu-dresden.de`, `frwe188h` | `ProxyJump barnard` | Key login and Slurm access verified; GPU cluster |
| `romeo` | `login1.romeo.hpc.tu-dresden.de`, `frwe188h` | `ProxyJump barnard` | Key login and Slurm access verified |
| `julia` | `julia.hpc.tu-dresden.de`, `frwe188h` | `ProxyJump barnard` | Alias configured, host-key verification unresolved; do not claim working access |

## Quick checks

```bash
ssh -o BatchMode=yes -o ConnectTimeout=15 rwth hostname
ssh -o BatchMode=yes -o ConnectTimeout=15 lrz hostname
ssh -o BatchMode=yes -o ConnectTimeout=15 barnard hostname
ssh -o BatchMode=yes -o ConnectTimeout=15 capella hostname
ssh -o BatchMode=yes -o ConnectTimeout=15 capella 'squeue -u frwe188h'
```

Use `BatchMode=yes` for unattended work. Do not wait indefinitely on password
prompts or disable host-key checking to work around an authentication failure.
Use only Alpha and Capella for ZIH GPU serving in this workflow.

## Keys and the dedicated agent

The aliases select `~/.ssh/id_ed25519_rwth`, `~/.ssh/id_ed25519_tum`,
`~/.ssh/id_ed25519_lrz` and `~/.ssh/id_ed25519_dresden` respectively.
Only their public keys were registered at the destinations. Do not copy private
keys to remote clusters or put credentials into code, logs or Git.

TUM, LRZ and Dresden explicitly select this agent socket with `IdentityAgent`:

```text
/home/hpc/y500bb/y500bb12/.ssh/agent-clusters-helma1.sock
```

This avoids reliance on the current terminal's `SSH_AUTH_SOCK`. All aliases
have agent forwarding disabled. Inspect loaded public identities with:

```bash
SSH_AUTH_SOCK="$HOME/.ssh/agent-clusters-helma1.sock" ssh-add -l
```

The agent is a running process on Helma1, not a permanent credential service.
After a restart or loss of its unlocked keys, the user may need to unlock them
again interactively. A different Helma login node does not automatically have
the same live agent or loopback tunnel. RWTH may require renewed authentication
under its access rules even though its SSH public key is registered.

## Dresden VPN tunnel

The user's Mac provides Dresden network connectivity through eduVPN. The
reverse tunnel terminates specifically on **Helma1** and binds loopback only:

```bash
# Run on the user's Mac with the Dresden eduVPN connection active.
ssh -NT -o HostName=helma1.nhr.fau.de \
  -o ExitOnForwardFailure=yes \
  -o ServerAliveInterval=30 -o ServerAliveCountMax=3 \
  -R 127.0.0.1:22231:login1.barnard.hpc.tu-dresden.de:22 helma
```

The Mac's `helma` alias selects `~/.ssh/id_ed25519_nhr_fau` and
`ProxyJump csnhr`; its `csnhr` alias supplies the correct gateway identity.
Using a raw gateway hostname previously missed that identity selection.
The direct connection from the Mac to Helma1 timed out.

Keep the Mac awake with `caffeinate -i`, its lid open, eduVPN connected, and the
tunnel running. These may run in separate local tmux windows. Detaching tmux
does not stop processes, but tmux does not prevent laptop sleep or preserve a
lost VPN. Once submitted, Slurm jobs on ZIH do not depend on the tunnel;
Helma-side monitoring and additional submissions do. Transfers through this
route can be slow, so download public model weights directly on ZIH.

## Julia host-key check remains open

The observed Julia ED25519 fingerprint was
`SHA256:+vn9i5MRBxbh2u41cmZHi90ceQzJ3gqHlEW0tRtt0dY`.
The [ZIH published fingerprint page](https://compendium.hpc.tu-dresden.de/access/key_fingerprints/)
listed `SHA256:k5zc2E0Y8zcLErDD1Ej3g0OqkoLDF22ADmfX+b+8Z9g`
when checked in this session. RSA and ECDSA also differed.
Do not accept the mismatch automatically. Obtain confirmation from ZIH or
compare with a previously trusted Mac entry; the user was asked to run:

```bash
ssh-keygen -F julia.hpc.tu-dresden.de | ssh-keygen -lf -
```

## OT-next-data on ZIH

- Workspace: `/data/horse/ws/frwe188h-trp-shared/ot-next-data`.
- Workspace expiry observed: 2026-11-18; use `ws_list` to recheck.
- Account verified on Capella: `p_agents_finetuning`.
- Source snapshots: `/home/frwe188h/ot-next-data-snapshots/<commit>`.
- Initial snapshot: `07332c7`; later runs may use a newer frozen snapshot.
- Scale SWE preparation and pilot logs:
  `/data/horse/ws/frwe188h-trp-shared/ot-next-data/runs/scaleswe-pass8-zih-20261011`.
- The old `/home/frwe188h/OT-next-data` checkout was deleted with explicit user
  authorization. Do not recreate or confuse it with the new snapshots.
- Other shared workspace projects and environments belong to existing work;
  do not overwrite them.

Capella system preflight `4406590` passed. Read the current run logs and Slurm
state for end-to-end pilot and full-run status; the preflight alone did not
test generation. Keep large data, models, environments, caches and results in
the workspace, with compute execution staged to node-local scratch.
Snapshot code per run and reuse site-local data/model caches. Do not assume the
Helma launcher works unchanged on CLAIX or LRZ merely because SSH works there.

## Scale SWE unattended continuation, 2026-10-12

The current run has a Helma-side reconnecting controller at:

```text
/home/hpc/y500bb/y500bb12/.local/state/ot-next-data/zih-scaleswe-control-20261012
```

Read `status.json`, `watch.log` and `process.json` there before starting another
controller. `watch.py` retries connections for up to 12 hours from startup;
it is a temporary process, not a permanent service. `remote_gate.py` checks
both pilots and submits their prepared full arrays only after successful Slurm
completion, passing stages 3 through 7, exact task/attempt coverage, oracle 1,
NOP 0, real verifier rewards, nonempty agent/tool trajectories and no recorded
infrastructure errors. Initially task timeouts also blocked automatic submission;
a specific timeout may be accepted only after inspecting its saved evidence.
An agent reward of zero is allowed.

The run-specific controller records `pilot-gate.json` on ZIH and an exclusive
`gated-submission.json` per full array before calling Slurm. A submission marked
`submission_started` without a confirmed job ID requires reconciliation against
Slurm, not blind resubmission. Failed pilots stop the gate for investigation.
At controller startup, Instruct pilot `4406612` was running, the thinking model
was still downloading, and the Mac tunnel was timing out. These are historical
observations; read current records rather than assuming completion.

After the user restored the tunnel on 2026-10-12, both model downloads and both
pilots were confirmed complete. Pilots `4406612` (Instruct) and `4406624`
(thinking) passed task builds, oracle 1 and NOP 0 on both selected tasks, with
saved generation trajectories and no model-server, environment or verifier
infrastructure errors. The thinking model's `beetbox_beets_pr3814` attempt hit
its unchanged 1200-second task limit after active editing and received verifier
reward zero. Inspection confirmed an unsolved benchmark attempt; its timeout,
edits and reward are preserved. The gate records that specific reviewed case.

The full Scale SWE pass@8 arrays were then submitted on Capella:

- Instruct: `4407591`.
- Thinking: `4407592`.
- Each model: 16,786 tasks, eight attempts per task, 1,400 shards, initially at
  most eight active shards, one H100 per shard, 12 CPUs and 128 GiB per shard.
- Code snapshot: `d08ecfe`; exact reproduction Parquet preserved.
- Evidence of pilot inspection: `pilot-gate.json` in the ZIH run directory.
- Array records: `full/<model>/array/gated-submission.json` in that directory.

The old 12-hour controller expired during the outage. Full submission was
performed after reconnecting and checking the evidence, not by an active
overnight watcher. Do not restart a controller or submit duplicate arrays.

The user's three-day target prompted live array throttle increases on
2026-10-12: Instruct `4407591` now permits 64 active shards and thinking
`4407592` permits 220. The combined cap matches the observed normal QoS
per-user limit of 284 GPUs. This does not reserve those GPUs or guarantee the
deadline. Original submission plans retain their historical settings.
Current adjustments are recorded in `deadline-acceleration.json` in the run.

Separate throughput pilots were submitted as `4407635` (Instruct) and
`4407630` (thinking), under `throughput-pilot-instruct-c3` and
`throughput-pilot-thinking-c3`. Each tests three simultaneous attempts on one
GPU, with 14 CPUs, two serving CPUs, two tasks and two attempts per task.
All 16,786 task payloads were checked: each requests four CPUs, 4096 MiB and
a 1200-second agent limit. Sampling and task limits remain unchanged.
These pilots were pending when this note was written. Inspect their evidence
before adopting the settings; the full arrays still use concurrency one.
Do not silently edit frozen contracts or submit overlapping replacement shards.

An automatic continuation was started directly on `c1.capella` on 2026-10-12
at 12:18 UTC, PID `2959059`. Its code, log, lock and status live in
`acceleration-controller/` inside the same ZIH run directory. It polls each
pilot every 60 seconds for up to 72 hours, independently of the Mac tunnel.
Read `status.json` and `process.json` before restarting anything. The gate
requires exact attempt coverage, real verifier rewards, nonempty saved tool
trajectories, no infrastructure errors, execution concurrency three, and at
least 1.05 times the previous pilot's per-attempt throughput. Active agent
timeouts with verifier zero remain recorded benchmark outcomes.

Prepared replacement contracts and arrays live under `full-c3/<model>/`.
After a pilot passes, the controller cancels only pending original array
elements using Slurm's state filter, then checks accounting to exclude any
element that started during the transition. It submits only verified
never-started cancelled indices. Running and finished original shards retain
ownership of their task slices. Per-model `*-coverage.json` records both
disjoint execution groups and their immutable contracts. The existing
single-contract merge command cannot merge these groups directly; preserve
their provenance when collecting results. Ambiguous submissions or failed
gates stop that model's transition and are recorded as blocked.

## SWE-Lego migration to CLAIX, 2026-10-12

The user requested cancelling the queued Helma run and moving it to CLAIX.
Helma jobs `987455`, `987456`, `987457` (all 90 elements), and `987458` were
confirmed cancelled before starting. The actual frozen source contains 4,277
tasks, not the 1,800 in an earlier conversation table. Preserve its 34,216
planned attempts with `qwen3-30b-instruct-2507`.

CLAIX workspace: `/rwthfs/rz/cluster/hpcwork/lva77460/ot-next-data-swelego`.
Run directory: `runs/swelego-pass8-20261012` beneath that workspace.
GPU preflight `4948018` completed successfully on `c23g`: H100 95,830 MiB,
driver 580.178.04, CUDA module 13.1.0, PyTorch 2.13.0+cu130 CUDA execution,
and Apptainer 1.5.4. Model weights were downloaded at revision
`0d7cf23991f47feeb3a57ecb4c9cee8ea4a17bfe` into the workspace's `models/`.
Pilot `4948096` was submitted and observed running. The isolated code snapshot
uses the CLAIX launcher with `gpu:hopper:1`; other repair work was not modified.

All 101 required image environments were already published in
[SWE-Lego PR 9](https://huggingface.co/datasets/FWeindel/validated-tasks/discussions/9).
The downloader pins dataset revision `65cc78ebebb1201ab7bd4590cd359d55bc57a912`
and manifest `images/manifests/2026-10-11-swelego-real-data-verified-d15f2a1ef8c8.json`.
The 101 bundles cover all 4,277 tasks. They occupy about 230 GiB sparsely, but
their logical file sizes total about 1.17 TiB. Download one file at a time,
copy with sparse preservation, verify its manifest SHA256 and publish each
complete bundle atomically. Do not materialize all dense overlays at once.
The first pilot images were copied from Helma; the remaining Helma transfer
was stopped after verifying the HF publication. CLAIX fetches the remaining
images directly using `fetch-hf-images.py` (initial PID `274257`), with
progress in `hf-image-status.json` and `hf-images.log`.

The CLAIX controller is `controller.py` (initial PID `217101`) with
`status.json`, `watch.log`, `pilot-job.json` and eventually `full-job.json`.
It waits for the pilot, checks exact coverage, real verifier rewards, saved
tool trajectories, absence of infrastructure errors and cached-image reuse,
then waits for all image downloads before submitting the prepared full array.
The full plan uses 360 shards, cap 32, five simultaneous attempts per GPU,
24 CPUs, 120 GiB memory and a 24-hour limit per shard. It has not been submitted
merely because the pilot started. Read the current records before acting.

Direct SSH from Helma to `rwth` timed out on 2026-10-14, while SSH through the
configured TUM jump host worked. The `rwth` aliases now use `ProxyJump tum` in
both the normal SSH config and the repair-specific config. Direct route cause
is not established; this points to a route/path issue rather than rejected
credentials or a cluster-wide login outage. To test direct routing explicitly,
use `ssh -o ProxyJump=none rwth hostname`. Do not stop the shared SSH master
used by other repair sessions. CLAIX jobs continue independently of Helma SSH.

The first CLAIX generation pilot `4948096` failed before task execution:
FlashInfer's CUDA JIT inherited `CC=icx`, which nvcc rejected as an unsupported
Intel compiler. Its evidence is preserved under `pilot/submission/`.
The controller now explicitly exports `CC=/usr/bin/gcc`, `CXX=/usr/bin/g++`
and `CUDAHOSTCXX=/usr/bin/g++`; loading CUDA/13.1.0 was verified to preserve
these settings. Retry `4948206` uses the same frozen task/model contract and
writes to `pilot/submission-gcc/`. Its submission record is
`pilot-job-gcc.json`; compiler settings are recorded in `compiler-retry.json`.
The restarted controller PID is `477006`, with log `watch-gcc.log`.
Read its status before concluding that the generation pilot passed; the
earlier CUDA tensor preflight did not exercise model-server JIT compilation.

### SWE-Lego approved CLAIX/ZIH split (2026-10-12)

This supersedes the unsplit 360-shard CLAIX plan above. Pilot `4948206`
started the model with GCC successfully, but `PSLmodels__microdf-219` failed
setup because RWTH intentionally blocks `repo.anaconda.com` for licensing.
The user approved retaining the exact task payloads and running the 138
explicitly affected tasks on ZIH, with the remaining 4,139 on CLAIX.
The old unsplit controller PID `477006` was stopped; its full array was never
submitted. Its running pilot and evidence were preserved.

The new run directory on each site is `runs/swelego-split-20261012` under
its existing OT workspace. Each has `split-manifest.json`, recording all
original task payload hashes, disjoint task lists and source Parquet SHA256.
Local audit artifacts are at
`/hnvme/workspace/y500bb12-crosscodeeval-pilot/validation-smoke/swelego-split-20261012`.
All 4,277 tasks remain in scope, Instruct only, eight attempts each.

- CLAIX: 4,139 tasks, 345 shards, array cap 32, five simultaneous attempts,
  24 CPUs and 120 GiB per H100. Fresh five-task pilot `4948657` started.
- ZIH Capella: 138 tasks, 24 shards, array cap 16, one attempt at a time,
  12 CPUs and 128 GiB per GPU. Its eight-task pilot covers all eight cached
  environments required by this subset, including the failed microdf task.
  `fetch-hf-images.py` downloads those eight original bundles directly from
  the pinned HF revision, preserving sparse files and checking SHA256.

Each site's `controller.py` runs detached and records `controller.pid`,
`controller.log` and `status.json`. It requires successful pilot accounting,
stage-6/7 coverage, real verifier rewards, nonempty tool trajectories, no
infrastructure errors and cached image reuse before submitting its full
array. Only active agent timeouts with verifier zero are allowed. Submission
claims prevent duplicate submission after ambiguous errors. The ZIH pilot
waits for its eight bundles; CLAIX full submission waits for the original
101-bundle downloader. Read `pilot-job.json`, `pilot-gate.json` and
`full-job.json` for actual submission and acceptance, not the prepared plans.
The controllers do not send chat notifications. They stop on failures.
Keep each group's contract provenance when combining final results; an
ordinary single-contract merge cannot combine the two site contracts.

At 2026-10-12 13:38 UTC, CLAIX split pilot `4948657` passed its gate:
all five tasks had real verifier rewards and saved tool trajectories, with
no infrastructure findings. NetworkX and Firefly received reward 1; the
other three received 0. Dataspec reached its agent time limit with a saved
active trajectory and real verifier zero, as permitted by the gate.
All 101 HF image bundles finished downloading. The controller submitted
CLAIX full array `4949476`, indices 0-344, cap 32, covering 4,139 tasks.

ZIH downloaded all eight required image bundles and submitted pilot
`4407852` at 13:15 UTC. Last verified scheduler state was PENDING (Priority),
with no start estimate. The Mac tunnel subsequently timed out and by 13:38
UTC localhost port 22231 refused connections, so no later ZIH status was
verified. Its controller PID `3587227` runs on Capella and continues without
the tunnel. Reconnect and read its status and submission records before
claiming ZIH pilot success or full-array submission. Do not submit duplicates.

### Alpha/Capella SWE-Lego pilot race (2026-10-12, 14:01 UTC)

Alpha login recovered. Its A100 GPUs have 40 GiB each, so the same Instruct
model is served with `OT_SERVE_GPUS=2`, `OT_GPUS_PER_NODE=8`, two allocated
GPUs and 12 CPUs. Alpha enforces at most six CPU cores per requested GPU.
Pilot `2366905` is submitted from Alpha using the same eight original tasks.
Its records and separately frozen pilot/full contracts are under
`runs/swelego-split-20261012/alpha` in the shared ZIH workspace. Capella pilot
`4407852` remains submitted. Neither running pilot was cancelled.

Both controllers now use a shared `full-race.lock` and
`full-race-claim.json` in the parent split run. The first pilot that passes
all acceptance checks claims the full 138-task collection and submits the
24-shard array on its own cluster. The other controller exits without a
second full submission. An ambiguous submission remains blocked. A local
concurrency test verified that simultaneous successful gates produce exactly
one full submission. Scripts are archived locally in `/tmp/swelego-alpha-race`
and persist at the remote run paths. Capella's original controller was backed
up as `controller.before-alpha-race.py` before replacement; current PIDs are
recorded in each run's `controller.pid`.

CLAIX array `4949476` had 11 running shards, no failed Slurm jobs and 55
completed attempt log entries at about 14:00 UTC. No infrastructure-error
lines were found in the array logs at that check. This is early evidence,
not final acceptance. Initial throughput including startup was 22.5 attempts
per allocated GPU-hour, implying about 46 hours for the 33,112 CLAIX attempts
if 32 GPUs stay active, or about 134 hours if only 11 stay active. These are
provisional extrapolations; no full shard had finished and queue growth,
task variation and retries can materially change them.

### Live reconciliation and SWE-Lego recovery, 2026-10-15

The session ran on Helma4. The Dresden reverse tunnel on Helma1 still worked:
use `ssh helma1 'ssh capella ...'` (or Alpha) from another Helma login node.
A refusal on Helma4's loopback port does not establish that the tunnel is down.
CLAIX authentication was restored by the user. Direct and TUM routes later
timed out intermittently; `ssh -o ProxyJump=lrz rwth ...` worked. These are
dated observations, not permanent changes to the shared SSH configuration.

Scale SWE has newer records than the original acceleration controller:
`runs/scaleswe-pass8-zih-20261011/runtime-concurrency2-20261014/`.
Live replacement arrays `4416097` (1,293 Instruct shards) and `4416098`
(1,342 Thinking shards) were all PENDING with `JobHeldUser`; none were running.
Their caps are 64 and 220, respectively, with concurrency two, 12 CPUs,
four serving CPUs, and four reserved CPUs per trial. All four pilot resource
records confirmed effective concurrency two. The gate finished blocked:
pilots `4416094` and `4416095` contain terminal errors from the protected
worker recovery path (`'functools.partial' object has no attribute 'start'`).
Do not release these arrays without a repaired, accepted pilot. Their holds
were not changed in this session.

The user authorized resubmitting remaining SWE-Lego attempts. Live CLAIX
jobs were unrelated to SWE-Lego. Reconciliation retained 5,916 CLAIX verifier
outcomes and 1,089 Alpha verifier outcomes. It selected 27,196 missing CLAIX
attempts across 3,733 tasks and 15 missing Alpha attempts across nine tasks.
Whole accepted replacement tasks supersede their originals. Other retained
verifier outcomes and their IDs are listed explicitly; retries are grouped
by the number of missing attempts (one through eight). Do not concatenate
discarded outcomes or diagnostic pilots into the final eight attempts.

Current recovery directories are `runs/swelego-recovery-20261015-v2` under
the CLAIX SWE-Lego workspace and the shared ZIH OT workspace. CLAIX uses
five concurrent trials per H100, 24 CPUs and an aggregate array cap of 32.
The small Alpha remainder is resubmitted on Capella with two trials per H100,
12 CPUs, and three one-shard arrays. Original payloads, model sampling and
task resource limits are preserved. New contracts freeze the compatible
private-IPC and worker recovery fixes; worker imports and hook installation
were checked before submission.

Pilots: CLAIX `5019426`; Capella `4420236`. Capella replacement arrays are
`4420237`, `4420238`, `4420239`. Read each site's `submission-summary.json`
for all array IDs and `gate-status.json` for current acceptance. Full arrays
are submitted held. Detached gates release them only after successful pilot
accounting, exact stage-6/7 coverage, real verifier rewards, saved tool
trajectories, effective concurrency, and no infrastructure errors. Gates
expire after seven days and leave failed or unverified arrays held.

The earlier `swelego-recovery-20261015` directories are superseded. Their
CLAIX pilot `5018935` failed before task execution because a newer worker
file referenced an absent helper module. Compatible existing fixes were
used in v2. Pending v1 jobs were cancelled; evidence and frozen contracts
were preserved. Do not restart v1 controllers. Local scripts and captured
status records are under `/home/hpc/y500bb/y500bb12/swelego-recovery-20261015`.

### Follow-up repair, 2026-10-15 after 08:20 UTC

The user asked to fix the Scale SWE blockage. A focused regression reproduced
the unsafe worker re-import during `_recover`, and passed after injecting the
existing start/stop anchor callbacks. Only `bridge_worker.py` and
`protected_step.py` were patched against the exact frozen Scale SWE snapshot;
unrelated current-checkout modules were not copied. Worker import and hook
installation checks passed. Fresh contracts were created through the normal
contract API, with exact equality of original and new task IDs/content hashes.

New Scale SWE root: `runs/scaleswe-pass8-zih-20261011/runtime-recovery-20261015`.
Instruct pilot array `4420456`, replacement `4420459` (1,293 shards, cap 64).
Thinking pilot array `4420457`, replacement `4420460` (1,342 shards, cap 220).
All keep concurrency two and the original task/model settings. Previous held
arrays `4416097` and `4416098` were cancelled only after verifying their exact
pending indices and submitting matching held replacements. The 107/58 retained
original shards remain owned by the original coverage manifest. Ownership
receipts, `prepared.json`, `submissions.json`, and a detached `gate.py` live in
the new root. The gate was watching queued pilots at 08:25 UTC and releases
each model only after its own pilot passes. Do not release stale arrays or
restart older controllers. Local patch, regression and status artifacts:
`/home/hpc/y500bb/y500bb12/scaleswe-recovery-20261015`.

SWE-Lego CLAIX pilot `5019426` subsequently failed all five attempts during
container preparation on `n23g0019`: user namespace limit exhausted. The
private-IPC flag does not guarantee that a node has free namespace capacity;
the occupied resources on this particular node were not inspected here.
No unrelated IPC or processes were removed. Recovery was moved
to the available CLAIX-2025 `c25g` partition, preserving concurrency five,
the original task payloads and limits. New root:
`runs/swelego-recovery-20261015-c25g`. Pilot `5020738` was RUNNING on `w25g0007`
at 08:25 UTC, with effective concurrency five and vLLM starting. End-to-end
pilot acceptance was not yet established. Its newly frozen full arrays are
held behind the local gate; read `submission-summary.json` for IDs. The v2
CLAIX held arrays were cancelled and recorded as superseded. Capella SWE-Lego
pilot `4420236` and its three held recovery arrays remain current. At 08:23 UTC
the Capella pilot was pending for Priority, with a provisional Slurm start
estimate of 15:24 Europe/Berlin; this is not a guarantee.

### Namespace follow-up, 2026-10-15

CLAIX c25g SWE-Lego pilot `5020738` passed the gate: five recorded and graded attempts, effective concurrency five, no reported environment, verifier or model-server trajectory errors. Gate released arrays `5020740` through `5020747` at 08:46:57 UTC (aggregate cap 32); they were pending at the subsequent check. This is pilot success, not full-run completion.

The failed c23g pilot `5019426` on `n23g0019` failed in workspace seeding, whose command already uses `--containall`; instance starts also use private IPC. Historical fakeroot SysV IPC residue is a plausible cause but has not been confirmed on that node. Read-only diagnostic job `5021528` is queued on that exact node; output will be in `/rwthfs/rz/cluster/hpcwork/lva77460/ot-next-data-swelego/runs/namespace-investigation-20261015/probe-5021528.out`. No host IPC was removed. Local investigation: `namespace-investigation-20261015/findings.json`.

User authorized old-resource cleanup. Exclusive CLAIX cleanup job `5022596` targets `n23g0019` after read-only diagnostic `5021528`. It only removes old account-owned fakeroot-shaped IPC triples with no live recorded queue PIDs and aborts if any account process is outside its allocation. No process killing. Four local guard tests passed. Submission is not evidence that cleanup has run; inspect `runs/namespace-investigation-20261015/cleanup-5022596.out` under the CLAIX SWE-Lego root.

### Scale SWE Helma acceleration, 2026-10-15

User authorized adding Helma work alongside ZIH. Local controller/source:
`/home/hpc/y500bb/y500bb12/scaleswe-helma-20261015`.
Run workspace: `/hnvme/workspace/y500bb12-optiagent/runs/scaleswe-helma-20261015`.
Thinking pilot `1023481` (three tasks, two diagnostic attempts each) and full
array `1023482` (12 shards, 48 tasks, 384 production attempts) are submitted
held. The pilot is released automatically after its three exact cached images
arrive from ZIH. The full array requires accepted stages 6/7, six effective
concurrent trials, complete trajectories and verifier rewards, full image
staging, then confirmed cancellation of the corresponding four pending ZIH
shards (`4420460_1`, `_2`, `_3`, `_6`). No ZIH production ownership has moved
until `ownership-transfer.json` confirms it. Running source shards block the
handoff; no running jobs are cancelled. Pilot results are excluded from pass@8.

Helma uses one H200, 32 CPUs and six concurrent trials per job; full array cap
four. Task manifests match original task SHA256s; task limits, model settings
and eight production attempts are retained. CPU preflight supports seven
trials (28 trial CPUs, four reserved CPUs per trial). Full shards have four
tasks each to fit the Helma 24-hour allocation; source ZIH shards had twelve.
Model weights already exist at `/hnvme/workspace/y500bb12-models/Qwen3-30B-A3B`.
Source code is the exact minimally fixed ZIH snapshot. Data/images are staged
in workspace storage, not home. Guard tests cover pending handoff, running
source rejection, missing source rejection, and manifest/resource checks.

Controller status: workspace `controller-status.json`. Controller PID is
recorded locally in `controller-process.json`; image transfer log is local
`transfer-images.log`. ZIH handoff audit will be
`runtime-recovery-20261015/helma-transfer-20261015.json`. The original ZIH gate
can still release the remaining array elements; cancelled indices cannot run.

### Live status and Helma staging repair, 2026-10-15 around 13:00 CEST

Both Scale SWE ZIH pilot arrays (`4420456`, `4420457`) passed all four elements and the controller released full arrays `4420459` and `4420460`; full arrays were pending priority at 12:59 CEST. SWE-Lego Capella pilot `4420236` was running. CLAIX c25g had nine completed and nine running shards, with 290 recorded attempts and 282 verifier-graded attempts in nine final reports; 13 RuntimeError records and one TaskMemoryLimitError record require review (not necessarily distinct ungraded attempts). Dataset-wide pass@8 remains incomplete.

Helma pilot `1023481` failed before trials: the copied source external symlink referenced a ZIH-only path. Restored isolated local upstream checkouts with exact pinned commits and clean tracked state, repaired only the dependency link, and submitted replacement pilot `1024251`. Its controller restarted; full `1023482` remains gated. Old pilot logs are retained. No source ZIH shards have been transferred. Full image transfer continues.
