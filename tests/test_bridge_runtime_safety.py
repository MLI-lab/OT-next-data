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


def test_loopback_hosts_are_local_readonly_and_idempotent(patch, tmp_path, monkeypatch):
    monkeypatch.setenv('APPTAINER_BINDPATH', '/input:/input:ro')
    patch.configure_loopback_hosts(str(tmp_path))
    hosts = tmp_path / 'container-hosts'
    assert hosts.read_text() == '127.0.0.1 localhost\n::1 localhost ip6-localhost ip6-loopback\n'
    before = hosts.stat().st_ino
    patch.configure_loopback_hosts(str(tmp_path))
    assert hosts.stat().st_ino == before
    assert patch.os.environ['APPTAINER_BINDPATH'].split(',') == [
        '/input:/input:ro', f'{hosts}:/etc/hosts:ro']


def test_loopback_hosts_respect_explicit_override(patch, tmp_path, monkeypatch):
    monkeypatch.setenv('APPTAINER_BINDPATH', '/custom/hosts:/etc/hosts:ro')
    patch.configure_loopback_hosts(str(tmp_path))
    assert not (tmp_path / 'container-hosts').exists()


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


def test_host_ca_files_are_mounted_read_only_in_containers(patch, tmp_path, monkeypatch):
    for name in ('SSL_CERT_FILE', 'REQUESTS_CA_BUNDLE', 'CURL_CA_BUNDLE'):
        monkeypatch.delenv(name, raising=False)
    bundle = tmp_path / 'host-ca.pem'
    bundle.write_text('test certificate bytes')
    monkeypatch.setenv('REQUESTS_CA_BUNDLE', str(bundle))
    monkeypatch.setenv('APPTAINER_BINDPATH', '/existing:/existing:ro')
    monkeypatch.setenv('APPTAINERENV_REQUESTS_CA_BUNDLE', '')
    patch.configure_container_certificates()
    patch.configure_container_certificates()
    target = '/run/ot-certificates/requests_ca_bundle.pem'
    assert patch.os.environ['REQUESTS_CA_BUNDLE'] == str(bundle)
    assert patch.os.environ['APPTAINERENV_REQUESTS_CA_BUNDLE'] == target
    assert patch.os.environ['APPTAINER_BINDPATH'].split(',') == [
        '/existing:/existing:ro', f'{bundle}:{target}:ro']


def test_nested_trial_reserves_its_own_cpus_without_overlap(patch, monkeypatch):
    monkeypatch.setenv('SLURM_JOB_ID', '100')
    monkeypatch.setenv('SLURM_STEP_ID', '0')
    monkeypatch.setenv('SLURMD_NODENAME', 'worker-node')
    monkeypatch.setenv('OT_TRIAL_SRUN', '1')
    command = patch.trial_step_prefix({'task_env_config': {'cpus': 2, 'memory_mb': 4096}}, 'env-x')
    assert '-c2' in command and '--mem=4096M' in command
    assert '--gres=none' in command and '--exact' in command
    assert command[command.index('-w') + 1] == 'worker-node'
    assert '--cpu-bind=none' in command
    assert '--overlap' not in command
    monkeypatch.setenv('OT_TRIAL_SRUN', '0')
    assert patch.trial_step_prefix({}, 'env-x') == []


def test_network_fallback_warns_and_records_effective_host(patch, monkeypatch, tmp_path, capsys):
    import json
    monkeypatch.setenv('OT_NET_ISOLATION', '1')
    status = tmp_path / 'network.json'
    monkeypatch.setenv('OT_NETWORK_STATUS_PATH', str(status))
    assert patch.network_isolation_available(str(tmp_path)) is False
    assert 'WARNING' in capsys.readouterr().err
    assert json.loads(status.read_text())['effective'] == 'host'


def test_explicit_host_mode_supplies_dns_and_proxy(patch, monkeypatch):
    monkeypatch.setenv('OT_NET_ISOLATION', '0')
    monkeypatch.delenv('APPTAINER_BINDPATH', raising=False)
    monkeypatch.setenv('https_proxy', 'http://proxy.example:8080')
    patch.configure_explicit_host_network()
    import os
    assert '/etc/resolv.conf:/etc/resolv.conf:ro' in os.environ['APPTAINER_BINDPATH']
    assert os.environ['APPTAINERENV_https_proxy'] == 'http://proxy.example:8080'


