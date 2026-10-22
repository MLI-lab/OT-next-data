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
import stat
import socketserver
import subprocess
import threading
import time
import tempfile

try:
    from harbor_patches.trial_step import StepSlot
    from harbor_patches.fakeroot_ipc import OwnedFakerootIPC
    from harbor_patches import start_wait
except ModuleNotFoundError:
    from trial_step import StepSlot
    from fakeroot_ipc import OwnedFakerootIPC
    import start_wait

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


def memory_events_path(cgroup_file='/proc/self/cgroup', cgroup_root='/sys/fs/cgroup'):
    """Find an enforcing task-step counter on both cgroup v1 and v2 clusters."""
    groups = [line.split(':', 2) for line in Path(cgroup_file).read_text().splitlines()]
    step = 'step_' + os.environ.get('SLURM_STEP_ID', '')
    for _, controllers, group in groups:
        if not controllers:
            path = Path(cgroup_root) / group.lstrip('/')
            limit_name, events_name = 'memory.max', 'memory.events'
        elif 'memory' in controllers.split(','):
            path = Path(cgroup_root) / 'memory' / group.lstrip('/')
            limit_name, events_name = 'memory.limit_in_bytes', 'memory.oom_control'
        else:
            continue
        while step in path.parts:
            limit, events = path / limit_name, path / events_name
            if limit.exists() and events.exists():
                value = limit.read_text().strip()
                # v1 expresses unlimited as page-aligned LONG_MAX. Never climb
                # into the job cgroup: another trial's OOM is not ours.
                if value.isdigit() and 0 < int(value) < (1 << 60) and oom_count(events) is not None:
                    return str(events)
            path = path.parent
    raise RuntimeError('OOM recovery requires an enforcing task-step cgroup memory limit')


def oom_count(path):
    try:
        return int(dict(line.split() for line in Path(path).read_text().splitlines())['oom_kill'])
    except (OSError, KeyError, ValueError, TypeError):
        return None


def cpu_count(cpuset):
    """Number of CPUs in a cpuset list such as '0-3,8'."""
    count = 0
    for part in cpuset.strip().split(','):
        if part:
            first, _, last = part.partition('-')
            count += int(last or first) - int(first) + 1
    return count


def step_usage(memory_events):
    """Exact lifetime counters of one task step's cgroup v2: peak memory, CPU time, peak processes.

    Read from the directory that enforces the step's memory limit, so the
    numbers cover every container process of this environment (and the small
    executor). Missing files (cgroup v1, removed cgroup) leave fields out.
    """
    directory = Path(memory_events).parent

    def number(name):
        try:
            value = (directory / name).read_text().strip()
        except OSError:
            return None
        return int(value) if value.isdigit() else None
    usage = {'memory_peak_bytes': number('memory.peak'), 'memory_limit_bytes': number('memory.max'),
             'pids_peak': number('pids.peak')}
    try:
        stat = dict(line.split() for line in (directory / 'cpu.stat').read_text().splitlines())
        usage['cpu_usec'] = int(stat['usage_usec'])
    except (OSError, KeyError, ValueError):
        pass
    # Slurm pins cores with a cpuset; the effective set may sit on the step level.
    path = directory
    while 'step_' in ''.join(path.parts):
        try:
            usage['cpus'] = cpu_count((path / 'cpuset.cpus.effective').read_text())
            break
        except (OSError, ValueError):
            path = path.parent
    return {key: value for key, value in usage.items() if value is not None}


def usage_report(steps):
    """Combine the steps of one environment (one per OOM-recovery generation)."""
    steps = [s for s in steps if s]
    if not steps:
        return None
    report = {'steps': len(steps)}

    def largest(key):
        values = [s[key] for s in steps if key in s]
        return max(values) if values else None
    peak, limit = largest('memory_peak_bytes'), largest('memory_limit_bytes')
    if peak is not None:
        report['memory_peak_mb'] = round(peak / 2**20, 1)
    if limit is not None:
        report['memory_limit_mb'] = round(limit / 2**20, 1)
    if any('cpu_usec' in s for s in steps):
        report['cpu_seconds'] = round(sum(s.get('cpu_usec', 0) for s in steps) / 1e6, 2)
    report['step_seconds'] = round(sum(s.get('seconds', 0) for s in steps), 2)
    for key in ('cpus', 'pids_peak'):
        value = largest(key)
        if value is not None:
            report[key] = value
    return report


