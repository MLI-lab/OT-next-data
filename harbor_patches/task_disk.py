"""Task disk: measure, reserve and limit what each task container writes.

`storage` in task.toml (Harbor's `storage_mb`) is the disk a task may use. Docker
and cloud backends enforce it; our Apptainer bridge writes the container's
writable layer as a plain directory on node-local disk, with no size limit. A
size-limited ext3 overlay image would enforce it exactly, but under --fakeroot it
goes through fuse2fs and measured 7-13x slower writes on Helma (2026-11-08), so
package installs and builds would slow down. Instead:

1. Measure: the trial's staging directory (writable layer, /tmp, workspace, logs)
   is measured with `du` while it runs; the peak goes into the trial's resources
   (`disk_peak_mb`, `disk_limit_mb`). `du` runs at most every few seconds; in between a
   cheap free-space check of the staging filesystem (statvfs, every 0.1 s) triggers an
   earlier measurement once free space has dropped by half of the container's remaining
   headroom, so a fast writer is caught within about one measurement.
2. Reserve: before a task container starts, its `storage` is reserved against the
   node's free local disk in a ledger shared by every bridge worker on the node.
   A container waits until it fits: reserved storage of running tasks plus its own
   must stay within free disk + what they already wrote - a safety margin.
3. Limit: when a task declares `storage` and the limit is on, a watchdog stops the
   container once it has written more than that and the operation fails with
   "Task disk limit exceeded". A task can overshoot by what it writes during one
   measurement plus 0.1 s. A single file can never exceed the storage: the trial's
   step runs under RLIMIT_FSIZE = storage (bridge_worker.trial_step_prefix).

Judges (stage 5) inspect tasks rather than solve them: they get the reservation
and measurement but no limit (`OT_TASK_STORAGE_LIMIT=0` on the client side).
"""
from contextvars import ContextVar
import fcntl
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
import time

DISK_LIMIT_MESSAGE = 'Task disk limit exceeded'
LEDGER = '.ot-task-disk-reservations.json'
_storage = ContextVar('ot_task_storage_mb', default=None)


def poll_seconds():
    """Longest time between two full measurements (du) of a container's files."""
    return float(os.environ.get('OT_TASK_DISK_POLL_SEC', '5'))


def check_seconds():
    """Interval of the cheap free-space check that triggers an earlier measurement."""
    return float(os.environ.get('OT_TASK_DISK_CHECK_SEC', '0.1'))


def nonreclaimable_bytes(cgroup):
    """anon + kernel + shmem of a cgroup v2 (memory the kernel cannot drop; swap is off).
    MiMo's measure of what actually causes an OOM kill, as opposed to memory.peak,
    which includes page cache."""
    try:
        stat = dict(line.split() for line in (Path(cgroup) / 'memory.stat').read_text().splitlines())
    except (OSError, ValueError):
        return None
    try:
        return int(stat['anon']) + int(stat.get('kernel', 0)) + int(stat.get('shmem', 0))
    except (KeyError, ValueError):
        return None


def free_bytes(path):
    try:
        stat = os.statvfs(path)
    except OSError:
        return None
    return stat.f_bavail * stat.f_frsize


def margin_bytes():
    """Free disk kept back on the node for images, logs and the bridge itself."""
    return int(os.environ.get('OT_TASK_DISK_MARGIN_BYTES', str(10 * 2**30)))


def used_bytes(path, timeout=300):
    """Allocated bytes below `path` (like du -s), staying on its filesystem."""
    try:
        out = subprocess.run(['du', '-s', '-x', '-B1', str(path)], capture_output=True, text=True,
                             timeout=timeout)
        value = out.stdout.split()[0] if out.stdout.split() else ''
        if value.isdigit():
            return int(value)
    except (OSError, subprocess.TimeoutExpired):
        pass
    total = 0
    for root, dirs, files in os.walk(path, onerror=lambda error: None):
        for name in files + dirs:
            try:
                total += os.lstat(os.path.join(root, name)).st_blocks * 512
            except OSError:
                pass
    return total


def _alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


