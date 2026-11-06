import io
import tarfile
import json

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from data.calibforge import patch, repairs


def task(files):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w:gz') as archive:
        for name, text in files.items():
            data = text.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return stream.getvalue()


def members(binary):
    with tarfile.open(fileobj=io.BytesIO(binary)) as archive:
        return {m.name: archive.extractfile(m).read().decode() for m in archive if m.isfile()}


def test_no_repairs_leaves_check_derived_changes_out():
    task_id = next(iter(repairs.REQUIRED_SOLUTION_FILES))
    source = {'environment/Dockerfile': 'FROM python:3.12@sha256:' + '0' * 64 + '\n',
              'instruction.md': '```\ncd /app && ./run\n```\n', 'task.toml': 'version = "1.0"\n',
              'tests/test.sh': 'pytest\n', 'tests/test_outputs.py': 'def test_a():\n    pass\n'}
    plain, changes, _ = patch.patch_task(task_id, task(source), None, repairs=False)
    assert not {'R3', 'V1'} & changes.keys()
    assert members(plain)['instruction.md'] == source['instruction.md']
    assert members(plain)['tests/test_outputs.py'] == source['tests/test_outputs.py']
    repaired, changes, _ = patch.patch_task(task_id, task(source), None)
    assert {'R3', 'V1'} <= changes.keys()
    assert '/app/run' in members(repaired)['instruction.md']


def test_no_op_exclusions_are_repairs_not_backend_exclusions():
    assert len(repairs.CHECK_EXCLUSIONS) == 5
    assert not repairs.CHECK_EXCLUSIONS.keys() & patch.DROPPED.keys()
    assert all(r['category'].startswith('backend') for r in patch.DROPPED.values())


@pytest.mark.parametrize('no_repairs', [False, True])
def test_main_retains_historical_exclusions(tmp_path, monkeypatch, no_repairs):
    ids = list(patch.DROPPED) + list(repairs.CHECK_EXCLUSIONS)
    binary = task({'environment/Dockerfile': 'FROM python:3.12\n'})
    source = tmp_path / 'source.parquet'
    output = tmp_path / 'output'
    pq.write_table(pa.table({'path': ids, 'task_binary': [binary] * len(ids)}), source)
    monkeypatch.setattr('data.utils.patch_reporting.write_patch_report', lambda *a, **kw: None)
    monkeypatch.setattr('sys.argv', ['patch.py', '--source', str(source), '--output', str(output)]
                        + (['--no-repairs'] if no_repairs else []))
    patch.main()
    assert pq.read_table(output / source.name)['path'].to_pylist() == ids
    manifest = json.loads((output / 'calibforge_repair_manifest.json').read_text())
    assert manifest['dropped'] == {}
    assert manifest['counts']['dropped'] == 0
    assert set(manifest['historical_exclusions']) == set(ids)
