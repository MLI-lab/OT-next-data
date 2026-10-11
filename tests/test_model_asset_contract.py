"""Local model contracts survive changes to the submitting shell's environment."""
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from validation import contract
from validation.stages.runner import parser


@pytest.mark.parametrize('empty_directory', [False, True])
def test_missing_model_assets_rejected(tmp_path, monkeypatch, empty_directory):
    monkeypatch.setenv('OT_MODELS', str(tmp_path))
    weights = tmp_path / 'Qwen3-30B-A3B-Instruct-2507'
    if empty_directory:
        weights.mkdir()
    args = SimpleNamespace(serve_model='qwen3-30b-instruct-2507', serve_weights=None)
    with pytest.raises(ValueError, match='local model assets missing'):
        contract.local_model_assets(args)


def test_prepared_model_path_survives_environment_change(tmp_path, monkeypatch):
    task = tmp_path / 'task'
    task.mkdir()
    (task / 'task.toml').write_text('schema_version = "1.0"\n')
    (task / 'instruction.md').write_text('Write the answer.\n')
    models = tmp_path / 'models'
    weights = models / 'Qwen3-30B-A3B-Instruct-2507'
    weights.mkdir(parents=True)
    (weights / 'config.json').write_text('{}')
    monkeypatch.setenv('OT_MODELS', str(models))
    args = parser().parse_args([str(task), '--serve-model', 'qwen3-30b-instruct-2507'])
    path = tmp_path / 'contract.json'
    frozen = contract.create(args, [6, 7], path)
    assert args.serve_weights == weights
    assert frozen['arguments']['serve_weights'] == str(weights)
    assert frozen['execution_profile']['serve_weights'] == str(weights)
    monkeypatch.setenv('OT_MODELS', str(tmp_path / 'wrong-models'))
    monkeypatch.setenv('OT_WORKSPACE', str(tmp_path / 'wrong-workspace'))
    contract.verify_source(args, contract.read(path))
    # Exercise the CLI's reconstruction, not just the preparing Namespace.
    replay = parser().parse_args(['--contract', str(path)])
    monkeypatch.setattr(sys, 'argv', ['run.py', '--contract', str(path)])
    assert contract.bind(replay, [6, 7]) == [6, 7]
    assert Path(replay.serve_weights) == weights
    (weights / 'config.json').write_text('{"changed": true}')
    with pytest.raises(ValueError, match='local serving assets differ'):
        contract.verify_source(replay, contract.read(path))
