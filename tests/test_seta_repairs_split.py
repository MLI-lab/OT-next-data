import tomllib

import pytest

from data.seta import patch


@pytest.mark.parametrize('environment', [
    '', '[environment]\ncpus = 1\n',
    '[environment]\nallow_internet = true # upstream needs internet\ncpus = 1\n',
    '[environment]\nallow_internet = false\ncpus = 1\n',
])
def test_offline_first_overrides_upstream_and_preserves_other_sections(environment):
    config = 'version = "1.0"\n' + environment + '[verifier]\ntimeout_sec = 60\n'
    files = {'task.toml': (config.encode(), 0o644), 'tests/test.sh': (b'original', 0o755)}
    result = patch.unpack(patch.offline_task(patch.pack(files)))
    parsed = tomllib.loads(result['task.toml'][0].decode())
    assert parsed['environment']['allow_internet'] is False
    assert parsed['verifier'] == {'timeout_sec': 60}
    assert result['tests/test.sh'] == files['tests/test.sh']
    assert patch.offline_task(patch.pack(result)) == patch.pack(result)


def test_conversion_does_not_apply_reference_memory_repair():
    task = next(iter(patch.REFERENCE_MEMORY_TARGETS))
    files = {'task.toml': (b'[environment]\nmemory = "2G"\n', 0o644),
             'instruction.md': (b'Original task instructions.\n', 0o644),
             'tests/test.sh': (b'#!/bin/bash\ntrue\n', 0o755)}
    result = patch.unpack(patch.conversion_only(patch.pack(files), task))
    assert b'memory = "2G"' in result['task.toml'][0]
    assert result['instruction.md'] == files['instruction.md']
    assert result['tests/test.sh'] == files['tests/test.sh']


def test_conversion_keeps_setup_memory_fix():
    files = {'task.toml': (b'[environment]\nmemory = "2G"\n', 0o644),
             'instruction.md': (b'Original task instructions.\n', 0o644),
             'tests/test.sh': (b'#!/bin/bash\ntrue\n', 0o755)}
    result = patch.unpack(patch.conversion_only(patch.pack(files), 'stack_overflow__synth__54789050'))
    assert b'memory = "6G"' in result['task.toml'][0]


def test_rocky_build_replaces_conflicting_minimal_packages_without_dropping_task():
    files = {'task.toml': (b'[environment]\ncpus = 1\n', 0o644),
             'instruction.md': (b'Original task.\n', 0o644),
             'tests/test.sh': (b'#!/bin/bash\ntrue\n', 0o755),
             'environment/Dockerfile': (patch.ROCKY_DOCKERFILE, 0o644)}
    result = patch.unpack(patch.conversion_only(patch.pack(files), 'rocky-task'))
    assert result['environment/Dockerfile'][0] == patch.ROCKY_DOCKERFILE.replace(
        b'dnf install -y ', b'dnf install -y --allowerasing ', 1)
    assert result['tests/test.sh'] == files['tests/test.sh']


def test_baseline_preserves_upstream_agent_pytest_selection():
    import json
    command = 'pip install pytest==7.4.0 pytest-json-ctrf'
    files = {'task.toml': (b'[environment]\ncpus = 1\n', 0o644),
             'instruction.md': (b'Original task.\n', 0o644),
             'environment/Dockerfile': (patch.SHARED_DOCKERFILE, 0o644),
             'setup_files/setup.sh': (command.encode(), 0o755),
             'setup_files/operations.json': (json.dumps({'operations': [{'op': 'RUN', 'command': command}]}).encode(), 0o644),
             'tests/test.sh': (b'uvx -p 3.13 -w pytest==8.4.1 pytest /tests/test_outputs.py\n', 0o755)}
    assert patch.setup_pytest_edits(files)
    assert patch.unpinned_setup_commands(files)
    result = patch.unpack(patch.conversion_only(patch.pack(files), 'baseline-task'))
    for name in ('setup_files/setup.sh', 'setup_files/operations.json', 'tests/test.sh'):
        assert result[name] == files[name]
