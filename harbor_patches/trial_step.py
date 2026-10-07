"""Enforce task CPU/memory limits with reusable Slurm lifecycle workers.

Pinned Harbor does not assign each environment its own Slurm step. Reuse idle
steps with matching limits to avoid scheduler throttling; containers stay fresh.
A private socket carries lifecycle commands, and cleanup owns only this step's
processes and fakeroot IPC. OT_STEP_REUSE=0 disables reuse for diagnostics.
"""
import json
import os
from pathlib import Path
import signal
import shutil
import socket
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
import traceback

try:
    from harbor_patches.bridge_timeouts import exec_rpc_timeout
    from harbor_patches.fakeroot_ipc import OwnedFakerootIPC
except ModuleNotFoundError:  # Executed directly by srun.
    from bridge_timeouts import exec_rpc_timeout
    from fakeroot_ipc import OwnedFakerootIPC

LIFECYCLE_TIMEOUT = 7200  # Builds and archive transfers can outlast individual execs.


class InstanceRegistry(dict):
    """The worker's table of running containers, which also remembers early stop requests.

    A trial that gives up while its container is still starting (the start waited longer
    than the trial's limit) sends its stop request before the worker knows the container.
    The pinned worker answers "no instance" and forgets it; the start then finishes and
    nobody ever stops that container. In a Slurm job it kept its step and one core until
    the job ended, so every such trial made the next ones wait longer for a core (59
    leaked steps in the eight pass@16 jobs after four hours, and a growing number of
    start timeouts). Here a stop for an unknown container is remembered: a start that is
    still waiting gives up, and one that finishes anyway is stopped at once.
    """
    KEEP = 20000

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.abandoned = {}

    def pop(self, env_id, *default):
        if env_id not in self:
            self.abandoned[env_id] = None
            while len(self.abandoned) > self.KEEP:
                self.abandoned.pop(next(iter(self.abandoned)))
        return super().pop(env_id, *default)

    def __setitem__(self, env_id, instance):
        if env_id in self.abandoned:
            self.abandoned.pop(env_id, None)
            print(f'[{env_id}] started after its trial had given up; stopping it', flush=True)
            threading.Thread(target=self._stop, args=(env_id, instance), daemon=True).start()
            return
        super().__setitem__(env_id, instance)

    @staticmethod
    def _stop(env_id, instance):
        try:
            instance.stop({})
        except Exception as exc:
            print(f'[{env_id}] stopping the abandoned container failed: {exc}', flush=True)


REGISTRY = InstanceRegistry()


def reuse_steps():
    return os.environ.get('OT_STEP_REUSE', '1') != '0'


class StepSlot:
    """Parent-side handle of one srun step and the server running in it."""

    def __init__(self, key, command, config):
        self.key, self.uses = key, 0
        # Keep RPC paths below Unix socket's 108-byte limit. TMPDIR is node-local
        # and private per Slurm job; close() removes the control directory.
        self.control = Path(tempfile.mkdtemp(prefix='trial-', dir=os.environ.get('TMPDIR', '/tmp')))
        self.log = None
        self.process = None
        if len(os.fsencode(self.control / 'rpc.sock')) >= 108:
            shutil.rmtree(self.control)
            raise ValueError(f'Trial socket path too long: {self.control}')
        config_path = self.control / 'config.json'
        config_path.write_text(json.dumps(config))
        self.log = (self.control / 'step.log').open('w')
        self.process = subprocess.Popen(
            list(command) + [sys.executable, '-u', str(Path(__file__).resolve()), str(config_path)],
            stdin=subprocess.DEVNULL, stdout=self.log, stderr=subprocess.STDOUT,
            start_new_session=True)

    def ready(self):
        return (self.control / 'rpc.sock').exists()

    def alive(self):
        return self.process is not None and self.process.poll() is None and self.ready()

    def request(self, env_id, operation, payload, timeout):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
            conn.settimeout(timeout)
            conn.connect(str(self.control / 'rpc.sock'))
            with conn.makefile('rwb') as stream:
                stream.write(json.dumps({'operation': operation, 'env_id': env_id, 'payload': payload}).encode() + b'\n')
                stream.flush()
                line = stream.readline()
        if not line:
            raise RuntimeError(f'{env_id}: resource step exited during {operation}; {self.log_tail()}')
        response = json.loads(line)
        if 'error' in response:
            raise RuntimeError(response['error'])
        return response['result']

    def log_tail(self):
        """Keep bounded diagnostics in the shared log, without per-task files."""
        if not (self.control / 'step.log').exists():
            return ''
        with (self.control / 'step.log').open('rb') as log:
            log.seek(0, os.SEEK_END)
            log.seek(max(0, log.tell() - 8192))
            return log.read().decode(errors='replace').strip()

    def close(self, tag='step'):
        """End the step and remove its control files; harmless when repeated."""
        if self.process is not None and self.process.poll() is None:
            try:
                if self.ready():
                    self.request(tag, 'close', {}, 10)
                self.process.wait(timeout=10)
            except Exception:
                pass
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=45)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=10)
        if self.log is not None:
            self.log.close()
        if self.control.exists():
            tail = self.log_tail()
            if tail:
                print(f'[{tag}] lifecycle log tail:\n{tail}', flush=True)
            shutil.rmtree(self.control)


