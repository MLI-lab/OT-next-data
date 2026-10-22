"""Per-trial resource measurements: cgroup counters at step close, reported once per environment in result.json."""
import asyncio
import json
import logging
from types import SimpleNamespace

from harbor_patches import protected_step as p
from harbor_patches import resource_usage as r


def fake_step(root, *, peak=512 * 2**20, limit=2 * 2**30, usec=3_500_000, pids=17, cpuset='4-5'):
    step = root / 'job_1' / 'step_0'
    user = step / 'user'
    user.mkdir(parents=True)
    (user / 'memory.events').write_text('low 0\nhigh 0\nmax 0\noom 0\noom_kill 0\n')
    (user / 'memory.peak').write_text(f'{peak}\n')
    (user / 'memory.max').write_text(f'{limit}\n')
    (user / 'pids.peak').write_text(f'{pids}\n')
    (user / 'cpu.stat').write_text(f'usage_usec {usec}\nuser_usec 1\nsystem_usec 2\n')
    (step / 'cpuset.cpus.effective').write_text(cpuset + '\n')
    return user / 'memory.events'


def test_step_usage_reads_exact_counters_and_step_cpuset(tmp_path):
    usage = p.step_usage(fake_step(tmp_path))
    assert usage == {'memory_peak_bytes': 512 * 2**20, 'memory_limit_bytes': 2 * 2**30,
                     'pids_peak': 17, 'cpu_usec': 3_500_000, 'cpus': 2}


def test_step_usage_leaves_out_missing_counters(tmp_path):
    events = tmp_path / 'job_1' / 'step_0' / 'memory.events'
    events.parent.mkdir(parents=True)
    events.write_text('oom_kill 0\n')
    (events.parent / 'memory.max').write_text('max\n')
    assert p.step_usage(events) == {}


def test_usage_report_combines_oom_recovery_steps():
    report = p.usage_report([
        {'memory_peak_bytes': 2 * 2**30, 'memory_limit_bytes': 2 * 2**30, 'cpu_usec': 10_000_000,
         'pids_peak': 40, 'cpus': 1, 'seconds': 30.0},
        {'memory_peak_bytes': 2**30, 'memory_limit_bytes': 2 * 2**30, 'cpu_usec': 5_000_000,
         'pids_peak': 12, 'cpus': 1, 'seconds': 20.0}])
    assert report == {'steps': 2, 'memory_peak_mb': 2048.0, 'memory_limit_mb': 2048.0,
                      'cpu_seconds': 15.0, 'step_seconds': 50.0, 'cpus': 1, 'pids_peak': 40}
    assert p.usage_report([]) is None


def test_stop_returns_usage_measured_before_the_step_closes(tmp_path):
    events = fake_step(tmp_path)

    class Inner:
        staging_dir = None
        def stop(self, payload):
            return {'stopped': True}
    obj = p.ProtectedInstance('env-1', '', '', 1000, instance_factory=lambda *a: Inner(), step_prefix=lambda *a: [])
    obj.info, obj.generation, obj.step_ready = {'memory_events': str(events)}, 1, 0.0
    closed = []
    obj.slot = SimpleNamespace(alive=lambda: True, interrupt=lambda tag: None,
                               close=lambda tag: (closed.append(tag), [f.unlink() for f in events.parent.iterdir()]),
                               control=tmp_path)
    result = obj.stop({})
    assert closed == ['env-1']
    resources = result['resources']
    assert result['stopped'] is True
    assert resources['memory_peak_mb'] == 512.0 and resources['cpu_seconds'] == 3.5
    assert resources['memory_limit_mb'] == 2048.0 and resources['cpus'] == 2 and resources['pids_peak'] == 17
    assert resources['steps'] == 1 and resources['step_seconds'] > 0


def test_record_names_agent_and_separate_verifier_environments():
    agent, verifier = object(), object()
    holder = {'trial': SimpleNamespace(agent_environment=agent), 'resources': {}}
    r.record(holder, agent, {'memory_peak_mb': 400.0})
    r.record(holder, verifier, {'memory_peak_mb': 250.0})
    r.record(holder, verifier, {'memory_peak_mb': 260.0})
    r.record(holder, verifier, None)
    r.record(None, agent, {'memory_peak_mb': 1.0})
    assert holder['resources'] == {'agent': {'memory_peak_mb': 400.0}, 'verifier': {'memory_peak_mb': 250.0},
                                   'verifier_2': {'memory_peak_mb': 260.0}}


def test_stop_result_reaches_only_its_own_trial_result_json(monkeypatch):
    from harbor.environments.apptainer import apptainer as bridge
    from harbor.models.trial.result import TrialResult
    from harbor.trial.trial import Trial
    for owner, name in ((bridge, '_poll_job'), (bridge, '_async_http_post'), (bridge.ApptainerEnvironment, 'stop'),
                        (Trial, 'run'), (TrialResult, 'model_dump_json')):
        monkeypatch.setattr(owner, name, getattr(owner, name))
    monkeypatch.setattr(bridge, '_ot_resource_usage', False, raising=False)

    async def post(url, body, *args, **kwargs):
        return {'job_id': 'job-1'}

    async def poll(url, job_id, *args, **kwargs):
        return {'state': 'done', 'stopped': True, 'resources': {'memory_peak_mb': 300.0, 'cpu_seconds': 1.5}}
    monkeypatch.setattr(bridge, '_async_http_post', post)
    monkeypatch.setattr(bridge, '_poll_job', poll)
    r.install()

    env = object.__new__(bridge.ApptainerEnvironment)
    env._env_id, env._bridge_url, env.logger = 'env-1', 'http://bridge', logging.getLogger('test')
    own, other = TrialResult.model_construct(), TrialResult.model_construct()
    trial = SimpleNamespace(agent_environment=env, _result=own)

    async def trial_run():
        token = r.TRIAL.set({'trial': trial, 'resources': {}})
        try:
            # Harbor stops environments in tasks created from the trial's context.
            await asyncio.ensure_future(env.stop(delete=True))
            return own.model_dump_json(indent=4), other.model_dump_json(indent=4)
        finally:
            r.TRIAL.reset(token)
    mine, theirs = asyncio.run(trial_run())
    assert json.loads(mine)['resources'] == {'agent': {'memory_peak_mb': 300.0, 'cpu_seconds': 1.5}}
    assert 'resources' not in json.loads(theirs)
    assert env._env_id is None