class Ledger:
    """Disk reservations of all task containers on this node's staging filesystem."""

    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / LEDGER

    def _locked(self, change):
        with open(self.path.with_suffix('.lock'), 'a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                entries = json.loads(self.path.read_text())
            except (OSError, ValueError):
                entries = {}
            entries = {k: v for k, v in entries.items() if _alive(int(v.get('pid', 0)))}
            result = change(entries)
            temporary = self.path.with_name(self.path.name + f'.{os.getpid()}.tmp')
            temporary.write_text(json.dumps(entries))
            temporary.replace(self.path)
            return result

    def try_reserve(self, key, nbytes):
        """Reserve `nbytes` if it fits now. Returns (reserved, capacity) or raises when it never can."""
        def change(entries):
            free = shutil.disk_usage(self.root).free
            written = sum(int(v.get('used', 0)) for v in entries.values())
            capacity = free + written - margin_bytes()
            if os.environ.get('OT_TASK_DISK_CAPACITY_BYTES'):
                # A fixed share of the node's disk (tests, or a node shared with other jobs).
                capacity = min(capacity, int(os.environ['OT_TASK_DISK_CAPACITY_BYTES']))
            reserved = sum(int(v['bytes']) for v in entries.values())
            if nbytes > capacity and not entries:
                raise RuntimeError(f'Task storage {nbytes / 2**30:.1f} GiB exceeds the free node disk '
                                   f'({max(capacity, 0) / 2**30:.1f} GiB after a {margin_bytes() / 2**30:.0f} GiB margin)')
            if reserved + nbytes <= capacity:
                entries[key] = {'bytes': nbytes, 'used': 0, 'pid': os.getpid(), 'since': time.time()}
                return True, capacity
            return False, capacity
        return self._locked(change)

    def reserve(self, key, nbytes, *, wait_sec, stopped=lambda: False, waiting=None):
        deadline = time.monotonic() + wait_sec
        while True:
            reserved, capacity = self.try_reserve(key, nbytes)
            if reserved:
                return
            if stopped():
                raise RuntimeError('Task was stopped while waiting for disk')
            if time.monotonic() >= deadline:
                raise RuntimeError(f'Task storage {nbytes / 2**30:.1f} GiB did not fit on the node disk '
                                   f'within {wait_sec:.0f} s (capacity {capacity / 2**30:.1f} GiB)')
            if waiting:
                waiting()
            time.sleep(2)

    def update(self, key, used):
        def change(entries):
            if key in entries:
                entries[key]['used'] = int(used)
        self._locked(change)

    def release(self, key):
        self._locked(lambda entries: entries.pop(key, None))


class Watchdog(threading.Thread):
    """Measure one container's staging directory; stop it when it exceeds its limit."""

    def __init__(self, path, limit_bytes, on_exceeded, ledger=None, key=None):
        super().__init__(daemon=True, name=f'disk-{key}')
        self.path, self.limit, self.on_exceeded = Path(path), limit_bytes, on_exceeded
        self.ledger, self.key = ledger, key
        self.peak = 0
        self.exceeded = None
        self.stopping = threading.Event()
        # The trial step's cgroup, set by the controller (a new one per OOM-recovery step).
        self.memory_cgroup = None
        self.memory_peak = 0

    def measure(self):
        used = used_bytes(self.path)
        self.peak = max(self.peak, used)
        if self.ledger is not None:
            try:
                self.ledger.update(self.key, used)
            except OSError:
                pass
        if self.limit and used > self.limit and self.exceeded is None:
            self.exceeded = used
            self.on_exceeded(used, self.limit)
        return used

    def due(self, now, free):
        """Measure now? Regularly, or early when free disk dropped by half the remaining headroom.
        Free disk is shared with other containers, so a drop is only a hint; du decides."""
        if now - self.measured_at >= max(poll_seconds(), 3 * self.duration):
            return True
        if not self.limit or free is None or self.free_at is None or now - self.measured_at < 2 * self.duration:
            return False
        headroom = max(self.limit - self.used, 0)
        return self.free_at - free >= max(headroom / 2, 1 << 20)

    def sample_memory(self):
        if self.memory_cgroup is not None:
            value = nonreclaimable_bytes(self.memory_cgroup)
            if value is not None:
                self.memory_peak = max(self.memory_peak, value)

    def run(self):
        self.measured_at, self.duration, self.used, self.free_at = float('-inf'), 0.0, 0, None
        memory_at = float('-inf')
        while not self.stopping.is_set():
            now = time.monotonic()
            if now - memory_at >= 1.0:
                self.sample_memory()
                memory_at = now
            if self.path.exists() and self.due(now, free_bytes(self.path)):
                self.free_at = free_bytes(self.path)
                self.used = self.measure()
                self.measured_at, self.duration = now, time.monotonic() - now
            self.stopping.wait(check_seconds())

    def stop(self):
        self.stopping.set()
        if self.is_alive():
            self.join(timeout=60)
        if self.path.exists():
            self.measure()


def exceeded_message(used, limit):
    return (f'{DISK_LIMIT_MESSAGE}: the task wrote {used / 2**20:.0f} MiB, its task.toml storage is '
            f'{limit / 2**20:.0f} MiB')


def install_client(bridge):
    """Send the task's storage with each container start; judges get no limit."""
    cls = bridge.ApptainerEnvironment
    if getattr(cls, '_ot_task_disk', False):
        return
    original_start, original_post = cls.start, bridge._async_http_post

    async def start(self, *args, **kwargs):
        token = _storage.set(getattr(self.task_env_config, 'storage_mb', None))
        try:
            return await original_start(self, *args, **kwargs)
        finally:
            _storage.reset(token)

    async def post(url, data, *args, **kwargs):
        storage = _storage.get()
        if url.endswith('/env/create') and storage:
            data = dict(data, task_env_config=dict(data.get('task_env_config', {}), storage_mb=int(storage),
                        storage_limit=os.environ.get('OT_TASK_STORAGE_LIMIT', '1') != '0'))
        return await original_post(url, data, *args, **kwargs)

    cls.start = start
    bridge._async_http_post = post
    cls._ot_task_disk = True
