import importlib

from validation.rules.registry import RULES
from validation.rules.static import terminal_bench


def test_registry_matches_the_training_profile_and_modules_import():
    _, selected, _ = terminal_bench.load_checks('training')
    active = {r.name.replace('_', '-') for r in RULES if r.kind == 'static' and r.status == 'active'}
    assert active == {n.removeprefix('check-').rsplit('.', 1)[0] for n in selected}
    for rule in RULES:
        importlib.import_module(rule.module)
        if rule.repair:
            importlib.import_module(rule.repair)


def test_patched_rules_are_exactly_the_runner_patches():
    patched = {r.name for r in RULES if r.origin == 'TB patched'}
    assert patched == {n.removeprefix('check-').removesuffix('.sh') for n in terminal_bench.PATCHES}
    for rule in RULES:
        if rule.origin == 'TB patched':
            assert importlib.import_module(rule.module).CHECK == f'check-{rule.name}.sh'


def make_task(root, name, files):
    task = root / name
    for path, text in {'instruction.md': 'Fix the bug.\n', 'task.toml': 'version = "1.0"\n',
                       'environment/Dockerfile': 'FROM python:3.12-slim\nWORKDIR /app\n',
                       'tests/test.sh': '#!/bin/bash\npytest /tests/test_x.py\n', **files}.items():
        (task / path).parent.mkdir(parents=True, exist_ok=True)
        (task / path).write_text(text)
    return task


def test_stage4_runs_our_rules_as_scripts(tmp_path, monkeypatch):
    import json
    good = make_task(tmp_path, 'good', {'tests/test_x.py': 'import subprocess\n\ndef test_x():\n    subprocess.run(["true"])\n'})
    bad = make_task(tmp_path, 'bad', {'tests/test_x.py': 'def test_x():\n    assert pd.DataFrame().empty\n'})
    # Exit 1 of a warning-only rule is a warning; exit 2 (the rule crashed) is an error, never a pass.
    warn, crash = tmp_path / 'warn.py', tmp_path / 'crash.py'
    warn.write_text('import sys\nsys.exit(1)\n')
    crash.write_text('import sys\nsys.exit(2)\n')
    monkeypatch.setitem(terminal_bench.LOCAL_CHECKS, 'check-artifact-efficiency.py', warn)
    out = tmp_path / 'report'
    assert terminal_bench.run_checks([good, bad], out, 'training', group='task-quality') == 1
    tasks = {t['task']: {c['check']: c['status'] for c in t['checks']} for t in json.loads((out / 'summary.json').read_text())['tasks']}
    assert tasks['bad']['check-python-missing-imports.py'] == 'failed'
    assert tasks['good']['check-python-missing-imports.py'] == 'passed'
    assert tasks['good']['check-artifact-efficiency.py'] == 'warning'
    monkeypatch.setitem(terminal_bench.LOCAL_CHECKS, 'check-do-not-modify.py', crash)
    out = tmp_path / 'report2'
    terminal_bench.run_checks([good], out, 'training', group='task-quality')
    entry = json.loads((out / 'summary.json').read_text())['tasks'][0]
    assert {c['check']: c['status'] for c in entry['checks']}['check-do-not-modify.py'] == 'error'
    assert entry['status'] == 'failed'
