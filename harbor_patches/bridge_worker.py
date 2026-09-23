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

Directory COPY: Docker copies a directory's contents, apptainer's %files copies the directory
itself, and upstream translates one into the other unchanged, so `COPY data/ /root/data/` lands
at /root/data/data and the task cannot find its inputs. Directory sources are expanded into
their children here.

Baked /tests: upstream binds a staging dir at /tests on every instance, which hides the /tests a
Harbor separate-verifier image builds in from its own tests/ context. The bind is dropped for
images that ship /tests/test.sh themselves.

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
import socket
import subprocess
import sys
import time
from pathlib import Path
from harbor.environments.apptainer import worker

_original_start = worker.ApptainerInstance.start
_original_stop = worker.ApptainerInstance.stop
_original_parse_copies = worker._parse_copies


def parse_copies_docker_semantics(dockerfile_path):
    """Docker COPY of a directory copies its CONTENTS; apptainer %files copies the directory.

    Upstream's Dockerfile-to-def translation emits one %files line per COPY, so
    `COPY data/ /root/data/` lands the files at /root/data/data/ and the task starts without its
    inputs. Expanding a directory source into its children is what Docker does, and %files then
    reproduces it. The destination keeps a trailing slash so apptainer treats it as a directory
    rather than renaming the first child onto it.

    Files are left alone, so Dockerfiles that copy only files are unaffected.
    """
    context = os.path.dirname(os.path.abspath(dockerfile_path))
    expanded = []
    for sources, dest in _original_parse_copies(dockerfile_path):
        for src in sources:
            abs_src = os.path.normpath(os.path.join(context, src))
            if os.path.isdir(abs_src):
                target = dest.rstrip('/') + '/'
                for child in sorted(os.listdir(abs_src)):
                    expanded.append(([os.path.join(src, child)], target))
            else:
                expanded.append(([src], dest))
    return expanded


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
    # --nodes=1 -w <this host>: on a multi-node allocation the step must run on
    # the node whose worker started it - its container staging and $TMPDIR are
    # local - and an unpinned step dies immediately (873451).
    # SLURMD_NODENAME is the name Slurm itself uses; a hostname with a domain
    # suffix would be rejected and the step would die at once.
    # On the batch node this is the shape that has always worked; do not add
    # --nodes/-w here, they make steps wait for resources and a trial hangs
    # until the time limit (873508). A worker started on another node runs
    # inside a step of its own, so its trial steps are nested: they then need
    # --overlap and must be pinned to that node.
    nested = []
    if os.environ.get('SLURM_STEP_ID'):
        node = os.environ.get('SLURMD_NODENAME') or socket.gethostname().split('.')[0]
        # --cpu-bind=none: the parent step already holds a core mask, and an
        # inherited binding makes every nested step fight over the same core.
        nested = ['--overlap', '--nodes=1', '-w', node, '--cpu-bind=none']
    return ['srun', '--quiet', '--exact', '-n1', *nested,
            f'-c{cpus}', f'--mem={mem}', '--gres=none', '--job-name', f'trial-{env_id}']


def anchor_log_tail(env, lines=8):
    """The last lines the anchor wrote, so the failure says why instead of where."""
    path = Path(env.staging_dir) / 'tmux-anchor.log'
    try:
        env._helma_tmux_log.flush()
    except Exception:  # noqa: BLE001
        pass
    try:
        text = path.read_text(errors='replace').strip().splitlines()[-lines:]
    except OSError as e:
        return f'(no tmux-anchor.log: {e})'
    return ' | '.join(text) or '(tmux-anchor.log is empty)'


def start_with_anchor(self, payload):
    result = _original_start(self, payload)
    cmd = [worker.APPTAINER, 'exec', '--pwd', '/tmp',
           f'instance://{self.instance_name}', '/bin/bash', '-c']
    step = trial_step_prefix(payload, self.env_id)
    # tmux does not create its own socket directory in this container: the instance gets a
    # fresh per-instance /tmp bind instead of the host's 1777 /tmp, and every tmux call then
    # dies with "error creating /tmp/tmux-<uid>/default (No such file or directory)". Creating
    # the directory first is enough, verified by hand on h24-01 with and without --fakeroot
    # (job 878099). Under --fakeroot `id -u` reports 0 while tmux still names the socket after
    # the real host uid, so both names are created.
    uid = os.getuid()
    subprocess.run(cmd + [f'mkdir -p -m 700 /tmp/tmux-{uid} /tmp/tmux-0'],
                   capture_output=True, timeout=60)
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
                raise RuntimeError('Persistent tmux owner exited: ' + anchor_log_tail(self))
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


_baked_tests = {}


def image_bakes_tests(sif):
    """Does this SIF already contain /tests/test.sh? Probed once per image, then cached."""
    if sif not in _baked_tests:
        apptainer = worker.APPTAINER or worker.detect_apptainer()
        probe = subprocess.run([apptainer, 'exec', sif, 'test', '-f', '/tests/test.sh'],
                               capture_output=True, timeout=120)
        _baked_tests[sif] = probe.returncode == 0
        if _baked_tests[sif]:
            print(f'[worker] {os.path.basename(sif)} bakes /tests; not masking it with a bind',
                  flush=True)
    return _baked_tests[sif]


def run_keeping_baked_tests(original_run):
    """Drop the `--bind <staging>:/tests:rw` for images that ship their own /tests.

    Upstream binds a staging directory at /tests on every instance, because it assumes the
    verifier's tests are uploaded at run time. Harbor's separate-verifier mode does the opposite:
    the verifier image is built with tests/ as its build context and ends `COPY . /tests/`, so the
    bind hides the verifier and `bash /tests/test.sh` exits 127. Tasks that upload their tests are
    unaffected, since their image has no /tests/test.sh and the bind stays.
    """
    def run(cmd, *args, **kwargs):
        if isinstance(cmd, list) and len(cmd) > 2 and cmd[1:3] == ['instance', 'start']:
            sif = next((c for c in cmd if isinstance(c, str) and c.endswith('.sif')), '')
            if sif and image_bakes_tests(sif):
                kept, skip = [], False
                for i, arg in enumerate(cmd):
                    if skip:
                        skip = False
                        continue
                    if (arg == '--bind' and i + 1 < len(cmd)
                            and str(cmd[i + 1]).endswith(':/tests:rw')):
                        skip = True
                        continue
                    kept.append(arg)
                cmd = kept
        return original_run(cmd, *args, **kwargs)
    return run


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
    worker._parse_copies = parse_copies_docker_semantics
    worker.subprocess.run = run_keeping_baked_tests(worker.subprocess.run)
    sif_cache = next((a.split('=', 1)[1] if '=' in a else sys.argv[i + 1]
                      for i, a in enumerate(sys.argv) if a.startswith('--sif-cache')), '')
    if network_isolation_available(sif_cache):
        worker.subprocess.run = run_with_net_flags(worker.subprocess.run)
    worker.main()
