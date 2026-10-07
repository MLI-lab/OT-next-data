"""Cleanup must never infer ownership from missing node-wide IPC resources."""
import os
from pathlib import Path
import shutil
from types import SimpleNamespace

import pytest

from harbor_patches import fakeroot_ipc as ipc


@pytest.fixture
def node(tmp_path, monkeypatch):
    proc, cgroups = tmp_path / 'proc', tmp_path / 'cgroups'
    group = '/slurm/job_12/step_3'
    (proc / 'self').mkdir(parents=True)
    (proc / 'self/cgroup').write_text(f'0::{group}\n')
    (proc / 'sysvipc').mkdir()
    (cgroups / group.lstrip('/')).mkdir(parents=True)
    (cgroups / group.lstrip('/') / 'cgroup.procs').write_text('501\n502\n')
    for pid, owner_group in ((501, group), (502, '/slurm/job_99/step_1')):
        (proc / str(pid)).mkdir()
        (proc / str(pid) / 'comm').write_text('faked-sysv\n')
        (proc / str(pid) / 'cgroup').write_text(f'0::{owner_group}\n')
    uid = os.getuid()
    def tables(own=True, extra=True, changed=False):
        msg = 'key msqid uid cuid ctime lspid lrpid\n'
        sem = 'key semid uid cuid ctime nsems\n'
        if own:
            msg += f'100 10 {uid} {uid} 1000 0 501\n101 11 {uid} {uid} 1000 501 0\n'
            sem += f'102 12 {uid} {uid} {2000 if changed else 1000} 1\n'
        if extra:
            msg += f'200 20 {uid} {uid} 1000 0 502\n201 21 {uid} {uid} 1000 502 0\n'
            # New semaphore with no queues: exactly the old cleanup's race.
            sem += f'202 22 {uid} {uid} 1000 1\n302 32 {uid} {uid} 1000 1\n'
        (proc / 'sysvipc/msg').write_text(msg)
        (proc / 'sysvipc/sem').write_text(sem)
    tables()
    monkeypatch.setenv('SLURM_STEP_ID', '3')
    removed = []
    def remove(cmd, **kwargs):
        removed.append(cmd)
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(ipc.subprocess, 'run', remove)
    return SimpleNamespace(proc=proc, tables=tables, removed=removed,
                           tracker=ipc.OwnedFakerootIPC(proc, cgroups))


def test_cleanup_requires_positive_ownership_and_dead_owner(node):
    node.tracker.observe()
    assert node.tracker.cleanup() == 0  # Own daemon is still live.
    shutil.rmtree(node.proc / '501')
    assert node.tracker.cleanup() == 3
    assert node.removed == [['ipcrm', '-q', '10'], ['ipcrm', '-q', '11'], ['ipcrm', '-s', '12']]


def test_new_or_foreign_semaphores_are_never_swept(node):
    # No observation/ownership record => no cleanup, even with absent queues.
    assert node.tracker.cleanup() == 0
    node.tracker.observe()
    shutil.rmtree(node.proc / '501')
    node.tables(own=False)  # Owned resources already vanished; new IPC remains.
    assert node.tracker.cleanup() == 0
    assert node.removed == []


def test_reused_semid_with_different_identity_is_preserved(node):
    node.tracker.observe()
    shutil.rmtree(node.proc / '501')
    node.tables(changed=True)
    node.tracker.cleanup()
    assert node.removed == [['ipcrm', '-q', '10'], ['ipcrm', '-q', '11']]


def test_tracked_orphan_sem_can_be_cleaned_after_queues_disappear(node):
    node.tracker.observe()
    shutil.rmtree(node.proc / '501')
    (node.proc / 'sysvipc/msg').write_text('key msqid uid cuid ctime lspid lrpid\n')
    assert node.tracker.cleanup() == 1
    assert node.removed == [['ipcrm', '-s', '12']]


def test_no_cleanup_from_batch_or_without_readable_ownership(node, monkeypatch):
    monkeypatch.setenv('SLURM_STEP_ID', 'batch')
    node.tracker.observe()
    monkeypatch.setenv('SLURM_STEP_ID', '3')
    (node.proc / 'self/cgroup').unlink()
    node.tracker.observe()
    shutil.rmtree(node.proc / '501')
    assert node.tracker.cleanup() == 0


def test_live_client_blocks_cleanup_even_after_owner_exits(node):
    node.tracker.observe()
    shutil.rmtree(node.proc / '501')
    p = node.proc / 'sysvipc/msg'
    p.write_text(p.read_text().replace('1000 0 501', '1000 502 501'))
    assert node.tracker.cleanup() == 0
