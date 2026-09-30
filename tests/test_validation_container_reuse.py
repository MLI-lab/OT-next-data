import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from validation.stages.container_reuse import ReuseScope, install


@pytest.fixture
def fake_environment():
    class Environment:
        starts = []
        stops = []
        def __init__(self, directory='/task/environment', fail_stop=False):
            self.environment_dir = Path(directory)
            self._bridge_url = 'bridge'
            self.task_env_config = SimpleNamespace(model_dump_json=lambda: '{}')
            self._env_id = None
            self.fail_stop = fail_stop
            self.commands = []
        async def start(self, force_build=False):
            self._env_id = str(len(self.starts) + 1)
            self.starts.append(self._env_id)
        async def stop(self, delete=True):
            self.stops.append(self._env_id)
            self._env_id = None
            if self.fail_stop:
                raise RuntimeError('cleanup error')
        async def exec(self, command, **kwargs):
            self.commands.append(command)
            return SimpleNamespace(return_code=0)
    install(Environment)
    return Environment


def test_reuses_same_task_and_releases_once(fake_environment):
    E = fake_environment
    async def run():
        async with ReuseScope() as scope:
            clients = [E() for _ in range(3)]
            for client in clients:
                await client.start()
                await client.stop()
            assert [e._env_id for e in clients] == ['1'] * 3
            assert (scope.starts, scope.reuses) == (1, 2)
            assert not E.stops
            await scope.clear_logs()
            assert '/logs/verifier' in clients[0].commands[0]
        assert all(e._env_id is None for e in clients)
    asyncio.run(run())
    assert E.stops == ['1']


def test_concurrent_tasks_and_standalone_are_isolated(fake_environment):
    E = fake_environment
    async def task():
        async with ReuseScope():
            first, second = E(), E()
            await first.start()
            await asyncio.sleep(0)
            await second.start()
            assert first._env_id == second._env_id
            return first._env_id
    async def run():
        assert len(set(await asyncio.gather(task(), task()))) == 2
        fresh = E()
        await fresh.start()
        await fresh.stop()
        assert fresh._env_id is None
    asyncio.run(run())
    assert len(E.starts) == len(E.stops) == 3


def test_cleanup_attempts_every_environment_after_error(fake_environment):
    E = fake_environment
    async def run():
        with pytest.raises(ExceptionGroup, match='cleanup failed'):
            async with ReuseScope():
                await E(fail_stop=True).start()
                await E('/task/tests').start()
    asyncio.run(run())
    assert E.stops == ['1', '2']


@pytest.mark.parametrize('nop_reward', [0, 1])
def test_bundle_order_rewards_and_journals(tmp_path, monkeypatch, fake_environment, nop_reward):
    from validation.stages import paired, runner
    from validation import contract
    from validation.data import selection
    tasks = [tmp_path / name for name in ('a', 'b')]
    for task in tasks:
        (task / 'solution').mkdir(parents=True)
        (task / 'solution/solve.sh').write_text('true')
    args = runner.parser().parse_args([str(tmp_path), '--out', str(tmp_path / 'out'),
                                     '--reuse-validation-containers', '--concurrency', '2'])
    monkeypatch.setattr(contract, 'verify_materialized', lambda a: None)
    monkeypatch.setattr(selection, 'discover_tasks', lambda p: tasks)
    monkeypatch.setattr(selection, 'select_paths', lambda paths, a: paths)
    monkeypatch.setattr(paired.runtime, 'check_runtime_task', lambda *a: None)
    events = {str(t): [] for t in tasks}
    rewards = {}
    async def phase(task, stage):
        events[str(task)].append(stage)
        env = fake_environment(str(task / 'environment'))
        await env.start()
        await asyncio.sleep(0)
        await env.stop()
    async def build(task, out, args):
        await phase(task, 3)
        return {'status': 'passed'}
    def config(task, out, args, agent):
        return {'task': str(task), 'agent': agent, 'out': str(out)}
    async def execute(config):
        stage = 5 if config['agent'] == 'nop' else 4
        await phase(Path(config['task']), stage)
        out = Path(config['out'])
        rewards[out] = nop_reward if stage == 5 else 1
        return out
    monkeypatch.setattr(paired.runtime, 'build_task', build)
    monkeypatch.setattr(paired.runtime, 'job_config', config)
    monkeypatch.setattr(paired.runtime, 'execute_job', execute)
    monkeypatch.setattr(paired.runtime, 'trial_results', lambda path: [(path, {
        'verifier_result': {'rewards': {'reward': rewards[path]}}})])
    reports = paired.run_validation_bundle(args, {3, 4, 5})
    assert [stage for stage, _, _ in reports] == [3, 5, 4]
    assert list(events.values()) == [[3, 5, 4], [3, 5, 4]]
    assert len(fake_environment.starts) == len(fake_environment.stops) == 2
    for stage, path, report in reports:
        failed = stage == 5 and nop_reward != 0
        assert report['complete'] and report['has_findings'] == failed
        assert len((path.parent / 'outcomes.jsonl').read_text().splitlines()) == 2
        assert all(i['status'] == ('failed' if failed else 'passed') for i in report['items'])
        if stage == 4:
            assert all(i['container_reuses'] == 2 for i in report['items'])


@pytest.mark.parametrize('stages,enabled,bundles,singles', [
    ([1, 3, 4, 5], True, [{3, 4, 5}], [1]),
    ([4], True, [], [4]), ([3, 4, 5], False, [], [3, 4, 5])])
def test_pipeline_dispatch(tmp_path, monkeypatch, stages, enabled, bundles, singles):
    from validation import run
    from validation.stages import paired, runner
    args = runner.parser().parse_args([str(tmp_path), '--out', str(tmp_path / 'out')])
    args.reuse_validation_containers = enabled
    calls, individual = [], []
    def bundle(args, selected):
        calls.append(selected)
        return [(n, tmp_path / str(n), {'has_findings': False}) for n in (3, 5, 4) if n in selected]
    def single(n, args):
        individual.append(n)
        return tmp_path / str(n), {'has_findings': False, 'items': []}
    monkeypatch.setattr(paired, 'run_validation_bundle', bundle)
    monkeypatch.setattr(run, 'run_stage', single)
    assert run.run_selected(args, stages) == 0
    assert calls == bundles and individual == singles


@pytest.mark.parametrize('extra', [['--attempts', '2'], ['--force-build'], ['--backend', 'docker']])
def test_reuse_rejects_incompatible_settings(tmp_path, extra):
    from validation.stages import runner
    args = runner.parser().parse_args([str(tmp_path), '--reuse-validation-containers', *extra])
    with pytest.raises(ValueError, match='requires Apptainer'):
        runner.check_args(args)


def test_partial_start_failure_is_cleaned_up():
    class BrokenEnvironment:
        environment_dir = Path('/task/environment')
        _bridge_url = 'bridge'
        task_env_config = SimpleNamespace(model_dump_json=lambda: '{}')
        _env_id = None
        stopped = False
        async def start(self, force_build=False):
            self._env_id = 'partially-created'
            raise RuntimeError('startup failed')
        async def stop(self, delete=True):
            self.stopped = True
            self._env_id = None
    install(BrokenEnvironment)
    env = BrokenEnvironment()
    async def run():
        with pytest.raises(RuntimeError, match='startup failed'):
            async with ReuseScope():
                await env.start()
    asyncio.run(run())
    assert env.stopped and env._env_id is None
