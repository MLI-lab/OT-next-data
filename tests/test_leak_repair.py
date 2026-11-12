import json
import shutil
import re

import pytest

from validation.leak_repair.launch import stage, render
from validation.leak_repair.tool import run, diff_test_files, changed_test_files

SUFFIX = 'You have 120 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.'


def task(path, extra=None):
    """A task whose verifier runs /app/run_tests.sh, which the agent could edit (agent_editable_test fails)."""
    files = {'instruction.md': f'Write /app/out.txt.\n\n{SUFFIX}\n',
             'task.toml': '[agent]\ntimeout_sec = 120\n[verifier]\ntimeout_sec = 60\n[environment]\ncpus = 1\nmemory_mb = 2048\n',
             'environment/Dockerfile': 'FROM ubuntu:24.04\nWORKDIR /app\nCOPY run_tests.sh /app/run_tests.sh\n',
             'environment/run_tests.sh': '#!/bin/bash\ntest -f /app/out.txt\n',
             'tests/test.sh': '#!/bin/bash\nbash /app/run_tests.sh && echo 1 > /logs/verifier/reward.txt || echo 0 > /logs/verifier/reward.txt\n',
             'tests/test_outputs.py': 'import os\n\ndef test_out():\n    assert os.path.exists("/app/out.txt")\n',
             'solution/solve.sh': '#!/bin/bash\necho hi > /app/out.txt\n', **(extra or {})}
    for name, text in files.items():
        (path / name).parent.mkdir(parents=True, exist_ok=True)
        (path / name).write_text(text)


def group(tmp_path, check='agent_editable_test', names=('a', 'b', 'c')):
    root = tmp_path / 'group'
    for name in names:
        task(root / 'tasks' / name)
        (root / 'evidence').mkdir(parents=True, exist_ok=True)
        (root / 'evidence' / f'{name}.json').write_text(json.dumps({'details': 'the verifier runs /app/run_tests.sh'}))
    (root / 'group.json').write_text(json.dumps({'dataset': 'example', 'check': check, 'tasks': list(names)}))
    return root


REPAIR = '''
def repair_agent_editable_test(files, evidence):
    if b'bash /app/run_tests.sh' not in files['tests/test.sh']:
        return None
    if b'b' == files.get('marker', b''):
        raise KeyError('broken')
    repaired = dict(files)
    repaired['tests/run_tests.sh'] = files['environment/run_tests.sh']
    repaired['tests/test.sh'] = files['tests/test.sh'].replace(b'/app/run_tests.sh', b'/tests/run_tests.sh')
    if TOUCH_TESTS:
        repaired['tests/test_outputs.py'] = b'import sys\\n' + files['tests/test_outputs.py'].replace(b'import os\\n', b'')
    return {'decision': 'fixed', 'reason': 'runs the harness from /tests'}, repaired
'''


def test_test_changes_classify_moves_imports_and_removals():
    before = {'app/test_x.py': b'import a\nassert 1\n', 'tests/test_y.py': b'assert 2\n', 'tests/test.sh': b'x'}
    after = {'tests/test_x.py': b'import a\nassert 1\n', 'tests/test.sh': b'y',
             'tests/test_z.py': b'from b import c\nassert 1\n'}
    entries = diff_test_files(before, after)
    assert {'file': 'tests/test_y.py', 'kind': 'removed'} in entries
    assert {'file': 'tests/test_x.py', 'kind': 'moved', 'origin': 'app/test_x.py'} in entries
    assert {'file': 'tests/test_z.py', 'kind': 'modified', 'imports_only': False} in entries
    after['tests/test_y.py'] = b'import os\nassert 2\n'
    assert {'file': 'tests/test_y.py', 'kind': 'modified', 'imports_only': True, 'origin': 'tests/test_y.py'} in \
        diff_test_files(before, after)
    assert [e['kind'] for e in changed_test_files(entries)] == ['removed', 'modified']


def test_check_stops_at_function_errors(tmp_path, capsys):
    root = group(tmp_path)
    (root / 'tasks/b/marker').write_text('b')
    work = tmp_path / 'work'
    work.mkdir()
    (work / 'leak_repairs.py').write_text('TOUCH_TESTS = False\n' + REPAIR)
    records = run(root, work, concurrency=2)
    assert records['b']['decision'] is None and 'KeyError' in records['b']['error']
    out = capsys.readouterr().out
    assert 'failed on 1 of 3 tasks' in out and 'no checks were run' in out
    assert not (work / 'check/checks-run').exists()


