import hashlib
import json
import os
from pathlib import Path
import subprocess

import pytest

from hpc.zih import runtime_storage as storage


def test_cached_runtime_never_needs_original_python(tmp_path, monkeypatch):
    source = tmp_path / 'old-venv'
    source.mkdir()
    config = 'home = /unavailable/home/python/bin\nversion_info = 3.12.14\n'
    (source / 'pyvenv.cfg').write_text(config)
    store = tmp_path / 'runtimes'
    store.mkdir()
    info = {'base': '/unavailable/home/python', 'version': '3.12.14 test',
            'purelib': str(source / 'site-packages'), 'platlib': str(source / 'site-packages')}
    key = hashlib.sha256((str(source) + info['base'] + info['version']).encode()).hexdigest()[:16]
    target = store / key
    target.mkdir()
    metadata = {'source': str(source), 'original': info}
    (target / 'ready.json').write_text(json.dumps(metadata))
    pointer_key = hashlib.sha256((str(source) + config).encode()).hexdigest()[:16]
    (store / ('environment-' + pointer_key + '.json')).write_text(json.dumps(metadata))
    monkeypatch.setattr(storage, 'data_path', lambda p: Path(p).resolve())
    def forbidden(*args, **kwargs):
        raise AssertionError('Cached runtime must not execute home Python')
    monkeypatch.setattr(storage, 'inspect_python', forbidden)
    result = storage.prepare(source, store)
    assert result['python'] == str(target / 'venv/bin/python')


def test_home_cannot_be_used_for_runtime_or_packages():
    with pytest.raises(ValueError, match='data workspace'):
        storage.data_path('/home/example/env')


@pytest.mark.parametrize('cluster,policy', [
    ('barnard', 'ssd_then_ram'), ('romeo', 'ssd_then_ram'), ('julia', 'ram')])
def test_cluster_partition_path_mapping(cluster, policy):
    script = Path(__file__).resolve().parents[1] / 'hpc/zih/storage_paths.sh'
    root = '/data/horse/ws/example/dataset'
    result = subprocess.run(['bash', '-c',
        'source "$1"; zih_storage_paths "$2" || exit; '
        'printf "%s\\n" "$ZIH_STATIC_POLICY" "$ZIH_RUNTIME_DIR" "$ZIH_CACHE_DIR"',
        'test', str(script), root], capture_output=True, text=True,
        env={**os.environ, 'SLURM_CLUSTER_NAME': cluster, 'SLURM_JOB_PARTITION': cluster})
    assert result.returncode == 0
    assert result.stdout.splitlines() == [policy, root + '/runtimes', root + '/cache']


def test_unmapped_partition_fails_instead_of_guessing():
    script = Path(__file__).resolve().parents[1] / 'hpc/zih/storage_paths.sh'
    result = subprocess.run(['bash', '-c', 'source "$1"; zih_storage_paths /data/horse/ws/example',
                             'test', str(script)], capture_output=True, text=True,
        env={**os.environ, 'SLURM_CLUSTER_NAME': 'julia', 'SLURM_JOB_PARTITION': 'unknown'})
    assert result.returncode == 2
    assert 'No ZIH storage mapping' in result.stderr