def remove_tree(path):
    """Delete a staging tree, including read-only directories left by the task.

    Go marks its module cache read-only; in the overlay upper directory those
    directories belong to the calling user but block unlinking their entries.
    Only on a permission error, add owner rwx to every real directory (never
    following symlinks) and delete again.
    """
    try:
        shutil.rmtree(path)
        return
    except PermissionError:
        pass
    def writable(directory):
        mode = os.lstat(directory).st_mode
        if stat.S_ISDIR(mode) and mode & stat.S_IRWXU != stat.S_IRWXU:
            os.chmod(directory, stat.S_IMODE(mode) | stat.S_IRWXU)
    writable(path)
    for root, directories, _ in os.walk(path):
        for name in directories:
            writable(os.path.join(root, name))
    shutil.rmtree(path)


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
                 *, instance_factory, step_prefix, is_abandoned=lambda env_id: False,
                 start_anchor=None, stop_anchor=None):
        self.env_id = env_id
        self.inner = instance_factory(env_id, sif_cache_dir, staging_base, max_exec_output_chars)
        self.step_prefix, self.is_abandoned = step_prefix, is_abandoned
        self.start_anchor, self.stop_anchor = start_anchor, stop_anchor
        self.recovery_deadline = None
        self.slot = None
        self.start_command = None
        self.payload = None
        self.started = False
        self._pool_key = None
        self.lock = threading.RLock()
        self.generation = 0
        self.stopping = threading.Event()
        self.command_generation = 0
        self.commands_cancelled = False
        self.usage = []

    def _record_usage(self):
        """Read this step's counters once, before Slurm removes its cgroup."""
        info = getattr(self, 'info', None)
        if not info or getattr(self, 'usage_generation', None) == self.generation:
            return
        self.usage_generation = self.generation
        try:
            usage = step_usage(info['memory_events'])
        except Exception as exc:
            print(f'[{self.env_id}] cannot read task step usage: {exc}', flush=True)
            return
        usage['seconds'] = time.monotonic() - self.step_ready
        self.usage.append(usage)

    def cancel_commands(self, generation):
        """Reap this environment's entire step, retaining its writable files.

        Runs on the control thread, never behind the busy command's lock.
        A later grading/transfer operation reopens the saved filesystem.
        """
        if generation <= self.command_generation:
            return
        self.command_generation = generation
        self.commands_cancelled = True
        slot = self.slot
        if slot is not None:
            slot.interrupt(self.env_id)
        acquired = self.lock.acquire(timeout=15)
        if not acquired and slot is not None:
            slot.force_stop(self.env_id)
            acquired = self.lock.acquire(timeout=5)
        if not acquired:
            raise RuntimeError('Cancelled executor was force-stopped but its operation lock did not release')
        try:
            if slot is not None:
                self._close_step()
                if slot.process.poll() is None:
                    raise RuntimeError('Cancelled task executor is still alive')
        finally:
            self.lock.release()

    def _prepare_operation(self, payload):
        if payload.get('command_generation', 0) != self.command_generation:
            raise RuntimeError('Rejected command submitted before session cancellation')
        if self.stopping.is_set():
            raise RuntimeError('Environment is stopping')
        if self.commands_cancelled:
            self._recover(time.monotonic() + 180)
            self.commands_cancelled = False

    def _new_step(self, wait_limit=None):
        command = self.step_prefix(self.payload, self.env_id)
        if not command:
            raise RuntimeError('Protected execution requires a Slurm task step')
        self.slot = StepSlot(command)
        deadline = time.monotonic() + (wait_limit if wait_limit is not None else float(os.environ.get('OT_TRIAL_STEP_WAIT', '3600')))
        # Slurm creates the step; on Helma that is rate limited per user and can
        # take minutes. It is waiting for resources, not container start time.
        waited = time.monotonic()
        with start_wait.waiting(self.env_id, 'its Slurm step', detail=lambda: self.slot.log_tail()):
            while not self.slot.ready():
                if self.stopping.is_set() or self.is_abandoned(self.env_id):
                    raise RuntimeError('Task was stopped while its executor was starting')
                if self.slot.process.poll() is not None:
                    raise RuntimeError('Task executor failed to start: ' + self.slot.log_tail())
                if time.monotonic() >= deadline:
                    raise start_wait.StartWaitTimeout(
                        start_wait.wait_message(time.monotonic() - waited, deadline - waited)
                        + '; ' + self.slot.log_tail())
                time.sleep(.1)
        if self.stopping.is_set() or self.is_abandoned(self.env_id):
            raise RuntimeError('Task was stopped before its executor became ready')
        self.info = self.slot.request(self.env_id, 'info', {}, 10)
        self.step_ready = time.monotonic()
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

    def _confirmed_exec_oom(self, result, error):
        if self._oom():
            return True
        if error is None and result.get('return_code', 0) == 0:
            return False
        # A failing exec can return before Slurm publishes the OOM counter/log.
        # Allow a bounded reporting delay before Harbor classifies the response
        # as a terminal protocol error. No command is re-executed, and an exit
        # code or "Cannot allocate memory" string alone is never OOM evidence.
        deadline = time.monotonic() + 2.0
        while not self.stopping.is_set() and time.monotonic() < deadline:
            time.sleep(.1)
            if self._oom():
                return True
        return False

    def _close_step(self):
        self._record_usage()
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
                with active(self), start_wait.starting(self.env_id):
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
        # The worker can run as __main__. Importing it here under its package
        # name executes its initialization again after ApptainerInstance has
        # become a factory wrapper. Use the already initialized worker hooks.
        if not callable(self.start_anchor) or not callable(self.stop_anchor):
            raise RuntimeError('OOM recovery requires configured terminal anchor hooks')
        # Reap all old processes before opening the same writable overlay again.
        self._close_step()
        if self.slot.process.poll() is None:
            raise RuntimeError('Cannot recover before the old task executor has exited')
        self.stop_anchor(self.inner)
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
            self.start_anchor(self.inner)

    def exec(self, payload):
        with self.lock, active(self):
            self._prepare_operation(payload)
            deadline = time.monotonic() + (payload.get('timeout_sec') or 600)
            result, error = None, None
            try:
                result = self.inner.exec(payload)
            except Exception as exc:
                error = exc
            if self._confirmed_exec_oom(result, error):
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
            self._prepare_operation(payload)
            return self.inner.upload(payload)

    def download(self, payload):
        with self.lock, active(self):
            self._prepare_operation(payload)
            return self.inner.download(payload)

    def stop(self, payload):
        self.stopping.set()
        # exec/upload/download hold this lock while waiting on their RPC. A
        # cancelled client does not cancel that remote command. Interrupt its
        # executor before waiting for the lock, or cleanup can wait for days
        # while the abandoned step keeps CPUs and memory reserved.
        acquired = self.lock.acquire(timeout=.1)
        if not acquired:
            slot = self.slot
            if slot is not None:
                slot.interrupt(self.env_id)
            acquired = self.lock.acquire(timeout=10)
        if not acquired:
            if slot is not None:
                slot.force_stop(self.env_id)
            acquired = self.lock.acquire(timeout=5)
        if not acquired:
            raise RuntimeError('Task executor was force-stopped but its operation lock did not release')
        try:
            result = {'stopped': True}
            try:
                if self.slot and self.slot.alive():
                    with active(self):
                        result = self.inner.stop(payload)
            finally:
                self.started = False
                if self.slot:
                    self._close_step()
                    self.slot = None
                # A dead executor cannot run upstream stop. Its disk staging
                # belongs only to this instance and is removed after reaping.
                staging = getattr(self.inner, 'staging_dir', None)
                if staging and Path(staging).exists():
                    remove_tree(staging)
            # Measured once per environment; the trial writes it into result.json.
            resources = usage_report(self.usage)
            if resources and isinstance(result, dict):
                result = dict(result, resources=resources)
            return result
        finally:
            self.lock.release()


