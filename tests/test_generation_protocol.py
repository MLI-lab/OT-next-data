import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from teacher_traces import generation_protocol as protocol


@pytest.fixture
def inputs(tmp_path):
    model = tmp_path / 'model'
    model.mkdir()
    (model / 'tokenizer_config.json').write_text(json.dumps({'chat_template': '{{ messages }}'}))
    (model / 'download_complete.json').write_text(json.dumps({'revision': 'fixed-revision'}))
    task = tmp_path / 'tasks/example'
    for name in ['environment', 'tests']:
        (task / name).mkdir(parents=True)
    (task / 'task.toml').write_text('schema_version="1.0"\n[agent]\ntimeout_sec=123\n')
    (task / 'instruction.md').write_text('Produce an answer.')
    (task / 'environment/Dockerfile').write_text('FROM ubuntu:24.04\nWORKDIR /app\n')
    (task / 'tests/test.sh').write_text('echo 1 > /logs/verifier/reward.txt\n')
    cfg = {'agents': [{'name': 'terminus-2', 'kwargs': {'temperature': 0.7, 'max_tokens': 8192, 'max_episodes': 17}}]}
    serving = {'vllm_server': {'max_model_len': 32768, 'reasoning_parser': 'qwen3'}}
    (tmp_path / 'harbor.yaml').write_text(yaml.safe_dump(cfg))
    (tmp_path / 'serving.yaml').write_text(yaml.safe_dump(serving))
    return {'model': str(model), 'task_ids': ['example'], 'tasks_dir': str(task.parent),
            'harbor_config': str(tmp_path / 'harbor.yaml'), 'serving_config': str(tmp_path / 'serving.yaml')}


def test_capture_freezes_prompts_timeouts_verifier_and_model_template(inputs, tmp_path):
    pytest.importorskip('harbor')
    result = protocol.capture(inputs, tmp_path / 'protocol')
    assert result['agent']['effective_max_turns'] == 17
    assert result['limits']['server_context_tokens'] == 32768
    assert result['model']['revision'] == 'fixed-revision'
    assert result['model']['chat_template']['text'] == '{{ messages }}'
    assert result['agent']['prompt_template']
    assert result['tasks'][0]['resolved_task_config']['agent']['timeout_sec'] == 123
    assert 'tests/test.sh' in result['tasks'][0]['reward_implementation']
    assert result['sha256'] == protocol.digest({k: v for k, v in result.items() if k != 'sha256'})
    with pytest.raises(FileExistsError):
        protocol.capture(inputs, tmp_path / 'protocol')


def test_template_and_reward_changes_are_detectable(inputs, tmp_path):
    pytest.importorskip('harbor')
    first = protocol.capture(inputs, tmp_path / 'first')
    Path(inputs['model'], 'chat_template.jinja').write_text('Changed template {{ messages }}')
    Path(inputs['tasks_dir'], 'example/tests/test.sh').write_text('echo 0 > /logs/verifier/reward.txt\n')
    second = protocol.capture(inputs, tmp_path / 'second')
    assert first['model']['chat_template']['sha256'] != second['model']['chat_template']['sha256']
    assert first['tasks'][0]['reward_implementation'] != second['tasks'][0]['reward_implementation']


def test_missing_template_prevents_collection(inputs, tmp_path):
    Path(inputs['model'], 'tokenizer_config.json').write_text('{}')
    with pytest.raises(ValueError, match='chat template'):
        protocol.capture(inputs, tmp_path / 'failed')


def test_record_redacts_credentials_without_redacting_token_limits():
    assert protocol.redact({'api_key': 'secret', 'max_tokens': 8192, 'extra_env': {'OPENAI_API_KEY': 'secret'}}) == {'api_key': '<redacted>', 'max_tokens': 8192, 'extra_env': {'OPENAI_API_KEY': '<redacted>'}}