def test_check_reports_the_check_regressions_and_review_lists(tmp_path, capsys):
    root = group(tmp_path)
    work = tmp_path / 'work'
    work.mkdir()
    (work / 'leak_repairs.py').write_text('TOUCH_TESTS = True\n' + REPAIR.replace(
        "    if b'b' == files.get('marker', b''):\n        raise KeyError('broken')\n",
        "    if b'b' == files.get('marker', b''):\n        return {'decision': 'false_positive', 'reason': 'x'}, None\n"))
    (root / 'tasks/b/marker').write_text('b')
    (work / 'classification.jsonl').write_text(json.dumps({'task': 'a', 'verdict': 'true_positive', 'reason': 'x'}) + '\n')
    records = run(root, work, concurrency=2)
    assert records['a']['decision'] == 'fixed' and records['a']['check_after'] == 'pass'
    assert records['a']['test_changes'] == [
        {'file': 'tests/run_tests.sh', 'kind': 'moved', 'origin': 'environment/run_tests.sh'},
        {'file': 'tests/test_outputs.py', 'kind': 'modified', 'imports_only': True, 'origin': 'tests/test_outputs.py'}]
    out = capsys.readouterr().out
    assert 'fixed tasks that pass it now: 2 of 2' in out
    assert 'fixed tasks that pass all of them: 2 of 2' in out
    assert 'not read yet (read them and add your verdict): b' in out
    listed = out.split('have no verdict yet:')[1]
    assert 'Still open:' in out and 'tasks not read yet' in out
    assert '  c: ' in listed and '  a: ' not in listed  # a has a verdict, c not
    assert '/app/run_tests.sh' in (root / 'tasks/a/tests/test.sh').read_text()  # originals unchanged
    # The baseline of the unchanged originals is cached.
    assert len(list((work / 'baseline').iterdir())) == 1
    run(root, work, concurrency=2)
    assert len(list((work / 'baseline').iterdir())) == 1


def test_try_rejects_inconsistent_records(tmp_path):
    root = group(tmp_path, names=('a',))
    work = tmp_path / 'work'
    work.mkdir()
    (work / 'leak_repairs.py').write_text(
        'def repair_agent_editable_test(files, evidence):\n'
        '    files = dict(files); files["environment/Dockerfile"] = b"FROM x\\n"\n'
        '    return {"decision": "false_positive", "reason": "x"}, files\n')
    record = run(root, work, concurrency=1)['a']
    assert record['decision'] is None and 'only a fixed task returns files' in record['error']


@pytest.mark.parametrize('check', ['agent_editable_test', 'verifier_tamper', 'repository_history', 'reference_leaks',
                                   'no-cheat-dir', 'dockerfile-references'])
def test_stage_renders_every_check_with_one_image(tmp_path, check):
    root = group(tmp_path, check=check, names=('a',))
    task, key = stage(root, tmp_path / 'out', minutes=60)
    prompt = (task / 'instruction.md').read_text()
    assert prompt.startswith('The tasks in') and check in prompt.splitlines()[0] and not re.search(r'\{[a-z_]+\}', prompt)
    assert '/app/check-repairs' in prompt and 'You have 60 minutes' in prompt
    assert (task / 'setup_files/group/tasks/a/task.toml').is_file()
    assert (task / 'setup_files/code/validation/leak_repair/tool.py').is_file()
    assert not list((task / 'setup_files/code').rglob('__pycache__'))
    assert 'destination = "work"' in (task / 'task.toml').read_text()
    other = group(tmp_path / 'other', check='reference_leaks', names=('x', 'y'))
    assert stage(other, tmp_path / 'other-out', minutes=30)[1] == key
    with pytest.raises(ValueError, match='exists'):
        stage(root, tmp_path / 'out', minutes=60)


def test_try_lists_classification_mismatches(tmp_path, capsys):
    root = group(tmp_path, names=('a', 'b'))
    work = tmp_path / 'work'
    work.mkdir()
    (work / 'leak_repairs.py').write_text('TOUCH_TESTS = False\n' + REPAIR)
    (work / 'classification.jsonl').write_text(
        json.dumps({'task': 'a', 'verdict': 'true_positive', 'reason': 'x'}) + '\n' +
        json.dumps({'task': 'b', 'verdict': 'false_positive', 'reason': 'x'}) + '\n')
    records = run(root, work, concurrency=1)
    assert 'mismatch' not in records['a']
    assert records['b']['mismatch'] == 'your verdict false_positive, function decided fixed'
    assert 'Decisions that differ from your verdict:' in capsys.readouterr().out