def retained_run(cmd, kwargs, control_dir):
    """Spool captured output so executor cancellation cannot discard it."""
    kwargs = dict(kwargs)
    timeout = kwargs.pop('timeout', None)
    input_data = kwargs.pop('input', None)
    if input_data is not None:
        kwargs['stdin'] = subprocess.PIPE
    if kwargs.pop('capture_output', False):
        kwargs.update(stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    captures = {}
    for name in ('stdout', 'stderr'):
        if kwargs.get(name) == subprocess.PIPE:
            captures[name] = tempfile.NamedTemporaryFile(prefix='command-', suffix='.' + name,
                                                         dir=control_dir, delete=False)
            kwargs[name] = captures[name]
    kwargs['start_new_session'] = True
    try:
        with subprocess.Popen(cmd, **kwargs) as proc:
            expired = False
            try:
                proc.communicate(input_data, timeout=timeout)
            except subprocess.TimeoutExpired:
                expired = True
                # Kill descendants in the command's group as well as its shell.
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                proc.communicate()
            result = {'returncode': proc.returncode}
            if expired:
                result['timeout'] = True
            for name, output in captures.items():
                output.flush()
                value = Path(output.name).read_bytes()
                if kwargs.get('text') or kwargs.get('universal_newlines') or kwargs.get('encoding'):
                    value = value.decode(kwargs.get('encoding') or 'utf-8', kwargs.get('errors') or 'strict')
                result[name] = value
            return result
    finally:
        for output in captures.values():
            output.close()
            Path(output.name).unlink(missing_ok=True)


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
                        result = retained_run(p['cmd'], kwargs, control_dir)
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
