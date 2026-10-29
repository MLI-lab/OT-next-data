import json
from pathlib import Path

import pytest

from data.patch_repair_loop import controller as core, isolation


@pytest.fixture
def loop(tmp_path, monkeypatch):
    source = tmp_path / 'repo'
    patcher = source / 'data/example/patch.py'
    patcher.parent.mkdir(parents=True)
    patcher.write_text('# unfinished dataset repair\n')
    (source / 'validation').mkdir()
    (source / 'validation/runner.py').write_text('# runner v1\n')
    work = tmp_path / 'work'
    config = {'work_root': str(work), 'patcher': str(patcher), 'source': '/data/source.parquet',
              'patch_command': ['{python}', str(patcher), '--source', '{source}']}
    config_path = tmp_path / 'config.json'
    core.save(config_path, config)
    core.save(work / 'loop-state.json', {'config_sha256': core.config_digest(config),
              'status': 'blocked', 'phase': 'fixer', 'generation': 11, 'history': [{'job_id': '123'}]})
    monkeypatch.setattr(core, 'ROOT', source)
    return source, config_path, work, tmp_path / 'private'


def test_isolation_keeps_repairs_and_jobs_but_separates_code(loop, monkeypatch):
    source, config_path, work, private = loop
    hashes = core.dataset_hashes(json.loads(config_path.read_text()))
    core.save(source / '.patch-repair-infra/revision.json', {'revision': 'accepted-fix'})
    isolation.prepare(config_path, private)
    config = json.loads(config_path.read_text())
    state = json.loads((work / 'loop-state.json').read_text())
    assert config['patcher'] == str(private / 'data/example/patch.py')
    assert config['patch_command'][1] == config['patcher']
    assert config['source'] == '/data/source.parquet'
    assert Path(config['patcher']).read_text() == '# unfinished dataset repair\n'
    assert state['config_sha256'] == core.config_digest(config)
    assert state['generation'] == 11 and state['history'] == [{'job_id': '123'}]
    assert core.dataset_hashes(config) == hashes
    assert json.loads((private / '.patch-repair-infra/revision.json').read_text())['revision'] == 'accepted-fix'
    (private / 'validation/runner.py').write_text('# isolated repair\n')
    assert (source / 'validation/runner.py').read_text() == '# runner v1\n'
    # Another loop can retain a shared lock while this loop repairs its copy.
    from data.patch_repair_loop import recovery
    with recovery.infrastructure_lock():
        monkeypatch.setattr(core, 'ROOT', private)
        with recovery.infrastructure_lock(exclusive=True):
            assert isolation.prepare(config_path) == private
    Path(config['patcher']).write_text('# new repair\n')
    assert core.dataset_hashes(config) != hashes


@pytest.mark.parametrize('lock_name', ['.controller.lock', '.supervisor.lock'])
def test_live_loop_cannot_be_migrated(loop, lock_name):
    source, config_path, work, private = loop
    original = config_path.read_bytes()
    with core.lock(work / lock_name):
        with pytest.raises(RuntimeError, match='Another controller'):
            isolation.prepare(config_path, private)
    assert config_path.read_bytes() == original
    assert not private.exists()


def test_cannot_reuse_another_loops_checkout(loop):
    source, config_path, work, private = loop
    core.save(private / '.repair-loop.json', {'work_root': '/another/run'})
    with pytest.raises(ValueError, match='unrelated'):
        isolation.prepare(config_path, private)