class StepPool:
    """The steps whose container has stopped, waiting for the next task.

    A new task takes an idle step with the same limits. If there is none, it gets a new
    step, and one idle step with other limits is closed first to give its cores back:
    that is the step the previous task left behind, so the number of steps stays at the
    number of trials in flight. A step in use is never touched.
    """

    def __init__(self):
        self.lock = threading.Lock()
        self.idle = []
        self.created = self.reused = self.closed = 0

    def acquire(self, key, command, config):
        """(step, reused). Dead idle steps are dropped on the way."""
        retire = []
        with self.lock:
            dead = [slot for slot in self.idle if not slot.alive()]
            self.idle = [slot for slot in self.idle if slot not in dead]
            retire += dead
            match = next((slot for slot in self.idle if slot.key == key), None) if reuse_steps() else None
            if match is not None:
                self.idle.remove(match)
                self.reused += 1
            elif self.idle:
                retire.append(self.idle.pop(0))
        for slot in retire:
            self.discard(slot, 'step-pool')
        if match is not None:
            match.uses += 1
            return match, True
        slot = StepSlot(key, command, config)
        slot.uses = 1
        with self.lock:
            self.created += 1
        return slot, False

    def release(self, slot):
        with self.lock:
            self.idle.append(slot)

    def discard(self, slot, tag):
        with self.lock:
            if slot in self.idle:
                self.idle.remove(slot)
            self.closed += 1
        slot.close(tag)

    def close_all(self):
        with self.lock:
            idle, self.idle = self.idle, []
        for slot in idle:
            slot.close('step-pool')


POOL = StepPool()