def test_namespace_fallback_uses_host_dns_and_proxy(patch, monkeypatch, tmp_path):
    import os
    import sys
    from types import SimpleNamespace

    monkeypatch.setenv('OT_NET_ISOLATION', '1')
    monkeypatch.setenv('SLURM_JOB_ID', '123')
    monkeypatch.setenv('OT_TRIAL_SRUN', '1')
    monkeypatch.setenv('https_proxy', 'http://proxy.example:8080')
    monkeypatch.delenv('APPTAINER_BINDPATH', raising=False)
    monkeypatch.delenv('APPTAINERENV_https_proxy', raising=False)
    monkeypatch.setattr(patch, 'network_isolation_available', lambda cache: False)
    monkeypatch.setattr(patch, 'sweep_partial_archives', lambda: None)
    monkeypatch.setitem(sys.modules, 'trial_step', SimpleNamespace(SlurmInstance=lambda *args: None, REGISTRY={}))
    monkeypatch.setattr(patch.worker, 'ApptainerInstance', patch.worker.ApptainerInstance)
    monkeypatch.setattr(patch.worker, '_INSTANCE_START_SEM', patch.worker._INSTANCE_START_SEM)
    monkeypatch.setattr(patch.worker, '_cleanup_stale_instances', patch.worker._cleanup_stale_instances)

    patch.configure_worker(str(tmp_path), str(tmp_path))

    assert '/etc/resolv.conf:/etc/resolv.conf:ro' in os.environ['APPTAINER_BINDPATH']
    assert os.environ['APPTAINERENV_https_proxy'] == 'http://proxy.example:8080'


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
    run = patch.run_container_commands(stalled_twice)
    mkdir = ['apptainer', 'exec', 'instance://hb_env_1', 'bash', '-c', 'mkdir -p /setup_files']
    assert run(mkdir, timeout=30) == 'done' and len(calls) == 3
    # An upload that also copies, and a mkdir past the budget, fail as before.
    for cmd, now in ((mkdir[:-1] + ['mkdir -p /a; cp /workspace/x /a/x'], [0]), (mkdir, [0, 400])):
        calls.clear()
        with pytest.raises(subprocess.TimeoutExpired):
            patch.run_container_commands(stalled_twice, clock=lambda: now.pop(0) if len(now) > 1 else now[0])(cmd)
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
    monkeypatch.setattr(patch.worker, 'APPTAINER', 'apptainer', raising=False)
    monkeypatch.setattr(patch.subprocess, 'run', run)
    def launch(cmd, **kwargs):
        assert cmd[0] == 'apptainer'  # tmux inherits the lifecycle step; no nested srun
        return Anchor()
    monkeypatch.setattr(patch.subprocess, 'Popen', launch)
    env = SimpleNamespace(instance_name='hb_env_1', env_id='env-1', staging_dir=str(tmp_path))
    slow_probes = 3
    assert patch.start_with_anchor(env, {}) == {'started': True}
    assert len(probes) == 3 and not stopped
    probes.clear()
    monkeypatch.setattr(patch, 'STALL_BUDGET', 15)
    now = iter([0, 0, 20])
    monkeypatch.setattr(patch.time, 'monotonic', lambda: next(now))
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


def test_anchor_launch_failure_stops_the_started_container(patch, monkeypatch, tmp_path):
    from types import SimpleNamespace
    stopped = []
    monkeypatch.setattr(patch, '_original_start', lambda self, payload: {})
    monkeypatch.setattr(patch, '_original_stop', lambda self, payload: stopped.append(self.env_id))
    monkeypatch.setattr(patch.worker, 'APPTAINER', 'apptainer')
    monkeypatch.setattr(patch.subprocess, 'run', lambda *a, **k: SimpleNamespace(returncode=0))
    def fail(*args, **kwargs):
        raise OSError('cannot launch anchor')
    monkeypatch.setattr(patch.subprocess, 'Popen', fail)
    env = SimpleNamespace(env_id='env-1', instance_name='hb_env_1', staging_dir=str(tmp_path))
    with pytest.raises(OSError, match='cannot launch anchor'):
        patch.start_with_anchor(env, {})
    assert stopped == ['env-1']
    assert env._helma_tmux_log.closed


