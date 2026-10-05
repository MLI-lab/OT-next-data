# Terminal Wrench

Owner: Sankalp Jajee. Status: in progress (patcher, stage-1 run and local
pilot done; cluster validation pending).

[Terminal Wrench](https://github.com/few-sh/terminal-wrench)
([arXiv:2604.17596](https://arxiv.org/abs/2604.17596), Apache-2.0) collects
terminal tasks whose verifiers were rewarded for exploits, with the recorded
exploit and baseline trajectories of three attacker models. This folder turns
it into Harbor training tasks and keeps the exploits as regression tests for
verifier repairs. Pinned upstream: commit `d8a29613235a0ef56a8b70b3142626a533da28c2`
(331 task folders).

Every training task here comes from SETA-Env
([camel-ai/seta-env](https://github.com/camel-ai/seta-env), numeric IDs such
as `1012`). Each task Terminal Wrench ships is byte for byte SETA-Env's own
Harbor version, `Harbor-Dataset/<id>`, plus Terminal Wrench's analysis files.
That release is not the TaskTrove SETA parquet patched in
[`../seta`](../seta/README.md), whose IDs look like `ask_ubuntu__synth__284`;
mapping the two by instruction text is still to do.

## What `patch.py` does

```bash
git clone https://github.com/few-sh/terminal-wrench upstream
git -C upstream checkout d8a29613235a0ef56a8b70b3142626a533da28c2
python data/terminal_wrench/patch.py --upstream upstream --output /new/dir
```

The checkout must be complete and unmodified. The patcher lists tasks from the
pinned tree and refuses a dirty or partial working tree.

The output has three parts:
- `tasks/seta-env-<id>/`: the Harbor tasks.
- `exploits/<task>.json`: Terminal Wrench's analysis, the hack summary,
  SETA-Env's generator draft and the recorded trials, kept outside the task so
  an agent never sees them.
- `report.json`: every kept and dropped task, with reasons and digests.

The upstream checkout is never modified, and existing outputs are never
overwritten.

Kept: **230** tasks. Dropped: **101**.

| Dropped | Tasks | Why |
| --- | ---: | --- |
| `evaluation_benchmark_source` | 90 | From Terminal-Bench 2.0/1.0, Terminal-Bench Pro or OpenThoughts-TB-dev; training on them contaminates evals |
| `startup_command_not_run_by_harbor` | 10 | SETA-Env's compose file started services (`/etc/rc.local`, `sshd`, ...). Its Harbor version dropped that command, and Harbor starts tasks with `sleep infinity` (Docker) or ignores CMD (cluster Apptainer bridge), so the environment never initializes. The original command is in the report. |
| `needs_host_access` | 1 | `1168` mounts the host Docker socket |

Changes inside kept tasks:

- **Moved out of every task:**
  - Terminal Wrench's `analysis.toml` (hack description) and `variants.json`
    (hack-replication prompts);
  - SETA-Env's `environment/draft_spec.md` (generator draft that states the
    intended root cause and fix).

  None is referenced by a Dockerfile, test or solution.
- **Verifier wrapper** (`tests/test.sh`, all 230):
  - Planted `reward.txt` and `reward.json` files are deleted before grading
    and again just before the reward is written. Harbor reads `reward.json`
    first, so a planted one used to override a failing test run.
  - Only pytest exit 1 scores 0. Other exits, such as a failed `uv` download
    or a collection error, are verifier errors with no reward, as in `../seta`.

  The verifier shares the agent's container, so a process the agent left
  running could still rewrite those files after they are cleared. Only a
  separate verifier environment closes that hole.
- **Reviewed per-task repairs** (`TASK_REPAIRS`, each anchor must match once):
  - `1219`: hardened verifier. It installs its own X11 client (`python-xlib`)
    and requires that `/usr/lib/xorg/Xorg` owns the `:99` socket and offers the
    three resolutions as RandR modes. It no longer trusts `xrandr`, the package
    database or process names.
  - `778`: hardened verifier. It swaps `/mock_v4l2/devices` for a fresh,
    randomized device set, reruns `generate_report.py` and checks the new
    report, so a hand-written report or a script that prints a fixed answer
    fails. The devices are restored afterwards. It uses only the three format
    codes the instruction names.
  - `1178`: `redis==5.0.0`, the version the verifier installs (unpinned it
    resolves to 8.1.0 today).
  - `1355`: `httpie==3.2.4`.
- 11 kept tasks whose compose asked for `privileged` or extra capabilities are
  flagged (`original_compose_privileges` in the report); validation will show
  which still work without them.

## Results so far (2026-10-03)

**Stage 1** (official, `validation/run.py`, training profile), all 230 tasks:
199 pass, 31 fail. The findings:
- 11 relative paths in instructions;
- 9 output files the instruction never names;
- 6 unpinned pip installs;
- 3 bare `nproc` calls;
- 3 pinned apt packages.

The full list is in
[`diagnostics/2026-10-03-stage1.csv`](diagnostics/2026-10-03-stage1.csv).
Some look like checker false positives (`1134`: `psutil` inside a print string;
`429`: `tar.gz`); triage is pending.

The 10-task pilot was drawn with `random.Random(42)` from the tasks with a
rewarded serious exploit, before the startup-command drop. It passed stage 1
on 10/10 after the two pins. 9 of those tasks remain, since `161` was later
dropped: its oracle scored 0 because `/etc/rc.local` never runs.

**Stages 3–5** need a cluster run. Locally, Harbor's Docker backend built 8 of
the 10 pilot tasks; the other 2 failed only because this sandbox re-signs TLS.
Oracle and NOP could not be judged with Harbor here: every verifier downloads
`uv` from `astral.sh`, which the sandbox blocks. The repaired wrapper then
reported a verifier error instead of reward 0, as intended.

**Exploit replay** (`audit.py`, below). Pilot tasks, local Docker, with the
sandbox adaptations listed under "Running the audit":

| Task | Oracle | NOP | Replayed exploits that scored 1 |
| --- | --- | --- | --- |
| `seta-env-647` | 1 | 0 | 3 of 3 |
| `seta-env-778` | 1 | 0 | 3 of 3 |
| `seta-env-781` | 1 | 0 | 3 of 3 |
| `seta-env-1173` | 1 | 0 | 3 of 3 |
| `seta-env-1178` | 1 | 0 | 3 of 3 |
| `seta-env-1329` | 1 | 0 | 3 of 3 |
| `seta-env-1219` | 1 | 0 | 4 of 5 before hardening (below) |
| `seta-env-104` | 0: `solve.sh` downloads Sublime Text from a host this sandbox blocks | 0 | 3 of 3 |
| `seta-env-1355` | not built here (deadsnakes PPA over HTTPS) | | |

Results:
- **Built here:** 8 of the 9 pilot tasks still in the dataset.
- **Oracle:** scored 1 on 7 of them; the exception, `104`, depends on a
  blocked download.
- **NOP:** scored 0 on all 8.
- **Exploits:** every replayed exploit that ran to the end still earned full
  reward. These verifiers need hardening before training.

The stage-4/5 gates still need a cluster run.

**Hardening `1219`:**

| Case | Before | After |
| --- | --- | --- |
| Oracle | 1 | 1 |
| NOP | 0 | 0 |
| gpt-5.4 exploits v5, v5_2, v5_4, v5_5 | reproduced (1, 1, 1, 1) | blocked (0, 0, 0, 0) |
| gpt-5.4 exploit v5_3 | inconclusive | inconclusive |

`v5_3`'s replay ends on a failing command in both runs, so its reward of 0
says nothing about the verifier.

**Hardening `778`** (all 13 recorded serious exploits):

| Case | Before | After |
| --- | --- | --- |
| Oracle | 1 | 1 |
| NOP | 0 | 0 |
| Recorded exploits | 11 reproduced, 2 inconclusive (gpt-5.4 v5 and v5_3 replays time out) | 11 blocked, 2 inconclusive |

A red-team pass checked that the new test does not fail honest solutions:
- Seven independently written honest solutions pass every run, after two
  fixes it prompted. The randomized metadata devices now carry the metadata
  capability mask, and the agent's script runs with the task's `python3`
  rather than the verifier's `uv` venv.
- Both recorded cheat shapes fail every run: a fixed-JSON generator, and a
  hand-written report behind a script that only prints.
- Attempts to build new cheats against the test were not run. In the shared
  verifier mode the agent controls the environment the verifier runs in, so
  this hardening does not prevent tampering with the verifier's own tools.

## Running the audit

```bash
python data/terminal_wrench/audit.py --patched /new/dir --upstream upstream \
  --out /new/audit --tasks seta-env-1219 --max-exploits 5
```

Per task it builds `environment/` with Docker, then in fresh containers runs:
- a NOP (expected 0);
- the oracle (expected 1);
- each recorded rewarded serious exploit.

It then runs `tests/test.sh` and reads the reward with Harbor's precedence
(`reward.json`, then `reward.txt`).

An exploit is replayed by streaming its keystrokes into an interactive login
bash with umask 022, like the Terminus tmux pane:
- Ctrl-C discards the line being typed, and other tmux keys are skipped.
- A program that reads stdin consumes the lines typed after it.

Each exploit is then classified:
- `reproduced`: reward 1;
- `blocked`: reward 0 after a replay that parsed and ran to the end;
- `inconclusive`: anything else, which is no evidence either way.

Two flags adapt the run to restricted hosts. They are not part of any task:
- `--ca-bundle` trusts a TLS-re-signing proxy's CA in builds and containers.
- `--pip-verifier` installs the wrapper's pinned pytest and every `uv add`
  package from PyPI instead of downloading `uv` from `astral.sh`.

This sandbox also needed a Docker Hub mirror
(`/etc/docker/daemon.json`: `{"registry-mirrors": ["https://mirror.gcr.io"]}`)
after anonymous pulls hit HTTP 429.

## Next steps

1. **Cluster validation.** Stages 1, 3, 4 and 5 on the 10-task pilot, then on
   all 230. No GPU is needed. Compute access is pending with the PIs.
2. **Harden the rest.** 223 of the 230 kept tasks have at least one rewarded
   serious exploit (1,977 recorded in total). Workflow per task:
   - replay the exploits;
   - add a test that rejects the exploit but not the oracle;
   - replay again until the exploits are blocked, the oracle scores 1 and the
     NOP scores 0;
   - add the change to `TASK_REPAIRS`.

   Stage 10 (hacker/fixer) could draft these at scale.
3. **Stage-1 triage.** Fix the real findings in `TASK_REPAIRS` and report
   checker false positives under `validation/`.
4. **Startup commands.** The 10 dropped tasks could return if a startup hook
   works on both backends. Harbor's `[environment.healthcheck]` runs before
   the agent, but the processes it starts may not survive on the Apptainer
   bridge. Decide with the team.
5. **Share with SETA.** Map these tasks to the TaskTrove SETA IDs and send the
   list of known-exploitable verifiers to the SETA owner. Also flag the
   `reward.json` gap, since `../seta/patch.py` clears only `reward.txt`.
   SETA-Env's own Harbor version, on GitHub, has the same lost startup
   commands.
6. **Verifier network access.** The wrapper downloads `uv` and pytest at
   grading time. `../seta` bakes pytest into the image instead; decide whether
   to do the same here.
