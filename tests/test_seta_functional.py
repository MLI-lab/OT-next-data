from data.seta.patch import functional_task, pack, unpack


def test_functional_profile_preserves_unrelated_instructions_and_unpinned_packages():
    files = {'instruction.md': (b'Write a useful script.', 0o644),
             'tests/test.sh': (b'#!/bin/bash\nuvx --with numpy pytest /tests/test_outputs.py\n', 0o755)}
    blob = pack(files)
    output, reasons = functional_task(blob, 'new_unmodified_task')
    assert output == blob
    assert reasons == []


def test_functional_timeout_preserves_instruction_and_agent_budget():
    files = {'instruction.md': (b'Original instruction without required suffix.', 0o644),
             'task.toml': (b'[agent]\ntimeout_sec = 300\n[environment]\nbuild_timeout_sec = 600.0\ncpus = 1\n', 0o644)}
    output, reasons = functional_task(pack(files), 'ask_ubuntu__synth__135')
    result = unpack(output)
    assert result['instruction.md'] == files['instruction.md']
    assert b'timeout_sec = 300' in result['task.toml'][0]
    assert b'build_timeout_sec = 1200.0' in result['task.toml'][0]
    assert reasons == ['increase setup timeout after observed setup failures']


def test_functional_profile_omits_manual_quality_replacement():
    files = {'instruction.md': (b'Original archive task.', 0o644),
             'tests/test_outputs.py': (b'original_verifier = True\n', 0o644),
             'solution/solve.sh': (b'original_reference\n', 0o755)}
    blob = pack(files)
    output, reasons = functional_task(blob, 'ask_ubuntu__synth__1')
    assert output == blob
    assert reasons == []