class SlurmInstance:
    """Parent-side proxy; the real container instance lives in serve()."""

    def __init__(self, env_id, sif_cache_dir, staging_base, max_exec_output_chars,
                 *, step_prefix, start_gate):
        self.step_prefix = step_prefix
        self.start_gate = start_gate
        self.env_id = env_id
        self.sif_cache = sif_cache_dir
        self.staging_base = staging_base
        self.max_exec_output_chars = max_exec_output_chars
        self.instance_name = f"hb_{env_id.replace('-', '_')}"[:50]
        self.started = False
        self.slot = None
        # The step of this environment; kept after stop so callers can inspect the outcome.
        self.process = None
        self.control = None
        # The worker's own instance pool is not used: steps are pooled here, containers never.
        self._pool_key = None

    def _request(self, operation, payload, timeout):
        if self.slot is None:
            raise RuntimeError(f'{self.env_id}: no resource step')
        return self.slot.request(self.env_id, operation, payload, timeout)

    def _log_tail(self):
        return self.slot.log_tail() if self.slot is not None else ''

    def start(self, payload):
        # The step's name does not carry the environment: the same step serves many.
        command = self.step_prefix(payload, 'slot')
        if not command:
            raise RuntimeError('Slurm lifecycle proxy requires a resource step')
        config = {'sif_cache': self.sif_cache, 'staging_base': self.staging_base,
                  'max_exec_output_chars': self.max_exec_output_chars, 'reuse': reuse_steps()}
        began = time.monotonic()
        self.slot, reused = POOL.acquire(tuple(command), command, config)
        self.process, self.control = self.slot.process, self.slot.control
        try:
            deadline = time.monotonic() + float(os.environ.get('OT_TRIAL_STEP_WAIT', '3600'))
            while not self.slot.ready():
                if self.process.poll() is not None:
                    raise RuntimeError(f'{self.env_id}: resource step exited: {self._log_tail()}')
                if time.monotonic() >= deadline:
                    raise TimeoutError(f'{self.env_id}: timed out waiting for resource step')
                if self.env_id in REGISTRY.abandoned:
                    # Do not take a core for a trial that is gone; the stop request stays
                    # remembered until the worker tries to register this container.
                    raise RuntimeError(f'{self.env_id}: stopped while waiting for a resource step')
                time.sleep(.1)
            # Honor the existing node-wide startup gate, although the actual
            # startup now occurs in a child process instead of a worker thread.
            step_ready = time.monotonic()
            with self.start_gate:
                gate_open = time.monotonic()
                result = self._request('start', payload, LIFECYCLE_TIMEOUT)
            self.started = True
            # Where a start spends its time: waiting for Slurm to create the step (nothing
            # when a step is reused), waiting for the node-wide limit on simultaneous
            # container starts, the start itself.
            timing = {'step_wait_s': round(step_ready - began, 2), 'gate_wait_s': round(gate_open - step_ready, 2),
                      'container_start_s': round(time.monotonic() - gate_open, 2)}
            print(f'[{self.env_id}] lifecycle resource step: {result.get("resource_step")} '
                  f'at {time.strftime("%H:%M:%S")} timing: {timing} step reused: {reused} '
                  f'(use {self.slot.uses}; pool created {POOL.created}, reused {POOL.reused}, closed {POOL.closed})',
                  flush=True)
            return result
        except BaseException:
            self._finish()
            raise

    def exec(self, payload):
        return self._request('exec', payload, exec_rpc_timeout(payload.get('timeout_sec')))

    def upload(self, payload):
        return self._request('upload', payload, LIFECYCLE_TIMEOUT)

    def download(self, payload):
        return self._request('download', payload, LIFECYCLE_TIMEOUT)

    def _finish(self):
        """Give up this environment's step for good; harmless when repeated."""
        slot, self.slot = self.slot, None
        self.started = False
        if slot is not None:
            POOL.discard(slot, self.env_id)

    def stop(self, payload):
        slot, clean = self.slot, False
        if slot is None:
            return {'stopped': True}
        try:
            if slot.process.poll() is None and slot.ready():
                result = self._request('stop', payload, LIFECYCLE_TIMEOUT)
                if isinstance(result, dict):
                    # The step says whether anything of this container is left in it.
                    clean = bool(result.pop('step_clean', False))
                return result
            return {'stopped': True}
        finally:
            self.started = False
            self.slot = None
            if clean and reuse_steps() and slot.alive():
                POOL.release(slot)
            else:
                # Successful stop has already shut down the container in its step.
                try:
                    slot.process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    pass
                POOL.discard(slot, self.env_id)


