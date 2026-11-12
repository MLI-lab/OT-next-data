"""CPU-only repair submission and the inference boundary to an independent GPU job."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from hpc.leak_repair_job import external_model
from validation.leak_repair import stage4
from validation.stages.runner import parser


def arguments(*extra):
    return parser().parse_args(['--leak-repair-model', 'qwen38-27b',
                               '--leak-repair-api-base', 'http://gpu-node:8000/v1', *extra])


def test_external_submission_uses_cpu_resources_and_preserves_task_contract(tmp_path, monkeypatch):
    import validation.contract
    contract = {'arguments': {'tasks': '/tasks.parquet', 'concurrency': 16, 'network_mode': 'simulated',
                              'trial_memory_mb': None, 'trial_cpus': None}, 'dataset': {'source': 'ScaleSWE'}}
    original = json.dumps(contract, sort_keys=True)
    monkeypatch.setattr(validation.contract, 'read', lambda path: contract)
    monkeypatch.setenv('OT_CLUSTER', 'helma')
    monkeypatch.setenv('OT_WORKSPACE', str(tmp_path))
    monkeypatch.setenv('OT_LEAK_REPAIR_API_KEY', 'private-test-key')
    commands = []
    monkeypatch.setattr(stage4.subprocess, 'check_output', lambda command, **kw: commands.append(command) or '77\n')
    args = arguments('--leak-repair-cpus', '192', '--leak-repair-memory', '512G')
    assert stage4.submit(args, tmp_path / 'contract.json', tmp_path, after='42') == '77'
    command = commands[0]
    assert '--partition=cpu' in command and '--cpus-per-task=192' in command and '--mem=512G' in command
    assert not any(arg.startswith('--gres') for arg in command)
    assert '--dependency=afterany:42' in command
    assert '--export=ALL,OT_LEAK_REPAIR_LOCAL=1' in command
    serialized = (tmp_path / 'leak-repair/request.json').read_text()
    request = json.loads(serialized)
    assert 'serve' not in request and 'private-test-key' not in serialized
    assert request['external_model']['model'] == 'openai/qwen38-27b'
    assert request['external_model']['agent_kwargs']
    assert request['network_mode'] == 'simulated'
    assert json.dumps(contract, sort_keys=True) == original


@pytest.mark.parametrize('url', ['file:///secret', 'http://user:secret@host/v1',
                               'http://host/v1?key=secret', 'http://host/v1#secret', 'missing-host'])
def test_endpoint_cannot_embed_credentials_or_use_non_http_transport(url):
    with pytest.raises(ValueError, match='HTTP'):
        stage4.external_model(arguments('--leak-repair-api-base', url))


def test_external_model_rejects_ambiguous_launch_settings():
    with pytest.raises(ValueError, match='serve-weights'):
        stage4.external_model(arguments('--leak-repair-serve-weights', '/weights'))
    with pytest.raises(ValueError, match='registered local'):
        stage4.external_model(arguments('--leak-repair-model', 'claude-sonnet-5-5'))


@pytest.fixture
def endpoint():
    state = {'model': 'qwen38-27b', 'status': 200, 'requests': []}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            state['requests'].append((self.path, self.headers.get('Authorization')))
            self.send_response(state['status'])
            self.end_headers()
            self.wfile.write(json.dumps({'data': [{'id': state['model']}]}).encode())

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield state, f'http://127.0.0.1:{server.server_port}/v1'
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_cpu_worker_checks_remote_model_and_authentication(endpoint, monkeypatch):
    state, url = endpoint
    monkeypatch.setenv('OT_LEAK_REPAIR_API_KEY', 'test-key')
    monkeypatch.setenv('OPENAI_API_KEY', 'old-key')
    monkeypatch.setenv('NO_PROXY', 'existing')
    monkeypatch.setenv('no_proxy', 'existing')
    settings = stage4.external_model(arguments('--leak-repair-api-base', url))
    assert external_model({'external_model': settings}) == settings
    assert state['requests'] == [('/v1/models', 'Bearer test-key')]
    import os
    assert os.environ['OPENAI_API_KEY'] == 'test-key'
    assert os.environ['NO_PROXY'] == 'existing,127.0.0.1'


def test_wrong_remote_model_is_not_silently_used(endpoint, monkeypatch):
    state, url = endpoint
    monkeypatch.setenv('OPENAI_API_KEY', 'EMPTY')
    monkeypatch.setenv('NO_PROXY', '')
    monkeypatch.setenv('no_proxy', '')
    state['model'] = 'different-model'
    settings = stage4.external_model(arguments('--leak-repair-api-base', url))
    with pytest.raises(RuntimeError, match='does not serve'):
        external_model({'external_model': settings})


def test_unavailable_server_fails_before_repair(endpoint, monkeypatch):
    state, url = endpoint
    monkeypatch.setenv('OPENAI_API_KEY', 'EMPTY')
    monkeypatch.setenv('NO_PROXY', '')
    monkeypatch.setenv('no_proxy', '')
    state['status'] = 401
    settings = stage4.external_model(arguments('--leak-repair-api-base', url))
    with pytest.raises(RuntimeError, match='unavailable from the CPU worker'):
        external_model({'external_model': settings})


def test_cannot_launch_two_model_backends():
    with pytest.raises(ValueError, match='both'):
        external_model({'external_model': {}, 'serve': {'model': 'qwen38-27b'}})


def test_service_inherits_resolver_exclusions_and_trial_counts():
    options = stage4.service_arguments({'static_profile': 'training', 'resolve_path_root': ['/'],
        'exclude': ['check_ai_detection.py=intentionally excluded by user policy'],
        'reference_runs': 1, 'nop_runs': 3, 'outcome_retries': 0, 'trial_memory_mb': None})
    assert options[options.index('--resolve-path-root') + 1] == '/'
    assert options[options.index('--exclude') + 1].startswith('check_ai_detection.py=')
    assert options[options.index('--nop-runs') + 1] == '3'
    assert '--trial-memory-mb' not in options


def test_zih_stage4_uses_zih_launcher():
    assert stage4.job_launcher('zih').as_posix().endswith('/hpc/zih/leak_repair.sbatch')