def test_dependency_archive_is_mounted_only_for_the_trial_that_has_one(patch, monkeypatch, tmp_path):
    import json
    spec = {'directory': str(tmp_path), 'target': '/opt/deps.tar', 'folder': '/cache', 'exclude': []}
    assert patch.dependency_archive({'task_name': 'task-1'}) is None          # not configured
    monkeypatch.setenv('OT_DEPENDENCY_ARCHIVES', json.dumps(spec))
    assert patch.dependency_archive({'task_name': 'task-1'}) == tmp_path / 'task-1.tar'
    assert patch.dependency_archive({'task_name': '../other'}) is None
    seen = []
    monkeypatch.setattr(patch, 'image_bakes_tests', lambda sif: False)
    run = patch.run_container_commands(lambda cmd, *a, **k: seen.append(cmd))
    start = ['apptainer', 'instance', 'start', '--cleanenv', 'image.sif', 'hb_env_1']
    run(start)
    patch._trial.archive = tmp_path / 'task-1.tar'
    run(start)
    run(['apptainer', 'exec', 'instance://hb_env_1', 'true'])
    patch._trial.archive = None
    start = start[:3] + ['--contain'] + start[3:]
    assert seen == [start, start[:4] + ['--bind', f'{tmp_path}/task-1.tar:/opt/deps.tar:ro'] + start[4:],
                    ['apptainer', 'exec', 'instance://hb_env_1', 'true']]


def test_dependency_archive_is_saved_only_by_a_marked_trial_with_reward_one(patch, monkeypatch, tmp_path):
    import json
    from types import SimpleNamespace
    archives = tmp_path / 'archives'
    spec = {'directory': str(archives), 'target': '/opt/deps.tar', 'folder': '/cache', 'exclude': ['./m2/*.xml']}
    monkeypatch.setenv('OT_DEPENDENCY_ARCHIVES', json.dumps(spec))
    commands = []
    def tar(cmd, stdout=None, **kwargs):
        commands.append(cmd)
        stdout.write(b'archive')
        return SimpleNamespace(returncode=0, stderr=b'')
    monkeypatch.setattr(patch.subprocess, 'run', tar)
    monkeypatch.setattr(patch.worker, 'APPTAINER', 'apptainer')
    def trial(reward, save=True):
        staging = tmp_path / f'staging-{len(list(tmp_path.iterdir()))}'
        (staging / 'logs/verifier').mkdir(parents=True)
        if reward is not None:
            (staging / 'logs/verifier/reward.txt').write_text(reward + '\n')
        return SimpleNamespace(env_id='env-1', instance_name='hb_env_1', staging_dir=str(staging), started=True,
                               _helma_dependency_archive=archives / 'task-1.tar', _helma_save_dependencies=save)
    for reward, save in (('0', True), (None, True), ('1', False)):
        assert patch.save_dependency_archive(trial(reward, save)) is None
    assert not commands and not archives.exists()
    assert patch.save_dependency_archive(trial('1')) == archives / 'task-1.tar'
    assert (archives / 'task-1.tar').read_bytes() == b'archive' and len(list(archives.iterdir())) == 1
    assert commands[0][:3] == ['apptainer', 'exec', 'instance://hb_env_1']
    assert commands[0][3:] == ['tar', '-cf', '-', '-C', '/cache', '--anchored', '--no-wildcards-match-slash', '--exclude=./m2/*.xml', '.']
    # A failed tar leaves the previous archive and no partial file.
    monkeypatch.setattr(patch.subprocess, 'run', lambda cmd, stdout=None, **k: SimpleNamespace(returncode=2, stderr=b'tar: gone'))
    assert patch.save_dependency_archive(trial('1')) is None
    assert (archives / 'task-1.tar').read_bytes() == b'archive' and len(list(archives.iterdir())) == 1


def test_only_old_half_written_archives_are_removed_at_startup(patch, monkeypatch, tmp_path):
    import json, os
    assert patch.sweep_partial_archives() == 0                                # not configured
    monkeypatch.setenv('OT_DEPENDENCY_ARCHIVES', json.dumps({'directory': str(tmp_path), 'target': '/t', 'folder': '/c'}))
    names = ('task-1.tar', 'task-1.tar.env-1.partial', 'task-2.tar.env-2.partial')
    for name in names:
        (tmp_path / name).write_bytes(b'x')
    old = (tmp_path / names[1]).stat().st_mtime - 2 * patch.SAVE_TIMEOUT - 1
    os.utime(tmp_path / names[0], (old, old))     # a finished archive, however old, stays
    os.utime(tmp_path / names[1], (old, old))
    assert patch.sweep_partial_archives() == 1    # the fresh one may still be written by another job
    assert sorted(p.name for p in tmp_path.iterdir()) == ['task-1.tar', 'task-2.tar.env-2.partial']