def test_other_checks_are_not_passed_to_the_loop(tmp_path):
    root = group(tmp_path, check='nproc', names=('a',))
    with pytest.raises(ValueError, match='not repaired by this loop'):
        stage(root, tmp_path / 'out', minutes=60)


def test_agent_editable_test_details_list_the_flagged_files(tmp_path):
    """The evidence for the loop comes from results.parquet, so the details must name the flagged files."""
    from validation.rules.__main__ import run_static
    from validation.rules.registry import BY_NAME
    from validation import run as results
    root = group(tmp_path, names=('a',))
    run_static(root / 'tasks', [BY_NAME['agent_editable_test']], tmp_path / 'out', 'training', 'example')
    row = results.read(tmp_path / 'out/results.parquet')[0]
    assert row['agent_editable_test'] == 'fail'
    assert results.details(row)['agent_editable_test']['details'] == '/app/run_tests.sh'


def test_verifier_tamper_reason_names_the_tools():
    from validation.rules.dynamic.verifier_tamper.stage import verdict
    record = verdict({'status': 'applied', 'wrapped': ['/usr/bin/python3']}, ['/usr/local/bin/pytest', '/usr/bin/python3'], 1, [1])
    assert record['reason'].endswith(': /usr/bin/python3, /usr/local/bin/pytest')


def test_runtimes_describes_each_referenced_runtime(tmp_path):
    from validation.leak_repair.launch import describe_runtimes as write
    root = group(tmp_path, names=('a', 'b'))
    (root / 'tasks/a/tests/ot-verifier-runtime.json').write_text(json.dumps({'id': 'r1', 'storage': 'squashfs'}))
    (root / 'tasks/b/tests/ot-verifier-runtime.json').write_text(json.dumps({'id': 'r2'}))
    cache = tmp_path / 'cache'
    cache.mkdir()
    (cache / 'r1.squashfs.json').write_text(json.dumps({'id': 'r1', 'spec': {'python': '3.13.9', 'requirements': ['pytest==8.4.1']},
                                                        'tools': ['pytest'], 'archive_bytes': 1}))
    written, missing = write(root, [cache])
    assert written == ['r1'] and missing == {'r2': ['b']}
    described = json.loads((root / 'runtimes/r1.json').read_text())
    assert described['spec']['requirements'] == ['pytest==8.4.1'] and described['tasks'] == ['a']
    assert 'archive_bytes' not in described


def test_verifier_tamper_prompt_points_to_runtimes(tmp_path):
    root = group(tmp_path, check='verifier_tamper', names=('a',))
    prompt = render(json.loads((root / 'group.json').read_text()), 60)
    assert '/app/group/runtimes/<id>.json lists what that runtime contains' in prompt.replace('`', '')


def test_container_check_round_trip_through_the_service(tmp_path, monkeypatch, capsys):
    """check-repairs sends the fixed tasks to the service; its results show up as the repaired check and regressions."""
    from validation.leak_repair import launch as service, tool as check_repairs
    root = group(tmp_path, check='repository_history')
    (root / 'tasks/c/environment/extra.txt').write_text('changes the image\n')

    def fake_run(self):
        for name in self.build:  # the changed image could not be built
            self.image_failures[name] = 'the changed image could not be built'
            shutil.rmtree(self.tasks / name)
        names = sorted(p.name for p in self.tasks.iterdir() if p.is_dir())
        if self.out.name == 'baseline':
            return {t: {'repository_history': ['fail', 'leak'], 'nop': ['pass', None]} for t in names}
        return {'a': {'repository_history': ['pass', None], 'nop': ['pass', None]},
                'b': {'repository_history': ['fail', 'still there'], 'nop': ['fail', 'scored 1']}}
    monkeypatch.setattr(service.Validation, 'run', fake_run)
    svc = service.Service(root, tmp_path / 'service', [])
    config = svc.start()
    try:
        (tmp_path / 'service.json').write_text(json.dumps(config))
        monkeypatch.setattr(check_repairs, 'SERVICE', tmp_path / 'service.json')
        work = tmp_path / 'work'
        work.mkdir()
        (work / 'leak_repairs.py').write_text('TOUCH_TESTS = False\n' + REPAIR.replace(
            'repair_agent_editable_test', 'repair_repository_history').replace(
            "    repaired = dict(files)\n", "    repaired = dict(files)\n    if 'extra.txt' in str(sorted(files)):\n"
            "        repaired['environment/extra.txt'] = b'changed\\n'\n"))
        records = run(root, work, concurrency=1)
        out = capsys.readouterr().out
        assert records['a']['check_after'] == 'pass' and records['b']['check_after'] == 'fail'
        assert [f['check'] for f in records['b']['newly_failing']] == ['nop']
        assert 'could not be built' in records['c']['container_skipped']
        assert 'fixed tasks that pass it now: 1 of 2' in out and 'still failing: b' in out
        assert 'nop now fails: b' in out and 'c: the changed image could not be built' in out
        assert '1 fixed tasks that could not be checked' in out
        # Calling again with the same repairs picks up the same validation instead of starting a new one.
        run(root, work, concurrency=1)
        assert len(svc.jobs) == 1
        bad = dict(config, token='wrong')
        with pytest.raises(OSError):
            check_repairs.request(bad, 'GET', '/result?job=1')
    finally:
        svc.stop()


