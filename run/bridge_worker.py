"""Pinned Harbor worker with a persistent tmux owner for Helma Apptainer execs.

A short-lived Apptainer exec kills its child tmux server on Helma. Keep the exec
that creates the server alive for the instance lifetime. Task containers retain
separate /tmp mounts, so the default tmux socket remains isolated per task.
This changes no installed Harbor files; the normal bridge protocol is retained.

Per-trial resource limits: inside a Slurm job the persistent exec (which owns
the tmux server and therefore every process the agent starts) runs as its own
Slurm step, `srun --exact -n1 -c<cpus> --mem=<mem>`, so each trial lives in its
own cgroup with its own cores and memory cap (Helma: task/cgroup, OOM kills
the step). Values: the task's own cpus/memory_mb from task.toml when present
(TaskTrove CrossCodeEval tasks set none), else PILOT_TRIAL_CPUS (1) and
PILOT_TRIAL_MEM (4G). Steps queue when the allocation's cores are taken, so
cores / PILOT_TRIAL_CPUS caps the trials in flight; PILOT_TRIAL_STEP_WAIT (s)
bounds that wait. PILOT_TRIAL_SRUN=0 disables the wrapper.

Network isolation: instances share the host loopback, so a server inside one
container (OpenHands' action server) is reachable from another. Where the node
allows unprivileged network namespaces, every `apptainer instance start` gets
`--net --network none` (own loopback per instance); this is probed once at
worker start with a real SIF and skipped, with a log line, where the kernel
forbids it (Helma: user.max_net_namespaces = 0). PILOT_NET_ISOLATION=0 disables.

Job-scoped startup cleanup: upstream `_cleanup_stale_instances` stops every
`hb_env_*` instance of the user on the host. Apptainer's instance registry is
per user and host (~/.apptainer/instances), so a job starting on a node would
kill the task containers of any other job already running there. Here only
the stale staging directories under this worker's own --staging-base are
removed (a fresh per-job $TMPDIR, so normally none); instances of this job
die with the job's cgroup.
"""
import glob
import re
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from harbor.environments.apptainer import worker

_original_start = worker.ApptainerInstance.start
_original_stop = worker.ApptainerInstance.stop


def stop_anchor(instance):
    proc = getattr(instance, '_helma_tmux_anchor', None)
    if proc is not None and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
    log = getattr(instance, '_helma_tmux_log', None)
    if log is not None:
        log.close()


def trial_step_prefix(payload, env_id):
    """srun wrapper giving the trial its own Slurm step cgroup, or [] outside Slurm."""
    if os.environ.get('PILOT_TRIAL_SRUN', '1') == '0' or not os.environ.get('SLURM_JOB_ID'):
        return []
    cfg = payload.get('task_env_config') or {}
    cpus = int(cfg.get('cpus') or os.environ.get('PILOT_TRIAL_CPUS', '1'))
    mem = f"{int(cfg['memory_mb'])}M" if cfg.get('memory_mb') else os.environ.get('PILOT_TRIAL_MEM', '4G')
    # --gres=none: a step would otherwise take the job's GPU, and GPUs cannot be
    # shared between steps, which serialized all trials (smoke 868318).
    return ['srun', '--quiet', '--exact', '-n1', f'-c{cpus}', f'--mem={mem}', '--gres=none',
            '--job-name', f'trial-{env_id}']


def start_with_anchor(self, payload):
    result = _original_start(self, payload)
    cmd = [worker.APPTAINER, 'exec', '--pwd', '/tmp',
           f'instance://{self.instance_name}', '/bin/bash', '-c']
    step = trial_step_prefix(payload, self.env_id)
    self._helma_tmux_log = open(Path(self.staging_dir) / 'tmux-anchor.log', 'w')
    self._helma_tmux_anchor = subprocess.Popen(
        step + cmd + ['tmux new-session -d -s _pilot_anchor && exec sleep infinity'],
        stdin=subprocess.DEVNULL, stdout=self._helma_tmux_log,
        stderr=subprocess.STDOUT, start_new_session=True)
    # With a step wrapper the anchor may queue for a free core; wait longer.
    deadline = time.monotonic() + (float(os.environ.get('PILOT_TRIAL_STEP_WAIT', '3600')) if step else 15.0)
    try:
        while time.monotonic() < deadline:
            if self._helma_tmux_anchor.poll() is not None:
                raise RuntimeError('Persistent tmux owner exited; inspect tmux-anchor.log')
            check = subprocess.run(cmd + ['tmux has-session -t _pilot_anchor'],
                                   capture_output=True, timeout=10)
            if check.returncode == 0:
                print(f'[{self.env_id}] Helma persistent tmux owner ready'
                      + (f' (Slurm step: {" ".join(step[2:6])})' if step else ''), flush=True)
                return result
            time.sleep(0.5)
        raise RuntimeError('Persistent tmux owner readiness timed out')
    except BaseException:
        stop_anchor(self)
        _original_stop(self, {})
        raise


def stop_with_anchor(self, payload):
    try:
        return _original_stop(self, payload)
    finally:
        stop_anchor(self)


NET_FLAGS = ['--net', '--network', 'none']


def network_isolation_available(sif_cache):
    """Can this node start a container in its own network namespace?"""
    sifs = sorted(glob.glob(os.path.join(sif_cache or '', '*.sif')))
    if os.environ.get('PILOT_NET_ISOLATION', '1') == '0' or not sifs:
        return False
    apptainer = worker.APPTAINER or worker.detect_apptainer()  # set by worker.main() only
    probe = subprocess.run([apptainer, 'exec'] + NET_FLAGS + [sifs[0], 'true'],
                           capture_output=True, text=True, timeout=120)
    if probe.returncode != 0:
        lines = [re.sub(r'\x1b\[[0-9;]*m', '', l).strip() for l in (probe.stderr or '').splitlines()]
        reason = next((l for l in lines if 'ERROR' in l), next((l for l in reversed(lines) if l), 'no output'))
        print(f'[worker] network isolation unavailable, instances share the host loopback: {reason}', flush=True)
        return False
    print('[worker] network isolation on: instances start with --net --network none', flush=True)
    return True


def run_with_net_flags(original_run):
    def run(cmd, *args, **kwargs):
        if isinstance(cmd, list) and len(cmd) > 2 and cmd[1:3] == ['instance', 'start']:
            cmd = cmd[:3] + NET_FLAGS + cmd[3:]
        return original_run(cmd, *args, **kwargs)
    return run


def cleanup_own_staging_only(hostname, staging_base):
    if os.path.isdir(staging_base):
        for entry in os.listdir(staging_base):
            if entry.startswith('apt_env-'):
                shutil.rmtree(os.path.join(staging_base, entry), ignore_errors=True)
                print(f'[{hostname}] Cleaned stale staging: {entry}', flush=True)


if __name__ == '__main__':
    # Slurm's host TMPDIR is not mounted inside isolated task containers.
    # APPTAINERENV changes only the container environment, not worker staging.
    for name in ('TMPDIR', 'TMP', 'TEMP'):
        os.environ[f'APPTAINERENV_{name}'] = '/tmp'
    worker._cleanup_stale_instances = cleanup_own_staging_only
    worker.ApptainerInstance.start = start_with_anchor
    worker.ApptainerInstance.stop = stop_with_anchor
    sif_cache = next((a.split('=', 1)[1] if '=' in a else sys.argv[i + 1]
                      for i, a in enumerate(sys.argv) if a.startswith('--sif-cache')), '')
    if network_isolation_available(sif_cache):
        worker.subprocess.run = run_with_net_flags(worker.subprocess.run)
    worker.main()
