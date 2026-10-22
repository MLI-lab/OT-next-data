"""Reviewed 2026-10-17 reference repairs: missing tools and measured limits."""
import tomllib

import pytest

from data.facet.patch import install_missing_tools, measured_resource_limits, RESOURCE_LIMITS

DOCKERFILE = {'environment/Dockerfile': b'FROM ubuntu:22.04\nWORKDIR /task_file\n'}
TOML = (b'version = "1.0"\n\n[verifier]\ntimeout_sec = 120.0\n\n[agent]\ntimeout_sec = 120.0\n\n'
        b'[environment]\nbuild_timeout_sec = 600.0\ncpus = 1\nmemory_mb = 2048\nstorage_mb = 10240\n')


@pytest.mark.parametrize('task_id,package', [('task_000701', 'python3 python3-pip'),
                                             ('task_007718', 'python3 python3-pip'),
                                             ('task_002689', 'r-cran-jsonlite'), ('task_003456', 'binutils')])
def test_new_missing_tools(task_id, package):
    text = install_missing_tools(DOCKERFILE, task_id)['environment/Dockerfile'].decode()
    assert f'--no-install-recommends {package} &&' in text and text.startswith('FROM ubuntu:22.04\n')


@pytest.mark.parametrize('task_id', sorted(RESOURCE_LIMITS))
def test_measured_limits(task_id):
    limits = RESOURCE_LIMITS[task_id]
    parsed = tomllib.loads(measured_resource_limits({'task.toml': TOML}, task_id)['task.toml'].decode())
    assert parsed['agent']['timeout_sec'] == limits['agent_timeout_sec']
    assert parsed['environment']['cpus'] == limits['cpus']
    assert parsed['environment']['memory_mb'] == limits['memory_mb']
    assert parsed['verifier']['timeout_sec'] == 120.0 and parsed['environment']['storage_mb'] == 10240


def test_measured_limits_leave_other_tasks_and_refuse_changed_upstream():
    assert measured_resource_limits({'task.toml': TOML}, 'task_000001') == {'task.toml': TOML}
    with pytest.raises(ValueError, match='measure again'):
        measured_resource_limits({'task.toml': TOML.replace(b'cpus = 1', b'cpus = 2')}, 'task_000536')


def test_measured_memory_uses_sizes_the_resource_check_allows():
    assert {limits['memory_mb'] for limits in RESOURCE_LIMITS.values()} <= {1024, 2048, 4096, 8192, 16384, 32768}


def test_verifier_precheck_removed_only_for_reviewed_task():
    from data.facet.patch import remove_verifier_precheck, VERIFIER_PRECHECKS
    script = '#!/bin/bash\nset +e\nmkdir -p /logs/verifier\n' + VERIFIER_PRECHECKS['task_006168'] + '\nuvx pytest /tests/test_state.py\n'
    files = {'tests/test.sh': script.encode()}
    assert remove_verifier_precheck(files, 'task_000001') == files
    patched = remove_verifier_precheck(files, 'task_006168')['tests/test.sh'].decode()
    assert 'Fast pre-check' not in patched and 'exit 0' not in patched and 'uvx pytest /tests/test_state.py' in patched
    assert remove_verifier_precheck({'tests/test.sh': patched.encode()}, 'task_006168')['tests/test.sh'] == patched.encode()