def test_instances_get_private_filesystems_without_losing_task_mounts(patch, monkeypatch):
    monkeypatch.setattr(patch, 'image_bakes_tests', lambda sif: False)
    seen = []
    run = patch.run_container_commands(lambda cmd, **kwargs: seen.append(cmd))
    command = ['apptainer', 'instance', 'start', '--bind', '/stage/tmp:/tmp:rw', '--fakeroot', 'task.sif', 'env']
    run(command)
    assert seen[0] == command[:3] + ['--contain'] + command[3:]
    assert '--contain' not in command  # do not mutate upstream retry arguments
    run(seen[0])
    assert seen[1].count('--contain') == 1
    exec_command = ['apptainer', 'exec', 'instance://env', 'true']
    run(exec_command)
    assert seen[2] == exec_command


def test_startup_preserves_baked_tests_and_other_mounts(patch, monkeypatch):
    monkeypatch.setattr(patch, 'image_bakes_tests', lambda sif: True)
    command = ['apptainer', 'instance', 'start', '--bind', '/stage/tests:/tests:rw',
               '--bind', '/stage/logs:/logs:rw', 'verifier.sif', 'env']
    result = patch.instance_start_command(command, patch.NET_FLAGS)
    assert '/stage/tests:/tests:rw' not in result
    assert '/stage/logs:/logs:rw' in result
    assert '--contain' in result and '--net' in result
    assert result[-2:] == ['verifier.sif', 'env']
    assert '/stage/tests:/tests:rw' in command


def test_directory_copy_expands_contents_and_keeps_file_copy(patch, monkeypatch, tmp_path):
    (tmp_path / 'data').mkdir()
    (tmp_path / 'data/a').write_text('a')
    (tmp_path / 'data/b').mkdir()
    monkeypatch.setattr(patch, '_original_parse_copies',
                        lambda path: [(['data'], '/app/data/'), (['single'], '/app/single')])
    assert patch.parse_copies_docker_semantics(str(tmp_path / 'Dockerfile')) == [
        (['data/a'], '/app/data/'), (['data/b'], '/app/data/'), (['single'], '/app/single')]


def test_slurm_dispatcher_does_not_install_child_container_hooks(patch, monkeypatch, tmp_path):
    import sys
    from types import SimpleNamespace
    sentinel = lambda *args, **kwargs: None
    monkeypatch.setitem(sys.modules, 'trial_step', SimpleNamespace(SlurmInstance=sentinel, REGISTRY={}))
    monkeypatch.setenv('SLURM_JOB_ID', '123')
    monkeypatch.setenv('OT_TRIAL_SRUN', '1')
    monkeypatch.setenv('OT_NET_ISOLATION', '0')
    monkeypatch.setattr(patch, 'sweep_partial_archives', lambda: None)
    backend = patch.worker.ApptainerInstance
    start, run, copies = backend.start, patch.worker.subprocess.run, patch.worker._parse_copies
    # Register upstream globals for restoration after configure_worker mutates them.
    for name in ('ApptainerInstance', '_INSTANCE_START_SEM', '_cleanup_stale_instances', '_instances'):
        monkeypatch.setattr(patch.worker, name, getattr(patch.worker, name))
    patch.configure_worker(str(tmp_path), str(tmp_path))
    assert patch.worker.ApptainerInstance.func is sentinel
    assert backend.start is start
    assert patch.worker.subprocess.run is run
    assert patch.worker._parse_copies is copies


