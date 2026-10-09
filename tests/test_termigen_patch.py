import json
import sys

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from data.termigen import patch as patcher


def task(dockerfile, **files):
    entries = {
        'environment/Dockerfile': (dockerfile.encode(), 0o644),
        'instruction.md': (b'Keep the original task.', 0o644),
        'tests/test_outputs.py': (b'import yaml\n', 0o644),
    }
    entries.update({name: (content, 0o644) for name, content in files.items()})
    return patcher.pack(entries)


def test_missing_verifier_dependency_is_a_byte_identical_noop():
    original = task(f'FROM {patcher.BASE_IMAGE}\nWORKDIR /app\n')
    output, report = patcher.patch_task(original)
    assert output == original
    assert report['status'] == 'no-op'
    assert report['rules'] == report['predictions'] == []


@pytest.mark.parametrize('dockerfile,files,rule', [
    # An earlier ENV rewrite must also be rolled back if COPY is unpatchable.
    (f'FROM {patcher.BASE_IMAGE}\nENV TOOL=/opt/tool\nENV PATH=$TOOL/bin:$PATH\n'
     'COPY absent /data/input\n', {}, 'missing-copy-source'),
    (f'FROM {patcher.BASE_IMAGE}\nCOPY payload /workspace/Dockerfile\n',
     {'environment/payload': b'payload'}, 'bridge-metadata-collision'),
    (f'FROM {patcher.BASE_IMAGE}\nENV LD_LIBRARY_PATH=/opt/lib:$LD_LIBRARY_PATH\n',
     {}, 'unresolved-env-reference'),
])
def test_unpatchable_conditions_are_predictions_and_preserve_exact_input(dockerfile, files, rule):
    original = task(dockerfile, **files)
    output, report = patcher.patch_task(original)
    assert output == original
    assert report['status'] == 'unpatchable'
    assert report['changed_files'] == report['added_files'] == report['rules'] == []
    assert report['predictions'] == [{'rule': rule, 'reason': report['unpatchable'][0]}]
    assert not any(key.startswith('archive') for key in report)


def test_cli_retains_every_task_and_writes_only_prediction_report(tmp_path, monkeypatch):
    rows = [
        {'path': 'missing', 'task_binary': task(f'FROM {patcher.BASE_IMAGE}\nCOPY absent /data/input\n')},
        {'path': 'metadata', 'task_binary': task(f'FROM {patcher.BASE_IMAGE}\nCOPY payload /workspace/Dockerfile\n',
                                             **{'environment/payload': b'payload'})},
        {'path': 'env', 'task_binary': task(f'FROM {patcher.BASE_IMAGE}\nENV LD_LIBRARY_PATH=$LD_LIBRARY_PATH\n')},
        {'path': 'yaml', 'task_binary': task(f'FROM {patcher.BASE_IMAGE}\n')},
        {'path': 'copy', 'task_binary': task(f'FROM {patcher.BASE_IMAGE}\nCOPY payload /workspace/payload\n',
                                         **{'environment/payload': b'payload'})},
    ]
    source = tmp_path / 'input.parquet'
    output = tmp_path / 'patched' / 'tasks.parquet'
    pq.write_table(pa.Table.from_pylist(rows), source)
    input_bytes = source.read_bytes()
    monkeypatch.setattr(sys, 'argv', ['patcher', '--input', str(source), '--output', str(output)])
    patcher.main()
    result = pq.read_table(output).to_pylist()
    assert [r['path'] for r in result] == [r['path'] for r in rows]
    assert result[:4] == rows[:4]
    assert result[4]['task_binary'] != rows[4]['task_binary']
    assert source.read_bytes() == input_bytes
    report = json.loads(output.with_suffix('.report.json').read_text())
    assert report['candidate_count'] == len(rows)
    assert {r['task_id'] for r in report['unpatchable_predictions']} == {'missing', 'metadata', 'env'}
    assert all(set(r) == {'task_id', 'rule', 'reason'} for r in report['unpatchable_predictions'])
    assert 'archive_stage' not in json.dumps(report)
    assert {p.name for p in output.parent.iterdir()} == {'tasks.parquet', 'tasks.report.json'}
