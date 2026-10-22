import io
import tarfile
import tomllib

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from data.tasktrove_nl2bash.patch import (
    LABEL, SETUP_LABEL, OUTPUT_LABEL, INSTRUCTION_SETUP, INPUT_LINES, INPUT_LINE, HELPER_LINE, clean_config, patch_archive, patch,
)


@pytest.mark.parametrize('value', ['false', 'true'])
def test_removes_only_verifier_setting(value):
    original = (f'[metadata]\nrestart_environment = true\n\n[verifier]\n'
                f'# Existing comment\nrestart_environment = {value} # unsupported\n'
                'timeout_sec = 600.0\n[agent]\ntimeout_sec = 600.0\n').encode()
    cleaned = clean_config(original)
    expected = tomllib.loads(original.decode())
    del expected['verifier']['restart_environment']
    assert tomllib.loads(cleaned.decode()) == expected
    assert b'# Existing comment\n' in cleaned
    assert clean_config(cleaned) == cleaned


def archive():
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode='w:gz') as tar:
        for name, body, mode in [
            ('task.toml', b'[verifier]\nrestart_environment = false\ntimeout_sec = 600\n', 0o644),
            ('solution/solve.sh', b'#!/bin/bash\nbash /tests/setup_seeds.sh\n', 0o755),
            ('setup_files/setup_seeds.sh', b'#!/bin/bash\necho setup\n', 0o755),
            ('tests/test.sh', b'#!/bin/bash\necho unchanged\n', 0o755),
            ('instruction.md', INSTRUCTION_SETUP + b'# Terminal Automation Request\n'
             + INPUT_LINES[0] + HELPER_LINE + b'- Write /output/command_capture.txt.\n', 0o644),
        ]:
            member = tarfile.TarInfo(name); member.size = len(body); member.mode = mode
            tar.addfile(member, io.BytesIO(body))
    return out.getvalue()


def test_archive_preserves_verifier_bytes_modes_and_is_idempotent():
    original = archive()
    result, edited = patch_archive(original)
    assert edited == [LABEL, SETUP_LABEL]
    assert patch_archive(original)[0] == result
    with tarfile.open(fileobj=io.BytesIO(result)) as tar:
        assert tar.extractfile('tests/test.sh').read() == b'#!/bin/bash\necho unchanged\n'
        assert tar.getmember('tests/test.sh').mode == 0o755
        assert tar.extractfile('solution/solve.sh').read() == b'#!/bin/bash\nbash /setup_files/setup.sh\n'
        assert tar.getmember('solution/solve.sh').mode == 0o755
        assert tar.extractfile('setup_files/setup.sh').read() == b'#!/bin/bash\necho setup\n'
        assert tar.getmember('setup_files/setup.sh').mode == 0o755
        assert 'setup_files/setup_seeds.sh' not in tar.getnames()
        assert tar.extractfile('instruction.md').read() == (
            b'# Terminal Automation Request\n' + INPUT_LINE + b'- Write /output/command_capture.txt.\n')
    assert patch_archive(result) == (result, [])


def test_reference_path_fix_requires_supplied_setup_script():
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode='w:gz') as tar:
        for name, body in [('task.toml', b'[verifier]\ntimeout_sec = 600\n'),
                           ('solution/solve.sh', b'bash /tests/setup_seeds.sh\n')]:
            member = tarfile.TarInfo(name); member.size = len(body)
            tar.addfile(member, io.BytesIO(body))
    with pytest.raises(ValueError, match='Unexpected seed setup layout'):
        patch_archive(out.getvalue())


def test_harness_detects_renamed_setup(tmp_path):
    from validation.stages.task_setup import detect
    with tarfile.open(fileobj=io.BytesIO(patch_archive(archive())[0])) as tar:
        tar.extractall(tmp_path, filter='data')
    assert detect(tmp_path) == 'bash /setup_files/setup.sh'


def test_parquet_patch_records_exact_change_and_preserves_columns(tmp_path):
    source = tmp_path/'source.parquet'
    table = pa.table({'path': ['task_0'], 'task_binary': [archive()], 'extra': ['retained']})
    pq.write_table(table.replace_schema_metadata({b'provenance': b'original'}), source)
    report = patch(source, tmp_path/'patched')
    result = pq.read_table(tmp_path/'patched/tasks.parquet')
    assert result.to_pylist()[0]['extra'] == 'retained'
    assert result.schema.metadata[b'provenance'] == b'original'
    assert report['tasks'][0]['changed_files'] == [
        'instruction.md', 'setup_files/setup.sh', 'setup_files/setup_seeds.sh',
        'solution/solve.sh', 'task.toml']
    assert report['tasks'][0]['labels'] == sorted([LABEL, SETUP_LABEL])
    assert 'Removed verifier.restart_environment' in report['tasks'][0]['reason']
    assert 'setup_files/setup.sh' in report['tasks'][0]['reason']
    assert source.read_bytes() != (tmp_path/'patched/tasks.parquet').read_bytes()


def test_task_267_goal_names_the_capture_file_the_verifier_reads():
    original = archive().replace(b'x', b'x')
    goal = b'Enable the shell options and print them, capturing output in $PWD/command_capture.txt.\n'
    out = io.BytesIO()
    with tarfile.open(fileobj=io.BytesIO(original)) as src, tarfile.open(fileobj=out, mode='w:gz') as tar:
        for member in src.getmembers():
            body = src.extractfile(member).read()
            if member.name == 'instruction.md':
                body = body.replace(b'# Terminal Automation Request\n', b'# Terminal Automation Request\n' + goal)
            member.size = len(body)
            tar.addfile(member, io.BytesIO(body))
    result, labels = patch_archive(out.getvalue(), 'task_267')
    assert labels == [LABEL, SETUP_LABEL, OUTPUT_LABEL]
    with tarfile.open(fileobj=io.BytesIO(result)) as tar:
        text = tar.extractfile('instruction.md').read()
    assert b'capturing output in /output/command_capture.txt.' in text and b'$PWD' not in text
    assert patch_archive(result, 'task_267') == (result, [])
    # Other tasks keep their goal unchanged.
    assert OUTPUT_LABEL not in patch_archive(out.getvalue(), 'task_268')[1]