def test_multi_node_serving_steps(tmp_path, monkeypatch):
    """A model larger than one node: a head step that becomes the server and a worker step on the other nodes."""
    import subprocess, sys
    from hpc import validation_worker as worker
    from config.models import MODELS, placement
    monkeypatch.delenv('OT_GPUS_PER_NODE', raising=False)
    monkeypatch.delenv('OT_SERVE_GPUS', raising=False)
    assert placement(MODELS['qwen3-30b-instruct-2507']) == {
        'gpus': 1, 'nodes': 1, 'gpus_per_node': 1, 'tensor_parallel': 1, 'pipeline_parallel': 1, 'replicas': 1}
    plan = placement(MODELS['qwen3-coder-480b-fp8'])
    assert plan == {'gpus': 8, 'nodes': 2, 'gpus_per_node': 4, 'tensor_parallel': 4, 'pipeline_parallel': 2, 'replicas': 1}
    # Copies of a small model on one node: each keeps its own GPUs; a model that fills the node has no room for a copy.
    two = placement(MODELS['qwen3-30b-instruct-2507'], replicas=2)
    assert (two['gpus'], two['nodes'], two['tensor_parallel'], two['replicas']) == (2, 1, 1, 2)
    for model in ('qwen35-122b', 'qwen3-coder-480b-fp8'):
        with pytest.raises(ValueError):
            placement(MODELS[model], replicas=2)
    monkeypatch.setenv('OT_GPUS_PER_NODE', '1')
    monkeypatch.setenv('OT_SERVE_GPUS', '3')
    assert placement(MODELS['qwen3-30b-instruct-2507'])['nodes'] == 3
    head, others = worker.serving_commands(plan, ['python3', '-m', 'server'], head='h21-01', head_ip='10.0.0.1',
        ray_port=21234, partition='h200', cpus=4, memory_mb=32768, python='/env/bin/python', tmp='/tmp/j')
    assert '--nodelist=h21-01' in head and '--gres=gpu:h200:4' in head
    assert head[-5:] == ['python3', '-m', 'server', '--distributed-executor-backend', 'ray']
    assert head[head.index('ray-node') + 1:][:7] == ['head', '10.0.0.1:21234', '4', '4', '/env/bin/python', '/tmp/j', '8']
    assert '--exclude=h21-01' in others and '--nodes=1' in others and others[-7] == 'worker'
    assert '--pid' not in head + others
    assert subprocess.run(['bash', '-n', '-c', worker.RAY_NODE]).returncode == 0


def test_thinking_of_a_served_model_reaches_the_agent():
    """vLLM names the field `reasoning`; the agent reads `reasoning_content` from the raw response."""
    import asyncio
    from harbor.llms.lite_llm import LiteLLM
    from harbor_patches import reasoning_field
    def response(**fields):
        return {'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': 'answer', **fields}}]}
    read = lambda r: reasoning_field.with_reasoning_content(r)['choices'][0]['message'].get('reasoning_content')
    assert read(response(reasoning='thinking')) == 'thinking'
    assert read(response(reasoning_content='older name', reasoning='x')) == 'older name'
    assert read(response()) is None and read(response(reasoning=None)) is None
    original = LiteLLM._dispatch_chat
    try:
        async def served(self, kwargs):
            return response(reasoning='thinking')
        LiteLLM._dispatch_chat = served
        LiteLLM._maps_reasoning_field = False
        reasoning_field.install()
        reasoning_field.install()
        out = asyncio.run(LiteLLM._dispatch_chat(object(), {}))
        assert out['choices'][0]['message']['reasoning_content'] == 'thinking'
    finally:
        LiteLLM._dispatch_chat = original
        LiteLLM._maps_reasoning_field = False


def test_trial_steps_stay_on_the_worker_node_in_a_multi_node_job(monkeypatch):
    patch = load_bridge_patch() if 'load_bridge_patch' in globals() else None
    if patch is None:
        spec = importlib.util.spec_from_file_location('bridge_worker_multi', Path(__file__).resolve().parents[1] / 'harbor_patches/bridge_worker.py')
        patch = importlib.util.module_from_spec(spec); spec.loader.exec_module(patch)
    monkeypatch.setenv('SLURM_JOB_ID', '1')
    monkeypatch.delenv('SLURM_STEP_ID', raising=False)
    monkeypatch.delenv('OT_TRIAL_SRUN', raising=False)
    monkeypatch.setenv('SLURMD_NODENAME', 'h24-08')
    monkeypatch.setenv('SLURM_JOB_NUM_NODES', '1')
    assert '-w' not in patch.trial_step_prefix({}, 'e')
    monkeypatch.setenv('SLURM_JOB_NUM_NODES', '2')
    step = patch.trial_step_prefix({}, 'e')
    assert step[step.index('-w') + 1] == 'h24-08' and '--nodes=1' in step


