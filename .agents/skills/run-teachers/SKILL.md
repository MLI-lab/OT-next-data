---
name: run-teachers
description: Generate teacher trajectories on Slurm - stages, throughput knobs, and the failures that cost days.
---

# Running teacher models on a dataset

```bash
source env.sh
sbatch hpc/helma/teacher_traces.sbatch <weak|strong> <smoke|diag|sweep|full>
```

Stages are task counts per language: smoke 1, diag 5, sweep 25, full 250.
`weak` = one GPU, `strong` = four. Always pass a smoke before a full run: it catches the staging errors a full run
would otherwise hit hours in, at a fraction of the cost.

## Knobs

| Variable | Meaning |
| --- | --- |
| `PILOT_CONCURRENCY` | Trials in flight = bridge worker slots. Also capped by cores: one Slurm step per trial, `PILOT_TRIAL_CPUS` each, 32 cores per GPU here. |
| `PILOT_ATTEMPTS` | Attempts per task; split pass@16 into four jobs of 4 and merge with `verify/pass_at_k.py`. |
| `PILOT_TRIAL_CPUS`, `PILOT_TRIAL_MEM` | Per-trial step limits (defaults 1 core, 4 GiB). |
| `PILOT_ORACLE_CHECK=0` | Skip the oracle stage once a task tarball is verified (saves ~30 min per 1K run). |
| `PILOT_AGENT` | Agent scaffold, default terminus-2. |

Sampling follows each model's card and lives in `run/prepare_run.py`, not
upstream: thinking models T 0.6 / top_p 0.95 / top_k 20; coder models
T 0.7 / top_p 0.8 / top_k 20 / repetition_penalty 1.05.

## Failures that cost days — do not re-learn these

- **Prefix caching is off upstream.** `hpc/vllm_utils.py` injects
  `--no-enable-prefix-caching` unless `--enable-prefix-caching` is in
  `extra_args`. With it on: 94-98% cache hits, ~3x throughput, and the agent
  timeout rate fell from 10% to 0.7%.
- **Two jobs on one node collide** unless every port is job-derived (bridge
  40000+, Ray 20000+, vLLM API 30000+, each `+ SLURM_JOB_ID % 10000`) and the
  runtime container gets its own PID namespace (`apptainer exec --pid`);
  otherwise `ray stop` in one job kills the other's raylet.
- **The upstream local runner writes `merged_harbor_config_<model>.yaml` into
  the repo root**, so two same-model jobs overwrite each other's config. The
  launcher therefore runs from a job-private copy of the OT-Agent checkout in
  `$TMPDIR/code`.
- **Harbor's `_cleanup_stale_instances` stops every `hb_env_*` instance of the
  user on the host**, including other jobs'. `harbor_patches/bridge_worker.py` replaces it
  with own-staging-only cleanup and pins `BRIDGE_INSTANCE_REUSE=0`.
- **`srun` per trial without `--gres=none` serializes the trials** on the GPU;
  `--overlap` pins every step to the same cores. Use
  `srun --exact -n1 -c<cpus> --mem=<mem> --gres=none`.
- **User network namespaces may be forbidden** (`user.max_net_namespaces=0`).
  The worker probes it and adds `--net --network none` only where allowed, so
  only tmux-based agents (terminus-2) are safe for concurrent trials; a
  server-based scaffold would cross-talk over the shared loopback.
- **File-count quotas bite before byte quotas.** Trials live on node-local
  `$TMPDIR` and are archived into batched tarballs; the launcher refuses to
  start when the shared quota is nearly exhausted.

## After a run

`runs/<run-id>/` holds configs, logs, `progress.json`,
`validated_attempt_summary.json` and `archives/*.tar.gz` — trials are archived,
so there are no loose `result.json` files. `verify/pass_at_k.py` reads the
summary; `teacher_traces/check_run.py` is the completion gate (it needs `OTAGENT_ROOT`).
Never kill a RUNNING job without explicit permission from the operator.
