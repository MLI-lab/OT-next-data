"""Task-quality preparation is opt-in through stage 4; environment-only tasks stay unchanged."""
from types import SimpleNamespace

import pytest

from validation import contract


@pytest.mark.parametrize('enabled', [True, False])
def test_stage3_and_final_bind_identical_tasks(tmp_path, monkeypatch, enabled):
    source = tmp_path / 'source' / 'sample'
    source.mkdir(parents=True)
    (source / 'instruction.md').write_text('Print hello.\n')
    (source / 'task.toml').write_text('[agent]\ntimeout_sec = 120\n')
    original = contract.task_digest(source)
    captured = []

    def create(args, stages, destination):
        captured.append(contract.inventory(args.tasks)[0])

    monkeypatch.setattr(contract, 'create', create)
    for label, stages in [('pilot', [1]), ('final', [1, 2, 3, 4])]:
        args = SimpleNamespace(tasks=source.parent, prepare_contract=tmp_path / (label + '.json'),
                               contract=None, prebuild_only=False, limit=None, task_id_range=None,
                               fix_instruction_suffix=enabled, fix_instruction_paths=False,
                               path_resolution_report=[])
        contract.bind(args, stages)
    assert captured[0][0]['sha256'] == original
    assert (captured[1][0]['sha256'] != original) == enabled
    assert contract.task_digest(source) == original