def test_a_lost_terminal_session_is_reported_with_its_keystrokes():
    import asyncio, logging
    from types import SimpleNamespace
    from harbor.agents.terminus_2 import tmux_session as module
    from harbor_patches import tmux_diagnostics
    original = module.TmuxSession.send_keys_and_capture
    try:
        async def ended(self, batches):
            raise module.TmuxSessionEndedError('gone')
        module.TmuxSession.send_keys_and_capture = ended
        module.TmuxSession._reports_session_end = False
        tmux_diagnostics.install()
        seen = []
        async def run_probe(command, timeout_sec, user):
            return SimpleNamespace(stdout='no server running', stderr='')
        logger = logging.getLogger('tmux-diagnostics-test')
        logger.error = lambda message, *args: seen.append(message % args)
        session = SimpleNamespace(_session_name='s', _user=None, _logger=logger, environment=SimpleNamespace(exec=run_probe))
        batch = module.TmuxKeystrokeBatch(keystrokes='echo x\n', duration_sec=1.0)
        with pytest.raises(module.TmuxSessionEndedError):
            asyncio.run(module.TmuxSession.send_keys_and_capture(session, [batch]))
        assert 'echo x' in seen[0] and 'no server running' in seen[0]
    finally:
        module.TmuxSession.send_keys_and_capture = original
        module.TmuxSession._reports_session_end = False


def test_bridge_records_how_long_requests_wait_and_run(tmp_path):
    spec = importlib.util.spec_from_file_location('bridge_server_timing', Path(__file__).resolve().parents[1] / 'harbor_patches/bridge_server.py')
    server = importlib.util.module_from_spec(spec); spec.loader.exec_module(server)
    job = lambda kind, queued, ran: {'type': kind, 'env_id': 'env-1', 'submitted': 1000.0, 'started': 1000.0 + queued,
                                    'finished': 1000.0 + queued + ran, 'state': 'done', 'worker_id': 'w-1'}
    line = server.timing_line(job('start', 81.4, 3.7))
    assert ' start env-1 queued=81.40 ran=3.70 state=done worker=w-1' in line
    assert server.timing_line(job('exec', 0.1, 2.0)) is None            # fast and routine: not recorded
    assert 'queued=9.00' in server.timing_line(job('exec', 9.0, 2.0))   # waited for a worker: recorded
    assert server.timing_line({'type': 'start', 'submitted': 1.0}) is None
    log = tmp_path / 'bridge-timing.log'
    server.record_timing(job('stop', 0.2, 1.1), str(log)); server.record_timing(None, str(log))
    assert log.read_text().count('\n') == 1 and ' stop env-1 ' in log.read_text()


def test_workspace_seed_preserves_image_and_task_precedence(patch, tmp_path):
    import subprocess
    import shutil
    image = tmp_path / 'image'
    (image / 'repo' / '.git').mkdir(parents=True)
    (image / 'repo' / '.git' / 'HEAD').write_text('ref: refs/heads/main\n')
    (image / 'repo' / 'source.py').write_text('image source')
    (image / 'repo' / 'config').write_text('old config')
    (image / 'repo' / 'run').write_text('#!/bin/sh\n')
    (image / 'repo' / 'run').chmod(0o755)
    (image / 'link').symlink_to('repo/source.py')
    calls = []
    def run(cmd, **kwargs):
        calls.append(cmd)
        assert ':/workspace:rw' not in ' '.join(cmd)
        assert '--writable-tmpfs' in cmd
        assert cmd[cmd.index('--overlay') + 1] == '/deferred:ro'
        target = Path(cmd[cmd.index('--bind') + 1].split(':')[0])
        shutil.copytree(image, target, dirs_exist_ok=True, symlinks=True)
        return subprocess.CompletedProcess(cmd, 0, '', '')
    patch._trial.workspace_seeded = set()
    for name in ('trial1', 'trial2'):
        workspace = tmp_path / name / 'workspace'
        (workspace / 'repo').mkdir(parents=True)
        (workspace / 'repo' / 'config').write_text('task config')
        cmd = ['apptainer', 'instance', 'start', '--bind', f'{workspace}:/workspace:rw',
               '--overlay', '/deferred:ro', '--overlay', '/writable', '/image.sif', name]
        patch.seed_image_workspace(cmd, run)
        patch.seed_image_workspace(cmd, run)  # fakeroot retry must not reset files
        assert (workspace / 'repo' / '.git' / 'HEAD').read_text().startswith('ref:')
        assert (workspace / 'repo' / 'config').read_text() == 'task config'
        assert (workspace / 'repo' / 'source.py').read_text() == 'image source'
        assert (workspace / 'repo' / 'run').stat().st_mode & 0o111
        assert (workspace / 'link').is_symlink()
        (workspace / 'repo' / 'source.py').write_text(name)
    assert len(calls) == 2
    assert (tmp_path / 'trial1/workspace/repo/source.py').read_text() == 'trial1'
    assert (image / 'repo/source.py').read_text() == 'image source'


