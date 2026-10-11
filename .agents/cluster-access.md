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
infrastructure errors or task timeouts. An agent reward of zero is allowed.

The run-specific controller records `pilot-gate.json` on ZIH and an exclusive
`gated-submission.json` per full array before calling Slurm. A submission marked
`submission_started` without a confirmed job ID requires reconciliation against
Slurm, not blind resubmission. Failed pilots stop the gate for investigation.
At controller startup, Instruct pilot `4406612` was running, the thinking model
was still downloading, and the Mac tunnel was timing out. These are historical
observations; read current records rather than assuming completion.
