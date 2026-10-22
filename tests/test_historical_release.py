import io
import json
import tarfile

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from data.utils.historical_release import restore
from validation.contract import files_digest


def payload(instruction, runtime=b'FROM python:3.12\n'):
    files = {'instruction.md': instruction, 'environment/Dockerfile': runtime}
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode='w:gz') as archive:
        for name, data in files.items():
            member = tarfile.TarInfo(name)
            member.size = len(data)
            member.mode = 0o644
            archive.addfile(member, io.BytesIO(data))
    return dict(path='example', task_binary=output.getvalue(), content_sha256=files_digest(files.items()),
                stages_passed='1,3,4,5', patch_changed=True, patch_labels=['anti-cheat-instruction'], run='old')


def fixture(tmp_path, *, runtime=b'FROM python:3.12\n', corrupt_digest=False):
    current, historical = payload(b'Current\n'), payload(b'Historical\n', runtime)
    if corrupt_digest:
        historical['content_sha256'] = '0' * 64
    for name, rows in [('current', [current]), ('historical', [historical]), ('archive', [])]:
        pq.write_table(pa.Table.from_pylist(rows, schema=pa.Table.from_pylist([current]).schema), tmp_path / (name+'.parquet'))
    record = {'run':'test', 'patch_provenance': {'test': {'tasks': [{'task_id':'example','labels':[] }]}},
              'automation_changes': {'test': {'tasks': [{'task_id':'example','labels':['anti-cheat-instruction']}]}}}
    (tmp_path/'run.json').write_text(json.dumps(record))
    return historical


def run(tmp_path):
    return restore(tmp_path/'current.parquet', tmp_path/'historical.parquet', tmp_path/'archive.parquet',
                   tmp_path/'run.json', 'test', 'a'*40, tmp_path/'output')


def test_restores_exact_historical_payload_and_keeps_archive(tmp_path):
    expected = fixture(tmp_path)
    result = run(tmp_path)
    row = pq.read_table(tmp_path/'output/tasks.parquet').to_pylist()[0]
    assert row['task_binary'] == expected['task_binary']
    assert row['content_sha256'] == expected['content_sha256']
    assert result['changed_retained'] == 1
    assert result['fresh_runtime_validation'] is False
    assert pq.read_table(tmp_path/'output/archive.parquet').num_rows == 0


def test_rejects_historical_runtime_change(tmp_path):
    fixture(tmp_path, runtime=b'FROM python:3.11\n')
    with pytest.raises(ValueError, match='Runtime differs'):
        run(tmp_path)


def test_rejects_incorrect_content_digest(tmp_path):
    fixture(tmp_path, corrupt_digest=True)
    with pytest.raises(ValueError, match='Invalid content digest'):
        run(tmp_path)


def test_archive_keeps_payload_and_immutable_evidence_link(tmp_path):
    fixture(tmp_path)
    archived = payload(b'Archived instruction\n')
    pq.write_table(pa.Table.from_pylist([archived]), tmp_path/'archive.parquet')
    run(tmp_path)
    row = pq.read_table(tmp_path/'output/archive.parquet').to_pylist()[0]
    assert row['task_binary'] == archived['task_binary']
    assert row['run'] == 'https://huggingface.co/datasets/FWeindel/validated-tasks/blob/' + 'a'*40 + '/runs/test.json'
