"""Keep container lifecycle state outside the disposable Slurm execution step.

Only Apptainer subprocesses (including the persistent terminal owner) run inside
that step. The controller retains the instance object, disk overlay and exact
start command. Confirmed OOM recovery never repeats the failed command or setup.
"""
import base64
from contextvars import ContextVar
from contextlib import contextmanager
import json
import os
from pathlib import Path
import signal
import shutil
import socketserver
import subprocess
import threading
import time

try:
    from harbor_patches.trial_step import StepSlot
    from harbor_patches.fakeroot_ipc import OwnedFakerootIPC
except ModuleNotFoundError:
    from trial_step import StepSlot
    from fakeroot_ipc import OwnedFakerootIPC

ACTIVE = ContextVar('protected_task_controller', default=None)
AGENT_TAG = '# ot-agent-terminal '
OOM_PREFIX = 'OT_TASK_OOM_RECOVERED '
OOM_FAILED_PREFIX = 'OT_TASK_OOM_UNRECOVERABLE '
FEEDBACK = ('Task memory limit exceeded. The container processes and terminal were restarted. '
            'The disk-backed task filesystem and mounts were preserved. Shell variables, '
            'working directory, running processes and memory-backed files were lost. '
            'The last command may have partially completed and was NOT replayed. '
            'Continue from the preserved files within the original remaining time budget.')