def test_stored_repair_functions_become_automatic_repairs(tmp_path):
    """leak_plans applies data/<dataset>/leak_repairs.py: validated fixes and false positives pass, the rest fails."""
    from validation.contract import files_digest
    from validation.leak_repair.tool import leak_plans, leak_modules, read_task
    from validation.run import encode, repair_rows
    root = group(tmp_path, names=('a', 'b', 'c', 'd'))
    (root / 'tasks/b/marker').write_text('b')
    (root / 'tasks/c/tests/test.sh').write_text('#!/bin/bash\necho no harness\n')
    data = tmp_path / 'data/example/leak_repairs'
    data.mkdir(parents=True)
    module = "DATASET = 'example'\nTOUCH_TESTS = False\n" + REPAIR.replace(
        "        raise KeyError('broken')", "        return {'decision': 'false_positive', 'reason': 'harness is the deliverable'}, None")
    (data / 'agent_editable_test.py').write_text(module)
    originals = {t: read_task(root / 'tasks' / t) for t in 'abcd'}
    # The session validated the fixed version of a, not the one of d.
    from validation.leak_repair.tool import apply, load_function
    fixed_a = apply(load_function(data / 'agent_editable_test.py', 'agent_editable_test'), originals['a'], {})[1]
    (data / 'agent_editable_test.json').write_text(json.dumps({'a': {'to': files_digest(fixed_a.items()), 'status': 'pass'}}))
    rows = [{'dataset': 'example', 'task_id': t, 'content_sha256': files_digest(originals[t].items()),
             'agent_editable_test': 'fail', 'details': encode({'agent_editable_test': {'details': '/app/run_tests.sh'}})}
            for t in 'abcd']
    plans = leak_plans(rows, lambda tasks: {t: originals[t] for t in tasks}, {}, leak_modules(tmp_path / 'data'))
    rules = {t: plans[t]['rules']['agent_editable_test'] for t in plans}
    assert rules['a']['status'] == 'pass' and rules['a']['applied'] and plans['a']['files'] == fixed_a
    assert {k: rules['b'][k] for k in ('status', 'applied', 'details')} == {
        'status': 'pass', 'applied': True, 'details': 'false positive: harness is the deliverable'}
    assert rules['b']['evidence'] == '/app/run_tests.sh'  # what the function got, for an exact replay
    assert rules['c']['status'] == 'fail' and not rules['c']['applied'] and 'not recognised' in rules['c']['details']
    assert rules['d']['status'] == 'fail' and 'not validated' in rules['d']['details']
    assert plans['d']['files'] == originals['d']  # unvalidated fixes are not applied
    repaired = {r['task_id']: r for r in repair_rows([dict(r) for r in rows], plans)}
    assert repaired['a']['agent_editable_test'] == 'pass' and repaired['a']['content_sha256'] == plans['a']['to']
    assert json.loads(repaired['c']['automatic_repairs'])['agent_editable_test']['applied'] is False


