"""Source conversion remains independent from post-validation repairs."""
import tomllib

import pytest

from data.mimo import patch, repairs


def source_task(task='format-code-task-000035'):
    instruction = 'Implement the requested feature.\n'
    instance = {'instance_id': task, 'cwd': '/testbed', 'problem_statement': instruction,
        'docker_image': 'upstream:' + task, 'verifier_timeout_sec': 1800,
        'test_command': 'bash /testbed/mimo_test_command.sh',
        'test_patch': 'diff --git a/test.txt b/test.txt\n--- a/test.txt\n+++ b/test.txt\n@@ -1 +1 @@\n-old\n+new\n'}
    return {'prompt': [{'content': instruction}]}, instance, patch.IMAGE_DIGESTS['repository'] + ':' + task


def test_no_repairs_keeps_source_instruction_history_and_memory():
    source = source_task()
    base = patch.code_files(*source, repairs=False)
    repaired = patch.code_files(*source)
    assert base['instruction.md'] == source[1]['problem_statement'].encode()
    assert 'repository-leak-repair' not in base['environment/Dockerfile']
    assert tomllib.loads(base['task.toml'])['environment']['memory_mb'] == 4096
    assert tomllib.loads(repaired['task.toml'])['environment']['memory_mb'] == 8192
    assert tomllib.loads(base['task.toml'])['environment']['allow_internet'] is False
    assert tomllib.loads(repaired['task.toml'])['environment']['allow_internet'] is False
    assert 'repository-leak-repair-v1' in repaired['environment/Dockerfile']
    assert b'Do not cheat' in repaired['instruction.md']
    for name in base:
        if name.startswith('tests/'):
            assert base[name] == repaired[name]


def test_only_build_defects_remain_excluded_without_repairs():
    baseline = patch.exclusions(False)
    assert set(baseline) == set(patch.INCOMPLETE_PACKAGES)
    assert not set(baseline) & set(repairs.NOP_REVIEW)
    assert not set(baseline) & set(repairs.RESIDUE)
    assert set(patch.exclusions(True)) == set(baseline) | set(repairs.EXCLUDED)


def test_historical_repairs_do_not_stack_or_mutate_input():
    base = patch.code_files(*source_task(), repairs=False)
    before = dict(base)
    result = repairs.apply('format-code-task-000035', base)
    assert base == before
    with pytest.raises(ValueError, match='already contains'):
        repairs.apply('format-code-task-000035', result)