def test_startup_tmux_install_is_disabled_but_task_commands_are_unchanged(patch):
    from harbor_patches.tmux_runtime import LEGACY_INSTALL
    calls = []
    def run(cmd, **kwargs):
        calls.append(cmd)
    wrapped = patch.run_container_commands(run)
    script = "echo 'precedence ::ffff:0:0/96 100'; " + LEGACY_INSTALL + 'true'
    cmd = ['apptainer', 'exec', 'instance://test', 'bash', '-c', script]
    patch._trial.workspace_seeded = set()
    wrapped(cmd)
    assert 'apt-get install' not in calls[-1][-1]
    patch._trial.workspace_seeded = None
    wrapped(cmd)
    assert calls[-1] == cmd


def test_workspace_seed_executes_copy_with_hidden_files_and_links(patch, tmp_path):
    import os
    import subprocess
    image = tmp_path / 'image'
    (image / '.git').mkdir(parents=True)
    (image / '.git/HEAD').write_text('image head')
    (image / 'run').write_text('#!/bin/sh\n')
    (image / 'run').chmod(0o751)
    os.link(image / 'run', image / 'hardlink')
    (image / 'link').symlink_to('run')
    workspace = tmp_path / 'workspace'
    (workspace / '.git').mkdir(parents=True)
    (workspace / '.git/config').write_text('task config')
    patch._trial.workspace_seeded = set()

    def run(cmd, **kwargs):
        target = cmd[cmd.index('--bind') + 1].split(':')[0]
        script = cmd[-1].replace('/workspace', str(image)).replace('/_ot_workspace_init', target)
        return subprocess.run(['sh', '-ec', script], **kwargs)

    patch.seed_image_workspace(['apptainer', 'instance', 'start', '--bind',
                               f'{workspace}:/workspace:rw', '/image.sif', 'test'], run)
    assert (workspace / '.git/HEAD').read_text() == 'image head'
    assert (workspace / '.git/config').read_text() == 'task config'
    assert (workspace / 'run').stat().st_mode & 0o777 == 0o751
    assert (workspace / 'run').stat().st_ino == (workspace / 'hardlink').stat().st_ino
    assert (workspace / 'link').readlink() == Path('run')


def test_workspace_merge_does_not_follow_symlinks(patch, tmp_path):
    source, dest, outside = [tmp_path / n for n in ('source', 'dest', 'outside')]
    for p in (source, dest, outside):
        p.mkdir()
    (outside / 'keep').write_text('untouched')
    (dest / 'collision').symlink_to(outside, target_is_directory=True)
    (source / 'collision').mkdir()
    (source / 'collision' / 'keep').write_text('task')
    (dest / 'tasklink').mkdir()
    (dest / 'tasklink' / 'old').touch()
    (source / 'tasklink').symlink_to('collision', target_is_directory=True)
    patch.merge_workspace(source, dest)
    assert (outside / 'keep').read_text() == 'untouched'
    assert not (dest / 'collision').is_symlink()
    assert (dest / 'tasklink').is_symlink()
    assert not list(source.iterdir())


@pytest.mark.parametrize('failure', ['exit', 'timeout'])
def test_workspace_copy_failure_prevents_instance_start(patch, tmp_path, failure):
    import subprocess
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    (workspace / 'task').write_text('keep')
    patch._trial.workspace_seeded = set()
    calls = []
    def run(cmd, **kwargs):
        calls.append(cmd)
        if failure == 'timeout':
            raise subprocess.TimeoutExpired(cmd, 300)
        return subprocess.CompletedProcess(cmd, 1, '', 'copy failed')
    wrapped = patch.run_container_commands(run)
    with pytest.raises((RuntimeError, subprocess.TimeoutExpired)):
        wrapped(['apptainer', 'instance', 'start', '--bind', f'{workspace}:/workspace:rw',
                 '/image.sif', 'test'])
    assert len(calls) == 1 and calls[0][1] == 'exec'
    assert (workspace / 'task').read_text() == 'keep'
    assert not list(tmp_path.glob('workspace-image-*'))


