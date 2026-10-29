"""A contract prepared with --path-resolution-report carries completed discoveries to its shards."""
import io
import json
import tarfile
from types import SimpleNamespace

import pyarrow as pa
import pyarrow.parquet as pq

from validation.rules.static.instruction_suffix import record_resolved_paths
from validation.rules.static.task_absolute_path.path_cache import KEY, read, task_fingerprint
from validation.data.materialize import materialize
from validation.stages.layout import LAYOUT, REFERENCE


def blob(files):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode='w') as archive:
        for name, data in files.items():
            member = tarfile.TarInfo(name)
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
    return output.getvalue()


def diagnostic(status):
    return {'agent_baseline': {'status': status, 'findings': [{'status': 'unique'}]}}


def test_completed_discoveries_reach_the_shard_cache(tmp_path):
    rows = [{'path': name, 'task_binary': blob({'instruction.md': f'read {name}.txt\n'.encode(),
                                                 'task.toml': b'[agent]\ntimeout_sec = 60\n'})}
            for name in ('resolved', 'unfinished', 'unflagged')]
    data = tmp_path / 'derived'
    data.mkdir()
    pq.write_table(pa.Table.from_pylist(rows), data / '0000.parquet')
    report = tmp_path / 'resolver-evidence.json'
    report.write_text(json.dumps({'stage': REFERENCE, 'stage_layout': LAYOUT, 'items': [
        {'task': '/x/resolved', 'status': 'passed', 'instruction_path_diagnostics': [diagnostic('completed')]},
        {'task': '/x/unfinished', 'status': 'error', 'instruction_path_diagnostics': [diagnostic('error')]}]}))
    record_resolved_paths(data / '0000.parquet', SimpleNamespace(path_resolution_report=[report]))
    assert set(json.loads(pq.read_schema(data / '0000.parquet').metadata[KEY])) == {'resolved'}
    assert pq.read_table(data / '0000.parquet').to_pylist() == rows
    shard = tmp_path / 'shard'
    materialize(data, shard)
    cache = read(shard)
    assert cache == {'resolved': task_fingerprint(shard / 'resolved')}


def test_no_report_leaves_the_parquet_unchanged(tmp_path):
    path = tmp_path / '0000.parquet'
    pq.write_table(pa.Table.from_pylist([{'path': 'a', 'task_binary': blob({'instruction.md': b'x'})}]), path)
    before = path.read_bytes()
    record_resolved_paths(path, SimpleNamespace(path_resolution_report=[]))
    assert path.read_bytes() == before
