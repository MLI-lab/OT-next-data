"""Resource repairs preserve task requirements and never change the verifier."""
import hashlib
import io
import tarfile
from types import SimpleNamespace

import pytest

from data.endless_terminals import patch


def archive(files):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w') as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return stream.getvalue()


def contents(binary):
    return {info.name: data for info, data in patch.read_task(binary)}


@pytest.mark.parametrize('cpus', [1, 4])
def test_workers_follow_declared_budget_and_preserve_verifier(monkeypatch, cpus):
    name = 'task_000000_b3ae7cf7'
    script = b'#!/bin/bash\npigz -p "$(nproc)" -c input.csv > result.gz\n'
    monkeypatch.setitem(patch.CPU_REPAIRS, name, hashlib.sha256(script).hexdigest())
    files = {'instruction.md': b'Compress input.csv into result.gz.\n',
             'task.toml': f'[agent]\ntimeout_sec = 123\n[environment]\ncpus = {cpus}\n'.encode(),
             'solution/solve.sh': script, 'tests/test_final_state.py': b'assert valid_output()\n'}
    original = archive(files)
    changed, evidence = patch.static_repairs(name, original, original)
    result = contents(changed)
    assert result['solution/solve.sh'] == script.replace(b'$(nproc)', str(cpus).encode())
    assert result['instruction.md'].endswith(b'You have 123 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.\n')
    assert result['tests/test_final_state.py'] == files['tests/test_final_state.py']
    assert result['task.toml'] == files['task.toml']
    assert {e['file'] for e in evidence['edits']} == {'instruction.md', 'solution/solve.sh'}


def test_cpu_repair_rejects_changed_source():
    files = {'instruction.md': b'Do the task.', 'task.toml': b'[agent]\ntimeout_sec = 300\n',
             'solution/solve.sh': b'echo changed\n'}
    original = archive(files)
    with pytest.raises(ValueError, match='reviewed CPU source changed'):
        patch.static_repairs('task_000000_b3ae7cf7', original, original)


def test_host_measurement_command_matches_audit(monkeypatch):
    name = 'task_000000_bc9c5a3e'
    script = b'cpu_cmd="nproc"\ncpu=$(nproc)\nlog "$cpu|$cpu_cmd"\n'
    monkeypatch.setitem(patch.CPU_REPAIRS, name, hashlib.sha256(script).hexdigest())
    original = archive({'instruction.md': b'Measure host CPUs.',
                        'task.toml': b'[agent]\ntimeout_sec = 300\n', 'solution/solve.sh': script})
    changed, _ = patch.static_repairs(name, original, original)
    assert contents(changed)['solution/solve.sh'] == b'cpu_cmd="nproc --all"\ncpu=$(nproc --all)\nlog "$cpu|$cpu_cmd"\n'


@pytest.mark.parametrize('setup', [False, True])
def test_pin_offsets_follow_original_body_with_optional_setup(monkeypatch, setup):
    name = 'example'
    script = b'#!/bin/bash\npip install demo\n'
    position = script.index(b'demo')
    monkeypatch.setitem(patch.PIN_REPAIRS['tasks'], name, {
        'script_sha256': patch.digest(script), 'edits': [
            {'start': position, 'end': position + 4, 'original': 'demo', 'replacement': 'demo==1.2.3'}]})
    files = {'instruction.md': b'Install demo.', 'task.toml': b'[agent]\ntimeout_sec = 300\n',
             'solution/solve.sh': script, 'tests/test_final_state.py': b'assert real_package()\n'}
    original = archive(files)
    if setup:
        files['solution/solve.sh'] = script.replace(b'#!/bin/bash\n', b'#!/bin/bash\nbash /setup_files/setup.sh || exit $?\n')
    result, _ = patch.static_repairs(name, original, archive(files))
    assert contents(result)['solution/solve.sh'] == files['solution/solve.sh'].replace(b'install demo', b'install demo==1.2.3')
    assert contents(result)['tests/test_final_state.py'] == files['tests/test_final_state.py']


def test_verifier_keeps_task_python_for_subprocesses(monkeypatch):
    name = 'task_000000_b5807114'
    script = b'#!/bin/bash\necho helper\n'
    monkeypatch.setattr(patch, 'JSONSCHEMA_SOURCE_SHA256', patch.digest(script))
    monkeypatch.delitem(patch.PIN_REPAIRS['tasks'], name)
    original = archive({'instruction.md': b'Validate JSON.', 'task.toml': b'[agent]\ntimeout_sec = 300\n',
                        'solution/solve.sh': script, 'tests/test.sh': patch.OFFLINE_TEST_SH,
                        'tests/test_final_state.py': b'assert subprocess_works()\n'})
    result, _ = patch.static_repairs(name, original, original)
    files = contents(result)
    assert b'export PATH=' not in files['tests/test.sh']
    assert b'/opt/et-verifier/venv/bin/pytest ' in files['tests/test.sh']
    assert files['tests/test_final_state.py'] == b'assert subprocess_works()\n'


def test_reviewed_filter_archives_original_and_keeps_ids(tmp_path, monkeypatch):
    import pyarrow as pa
    import pyarrow.parquet as pq
    binary = archive({'instruction.md': b'Create the report.',
                      'task.toml': b'[agent]\ntimeout_sec = 300\n',
                      'environment/Dockerfile': b'FROM ubuntu:22.04\nWORKDIR /home/user\n',
                      'solution/solve.sh': b'#!/bin/bash\ntrue\n',
                      'tests/test.sh': b'#!/bin/bash\ntrue\n'})
    source = tmp_path / 'source.parquet'
    pq.write_table(pa.Table.from_pylist([{'path': 'kept', 'task_binary': binary},
                                       {'path': 'excluded', 'task_binary': binary}]), source)
    monkeypatch.setattr(patch, 'SOURCE_SHA256', patch.digest(source.read_bytes()))
    monkeypatch.setattr(patch, 'REVIEWED_EXCLUSIONS', {'excluded': {
        'source_sha256': patch.digest(binary), 'category': 'reviewed-test', 'reason': 'Recorded evidence.'}})
    output = tmp_path / 'patched'
    patch.patch_main(SimpleNamespace(source=source, output=output, no_task_setup=True,
        no_share_images=True, no_offline_verifier=True, no_reviewed_repairs=True,
        no_static_repairs=False, filter_reviewed=True))
    assert pq.read_table(output / 'tasks.parquet')['path'].to_pylist() == ['kept']
    excluded, = pq.read_table(output / 'tasks.archive.parquet').to_pylist()
    assert excluded['path'] == 'excluded'
    assert excluded['task_binary'] == binary
    assert excluded['archive_reason'] == 'reviewed-test: Recorded evidence.'