def test_repair_dataset_runs_sessions_only_for_open_tasks_and_stores_the_function(tmp_path):
    """results.parquet -> open tasks -> session -> data/<dataset>/leak_repairs/<check>.py, applied on the next pass."""
    import asyncio
    from validation.contract import files_digest
    from validation.leak_repair import launch, tool
    from validation.run import encode, write
    root = group(tmp_path, names=('a', 'b'))
    tasks = root / 'tasks'
    rows = [{'dataset': 'example', 'task_id': t, 'content_sha256': files_digest(tool.read_task(tasks / t).items()),
             'agent_editable_test': 'fail', 'details': encode({'agent_editable_test': {'details': '/app/run_tests.sh'}})}
            for t in 'ab']
    write(tmp_path / 'results.parquet', rows)
    dataset_dir = tmp_path / 'data/example'
    sessions = []

    def fake_session(folder):
        """Stands in for the agent: writes the function and runs the real tool once, as the agent would."""
        sessions.append(folder)
        work = folder / 'work'
        work.mkdir(parents=True)
        (work / 'leak_repairs.py').write_text('TOUCH_TESTS = False\n' + REPAIR)
        (work / 'classification.jsonl').write_text(''.join(
            json.dumps({'task': t, 'verdict': 'true_positive', 'reason': 'x'}) + '\n' for t in 'ab'))
        tool.run(folder / 'task/setup_files/group', work, concurrency=1)

        async def done():
            return {}
        return done()
    stored = launch.repair_dataset(tmp_path / 'results.parquet', tasks, dataset_dir, 'example', tmp_path / 'out',
                                   fake_session, minutes=30)
    assert len(sessions) == 1 and stored['agent_editable_test'].name == 'agent_editable_test.py'
    module = (dataset_dir / 'leak_repairs/agent_editable_test.py').read_text()
    assert module.startswith("DATASET = 'example'")
    assert set(json.loads((dataset_dir / 'leak_repairs/agent_editable_test.json').read_text())) == {'a', 'b'}
    # The stored function now settles both tasks: the next pass applies it and starts no session.
    plans = tool.leak_plans(rows, lambda names: {t: tool.read_task(tasks / t) for t in names}, {},
                            tool.leak_modules(tmp_path / 'data'))
    assert {t: plans[t]['rules']['agent_editable_test']['status'] for t in 'ab'} == {'a': 'pass', 'b': 'pass'}
    assert launch.repair_dataset(tmp_path / 'results.parquet', tasks, dataset_dir, 'example', tmp_path / 'out2',
                                 fake_session) == {}
    assert len(sessions) == 1


def test_leak_plans_warnings_dataset_name_conflicts_and_cheat_evidence(tmp_path):
    from validation.contract import files_digest
    from validation.leak_repair.tool import apply, cheat_findings, leak_modules, leak_plans, load_function, read_task
    from validation.run import encode
    root = group(tmp_path, names=('a',))
    original = read_task(root / 'tasks/a')
    data = tmp_path / 'data/example/leak_repairs'
    data.mkdir(parents=True)
    seen = {}
    edit = ("def repair_{name}(files, evidence):\n"
            "    SEEN['{name}'] = evidence\n"
            "    files = dict(files); files['tests/test.sh'] = b'#!/bin/bash\\n# {name}\\n'\n"
            "    return {{'decision': 'fixed', 'reason': '{name}'}}, files\n")
    current = original
    for name, check in (('no_cheat_dir', 'no-cheat-dir'), ('verifier_tamper', 'verifier_tamper')):  # CHECKS order
        (data / f'{name}.py').write_text("DATASET = 'Example'\nSEEN = {}\n" + edit.format(name=name))
        # Each session saw the task as the earlier checks' repairs left it (CHECKS order).
        current = apply(load_function(data / f'{name}.py', check), current, {})[1]
        (data / f'{name}.json').write_text(json.dumps({'a': {'to': files_digest(current.items()), 'status': 'pass'}}))
    # The dataset column holds the task ID, as in the CalibForge results; the contract names the dataset.
    row = {'dataset': 'a', 'task_id': 'a', 'content_sha256': files_digest(original.items()),
           'verifier_tamper': 'warning', 'no-cheat-dir': 'fail',
           'details': encode({'verifier_tamper': {'details': 'grading ran tools ...: /usr/bin/python3'}})}
    extra = {'no-cheat-dir': cheat_findings({'cheat_dir_removal': {'not_applied': {'a': 'cheat/ is referenced by tests/test.sh:2'}}})}
    assert leak_plans([row], lambda t: {'a': original}, {}, leak_modules(tmp_path / 'data')) == {}
    plan = leak_plans([row], lambda t: {'a': original}, {}, leak_modules(tmp_path / 'data'), 'Example', extra)['a']
    # The warning check is repaired, and the second check's repair runs on top of the first one.
    assert {c: (r['status'], r['applied']) for c, r in plan['rules'].items()} == {
        'verifier_tamper': ('pass', True), 'no-cheat-dir': ('pass', True)}
    assert plan['files'] == current and plan['to'] == files_digest(current.items())
    # Only the earlier checks: the state the next check's session starts from.
    first = leak_plans([row], lambda t: {'a': original}, {}, leak_modules(tmp_path / 'data'), 'Example', extra,
                       ('no-cheat-dir',))['a']
    assert set(first['rules']) == {'no-cheat-dir'} and b'# no_cheat_dir' in first['files']['tests/test.sh']
    assert extra['no-cheat-dir']['a'].startswith('cheat/ is referenced')