def pack(value):
    if isinstance(value, bytes):
        return {'__bytes__': base64.b64encode(value).decode()}
    if isinstance(value, dict):
        return {k: pack(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [pack(v) for v in value]
    return value


def unpack(value):
    if isinstance(value, dict):
        if set(value) == {'__bytes__'}:
            return base64.b64decode(value['__bytes__'])
        return {k: unpack(v) for k, v in value.items()}
    if isinstance(value, list):
        return [unpack(v) for v in value]
    return value


def memory_events_path():
    """Find the nearest enforcing cgroup, not a node-wide OOM counter."""
    group = next(line.split('::', 1)[1].strip() for line in Path('/proc/self/cgroup').read_text().splitlines()
                 if line.startswith('0::'))
    path = Path('/sys/fs/cgroup') / group.lstrip('/')
    step = 'step_' + os.environ.get('SLURM_STEP_ID', '')
    while step in path.parts:
        limit = path / 'memory.max'
        if limit.exists() and limit.read_text().strip() != 'max':
            return str(path / 'memory.events')
        path = path.parent
    raise RuntimeError('OOM recovery requires an enforcing cgroup v2 memory limit')


def oom_count(path):
    try:
        return int(dict(line.split() for line in Path(path).read_text().splitlines())['oom_kill'])
    except (OSError, KeyError, ValueError, TypeError):
        return None


@contextmanager
def active(controller):
    token = ACTIVE.set(controller)
    try:
        yield
    finally:
        ACTIVE.reset(token)


def run_in_step(original, cmd, *args, **kwargs):
    controller = ACTIVE.get()
    if controller is None or not isinstance(cmd, list) or not cmd or Path(cmd[0]).name not in ('apptainer', 'singularity'):
        return original(cmd, *args, **kwargs)
    if args:
        raise TypeError('Protected container commands require keyword subprocess options')
    started = time.monotonic()
    try:
        result = controller.remote_run(cmd, kwargs)
    finally:
        elapsed = time.monotonic() - started
        if elapsed >= 5:
            operation = 'instance ' + cmd[2] if cmd[1] == 'instance' and len(cmd) > 2 else cmd[1]
            target = 'instance' if any(str(arg).startswith('instance://') for arg in cmd) else 'image'
            print(f'[{controller.env_id}] Apptainer {operation} ({target}) took {elapsed:.2f}s', flush=True)
    if cmd[1:3] == ['instance', 'start'] and result.returncode == 0:
        controller.start_command = list(cmd)
    return result


def popen_in_step(cmd, **kwargs):
    controller = ACTIVE.get()
    if controller is None:
        return subprocess.Popen(cmd, **kwargs)
    return RemoteProcess(controller.slot, cmd, kwargs)


def options(kwargs):
    result = dict(kwargs)
    for name in ('stdin', 'stdout', 'stderr'):
        value = result.get(name)
        if hasattr(value, 'fileno'):
            value.flush()
            if not isinstance(value.name, (str, os.PathLike)):
                raise ValueError('Only named file redirects may cross the task step boundary')
            result[name] = {'path': os.fspath(value.name), 'mode': 'rb' if name == 'stdin' else 'ab'}
    return pack(result)


class RemoteProcess:
    def __init__(self, slot, cmd, kwargs):
        self.slot, self.args = slot, cmd
        self.returncode = None
        self.pid = slot.request('executor', 'spawn', {'cmd': cmd, 'kwargs': options(kwargs)}, 30)['pid']

    def poll(self):
        if self.returncode is None:
            self.returncode = (self.slot.request('executor', 'poll', {'pid': self.pid}, 10)['returncode']
                               if self.slot.alive() else -signal.SIGKILL)
        return self.returncode

    def wait(self, timeout=None):
        until = time.monotonic() + (timeout if timeout is not None else 7200)
        while True:
            result = self.poll()
            if result is not None:
                return result
            if time.monotonic() >= until:
                raise subprocess.TimeoutExpired(self.args, timeout)
            time.sleep(.05)

    def terminate(self):
        if self.slot.alive():
            self.slot.request('executor', 'signal', {'pid': self.pid, 'signal': signal.SIGTERM}, 10)

    def kill(self):
        if self.slot.alive():
            self.slot.request('executor', 'signal', {'pid': self.pid, 'signal': signal.SIGKILL}, 10)


class ProtectedInstance:
    def __init__(self, env_id, sif_cache_dir, staging_base, max_exec_output_chars,
                 *, instance_factory, step_prefix, is_abandoned=lambda env_id: False):
        self.env_id = env_id
        self.inner = instance_factory(env_id, sif_cache_dir, staging_base, max_exec_output_chars)
        self.step_prefix, self.is_abandoned = step_prefix, is_abandoned
        self.recovery_deadline = None
        self.slot = None
        self.start_command = None
        self.payload = None
        self.started = False
        self._pool_key = None
        self.lock = threading.RLock()
        self.generation = 0
        self.stopping = threading.Event()

    def _new_step(self, wait_limit=None):
        command = self.step_prefix(self.payload, self.env_id)
        if not command:
            raise RuntimeError('Protected execution requires a Slurm task step')
        self.slot = StepSlot(command)
        deadline = time.monotonic() + (wait_limit if wait_limit is not None else float(os.environ.get('OT_TRIAL_STEP_WAIT', '3600')))
        while not self.slot.ready():
            if self.stopping.is_set() or self.is_abandoned(self.env_id):
                raise RuntimeError('Task was stopped while its executor was starting')
            if self.slot.process.poll() is not None:
                raise RuntimeError('Task executor failed to start: ' + self.slot.log_tail())
            if time.monotonic() >= deadline:
                raise TimeoutError('Timed out starting task executor')
            time.sleep(.1)
        if self.stopping.is_set() or self.is_abandoned(self.env_id):
            raise RuntimeError('Task was stopped before its executor became ready')
        self.info = self.slot.request(self.env_id, 'info', {}, 10)
        self.oom_baseline = oom_count(self.info['memory_events'])
        if self.oom_baseline is None:
            raise RuntimeError('Cannot read task OOM counters from the protected controller')
        self.generation += 1
        print(f'[{self.env_id}] protected task executor: {self.info}; generation={self.generation}', flush=True)

    def remote_run(self, cmd, kwargs):
        timeout = kwargs.get('timeout') or 7200
        if self.recovery_deadline is not None:
            remaining = self.recovery_deadline - time.monotonic()
            if remaining <= 0 or self.stopping.is_set():
                raise TimeoutError('Task stopped or command budget exhausted during OOM recovery')
            timeout = min(timeout, remaining)
            kwargs = dict(kwargs, timeout=timeout)
        response = self.slot.request(self.env_id, 'run', {'cmd': cmd, 'kwargs': options(kwargs)}, timeout + 60)
        response = unpack(response)
        if response.get('timeout'):
            raise subprocess.TimeoutExpired(cmd, timeout, output=response.get('stdout'), stderr=response.get('stderr'))
        result = subprocess.CompletedProcess(cmd, response['returncode'], response.get('stdout'), response.get('stderr'))
        if kwargs.get('check'):
            result.check_returncode()
        return result

    def _oom(self):
        count = oom_count(self.info['memory_events'])
        if count is not None and count > self.oom_baseline:
            return True
        # Slurm can remove the cgroup before the parent reads its counter.
        # This log belongs exclusively to this environment's executor generation.
        return 'oom_kill event' in self.slot.log_tail()

    def _close_step(self):
        # Ownership was observed by the in-step helper while daemons were live.
        # The protected controller can still reclaim those exact resources if
        # the helper is killed, after Slurm has reaped the entire old step.
        ipc = OwnedFakerootIPC()
        try:
            saved = json.loads((self.slot.control / 'ipc.json').read_text())
            ipc.sessions = {tuple(tuple(r) for r in resources): set(owners) for resources, owners in saved}
        except (OSError, ValueError, TypeError):
            pass
        self.slot.close(self.env_id)
        ipc.cleanup()

    def start(self, payload):
        with self.lock:
            self.payload = payload
            try:
                self._new_step()
                # The real instance start already takes the shared start gate.
                # Taking it twice here can deadlock concurrent controllers.
                with active(self):
                    result = self.inner.start(payload)
                if self._oom():
                    raise RuntimeError('Task memory limit exceeded during environment startup')
                if self.start_command is None:
                    raise RuntimeError('No persistent container start command was recorded')
                self.started = True
                return dict(result, resource_step=self.info, oom_recovery=True)
            except BaseException:
                self.stop({})
                raise

    def _recover(self, deadline):
        from harbor_patches.bridge_worker import start_anchor, stop_anchor
        # Reap all old processes before opening the same writable overlay again.
        self._close_step()
        if self.slot.process.poll() is None:
            raise RuntimeError('Cannot recover before the old task executor has exited')
        stop_anchor(self.inner)
        overlays = [self.start_command[i + 1] for i, v in enumerate(self.start_command[:-1]) if v == '--overlay']
        writable = [p for p in overlays if not p.endswith(':ro')]
        if len(writable) != 1 or not Path(writable[0]).exists() or '--writable-tmpfs' in self.start_command:
            raise RuntimeError('Cannot recover OOM without the original disk-backed writable overlay')
        for i, arg in enumerate(self.start_command[:-1]):
            if arg == '--bind' and not Path(self.start_command[i + 1].split(':', 1)[0]).exists():
                raise RuntimeError('Cannot recover OOM: an original bind source is missing')
        if self.stopping.is_set() or time.monotonic() >= deadline:
            raise TimeoutError('No command budget remains for OOM recovery')
        self._new_step(wait_limit=max(.1, min(120, deadline - time.monotonic())))
        # Bypass startup adapters: no image seeding, bootstrap, setup or payload
        # upload. Reopen exactly the same filesystem and bind mounts.
        with active(self):
            self.remote_run([self.start_command[0], 'instance', 'stop', self.inner.instance_name],
                            {'capture_output': True, 'text': True, 'timeout': 30})
            self.remote_run(self.start_command, {'capture_output': True, 'text': True, 'timeout': 120, 'check': True})
            start_anchor(self.inner)

    def exec(self, payload):
        with self.lock, active(self):
            deadline = time.monotonic() + (payload.get('timeout_sec') or 600)
            result, error = None, None
            try:
                result = self.inner.exec(payload)
            except Exception as exc:
                error = exc
            if self._oom():
                # Only marked terminal interactions may recover. Setup/verifier
                # OOMs fail their own phase and cannot silently rerun that work.
                token = next((line[len(AGENT_TAG):] for line in payload.get('command', '').splitlines()
                              if line.startswith(AGENT_TAG)), None)
                if token is None:
                    raise RuntimeError('Task memory limit exceeded outside a recoverable agent terminal interaction') from error
                try:
                    self.recovery_deadline = deadline
                    self._recover(deadline)
                except Exception as exc:
                    return {'return_code': 125, 'stdout': OOM_FAILED_PREFIX + token + '\n' + str(exc), 'stderr': ''}
                finally:
                    self.recovery_deadline = None
                return {'return_code': 0, 'stdout': OOM_PREFIX + token + '\n' + FEEDBACK, 'stderr': ''}
            if error is not None:
                raise error
            return result

    def upload(self, payload):
        with self.lock, active(self):
            return self.inner.upload(payload)

    def download(self, payload):
        with self.lock, active(self):
            return self.inner.download(payload)

    def stop(self, payload):
        self.stopping.set()
        with self.lock:
            try:
                if self.slot and self.slot.alive():
                    with active(self):
                        return self.inner.stop(payload)
                return {'stopped': True}
            finally:
                self.started = False
                if self.slot:
                    self._close_step()
                    self.slot = None
                # A dead executor cannot run upstream stop. Its disk staging
                # belongs only to this instance and is removed after reaping.
                staging = getattr(self.inner, 'staging_dir', None)
                if staging and Path(staging).exists():
                    shutil.rmtree(staging)


def serve_executor(control_dir):
    """Disposable subprocess launcher; all its descendants share task limits."""
    processes = {}
    stopping = threading.Event()
    ipc, ipc_lock = OwnedFakerootIPC(), threading.Lock()
    info = {'job_id': os.environ.get('SLURM_JOB_ID'), 'step_id': os.environ.get('SLURM_STEP_ID'),
            'memory_events': memory_events_path()}

    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            opened = []
            try:
                req = json.loads(self.rfile.readline())
                op, p = req['operation'], unpack(req.get('payload') or {})
                if op == 'info':
                    result = info
                elif op == 'close':
                    stopping.set()
                    result = {'closed': True}
                elif op in ('run', 'spawn'):
                    kwargs = p['kwargs']
                    kwargs.pop('check', None)
                    for name in ('stdin', 'stdout', 'stderr'):
                        value = kwargs.get(name)
                        if isinstance(value, dict) and 'path' in value:
                            f = open(value['path'], value['mode'])
                            opened.append(f)
                            kwargs[name] = f
                    if op == 'run':
                        try:
                            done = subprocess.run(p['cmd'], **kwargs)
                            result = {'returncode': done.returncode, 'stdout': done.stdout, 'stderr': done.stderr}
                        except subprocess.TimeoutExpired as exc:
                            result = {'timeout': True, 'stdout': exc.stdout, 'stderr': exc.stderr}
                    else:
                        proc = subprocess.Popen(p['cmd'], **kwargs)
                        processes[proc.pid] = proc
                        result = {'pid': proc.pid}
                elif op == 'poll':
                    result = {'returncode': processes[p['pid']].poll()}
                elif op == 'signal':
                    processes[p['pid']].send_signal(p['signal'])
                    result = {}
                else:
                    raise ValueError(f'Unknown executor operation: {op}')
                response = {'result': pack(result)}
            except Exception as exc:
                response = {'error': f'{type(exc).__name__}: {exc}'}
            finally:
                for f in opened:
                    f.close()
                try:
                    with ipc_lock:
                        ipc.observe()
                        path = Path(control_dir) / 'ipc.json'
                        temporary = path.with_suffix('.tmp')
                        temporary.write_text(json.dumps([(resources, sorted(owners)) for resources, owners in ipc.sessions.items()]))
                        temporary.replace(path)
                except OSError as exc:
                    print(f'Cannot save task IPC ownership: {exc}', flush=True)
            try:
                self.wfile.write(json.dumps(response).encode() + b'\n')
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass

    class Server(socketserver.ThreadingUnixStreamServer):
        daemon_threads = True
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *args: stopping.set())
    with Server(str(Path(control_dir) / 'rpc.sock'), Handler) as server:
        server.timeout = .1
        while not stopping.is_set():
            server.handle_request()
    # Slurm reaps every task-step descendant when the executor exits.
