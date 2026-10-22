"""Image reviews must use container evidence and clean up failed attempts."""
import asyncio
import json
import sys
from types import SimpleNamespace

import pytest

from validation.stages import image_review as review
from validation.stages.image_inspector import exchange
from validation.stages.runner import parser, check_args


def test_default_and_explicit_file_mode():
    assert parser().parse_args([]).review_evidence == 'image'
    assert parser().parse_args(['--review-evidence', 'files']).review_evidence == 'files'
    with pytest.raises(ValueError, match='finite and positive'):
        check_args(parser().parse_args(['--review-max-budget-usd', 'nan']))
    assert review.provider_for('terminus-2', True) == 'local'


class Environment:
    def __init__(self):
        self.commands = []
        self.stopped = False

    async def exec(self, command, **kwargs):
        self.commands.append(command)
        return SimpleNamespace(return_code=0, stdout='x' * 30000, stderr=None)

    async def stop(self, **kwargs):
        self.stopped = True

    async def upload_file(self, **kwargs):
        pass

    async def download_file(self, source, destination):
        destination.write_text(json.dumps({'checks': {'anti_cheat_robustness': {'outcome': 'pass', 'explanation': 'Inspected image.'}}}))


def test_inspector_auth_environment_bounds_and_cancellation(tmp_path):
    async def scenario():
        env = Environment()
        inspector = review.Inspector({'agent': env}, tmp_path / 'calls.jsonl')
        server = await asyncio.start_server(inspector.handle, '127.0.0.1', 0)
        port = server.sockets[0].getsockname()[1]
        async def call(token, **args):
            return await asyncio.to_thread(exchange, port, token, args)
        try:
            assert 'error' in await call('bad', command='id')
            assert 'error' in await call(inspector.token, command='id', environment='host')
            assert env.commands == []
            result = await call(inspector.token, command='id')
            assert len(result['output']) == 24000
            assert inspector.successful == 1
            assert json.loads(inspector.log.read_text())['command'] == 'id'
            inspector.calls = 40
            assert 'error' in await call(inspector.token, command='id')
            assert len(env.commands) == 1
        finally:
            server.close()
            await server.wait_closed()
            await inspector.close()
        assert not inspector.pending
    asyncio.run(scenario())


@pytest.mark.parametrize('mode', ['pass', 'no_inspection', 'bad_verdict', 'timeout'])
def test_review_evidence_and_cleanup(tmp_path, monkeypatch, mode):
    staged = tmp_path / 'staged'
    staged.mkdir()
    (staged / 'instruction.md').write_text('Inspect the task.')
    (staged / 'rubric-skips.json').write_text('{}')
    (staged / 'rubric.toml').write_text('[[criteria]]\nname="anti_cheat_robustness"\n')
    env = Environment()
    async def create(*args):
        return {'agent': env}
    monkeypatch.setattr(review, 'create_environments', create)
    monkeypatch.setattr(review.shutil, 'which', lambda command: '/bin/' + command)
    async def judge(command, prompt, cwd, environ, timeout):
        if mode == 'timeout':
            raise TimeoutError('judge timeout')
        if mode != 'no_inspection':
            config = json.loads(command[command.index('--mcp-config') + 1])['mcpServers']['image']
            result = await asyncio.to_thread(exchange, int(config['args'][-1]), config['env']['OT_REVIEW_INSPECT_TOKEN'], {'command': 'ls /app'})
            assert result['exit_code'] == 0
        if mode == 'bad_verdict':
            async def invalid(source, destination):
                destination.write_text('{invalid')
            env.download_file = invalid
        return 0, json.dumps({'is_error': False, 'total_cost_usd': 0.125}), ''
    monkeypatch.setattr(review, 'run_judge', judge)
    args = SimpleNamespace(review_local=False, dry_run=False, attempts=1, timeout_minutes=1,
                           review_model='anthropic/test', review_pricing=None, out=tmp_path / 'out')
    result = asyncio.run(review.review(tmp_path / 'source', staged, tmp_path / 'out', args, 'claude-code', 'anthropic/test'))
    assert result['status'] == ('passed' if mode == 'pass' else 'error')
    assert env.stopped
    assert env.commands[0] == 'rm -f /app/verdicts.json'
    if mode != 'timeout':
        assert result['usage']['trials'][0]['reported_cost_usd'] == 0.125
    if mode == 'no_inspection':
        assert 'No successful image inspection' in result['findings'][0]


def test_judge_timeout_reaps_process(tmp_path):
    import os
    async def scenario():
        pidfile = tmp_path / 'pid'
        code = f'import os,time;open({str(pidfile)!r},"w").write(str(os.getpid()));time.sleep(20)'
        with pytest.raises(TimeoutError):
            await review.run_judge([sys.executable, '-c', code], '', tmp_path, os.environ.copy(), .3)
        pid = int(pidfile.read_text())
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
    asyncio.run(scenario())


