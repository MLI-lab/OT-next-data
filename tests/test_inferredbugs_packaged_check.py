"""The cluster check must use the supplied artifact, not silently regenerate it."""
import importlib.util
from pathlib import Path
import sys

import pytest


@pytest.fixture
def check():
    path = Path(__file__).resolve().parents[1] / 'hpc/helma/inferredbugs_verifier_check.py'
    spec = importlib.util.spec_from_file_location('inferredbugs_packaged_check', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_checks_exact_archive_without_repackaging(check, tmp_path, monkeypatch):
    row = next(r for r in check.patcher.embedded_recipes() if r['task_id'] in check.patcher.embedded_warnings())
    task = row['task_id']
    fixture = tmp_path / 'source/inferredbugs' / row['language'] / row['project'] / str(row['bug_id'])
    fixture.mkdir(parents=True)
    (fixture / 'file_before.txt').write_bytes(b'buggy')
    (fixture / 'file_after.txt').write_bytes(b'fixed')
    files = {'tests/test.sh': b'echo exact-packaged-verifier', 'solution/solve.sh': b'echo exact-oracle'}
    parquet = tmp_path / 'tasks.parquet'
    check.patcher.pq.write_table(check.patcher.pa.Table.from_pylist([
        {'path': task, 'task_binary': check.patcher.pack(files)},
        {'path': 'unselected', 'task_binary': b'not-an-archive'},
    ]), parquet)
    ids = tmp_path / 'ids.txt'
    ids.write_text(task + '\n')
    seen = []

    def run_variant(a, task_id, row, meta, packaged, before, after, variant):
        assert packaged == files
        assert (before, after) == (b'buggy', b'fixed')
        seen.append(variant)
        return {'variant': variant, 'reward': '0', 'status': 'mock'}

    def repackage(*args, **kwargs):
        pytest.fail('A supplied parquet must not be regenerated')

    monkeypatch.setattr(check, 'run_variant', run_variant)
    monkeypatch.setattr(check.patcher, 'package', repackage)
    monkeypatch.setattr(sys, 'argv', ['check', '--parquet', str(parquet), '--ids-file', str(ids),
        '--inferredbugs-root', str(tmp_path / 'source'), '--scratch', str(tmp_path / 'scratch'),
        '--images', str(tmp_path), '--output', str(tmp_path / 'out'), '--proxy-helper', 'unused',
        '--variants', 'buggy,full'])
    check.main()
    assert sorted(seen) == ['buggy', 'full']
    ids.write_text('missing-task\n')
    with pytest.raises(ValueError, match='Selected tasks missing from parquet: missing-task'):
        check.main()