def leftover_processes(wait=5.0):
    """PIDs still in this step besides the server, after giving them a moment and a kill.

    The step's control group holds exactly what was started in it, so anything left after
    the container has stopped belongs to the previous task. Outside a Slurm step (tests)
    the group is not ours to judge, and nothing is reported.
    """
    if not os.environ.get('SLURM_STEP_ID'):
        return []
    try:
        group = next(line.split('::', 1)[1].strip() for line in open('/proc/self/cgroup') if line.startswith('0::'))
        procs = Path('/sys/fs/cgroup') / group.lstrip('/') / 'cgroup.procs'
        def others():
            return [int(pid) for pid in procs.read_text().split() if int(pid) != os.getpid()]
        deadline = time.monotonic() + wait
        killed = False
        while True:
            left = others()
            if not left or time.monotonic() >= deadline:
                return left
            if not killed and time.monotonic() >= deadline - wait / 2:
                for pid in left:
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except OSError:
                        pass
                killed = True
            time.sleep(.2)
    except (OSError, StopIteration, ValueError):
        return []


def serve(config_path):
    """Child entry point, launched once by srun; serves one environment after another."""
    import bridge_worker as patch
    config_path = Path(config_path)
    config = json.loads(config_path.read_text())
    # Install the container hooks without installing another lifecycle proxy.
    patch.configure_worker(config['staging_base'], config['sif_cache'], child=True)
    current = {'instance': None, 'ipc': None}
    stopping = threading.Event()

    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            operation = None
            try:
                request = json.loads(self.rfile.readline())
                operation = request['operation']
                if operation == 'close':
                    response = {'result': {'closed': True}}
                    return
                if operation not in {'start', 'exec', 'upload', 'download', 'stop'}:
                    raise ValueError(f'Unknown lifecycle operation: {operation}')
                if operation == 'start':
                    if current['instance'] is not None:
                        raise RuntimeError('this resource step still holds a container')
                    current['instance'] = patch.worker.ApptainerInstance(
                        request.get('env_id') or config.get('env_id'), config['sif_cache'],
                        config['staging_base'], config['max_exec_output_chars'])
                    current['ipc'] = OwnedFakerootIPC()
                instance = current['instance']
                if instance is None:
                    raise RuntimeError('this resource step holds no container')
                if operation == 'stop':
                    current['ipc'].observe()
                result = getattr(instance, operation)(request.get('payload') or {})
                if operation == 'start' and isinstance(result, dict):
                    current['ipc'].observe()
                    result['resource_step'] = {
                        'job_id': os.environ.get('SLURM_JOB_ID'),
                        'step_id': os.environ.get('SLURM_STEP_ID'),
                        'cpu_affinity': sorted(os.sched_getaffinity(0)),
                        'cpus_requested': os.environ.get('SLURM_CPUS_PER_TASK'),
                    }
                if operation == 'stop':
                    current['instance'] = None
                    left = leftover_processes()
                    if not left:
                        current['ipc'].cleanup()
                    if left:
                        print(f'processes left after stop, step will not be reused: {left}', flush=True)
                    if isinstance(result, dict):
                        result['step_clean'] = not left
                response = {'result': result}
            except Exception as exc:
                traceback.print_exc()
                response = {'error': f'{type(exc).__name__}: {exc}'}
            finally:
                try:
                    self.wfile.write(json.dumps(response).encode() + b'\n')
                    self.wfile.flush()
                finally:
                    # A step is kept only after a clean stop; any failure ends it.
                    kept = (operation == 'stop' and config.get('reuse') and 'error' not in response
                            and response['result'].get('step_clean'))
                    if operation == 'close' or (operation == 'stop' and not kept) \
                            or (operation == 'start' and 'error' in response):
                        stopping.set()

    class Server(socketserver.ThreadingUnixStreamServer):
        daemon_threads = True

    def stop_signal(signum, frame):
        stopping.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, stop_signal)
    socket_path = config_path.parent / 'rpc.sock'
    try:
        with Server(str(socket_path), Handler) as server:
            server.timeout = .2
            while not stopping.is_set():
                server.handle_request()
    finally:
        try:
            instance = current['instance']
            if instance is not None and (instance.started or (instance.staging_dir and Path(instance.staging_dir).exists())):
                current['ipc'].observe()
                instance.stop({})
                if not leftover_processes():
                    current['ipc'].cleanup()
        finally:
            socket_path.unlink(missing_ok=True)


if __name__ == '__main__':
    serve(sys.argv[1])
