import importlib.util
import json
from pathlib import Path
import subprocess

import pytest

PATH = Path(__file__).resolve().parents[1] / 'validation/rubrics/old/labeled_examples/charlie_hackable/run_reviews.py'
spec = importlib.util.spec_from_file_location('charlie_reviews', PATH)
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


@pytest.mark.parametrize('prefix, separator', [('', '\n'), ('Review follows.\n', '\n'), ('Review follows.\\n', '\\n')])
def test_accepts_single_fenced_verdict_with_prose(prefix, separator):
    verdict = {'checks': {'anti_cheat_robustness': {'outcome': 'pass', 'explanation': 'No leak found.'}}}
    text = prefix + '```json' + separator + json.dumps(verdict) + separator + '```'
    assert runner.parse_answer(text) == verdict


def test_rejects_ambiguous_multiple_fenced_answers():
    with pytest.raises(ValueError, match='one JSON verdict'):
        runner.parse_answer('```json\n{}\n```\n```json\n{}\n```')


@pytest.mark.parametrize('compact', [False, True])
def test_file_only_worker_uses_tools_without_pulling_or_removing_image(tmp_path, monkeypatch, compact):
    run = tmp_path / 'run'
    run.mkdir()
    (run / 'plan.json').write_text(json.dumps({'tasks': [{'directory': 'task-0001', 'task_id': 'example', 'image': 'unused/task:tag'}]}))
    (run / 'prompt.txt').write_text('Inspect task files.')
    (run / 'rubric.toml').write_text('unused by mocked schema reader')
    image = tmp_path / 'neutral.sif'
    image.write_bytes(b'neutral image')
    monkeypatch.setenv('TMPDIR', str(tmp_path))
    commands = []
    verdict = {'checks': {'anti_cheat_robustness': {'outcome': 'fail', 'explanation': 'An answer is copied by the Dockerfile.'}}}
    out = run / 'results/task-0001'

    def execute(command, **kwargs):
        commands.append(command)
        if command[0] == 'claude':
            assert 'mcp__image__inspect' in command
            assert '--system-prompt' not in command
            server_args = json.loads(command[command.index('--mcp-config') + 1])['mcpServers']['image']['args']
            output_dir = Path(server_args[server_args.index('--output-dir') + 1])
            log = Path(server_args[server_args.index('--log') + 1])
            (output_dir / 'verdicts.json').write_text(json.dumps(verdict))
            log.write_text(json.dumps({'command': 'cat /review/task/environment/Dockerfile', 'exit_code': 0}) + '\n')
            payload = {'is_error': False, 'result': 'Wrote /app/verdicts.json.', 'total_cost_usd': 0.125}
            return subprocess.CompletedProcess(command, 0, json.dumps(payload), '')
        assert command[:2] == ['apptainer', 'exec']
        assert str(image) in command
        assert command[command.index('--pwd') + 1] == '/app'
        return subprocess.CompletedProcess(command, 0, '', '')

    monkeypatch.setattr(runner.subprocess, 'run', execute)
    monkeypatch.setattr(runner, 'read_verdicts', lambda path, rubric: json.loads(path.read_text()))
    assert runner.worker(run, login_node=True, evidence='files', review_image=image,
                         compact=compact, summary_copy=tmp_path / 'published.json') == 0
    if compact:
        assert (run / 'summary.json').is_symlink()
        assert not out.exists()
        assert not list(tmp_path.glob('image-review-*'))
    assert image.read_bytes() == b'neutral image'
    assert all(command[:2] != ['apptainer', 'pull'] for command in commands)
    summary = json.loads((run / 'summary.json').read_text())
    assert summary['completed'] == summary['flagged'] == 1
    assert summary['cost']['reported_cost_usd'] == summary['cost']['average_per_task_usd'] == 0.125
    previous_calls = len(commands)
    assert runner.worker(run, login_node=True, evidence='files', review_image=image,
                         compact=compact, summary_copy=tmp_path / 'published.json', resume=True) == 0
    assert len(commands) == previous_calls
    resumed = json.loads((run / 'summary.json').read_text())
    assert resumed['results'] == summary['results']
    assert resumed['cost'] == summary['cost']


def test_codex_command_uses_medium_and_valid_toml(monkeypatch):
    import tomllib
    monkeypatch.setenv('PYTHONPATH', '/example/repo')
    command = runner.judge_command('codex', 'gpt-6-astra', 'medium',
                                   {'mcpServers': {'image': {'command': '/bin/python', 'args': ['server.py']}}}, 5)
    settings = {}
    for index, item in enumerate(command):
        if item == '-c':
            assignment = command[index + 1]
            key, value = assignment.split('=', 1)
            settings[key] = tomllib.loads('value=' + value)['value']
    assert settings['model_reasoning_effort'] == 'medium'
    assert settings['features.shell_tool'] is False
    assert settings['mcp_servers.image.env.PYTHONPATH'] == '/example/repo'
    assert settings['mcp_servers.image.required'] is True
    assert '--ignore-user-config' in command


def test_codex_cost_counts_cached_input_once():
    output = json.dumps({'type': 'turn.completed', 'usage': {
        'input_tokens': 1000, 'cached_input_tokens': 800, 'output_tokens': 100}})
    envelope = runner.codex_envelope(output, 'gpt-6-astra')
    assert not envelope['is_error']
    assert envelope['total_cost_usd'] == pytest.approx(0.0078)
    assert 'estimate' in envelope['cost_basis']
    failed = runner.codex_envelope(json.dumps({'type': 'turn.failed', 'error': {'message': 'Unavailable'}}), 'gpt-6-astra')
    assert failed['is_error']
    assert failed['total_cost_usd'] is None


def test_session_limit_is_preserved_and_stops_before_next_task(tmp_path, monkeypatch):
    run = tmp_path / 'run'
    run.mkdir()
    tasks = [{'directory': f'task-{i:04d}', 'task_id': f'task-{i}', 'image': 'unused'} for i in [1, 2]]
    (run / 'plan.json').write_text(json.dumps({'tasks': tasks}))
    (run / 'prompt.txt').write_text('Review task.')
    (run / 'rubric.toml').write_text('unused')
    image = tmp_path / 'image.sif'
    image.write_bytes(b'image')
    monkeypatch.setenv('TMPDIR', str(tmp_path))
    calls = []
    def execute(command, **kwargs):
        if command[0] == 'claude':
            calls.append(command)
            return subprocess.CompletedProcess(command, 1, json.dumps({
                'is_error': True, 'result': "You've hit your session limit · resets 6:30pm (Europe/Berlin)",
                'total_cost_usd': 0}), '')
        return subprocess.CompletedProcess(command, 0, '', '')
    monkeypatch.setattr(runner.subprocess, 'run', execute)
    result = runner.worker(run, login_node=True, evidence='files', review_image=image,
                           compact=True, summary_copy=tmp_path / 'published.json', continue_on_error=True)
    assert result == 2
    assert len(calls) == 1
    summary = json.loads((run / 'summary.json').read_text())
    assert summary['status'] == 'waiting_for_reset'
    assert '6:30pm' in summary['limit_message']
    assert summary['completed'] == 0
    assert not list(tmp_path.glob('image-review-*'))
