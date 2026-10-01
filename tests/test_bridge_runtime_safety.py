import base64
import importlib.util
from pathlib import Path

import pytest


@pytest.fixture
def patch():
    pytest.importorskip('harbor')
    path = Path(__file__).resolve().parents[1] / 'harbor_patches/bridge_worker.py'
    spec = importlib.util.spec_from_file_location('test_bridge_patch', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_worker_corrects_old_client_cache_key(patch, tmp_path):
    from harbor.utils.container_cache import environment_dir_hash_truncated
    (tmp_path / 'Dockerfile').write_text('FROM ubuntu\nCOPY data /app/data\n')
    (tmp_path / 'data').write_text('one')
    def payload():
        return {'dockerfile_hash': 'old', 'sif_path': '/cache/build_task-old.sif',
            'files_b64': {p.name: base64.b64encode(p.read_bytes()).decode() for p in tmp_path.iterdir()}}
    a = patch.content_keyed_payload(payload())
    assert a['dockerfile_hash'] == environment_dir_hash_truncated(tmp_path)
    (tmp_path / 'data').write_text('two')
    b = patch.content_keyed_payload(payload())
    assert a['sif_path'] != b['sif_path']
    assert patch.content_keyed_payload(b) == b


def test_network_fallback_warns_and_records_effective_host(patch, monkeypatch, tmp_path, capsys):
    import json
    monkeypatch.setenv('PILOT_NET_ISOLATION', '1')
    status = tmp_path / 'network.json'
    monkeypatch.setenv('PILOT_NETWORK_STATUS_PATH', str(status))
    assert patch.network_isolation_available(str(tmp_path)) is False
    assert 'WARNING' in capsys.readouterr().err
    assert json.loads(status.read_text())['effective'] == 'host'


def test_explicit_host_mode_supplies_dns_and_proxy(patch, monkeypatch):
    monkeypatch.setenv('PILOT_NET_ISOLATION', '0')
    monkeypatch.delenv('APPTAINER_BINDPATH', raising=False)
    monkeypatch.setenv('https_proxy', 'http://proxy.example:8080')
    patch.configure_explicit_host_network()
    import os
    assert '/etc/resolv.conf:/etc/resolv.conf:ro' in os.environ['APPTAINER_BINDPATH']
    assert os.environ['APPTAINERENV_https_proxy'] == 'http://proxy.example:8080'


def test_only_dead_fakeroot_queues_of_this_user_are_swept(patch):
    listing = ('\n------ Message Queues PIDs --------\n'
               'msqid      owner      lspid      lrpid\n'
               '54       me         1001       1002\n'      # both dead: leaked
               '55       me         1001       2000\n'      # receiver alive: in use
               '56       other      1001       1002\n'      # another user's queue
               '57       me         0          0\n')        # never used yet: too new to judge
    import os, pwd
    alive = lambda pid: pid == 2000
    listing = listing.replace('me ', pwd.getpwuid(os.getuid()).pw_name + ' ')
    assert patch.leaked_message_queues(listing, alive=alive) == ['54']


def test_instance_records_go_to_staging_unless_configured(patch, monkeypatch, tmp_path):
    import os
    monkeypatch.delenv('APPTAINER_CONFIGDIR', raising=False)
    patch.keep_instance_records_off_home('')
    assert 'APPTAINER_CONFIGDIR' not in os.environ
    patch.keep_instance_records_off_home(str(tmp_path))
    assert os.environ['APPTAINER_CONFIGDIR'] == str(tmp_path / 'apptainer-config')
    assert (tmp_path / 'apptainer-config').is_dir()
    monkeypatch.setenv('APPTAINER_CONFIGDIR', str(tmp_path / 'chosen'))
    patch.keep_instance_records_off_home(str(tmp_path / 'other'))
    assert os.environ['APPTAINER_CONFIGDIR'] == str(tmp_path / 'chosen')


def test_timed_out_mkdir_is_repeated_but_other_commands_are_not(patch):
    import subprocess
    calls = []
    def stalled_twice(cmd, *args, **kwargs):
        calls.append(cmd)
        if len(calls) < 3:
            raise subprocess.TimeoutExpired(cmd, 30)
        return 'done'
    run = patch.run_retrying_stalled_mkdir(stalled_twice)
    mkdir = ['apptainer', 'exec', 'instance://hb_env_1', 'bash', '-c', 'mkdir -p /setup_files']
    assert run(mkdir, timeout=30) == 'done' and len(calls) == 3
    # An upload that also copies, and a mkdir past the budget, fail as before.
    for cmd, now in ((mkdir[:-1] + ['mkdir -p /a; cp /workspace/x /a/x'], [0]), (mkdir, [0, 400])):
        calls.clear()
        with pytest.raises(subprocess.TimeoutExpired):
            patch.run_retrying_stalled_mkdir(stalled_twice, clock=lambda: now.pop(0) if len(now) > 1 else now[0])(cmd)
        assert len(calls) == 1


def test_slow_readiness_probe_is_repeated_until_the_stall_budget(patch, monkeypatch, tmp_path):
    import subprocess
    from types import SimpleNamespace
    probes, stopped = [], []
    class Anchor:
        def poll(self): return None
        def terminate(self): pass
        def wait(self, timeout=None): return 0
    def run(cmd, *args, **kwargs):
        if 'has-session' in cmd[-1]:
            probes.append(cmd)
            if len(probes) < slow_probes:
                raise subprocess.TimeoutExpired(cmd, 10)
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(patch, '_original_start', lambda self, payload: {'started': True})
    monkeypatch.setattr(patch, '_original_stop', lambda self, payload: stopped.append(self.env_id))
    monkeypatch.setattr(patch, 'trial_step_prefix', lambda payload, env_id: [])
    monkeypatch.setattr(patch.worker, 'APPTAINER', 'apptainer', raising=False)
    monkeypatch.setattr(patch.subprocess, 'run', run)
    monkeypatch.setattr(patch.subprocess, 'Popen', lambda *a, **k: Anchor())
    env = SimpleNamespace(instance_name='hb_env_1', env_id='env-1', staging_dir=str(tmp_path))
    slow_probes = 3
    assert patch.start_with_anchor(env, {}) == {'started': True}
    assert len(probes) == 3 and not stopped
    probes.clear()
    monkeypatch.setattr(patch, 'STALL_BUDGET', -1)
    with pytest.raises(subprocess.TimeoutExpired):
        patch.start_with_anchor(env, {})
    assert stopped == ['env-1']


def test_container_start_gate_spaces_starts_and_releases_after_error(patch):
    now = [10.0]
    delays = []
    def sleep(delay):
        delays.append(delay)
        now[0] += delay
    gate = patch.ContainerStartGate(1, .25, clock=lambda: now[0], sleep=sleep)
    with gate:
        pass
    with pytest.raises(RuntimeError):
        with gate:
            raise RuntimeError('start failed')
    with gate:
        pass
    assert delays == [.25, .25]
    assert gate.semaphore.acquire(blocking=False)
    assert not gate.semaphore.acquire(blocking=False)
    gate.semaphore.release()
    for interval in (-1, float('nan'), float('inf')):
        with pytest.raises(ValueError):
            patch.ContainerStartGate(1, interval)
