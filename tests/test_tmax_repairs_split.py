import pytest
from data.tmax import patch as repairs


@pytest.mark.parametrize('declaration', ['allow_internet = true\n', 'allow_internet = false\n', ''])
def test_offline_first_policy_overrides_explicit_or_missing_upstream_setting(declaration):
    import tomllib
    from data.tmax.patch import set_network_policy
    content = '[agent]\ntimeout_sec = 600\n\n[environment]\n' + declaration + 'cpus = 1\n'
    files = {'task.toml':(content.encode(),0o664)}
    assert not set_network_policy(files, 'example')
    config = tomllib.loads(files['task.toml'][0].decode())
    assert config['environment'] == {'cpus':1,'allow_internet':False}
    assert config['agent']['timeout_sec'] == 600


def test_only_four_upstream_defects_are_optional():
    from data.tmax import repairs as upstream
    assert set(upstream.TASK_REPAIRS) == {
        'task_003383_a3d7928b', 'task_001217_56df613c',
        'task_000161_a8d0d6f0', 'task_004436_d43bdd17',
    }
    for task, (name, before, after, count, reason) in upstream.TASK_REPAIRS.items():
        files = {name: ((before + '\n').encode() * count, 0o644)}
        original = dict(files)
        repairs.apply_base_changes(files, task)
        assert files == original
        assert upstream.apply(files, task)['task']
        assert files[name][0] == ((after + '\n').encode() * count)


@pytest.mark.parametrize('task', sorted(repairs.VERIFIER_REPAIRS))
def test_verifier_changes_remain_in_base_patch(task):
    from pathlib import Path
    from data.tmax import repairs as upstream
    source = (Path(__file__).parent / 'fixtures/tmax_verifiers' / (task + '.py.txt')).read_bytes()
    files = {'tests/test_final_state.py': (source, 0o644)}
    assert not upstream.apply(files, task)['task']
    assert files['tests/test_final_state.py'][0] == source
    assert repairs.apply_base_changes(files, task)['task']
    assert files['tests/test_final_state.py'][0] != source


@pytest.mark.parametrize('no_repairs', [False, True])
def test_patcher_preserves_instructions_and_modes_for_pipeline(tmp_path, monkeypatch, no_repairs):
    import sys
    import json
    import pyarrow as pa
    import pyarrow.parquet as pq
    from data.utils.harbor_parquet import pack_task
    # This ID previously had a historical absolute-path edit. The patcher must
    # not consult that catalog, even when its instruction is different now.
    task = 'task_000023_97c7568e'
    instruction = b'Edit src/main.rs.  \n\n'
    files = {
        'instruction.md': (instruction, 0o664),
        'environment/Dockerfile': (repairs.LEGACY_DOCKERFILE.encode(), 0o664),
        'environment/post_install.sh': (b'apt-get update && apt-get install -y python3\nmkdir -p /app\n', 0o755),
        'task.toml': (b'[environment]\ncpus = 1\nmemory_mb = 1024\n', 0o664),
        'tests/test_final_state.py': (b'assert True\n', 0o664),
    }
    source = tmp_path / 'source.parquet'
    pq.write_table(pa.table({'path': [task], 'task_binary': [pack_task(files)]}), source)
    output = tmp_path / 'output'
    monkeypatch.setattr(repairs, 'TASKS', 1)
    monkeypatch.setattr(repairs, 'UNSHARED', {})
    monkeypatch.setattr(repairs, 'ARCHIVED', {})
    monkeypatch.setattr(repairs, 'SERVICE_SETUP', {})
    monkeypatch.setattr(repairs, 'runtime_setup_trigger', lambda: False)
    monkeypatch.setattr(sys, 'argv', ['patch.py', '--source', str(source), '--output', str(output),
                                    '--runtime-setup', 'off'] + (['--no-repairs'] if no_repairs else []))
    repairs.main()
    row = pq.read_table(output / 'tasks.parquet').to_pylist()[0]
    actual = repairs.read_task(row['task_binary'])
    assert actual['instruction.md'] == files['instruction.md']
    assert actual['tests/test_final_state.py'] == files['tests/test_final_state.py']
    manifest = json.loads((output / 'tasks.manifest.json').read_text())
    assert [item['path'] for item in manifest['patcher_dependencies']] == ['repairs.py']
    assert not (output / 'published-reproduction.json').exists()