def test_image_build_bounds_memory_and_retries_only_registry_import(patch, monkeypatch):
    monkeypatch.setenv('OT_IMAGE_COMPRESSION_ARGS', '-processors 4 -mem 2048M')
    import subprocess
    calls, sleeps = [], []
    def run(cmd, **kwargs):
        calls.append((list(cmd), kwargs))
        return subprocess.CompletedProcess(cmd, 1 if len(calls) == 1 else 0, '',
            'conveyor failed to get: unexpected end of JSON input')
    result = patch.run_image_build(run, ['apptainer', 'build', '/cache/task.sif.tmp', 'docker://image'],
                                  (), {'timeout': 60}, clock=lambda: 0, sleep=sleeps.append)
    assert result.returncode == 0 and len(calls) == 2 and sleeps == [2]
    assert calls[0][0][2:4] == ['--mksquashfs-args', '-processors 4 -mem 2048M']
    assert '--force' in calls[1][0]
    calls.clear()
    patch.run_image_build(run, ['apptainer', 'build', '/cache/task.sif.tmp', '/task.def'],
                          (), {}, sleep=sleeps.append)
    assert len(calls) == 1


def test_no_global_compression_override(patch, monkeypatch):
    import subprocess
    monkeypatch.delenv('OT_IMAGE_COMPRESSION_ARGS', raising=False)
    calls = []
    def run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, '', '')
    patch.run_image_build(run, ['apptainer', 'build', '/cache/task.sif.tmp', '/task.def'], (), {})
    assert '--mksquashfs-args' not in calls[0]


def test_failed_preparation_cannot_rebuild_under_task_limits(patch, monkeypatch):
    monkeypatch.setenv('OT_IMAGES_PREPARED', '1')
    with pytest.raises(RuntimeError, match='Refusing to rebuild'):
        patch.prepared_image_only(None, '/Dockerfile', '/missing.sif')


@pytest.mark.parametrize('error', ['manifest unknown', 'unauthorized', 'mksquashfs command failed: exit status 137'])
def test_image_build_does_not_retry_permanent_or_memory_failure(patch, error):
    import subprocess
    calls = []
    def run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 1, '', error)
    patch.run_image_build(run, ['apptainer', 'build', '/cache/task.sif.tmp', 'docker://image'], (), {})
    assert len(calls) == 1


def test_registry_retries_share_original_timeout(patch):
    import subprocess
    now = [0]
    timeouts = []
    def run(cmd, **kwargs):
        timeouts.append(kwargs['timeout'])
        now[0] += 10
        return subprocess.CompletedProcess(cmd, 1, '', 'conveyor failed to get: unexpected end of JSON input')
    def sleep(seconds):
        now[0] += seconds
    patch.run_image_build(run, ['apptainer', 'build', '/cache/task.sif.tmp', 'docker://image'], (),
                          {'timeout': 25}, clock=lambda: now[0], sleep=sleep)
    assert timeouts == [25, 13]


@pytest.mark.parametrize('error', [
    '/.singularity.d/libs/fakeroot: error while loading shared libraries: libfakeroot.so',
    'faked: GLIBC_2.34 not found',
])
def test_fakeroot_helper_fallback_keeps_root_mapping(patch, tmp_path, error):
    from subprocess import CompletedProcess
    sif = tmp_path/'old.sif'
    sif.touch()
    calls = []
    def run(cmd, **kwargs):
        calls.append(cmd)
        return CompletedProcess(cmd, 0, '0\n', '') if '--ignore-fakeroot-command' in cmd else CompletedProcess(cmd, 1, '', error)
    cmd = ['apptainer', 'instance', 'start', '--fakeroot', str(sif), 'name']
    cache = {}
    fixed = patch.compatible_fakeroot_command(cmd, run, cache)
    assert '--ignore-fakeroot-command' in fixed
    assert '--fakeroot' in fixed
    assert fixed[-2:] == cmd[-2:]
    assert patch.compatible_fakeroot_command(cmd, run, cache) == fixed
    assert len(calls) == 2


@pytest.mark.parametrize('error,uid', [('unrelated mount error', '0'), ('faked: GLIBC_2.34 not found', '1000')])
def test_fakeroot_fallback_never_masks_other_errors_or_loses_root(patch, tmp_path, error, uid):
    from subprocess import CompletedProcess
    sif = tmp_path/'old.sif'
    sif.touch()
    def run(cmd, **kwargs):
        return CompletedProcess(cmd, 0, uid, '') if '--ignore-fakeroot-command' in cmd else CompletedProcess(cmd, 1, '', error)
    cmd = ['apptainer', 'instance', 'start', '--fakeroot', str(sif), 'name']
    assert patch.compatible_fakeroot_command(cmd, run, {}) == cmd
