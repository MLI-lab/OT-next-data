"""Reclaim only IPC observed belonging to this lifecycle step's live faked daemon.

Absence from a node-wide queue snapshot is not proof that a semaphore is unused:
other jobs and new fakeroot sessions can be creating resources concurrently.
Never remove untracked IPC, including old orphans left by another worker.
"""
import os
from pathlib import Path
import subprocess


def ipc_table(kind, proc=Path('/proc')):
    lines = (proc / 'sysvipc' / kind).read_text().splitlines()
    fields = lines[0].split()
    return [dict(zip(fields, map(int, line.split()))) for line in lines[1:]]


def identity(row, kind):
    fields = ('key', 'msqid' if kind == 'msg' else 'semid', 'uid', 'cuid', 'ctime')
    return tuple(row[field] for field in fields)


class OwnedFakerootIPC:
    def __init__(self, proc=Path('/proc'), cgroups=Path('/sys/fs/cgroup')):
        self.proc, self.cgroups = proc, cgroups
        self.sessions = {}

    def observe(self):
        """Capture queue/sem identities while their faked owner is demonstrably ours."""
        # Batch/coordinator processes can own many concurrent environments.
        # Only a dedicated numerical lifecycle step has the required boundary.
        if not os.environ.get('SLURM_STEP_ID', '').isdigit():
            return
        try:
            group = next(line[3:] for line in (self.proc / 'self/cgroup').read_text().splitlines()
                         if line.startswith('0::'))
            if not group or group == '/' or '..' in Path(group).parts:
                return
            def is_owner(pid):
                path = self.proc / str(pid)
                try:
                    return (path.stat().st_uid == os.getuid()
                            and (path / 'comm').read_text().strip().startswith('faked')
                            and f'0::{group}' in (path / 'cgroup').read_text().splitlines())
                except OSError:
                    return False
            owners = {int(value) for value in
                      (self.cgroups / group.lstrip('/') / 'cgroup.procs').read_text().split()
                      if is_owner(int(value))}
            if not owners:
                return
            queues = {row['key']: row for row in ipc_table('msg', self.proc)
                      if row['uid'] == os.getuid() and row['cuid'] == os.getuid()}
            sems = {row['key']: row for row in ipc_table('sem', self.proc)
                    if row['uid'] == os.getuid() and row['cuid'] == os.getuid()}
            for key, first in queues.items():
                second, sem = queues.get(key + 1), sems.get(key + 2)
                if not key or second is None or sem is None or sem['nsems'] != 1:
                    continue
                # Both queues must positively identify the same live daemon;
                # adjacent keys alone are not ownership evidence.
                pids = owners & {first['lspid'], first['lrpid']} & {second['lspid'], second['lrpid']}
                pids = {pid for pid in pids if is_owner(pid)}
                if not pids:
                    continue
                resources = (identity(first, 'msg'), identity(second, 'msg'), identity(sem, 'sem'))
                self.sessions[resources] = pids
        except (OSError, ValueError, KeyError, StopIteration, IndexError):
            # Unknown ownership always means leave the resource alone.
            return

    def cleanup(self):
        """After step processes stop, remove only unchanged, positively tracked IPC."""
        removed = 0
        try:
            queues = {identity(row, 'msg'): row for row in ipc_table('msg', self.proc)}
            sems = {identity(row, 'sem'): row for row in ipc_table('sem', self.proc)}
        except (OSError, ValueError, KeyError, IndexError):
            return removed
        for resources, owners in self.sessions.items():
            first, second, sem = resources
            live_pids = set(owners)
            for resource in (first, second):
                row = queues.get(resource)
                if row:
                    live_pids.update(pid for pid in (row['lspid'], row['lrpid']) if pid > 0)
            # PID reuse errs on the side of retaining an orphan.
            if any((self.proc / str(pid)).exists() for pid in live_pids):
                continue
            for kind, resource, snapshot in [('msg', first, queues), ('msg', second, queues), ('sem', sem, sems)]:
                if resource not in snapshot:
                    continue
                try:
                    # Recheck identity immediately before removal, including ctime.
                    if not any(identity(row, kind) == resource for row in ipc_table(kind, self.proc)):
                        continue
                    result = subprocess.run(['ipcrm', '-q' if kind == 'msg' else '-s', str(resource[1])],
                                            capture_output=True, timeout=10)
                    removed += result.returncode == 0
                except (OSError, ValueError, KeyError, IndexError, subprocess.TimeoutExpired):
                    continue
        if removed:
            print(f'[fakeroot IPC] Removed {removed} resources owned by the stopped lifecycle step', flush=True)
        return removed