def stored_function(tmp_path, dataset, original):
    """data/<dataset>/leak_repairs/agent_editable_test.py with a validated fix for `original` (task a)."""
    from validation.contract import files_digest
    from validation.leak_repair.tool import apply, load_function
    data = tmp_path / 'data' / dataset / 'leak_repairs'
    data.mkdir(parents=True)
    (data / 'agent_editable_test.py').write_text(f"DATASET = {dataset!r}\nTOUCH_TESTS = False\n" + REPAIR)
    fixed = apply(load_function(data / 'agent_editable_test.py', 'agent_editable_test'), original,
                  {'check': 'agent_editable_test', 'details': '/app/run_tests.sh'})[1]
    (data / 'agent_editable_test.json').write_text(json.dumps({'a': {'to': files_digest(fixed.items()), 'status': 'pass'}}))
    return data, fixed


def test_repair_takes_effect_right_after_its_stage(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from validation.contract import files_digest
    from validation.leak_repair import tool
    from validation.run import leak_repairs_after_stage
    root = group(tmp_path, names=('a', 'b'))
    tasks = root / 'tasks'
    original = tool.read_task(tasks / 'a')
    _, fixed = stored_function(tmp_path, 'a', original)   # without a contract, the dataset name comes from the task
    monkeypatch.setattr(tool, 'leak_modules', lambda root=None: _modules(tmp_path))
    hashes = {t: files_digest(tool.read_task(tasks / t).items()) for t in 'ab'}
    report = {'stage': 0, 'items': [{'task': 'a', 'checks': [{'check': 'check-agent-editable-test.py', 'status': 'failed',
                                                              'reason': '/app/run_tests.sh'}]},
                                    {'task': 'b', 'checks': [{'check': 'check-agent-editable-test.py', 'status': 'passed'}]}],
              'task_content_sha256': hashes}
    path = tmp_path / 'stage-0.json'
    path.write_text(json.dumps(report))
    args = SimpleNamespace(tasks=tasks, out=tmp_path / 'out', contract=None)
    (tmp_path / 'out').mkdir()
    leak_repairs_after_stage(0, path, report, args)
    assert args.tasks == tmp_path / 'out/leak-repaired-tasks'
    assert tool.read_task(args.tasks / 'a') == fixed and not (args.tasks / 'a').is_symlink()
    assert (args.tasks / 'b').is_symlink() and tool.read_task(tasks / 'a') == original  # originals untouched
    assert args._task_versions == {'a': files_digest(fixed.items())}
    saved = json.loads(path.read_text())['leak_repairs']['a']
    assert saved['from'] == hashes['a'] and saved['rules']['agent_editable_test']['status'] == 'pass'
    # Stages without leak checks change nothing.
    leak_repairs_after_stage(2, path, report, args)
    assert args._task_versions == {'a': files_digest(fixed.items())}


def _modules(tmp_path):
    from validation.leak_repair.tool import CHECKS, check_id
    import re
    found = {}
    for path in sorted((tmp_path / 'data').glob('*/leak_repairs/*.py')):
        dataset = re.search(r"^DATASET\s*=\s*'([^']+)'", path.read_text(), re.M).group(1)
        check = next(c for c in CHECKS if check_id(c) == path.stem)
        found.setdefault(dataset, {})[check] = (path, json.loads(path.with_suffix('.json').read_text()))
    return found


def test_replay_rebuilds_the_repaired_task_and_refuses_changed_functions(tmp_path):
    from validation.contract import files_digest
    from validation.automatic_repairs import archive_files, replace_in_archive
    from validation.leak_repair import tool
    root = group(tmp_path, names=('a',))
    original = tool.read_task(root / 'tasks/a')
    data, fixed = stored_function(tmp_path, 'Example', original)
    step = {'from': files_digest(original.items()), 'to': files_digest(fixed.items()),
            'rules': {'agent_editable_test': {'status': 'pass', 'applied': True, 'details': 'fixed: x',
                                              'evidence': '/app/run_tests.sh'}}}
    load = tool.repaired_loader(lambda names: {'a': original}, {0: {'leak_repairs': {'a': step}}}, 'Example',
                                _modules(tmp_path))
    assert load(['a'])['a'] == fixed
    # The archive gets the added file (tests/run_tests.sh) and keeps the rest.
    import io, tarfile
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w:gz') as tar:
        for name, data_ in original.items():
            info = tarfile.TarInfo(name); info.size = len(data_); tar.addfile(info, io.BytesIO(data_))
    rebuilt = archive_files(replace_in_archive(buffer.getvalue(), fixed))
    assert rebuilt == fixed and 'tests/run_tests.sh' in rebuilt
    smaller = {k: v for k, v in fixed.items() if k != 'tests/test_outputs.py'}
    assert 'tests/test_outputs.py' not in archive_files(replace_in_archive(buffer.getvalue(), smaller))
    # A stored function that changed since the run gives another result: refuse to publish it.
    (data / 'agent_editable_test.py').write_text((data / 'agent_editable_test.py').read_text().replace(
        "b'/tests/run_tests.sh'", "b'/tests/run_tests.sh '"))
    with pytest.raises(ValueError, match='changed since the run'):
        tool.repaired_loader(lambda names: {'a': original}, {0: {'leak_repairs': {'a': step}}}, 'Example',
                             _modules(tmp_path))(['a'])


def test_worker_stages_validated_repair_images_and_runtimes_when_present(tmp_path):
    """Validated repairs' images/runtimes are optional extras: staged when cached, listed as missing otherwise."""
    from hpc.local_assets import stage_images, stage_runtimes
    from validation.leak_repair.tool import leak_assets
    source = tmp_path / 'images'
    source.mkdir()
    (source / 'build_a-aaaaaaaaaaaa.sif').write_bytes(b'repaired image')
    staged = stage_images([], source, tmp_path / 'local-images', optional_keys={'aaaaaaaaaaaa', 'bbbbbbbbbbbb'})
    assert (staged / 'build_a-aaaaaaaaaaaa.sif').read_bytes() == b'repaired image'
    record = json.loads((staged / 'staging.json').read_text())[-1]
    assert record == {'leak_repair_images': ['aaaaaaaaaaaa'], 'leak_repair_images_missing': ['bbbbbbbbbbbb']}
    runtimes = tmp_path / 'runtimes'
    runtimes.mkdir()
    present, absent = '1' * 64, '2' * 64
    for suffix in ('.squashfs', '.squashfs.json'):
        (runtimes / (present + suffix)).write_bytes(b'rt')
    local, summary = stage_runtimes([], runtimes, tmp_path / 'local-runtimes', optional_ids={present, absent})
    assert (local / (present + '.squashfs')).is_file() and summary['missing'] == []
    assert summary['leak_repair_runtimes'] == [present] and summary['leak_repair_runtimes_missing'] == [absent]
    store = tmp_path / 'x.json'
    modules = {'example': {'agent_editable_test': (tmp_path / 'x.py', {
        'a': {'to': 'h', 'status': 'pass', 'images': ['aaaaaaaaaaaa'], 'runtimes': [present]},
        'other': {'to': 'h', 'status': 'pass', 'images': ['cccccccccccc']}})}}
    assert leak_assets(['a', 'b'], 'example', modules) == ({'aaaaaaaaaaaa'}, {present})
    assert leak_assets(['a'], 'unknown', modules) == (set(), set())


def test_store_records_the_repaired_versions_images_and_runtimes(tmp_path):
    from validation.leak_repair import launch, tool
    work = tmp_path / 'work'
    task = work / 'check/repaired/a'
    tool.write_task({'environment/Dockerfile': b'FROM x\n', 'tests/ot-verifier-runtime.json':
                     json.dumps({'id': '3' * 64}).encode(), 'instruction.md': b'x'}, task)
    (work / 'leak_repairs.py').write_text(REPAIR)
    (work / 'check/results.jsonl').write_text(json.dumps(
        {'task': 'a', 'decision': 'fixed', 'check_after': 'pass', 'to': 'digest', 'newly_failing': []}) + '\n')
    path = launch.store(work, tmp_path / 'data/example', 'example', 'agent_editable_test')
    entry = json.loads(path.with_suffix('.json').read_text())['a']
    assert entry['to'] == 'digest' and entry['runtimes'] == ['3' * 64]
    assert entry['images'] == tool.task_assets(task)['images'] and len(entry['images'][0]) == 12


def test_repair_is_not_applied_when_its_runtime_is_not_prepared(tmp_path, monkeypatch):
    from validation.leak_repair import tool
    monkeypatch.setenv('OT_RUNTIME_IMAGE_CACHE', str(tmp_path / 'runtimes'))
    (tmp_path / 'runtimes').mkdir()
    task = tmp_path / 't'
    tool.write_task({'tests/ot-verifier-runtime.json': json.dumps({'id': '4' * 64}).encode()}, task)
    assert not tool.runtime_ready(task)
    for suffix in ('.squashfs', '.squashfs.json'):
        (tmp_path / 'runtimes' / ('4' * 64 + suffix)).write_bytes(b'rt')
    assert tool.runtime_ready(task)
    tool.write_task({'instruction.md': b'x'}, tmp_path / 'plain')
    assert tool.runtime_ready(tmp_path / 'plain')


def test_the_service_takes_the_baseline_from_the_run_results(tmp_path, monkeypatch):
    """With GROUP/baseline.json (written from the run's results in stage 4) the originals are not validated again."""
    from validation.leak_repair import launch as service
    root = group(tmp_path, check='repository_history')
    rows = [{'task_id': 'a', 'repository_history': 'warning', 'nop': 'pass', 'details': '{}'},
            {'task_id': 'zz', 'repository_history': 'fail', 'nop': 'pass', 'details': '{}'}]
    assert service.write_baseline(root, rows, {'a': {}, 'b': {}}) == ['a']
    runs = []
    monkeypatch.setattr(service.Validation, 'run', lambda self: runs.append(self.out.name) or {})
    svc = service.Service(root, tmp_path / 'service', [])
    svc.start()
    try:
        assert svc.baseline['state'] == 'done'
        assert svc.baseline['results'] == {'a': {'repository_history': ['warning', None], 'nop': ['pass', None]}}
        assert runs == []
    finally:
        svc.stop()


def test_service_validations_use_the_second_bridge(monkeypatch):
    """OT_LEAK_SERVICE_ENV (set by the stage-4 job) points the service's checks at the run's network mode."""
    from validation.leak_repair.launch import Validation
    monkeypatch.setenv('OT_LEAK_SERVICE_ENV', json.dumps(
        {'APPTAINER_BRIDGE_URL': 'http://127.0.0.1:2', 'OT_NET_SIMULATED': '1', 'https_proxy': None}))
    env = Validation.environment({'APPTAINER_BRIDGE_URL': 'http://127.0.0.1:1', 'https_proxy': 'http://proxy', 'X': '1'})
    assert env == {'APPTAINER_BRIDGE_URL': 'http://127.0.0.1:2', 'OT_NET_SIMULATED': '1', 'X': '1'}


def test_store_keeps_the_docstring_and_future_imports_first_and_skips_untried_functions(tmp_path):
    """The DATASET line goes after the docstring and __future__ imports (Python requires them first); a function the
    tool never ran as it stands (a session that died mid-edit) is not stored, so it is never applied."""
    import os
    from validation.leak_repair import launch, tool
    work = tmp_path / 'work'
    (work / 'check').mkdir(parents=True)
    source = '"""Doc."""\nfrom __future__ import annotations\n# note\n' + REPAIR
    (work / 'leak_repairs.py').write_text(source)
    assert launch.store(work, tmp_path / 'data/example', 'example', 'agent_editable_test') is None
    (work / 'check/results.jsonl').write_text('')
    path = launch.store(work, tmp_path / 'data/example', 'example', 'agent_editable_test')
    text = path.read_text()
    assert text.startswith('"""Doc."""\nfrom __future__ import annotations\nDATASET = \'example\'\n# note\n')
    compile(text, str(path), 'exec')
    assert tool.load_function(path, 'agent_editable_test')
    later = (work / 'check/results.jsonl').stat().st_mtime + 10
    os.utime(work / 'leak_repairs.py', (later, later))  # edited after the last try
    path.unlink()
    assert launch.store(work, tmp_path / 'data/example', 'example', 'agent_editable_test') is None
    assert not path.exists()


def test_a_validation_with_findings_counts_as_completed(tmp_path):
    """validation/run.py exits 1 on findings: the service reads the results when every stage completed."""
    from validation.leak_repair import launch
    validation = launch.Validation(tmp_path / 'tasks', tmp_path / 'out', [])
    validation.out.mkdir()
    assert not validation.completed()
    (validation.out / 'results.parquet').write_bytes(b'')
    report = validation.out / 'pipeline-a.json'
    report.write_text(json.dumps({'complete': True, 'stages': [{'stage': 1, 'status': 'completed'},
                                                               {'stage': 3, 'status': 'findings'}]}))
    assert validation.completed()
    report.write_text(json.dumps({'complete': True, 'stages': [{'stage': 1, 'status': 'error'}]}))
    assert not validation.completed()
