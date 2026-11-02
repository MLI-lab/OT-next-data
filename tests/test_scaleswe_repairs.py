"""Keep the audit baseline independent of execution-derived repairs."""
import json
import sys

import pyarrow.parquet as pq
import pytest

from data.scaleswe import patch, repairs
from data.utils.harbor_parquet import pack_task, write_tasks


def test_no_repairs_preserves_required_tests_byte_for_byte():
    task = 'dwavesystems_dimod_pr432'
    blob = pack_task({'instruction.md': 'Fix the bug.',
        'tests/test_ids.json': json.dumps([*repairs.SKIPPED_REQUIRED_TESTS[task], 'tests/test_other.py::test_it'])})
    repaired, edits = patch.patch_blob(blob, task_id=task)
    assert edits and repaired != blob
    assert (repaired, edits) == repairs.patch_blob(blob, task_id=task)
    assert patch.patch_blob(blob, task_id=task, apply_repairs=False) == (blob, [])
    with pytest.raises(ValueError, match='Path repairs'):
        patch.patch_blob(blob, automatic_paths=True, apply_repairs=False)


def test_cli_no_repairs_keeps_historical_exclusions(tmp_path, monkeypatch):
    source = tmp_path / 'source.parquet'
    task = 'kurtbrose_pyjks_pr19'
    assert task in repairs.ARCHIVED_TASKS
    write_tasks([(task, {'instruction.md': 'Fix the bug.', 'task.toml': 'version = "1.0"\n',
                        'environment/Dockerfile': 'FROM scratch\n'})], source)
    output = tmp_path / 'baseline'
    monkeypatch.setattr(sys, 'argv', ['patch.py', '--source', str(source), '--output', str(output), '--no-repairs'])
    patch.main()
    result = pq.read_table(output / 'tasks.parquet').to_pylist()[0]
    original = pq.read_table(source).to_pylist()[0]
    assert result['path'] == original['path']
    before, after = patch.archive_files(original['task_binary']), patch.archive_files(result['task_binary'])
    assert {k: v for k, v in before.items() if k != 'task.toml'} == {k: v for k, v in after.items() if k != 'task.toml'}
    assert b'allow_internet = false' in after['task.toml'][0]
    assert len(pq.read_table(output / 'tasks.parquet')['content_sha256'][0].as_py()) == 64
    assert pq.read_table(output / 'tasks.archive.parquet').num_rows == 0


def test_historical_instruction_replay_is_idempotent_and_separate():
    blob = pack_task({'instruction.md': 'Fix the bug.\n'})
    repaired, edits = repairs.apply_release_instructions(blob, 'example')
    assert edits[0]['phase'] == 'anti-cheat-instruction'
    assert b'You have 1200 seconds' in patch.archive_files(repaired)['instruction.md'][0]
    assert repairs.apply_release_instructions(repaired, 'example') == (repaired, [])
    assert patch.patch_blob(blob, task_id='example', apply_repairs=False) == (blob, [])


@pytest.mark.parametrize('text', [
    '[environment]\nallow_internet = true\n[environment.healthcheck]\ncommand = "true"\n',
    '[environment]\nallow_internet = false\n',
    '[environment]\ncpus = 4\n',
    'version = "1.0"\n',
])
def test_offline_policy_overrides_true_and_unset_and_is_idempotent(text):
    import tomllib
    encoded = patch.offline_config(text.encode())
    assert tomllib.loads(encoded.decode())['environment']['allow_internet'] is False
    assert patch.offline_config(encoded) == encoded
