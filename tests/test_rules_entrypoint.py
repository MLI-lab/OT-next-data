import sys
from pathlib import Path

from validation.rules import __main__ as cli


def test_all_rules_use_pipeline_with_static_gate_and_final_leak_check(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(sys, 'argv', ['rules', str(tmp_path), '--out', str(tmp_path / 'out')])
    monkeypatch.setattr(cli, 'run_static', lambda *a: (_ for _ in ()).throw(AssertionError('No separate static run')))
    monkeypatch.setattr(cli.subprocess, 'call', lambda command: calls.append(command) or 0)
    assert cli.main() == 0
    command, = calls
    assert command[command.index('--stages') + 1] == '1,2,3,4'
    assert command[command.index('--out') + 1] == str(tmp_path / 'out')
    assert '--no-reference-leaks' not in command


def test_named_dynamic_retains_reference_leaks(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(sys, 'argv', ['rules', str(tmp_path), '--out', str(tmp_path / 'out'),
                                     '--rules', 'reference_leaks', '--', '--submit', 'helma'])
    monkeypatch.setattr(cli.subprocess, 'call', lambda command: calls.append(command) or 0)
    cli.main()
    command, = calls
    assert '--no-reference-leaks' not in command
    assert '--no-dynamic-do-not-modify' in command
    assert command[command.index('--submit') + 1] == 'helma'


def test_static_command_writes_results_and_supports_partial_reruns(tmp_path):
    from validation import run as results
    from validation.rules.registry import BY_NAME
    task = tmp_path / 'task'
    (task / 'environment').mkdir(parents=True)
    (task / 'tests').mkdir()
    (task / 'task.toml').write_text('version = "1.0"')
    (task / 'instruction.md').write_text('Fix the program.')
    (task / 'environment/Dockerfile').write_text('FROM ubuntu:24.04')
    (task / 'tests/test.sh').write_text('bash /app/run_tests.sh')
    out = tmp_path / 'out'
    assert cli.run_static(task, [BY_NAME['agent_editable_test']], out, 'training') == 1
    cli.run_static(task, [BY_NAME['nproc']], out, 'training')
    row, = results.read(out / 'results.parquet')
    assert row['agent_editable_test'] == 'fail'
    assert row['nproc'] == 'pass'
    assert row['revision'] == 1
