import hashlib

import pytest

from data.scaleswe.patch import (
    archive_files, environment_archive_stage, environment_repair_stage, restore_source_instruction,
)
from data.utils.full_source.harbor_parquet import pack_task


def test_restore_instruction_preserves_runtime_repairs_and_file_modes():
    original = b'Edit the package.\n'
    files = {
        'instruction.md': original,
        'tests/test.sh': (b'original verifier\n', 0o755),
        'environment/Dockerfile': b'FROM registry/task:123\n',
    }
    baseline = pack_task(files)
    files['instruction.md'] = b'Edit /workspace/package.\nExtra quality sentence.\n'
    files['tests/test.sh'] = (b'repaired verifier\n', 0o755)
    restored = restore_source_instruction(pack_task(files), original)
    actual = archive_files(restored)
    assert actual['instruction.md'][0] == original
    assert actual['tests/test.sh'] == (b'repaired verifier\n', 0o755)
    assert actual['environment/Dockerfile'] == archive_files(baseline)['environment/Dockerfile']


def test_instruction_only_task_reproduces_original_archive_exactly():
    original = {'instruction.md': b'Original\n', 'tests/test.sh': (b'test\n', 0o755)}
    payload = pack_task(original)
    changed = pack_task({**original, 'instruction.md': b'Edited\n'})
    assert hashlib.sha256(restore_source_instruction(changed, b'Original\n')).digest() == hashlib.sha256(payload).digest()


@pytest.mark.parametrize('label,stage', [
    ('verifier-no-op-passes', 5),
    ('task-changes-only-test-files', 5),
    ('verifier-timeout-without-fix', 5),
    ('image-missing-test-dependency', 4),
    ('verifier-network-dependency', 4),
])
def test_exclusion_attribution_preserves_historical_stage(label, stage):
    row = {'path': 'task', 'archive_stage': 0, 'archive_labels': ['patch-drop:' + label]}
    assert environment_archive_stage(row) == stage
    assert row['archive_stage'] == 0


def test_unknown_exclusion_requires_review():
    with pytest.raises(ValueError, match='Unclassified'):
        environment_archive_stage({'path': 'task', 'archive_labels': ['new-category']})


def test_repair_attribution_distinguishes_import_failures_and_noop_leakage():
    task = {'task_id': 'task', 'labels': ['verifier-import-path'], 'reason': 'Missing test helpers'}
    assert environment_repair_stage(task) == 4
    assert environment_repair_stage({**task, 'reason': 'Installed copy made no-op pass'}) == 5
    assert environment_repair_stage({**task, 'labels': ['instruction-path-clarified']}) is None
    with pytest.raises(ValueError, match='Unclassified'):
        environment_repair_stage({**task, 'labels': ['new-runtime-change']})
