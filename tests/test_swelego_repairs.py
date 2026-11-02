import copy
import json

from data.swelego import patch, repairs
from test_swelego_patch import fixture


def test_no_repairs_preserves_original_memory_and_instruction_policy():
    files, row, lock = fixture()
    row['instance_id'] = 'dask__dask-1150'
    files['task.toml'] += b'\n[agent]\ntimeout_sec = 1200\n'
    baseline = patch.patch_files(files, row, lock, apply_repairs=False)
    repaired = patch.patch_files(files, row, lock)
    assert b'memory_mb = 4096' in baseline['task.toml']
    assert b'memory_mb = 8192' in repaired['task.toml']
    assert b'You have 1200 seconds' not in baseline['instruction.md']
    assert b'You have 1200 seconds' in repaired['instruction.md']
    assert b'run `bash /setup_files/setup.sh`' not in repaired['instruction.md']
    assert b'allow_internet = false' in baseline['task.toml']


def test_mixed_build_and_verifier_fix_keeps_only_build_fix_in_baseline():
    task = 'holoviz__holoviews-6346'
    record = repairs.environment_repairs()[task]
    row = {'instance_id': task, 'base_commit': record['base_commit']}
    baseline = patch.environment_repair(row, apply_repairs=False)
    assert baseline['remove_directory_metadata'] == record['remove_directory_metadata']
    assert 'full_history' not in baseline
    assert patch.environment_repair(row)['full_history'] is True


def test_review_discovered_backend_pin_is_optional_but_build_pin_remains():
    task = 'statsmodels__statsmodels-8339'
    backend = patch.build_backends()[task]
    baseline = repairs.baseline_backend(task, backend)
    assert baseline['install'] == ['cython==0.29.37']
    assert 'setuptools-scm==6.4.2' in backend['install']


def test_no_repairs_omits_test_server_proxy_adjustments():
    _, row, lock = fixture()
    row['repo'] = 'cuenca-mx/cuenca-python'
    baseline, _ = patch.select_image(row, lock, apply_repairs=False)
    repaired, _ = patch.select_image(row, lock)
    assert '_no_proxy' not in baseline
    assert repaired['_no_proxy'] == 'api.cuenca.com,sandbox.cuenca.com'
