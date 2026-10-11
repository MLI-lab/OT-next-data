import io
import tarfile

import pyarrow as pa
import pyarrow.parquet as pq

from data.utils.environment_counts import fingerprints
from data.utils.patch_reporting import write_patch_report
from validation.publishing import patch_provenance
from validation.publishing.pr_runtime import render


def payload(base):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w') as archive:
        content = f'FROM {base}\n'.encode()
        info = tarfile.TarInfo('environment/Dockerfile')
        info.size = len(content)
        archive.addfile(info, io.BytesIO(content))
    return stream.getvalue()


def test_upstream_comparison_includes_archived_tasks(tmp_path):
    original = [{'path': name, 'task_binary': payload(base)}
                for name, base in [('a', 'python:3.11'), ('b', 'python:3.12'), ('c', 'python:3.13')]]
    patched = [dict(row, task_binary=payload('python:3.13')) for row in original[:2]]
    for name, rows in [('original', original), ('patched', patched)]:
        pq.write_table(pa.Table.from_pylist(rows), tmp_path / f'{name}.parquet')
    document = write_patch_report(tmp_path / 'original.parquet', tmp_path / 'patched.parquet',
        source={'dataset': 'source', 'url': 'https://example.org', 'revision': 'pinned'},
        dropped={'c': {'category': 'excluded', 'reason': 'Unavailable'}})
    assert document['source_environments']['unique'] == 3
    tables = {'demo': (patched, [dict(original[2], archive_stage=0)])}
    record = {}
    patch_provenance.attach(record, tables, [f'demo={tmp_path}/patched.manifest.json'])
    assert '| demo | 3 | 1 | -2 |' in render(record)
    assert record['patch_provenance']['demo']['environment_comparison']['kept']['tasks'] == 2


def test_context_identity_includes_support_files_but_not_task_instructions():
    files = {'environment/Dockerfile': ('0', 420, 'docker'),
             'environment/setup.sh': ('0', 493, 'setup'), 'instruction.md': ('0', 420, 'one')}
    original = fingerprints(files)
    files['instruction.md'] = ('0', 420, 'two')
    assert fingerprints(files) == original
    files['environment/setup.sh'] = ('0', 493, 'changed')
    assert fingerprints(files) != original
    assert fingerprints({'instruction.md': files['instruction.md']}) == []


def test_missing_baseline_is_not_zero_and_coverage_is_explicit():
    record = {'patch_provenance': {'old': {'environment_comparison': {
        'upstream': None, 'kept': {'unique': 0, 'tasks': 2, 'tasks_with_build_context': 0}}}}}
    text = render(record)
    assert '| old | Not recorded | 0 | Not recorded |' in text
    assert 'kept build contexts found for 0 of 2 tasks' in text
