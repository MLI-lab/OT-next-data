"""The audit baseline must not inherit post-run repairs or exclusions."""
from types import SimpleNamespace

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from data.endless import patch, repairs
from data.utils.harbor_parquet import pack_task


def test_no_repairs_preserves_instructions_and_reviewed_exclusions(tmp_path, monkeypatch):
    binary = pack_task({'instruction.md': b'Create the report.\n',
        'task.toml': b'[agent]\ntimeout_sec = 300\n',
        'environment/Dockerfile': b'FROM ubuntu:22.04\nWORKDIR /home/user\n',
        'solution/solve.sh': b'#!/bin/bash\ntrue\n', 'tests/test.sh': b'#!/bin/bash\ntrue\n'})
    source = tmp_path / 'source.parquet'
    pq.write_table(pa.Table.from_pylist([{'path': 'example', 'task_binary': binary}]), source)
    monkeypatch.setattr(patch, 'SOURCE_SHA256', patch.digest(source.read_bytes()))
    monkeypatch.setitem(patch.REVIEWED_EXCLUSIONS, 'example', {
        'source_sha256': patch.digest(binary), 'category': 'reference-result', 'reason': 'Historical failure.'})
    args = dict(source=source, no_task_setup=True, no_share_images=True,
        no_offline_verifier=True, no_reviewed_repairs=False, no_static_repairs=False)
    for name, no_repairs, filtered in [('baseline', True, True), ('repaired', False, False)]:
        output = tmp_path / name
        patch.patch_main(SimpleNamespace(**args, output=output, no_repairs=no_repairs, filter_reviewed=filtered))
        row, = pq.read_table(output / 'tasks.parquet').to_pylist()
        files = {m.name: d for m, d in patch.read_task(row['task_binary'])}
        assert row['path'] == 'example'
        if no_repairs:
            assert files['instruction.md'] == b'Create the report.\n'
        else:
            assert b'You have 300 seconds' in files['instruction.md']


def test_historical_path_edits_are_bound_to_both_payload_hashes(monkeypatch):
    before, after = b'Read report.txt.\n', b'Read /home/user/report.txt.\n'
    monkeypatch.setitem(repairs.PUBLISHED_PATH_REPAIRS, 'example', {
        'before_sha256': patch.digest(before), 'after_sha256': patch.digest(after),
        'edits': [{'start': 5, 'end': 5, 'old': '', 'new': '/home/user/'}]})
    source = pack_task({'instruction.md': before, 'tests/test_final_state.py': b'assert original_assertion\n'})
    result = dict((m.name, d) for m, d in patch.read_task(repairs.published_instruction_paths('example', source)))
    assert result['instruction.md'] == after
    assert result['tests/test_final_state.py'] == b'assert original_assertion\n'
    with pytest.raises(ValueError, match='input changed'):
        repairs.published_instruction_paths('example', pack_task({'instruction.md': b'Changed task.'}))


@pytest.mark.parametrize('setting', ['', 'allow_internet = true\n', 'allow_internet = false\n'])
def test_offline_first_overrides_upstream_without_changing_resources(setting):
    import tomllib
    original = ('[environment]\n' + setting + 'cpus = 2\nmemory_mb = 4096\n'
                '[agent]\ntimeout_sec = 300\n').encode()
    updated = patch.offline_config(original)
    parsed = tomllib.loads(updated.decode())
    assert parsed['environment'] == {'allow_internet': False, 'cpus': 2, 'memory_mb': 4096}
    assert parsed['agent'] == {'timeout_sec': 300}
    assert patch.offline_config(updated) == updated
