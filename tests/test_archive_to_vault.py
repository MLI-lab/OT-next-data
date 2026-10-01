import importlib.util
import json
import os
import time
from pathlib import Path

import pytest


@pytest.fixture
def tool(monkeypatch, tmp_path):
    path = Path(__file__).resolve().parents[1] / 'hpc/helma/archive_to_vault.py'
    spec = importlib.util.spec_from_file_location('archive_to_vault_test', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setenv('HOME', str(tmp_path / 'home'))
    run = tmp_path / 'home/repo/runs/run-1'
    (run / 'cache/locked').mkdir(parents=True)
    (run / 'result.json').write_text('{"ok": true}\n')
    (run / 'cache/locked/blob').write_text('x' * 1000)
    (run / 'cache/locked').chmod(0o555)
    (tmp_path / 'vault').mkdir()
    return mod, run, tmp_path / 'vault'


def test_round_trip_removes_then_restores_the_directory(tool):
    mod, run, vault = tool
    record = mod.archive(run, vault)
    marker = run.with_name('run-1.archived.json')
    assert not run.exists()
    assert record['archive'] == str(vault / 'repo/runs/run-1.tar.gz') and record['entries'] == 5
    assert json.loads(marker.read_text())['sha256'] == mod.sha256(record['archive'])
    mod.restore(run)
    assert (run / 'result.json').read_text() == '{"ok": true}\n'
    assert (run / 'cache/locked/blob').read_text() == 'x' * 1000
    assert not marker.exists() and Path(record['archive']).exists()


def test_recent_and_kept_directories_stay(tool, capsys):
    mod, run, vault = tool
    assert mod.archive(run, vault, idle_days=1) is None
    assert 'skipped' in capsys.readouterr().out and not list(vault.iterdir())
    assert mod.archive(run, vault, keep=True)['entries'] == 5
    assert run.is_dir() and not run.with_name('run-1.archived.json').exists()
    # The kept archive is reused, and only while it still matches the directory.
    old = time.time() - 5 * 86400
    (run / 'result.json').write_text('{"ok": false}\n')
    for path in [run, *run.rglob('*')]:
        os.utime(path, (old, old), follow_symlinks=False)
    with pytest.raises(mod.subprocess.CalledProcessError):
        mod.archive(run, vault, idle_days=1)
    assert run.is_dir()


def test_corrupt_archive_is_not_restored(tool):
    mod, run, vault = tool
    record = mod.archive(run, vault)
    Path(record['archive']).write_bytes(b'damaged')
    with pytest.raises(RuntimeError, match='sha256'):
        mod.restore(run)
    assert not run.exists() and run.with_name('run-1.archived.json').exists()
