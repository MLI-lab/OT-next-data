# Cluster access from Helma

Configured on `helma1.nhr.fau.de` for `y500bb12`, 2026-10-11 and 2026-10-12.
The connection settings live in `/home/hpc/y500bb/y500bb12/.ssh/config`.
Use these aliases rather than reconstructing SSH commands with raw hostnames.
Connectivity is conditional on the agent and tunnels below; check it at the
start of a new session. SSH access does not establish launcher compatibility.

| Alias | Destination and user | Route | Status established in this session |
| --- | --- | --- | --- |
| `rwth` | `login23-1.hpc.itc.rwth-aachen.de`, `lva77460` | Direct from Helma | Key login and Slurm access verified for CLAIX |
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

Direct SSH to `rwth` intermittently timed out during this work. The existing
route through TUM worked:
`ssh -F /home/hpc/y500bb/y500bb12/cluster-repair-20261012/ssh_config rwth`.
That configuration uses `ProxyJump tum` and connection multiplexing. No cause
for the direct-route failures was established. Do not stop the shared SSH
master used by other repair sessions. CLAIX jobs and its download/controller
processes continue independently of the Helma connection.