def test_worker_builds_actual_images_for_image_review(tmp_path):
    from hpc.validation_worker import review_runtime_tasks
    tasks = [tmp_path / 'task']
    assert review_runtime_tasks(tasks, [2], tmp_path, 'image') == tasks


def test_codex_forwards_inspector_authentication():
    from validation.rubrics.labeled_examples.charlie_hackable.run_reviews import judge_command
    command = judge_command('codex', 'test', 'medium', {'mcpServers': {'image': {'command': 'python', 'args': [], 'env': {'OT_REVIEW_INSPECT_TOKEN': 'test-token'}}}}, 5)
    assert 'mcp_servers.image.env.OT_REVIEW_INSPECT_TOKEN="test-token"' in command


@pytest.mark.parametrize('explicit', [False, True])
def test_legacy_contract_keeps_file_evidence(tmp_path, monkeypatch, explicit):
    from validation import contract
    frozen = {'stages': [5], 'arguments': {'tasks': str(tmp_path), 'out': str(tmp_path / 'out'), 'dry_run': False}}
    monkeypatch.setattr(contract, 'read', lambda path: frozen)
    monkeypatch.setattr(contract, 'verify_source', lambda *args: None)
    monkeypatch.setattr(contract, 'protocol', lambda *args: '')
    monkeypatch.setattr(sys, 'argv', ['validation'] + (['--review-evidence', 'image'] if explicit else []))
    args = SimpleNamespace(contract=tmp_path / 'contract.json', prepare_contract=None,
                           tasks=None, out=tmp_path, dry_run=False, review_evidence='image')
    if explicit:
        with pytest.raises(ValueError, match='Legacy contract'):
            contract.bind(args, [5])
    else:
        assert contract.bind(args, [5]) == [5]
        assert args.review_evidence == 'files'


def test_environment_creation_records_actual_context_and_cleans_failed_start(tmp_path, monkeypatch):
    from harbor.environments.factory import EnvironmentFactory
    from validation.stages import harbor, task_setup
    source = tmp_path / 'task'
    (source / 'environment').mkdir(parents=True)
    (source / 'environment/Dockerfile').write_text('FROM ubuntu:24.04\n')
    (source / 'instruction.md').write_text('Inspect.')
    (source / 'tests').mkdir()
    (source / 'tests/test.sh').write_text('#!/bin/sh\ntrue\n')
    (source / 'task.toml').write_text('version="1.0"\n[environment]\ncpus=1\nmemory_mb=1024\n')
    env = Environment()
    async def upload(**kwargs):
        assert kwargs['source_dir'] == source
        assert kwargs['target_dir'].endswith('/task')
    env.upload_dir = upload
    def create(**kwargs):
        assert kwargs['environment_dir'] == source / 'environment'
        assert kwargs['task_env_config'].cpus == 2
        return env
    monkeypatch.setattr(EnvironmentFactory, 'create_environment', create)
    monkeypatch.setattr(harbor, 'install_runtime_patches', lambda: None)
    async def prepare(*args, **kwargs):
        pass
    monkeypatch.setattr(task_setup, 'prepare', prepare)
    args = SimpleNamespace(backend='docker', environment_kwargs={}, force_build=False,
                           trial_cpus=2, trial_memory_mb=None)
    out = tmp_path / 'out'
    out.mkdir()
    result = asyncio.run(review.create_environments(source, out, args))
    assert result == {'agent': env}
    evidence = json.loads((out / 'image-evidence.json').read_text())[0]
    assert len(evidence['context_sha256']) == 64
    assert evidence['verifier_setup_executed'] is False
    async def fail(*args, **kwargs):
        raise RuntimeError('start failed')
    monkeypatch.setattr(task_setup, 'prepare', fail)
    with pytest.raises(RuntimeError, match='start failed'):
        asyncio.run(review.create_environments(source, out, args))
    assert env.stopped


def test_worker_reserves_all_concurrent_review_environments(monkeypatch):
    from hpc.validation_worker import trial_capacity
    from harbor.models.task import task as task_module
    from harbor.models.task import verifier_mode
    steps = [SimpleNamespace(cpus=3), SimpleNamespace(cpus=4)]
    config = SimpleNamespace(environment=SimpleNamespace(cpus=2), steps=steps)
    monkeypatch.setattr(task_module, 'Task', lambda path: SimpleNamespace(config=config))
    monkeypatch.setattr(verifier_mode, 'resolve_effective_verifier_env_config', lambda config, step: step)
    args = SimpleNamespace(trial_cpus=None, cpus=18, serve_model=None, concurrency=10)
    assert trial_capacity(['task'], args, image_review=True)['reserved_cpus_per_trial'] == 9
    assert trial_capacity(['task'], args, image_review=True)['max_concurrency'] == 2
    assert trial_capacity(['task'], args)['reserved_cpus_per_trial'] == 6
