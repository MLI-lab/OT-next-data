import json
from pathlib import Path
import re
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from validation.verify.check_terminal_bench import VENDOR, load_checks, run_checks
from validation import dataset_checks

@pytest.fixture(autouse=True)
def no_live_gptzero(monkeypatch):
    monkeypatch.delenv('GPTZERO_API_KEY', raising=False)


pytestmark = pytest.mark.skipif(sys.version_info < (3, 11), reason='upstream requires Python 3.11+')


def make_task(root, name='example'):
    task = root / name
    (task / 'environment').mkdir(parents=True)
    (task / 'tests').mkdir()
    (task / 'instruction.md').write_text('Compute the requested answer.\n')
    (task / 'task.toml').write_text('version = "1.0"\n')
    (task / 'environment/Dockerfile').write_text('FROM python:3.12-slim\nWORKDIR /app\n')
    (task / 'tests/test.sh').write_text('#!/bin/bash\nuv run pytest /tests/test_state.py\n')
    return task


def test_pinned_scripts_match_upstream_workflow_and_profiles():
    _, checks, excluded = load_checks('terminal-bench')
    workflow = (VENDOR / '.github/workflows/static-checks.yml').read_text()
    upstream = set(re.findall(r'\|((?:check-)[\w-]+\.sh)"', workflow))
    assert {n for n in set(checks) | set(excluded) if n.endswith('.sh')} == upstream
    assert len(checks) == 26 and len(excluded) == 2
    _, portable, skipped = load_checks('portable')
    assert len(portable) == 13
    assert 'check-task-package-name.sh' in skipped


def test_real_upstream_scripts_pass_and_catch_unpinned_dependency(tmp_path):
    good = make_task(tmp_path, 'good')
    bad = make_task(tmp_path, 'bad')
    (bad / 'environment/Dockerfile').write_text('FROM python:3.12-slim\nRUN pip install requests\n')
    out = tmp_path / 'report'
    assert run_checks([bad, good], out, 'portable') == 1
    report = json.loads((out / 'summary.json').read_text())
    assert report['complete'] and not report['passed']
    assert report['tasks'][1]['status'] == 'passed'
    failure = next(c for c in report['tasks'][0]['checks'] if c['check'] == 'check-pip-pinning.sh')
    assert failure['status'] == 'failed'
    assert 'requests' in Path(failure['log']).read_text()


def test_strict_policy_runs_and_reports_failures(tmp_path):
    task = make_task(tmp_path)
    out = tmp_path / 'report'
    assert run_checks([task], out, 'terminal-bench') == 1
    report = json.loads((out / 'summary.json').read_text())
    checks = {c['check']: c['status'] for c in report['tasks'][0]['checks']}
    assert checks['check-task-package-name.sh'] == 'failed'
    assert checks['check-canary.sh'] == 'failed'
    assert checks['check-separate-verifier.sh'] == 'failed'
    assert 'check-task-changelog.sh' in report['excluded_checks']


@pytest.mark.parametrize('problem', ['missing-file', 'invalid-toml', 'path with spaces'])
def test_bad_inputs_cannot_silently_pass(tmp_path, problem):
    task = make_task(tmp_path, problem)
    if problem == 'missing-file':
        (task / 'tests/test.sh').unlink()
    if problem == 'invalid-toml':
        (task / 'task.toml').write_text('broken = [')
    out = tmp_path / 'report'
    assert run_checks([task], out) == 1
    entry = json.loads((out / 'summary.json').read_text())['tasks'][0]
    assert entry['status'] == 'error' and not entry['checks']


@pytest.mark.parametrize('fail_fast', [False, True])
def test_all_collects_failures_unless_fail_fast(tmp_path, monkeypatch, fail_fast):
    calls = []
    def upstream(a):
        calls.append('upstream')
        return 1
    def local(a):
        calls.append('local')
        return 0
    monkeypatch.setattr(dataset_checks, 'CHECKS', {
        'images': (upstream, lambda a: None), 'reproduce': (local, lambda a: None)})
    monkeypatch.setattr(sys, 'argv', ['dataset_checks.py', 'all', str(tmp_path), '--out', str(tmp_path / 'out')]
                        + (['--fail-fast'] if fail_fast else []))
    with pytest.raises(SystemExit) as exc:
        dataset_checks.main()
    assert exc.value.code == 1
    assert calls == (['upstream'] if fail_fast else ['upstream', 'local'])
    report = json.loads((tmp_path / 'out/pipeline-summary.json').read_text())
    assert report['has_failures']
    assert report['complete'] == (not fail_fast)


def test_all_records_errors_skips_and_submissions(tmp_path, monkeypatch):
    def broken(a):
        raise ValueError('missing configuration')
    monkeypatch.delenv('APPTAINER_BRIDGE_URL', raising=False)
    monkeypatch.setattr(dataset_checks, 'CHECKS', {
        'images': (broken, lambda a: None),
        'reproduce': (lambda a: ('skip', 'no parquets'), lambda a: None),
        'isolation': (lambda a: 0, lambda a: None)})
    monkeypatch.setattr(sys, 'argv', ['dataset_checks.py', 'all', '--out', str(tmp_path)])
    with pytest.raises(SystemExit):
        dataset_checks.main()
    report = json.loads((tmp_path / 'pipeline-summary.json').read_text())
    assert [e['status'] for e in report['stages']] == ['error', 'skipped', 'submitted']
    assert report['complete']


def test_training_defaults_and_missing_ai_key_are_non_failing(tmp_path, capsys):
    from validation.verify.check_terminal_bench import TRAINING_EXCLUSIONS, AI_CHECK
    _, checks, excluded = load_checks('training')
    assert set(TRAINING_EXCLUSIONS) <= set(excluded)
    assert 'check-task-changelog.sh' in excluded
    assert AI_CHECK in checks and 'rubric_review.py' not in checks
    task = make_task(tmp_path)
    # Portable fixture passes its checks; missing API credentials must not fail it.
    out = tmp_path / 'no-key'
    assert run_checks([task], out, 'portable') == 0
    report = json.loads((out / 'summary.json').read_text())
    check = next(c for c in report['tasks'][0]['checks'] if c['check'] == AI_CHECK)
    assert check['status'] == 'skipped' and check['optional']
    assert 'no GPTZERO_API_KEY configured' in capsys.readouterr().out


def test_configured_ai_check_uses_python_without_real_api_calls(tmp_path, monkeypatch):
    import validation.verify.check_terminal_bench as checker
    monkeypatch.setenv('GPTZERO_API_KEY', 'test-only-not-a-real-key')
    task = make_task(tmp_path)
    _, checks, _ = load_checks('training')
    real_run = checker.subprocess.run
    calls = []
    def fake_run(command, **kwargs):
        if str(command[1]).endswith('check_ai_detection.py'):
            calls.append(command)
            kwargs['stdout'].write('AI detection check passed\n')
            return SimpleNamespace(returncode=0)
        return real_run(command, **kwargs)
    monkeypatch.setattr(checker.subprocess, 'run', fake_run)
    out = tmp_path / 'with-key'
    run_checks([task], out, 'training', exclude=[n for n in checks if n != checker.AI_CHECK])
    assert len(calls) == 1 and calls[0][0] == sys.executable
    assert json.loads((out / 'summary.json').read_text())['passed']


def test_path_check_ignores_file_names_inside_source_code(tmp_path):
    from validation.verify.check_terminal_bench import without_source_code
    task = make_task(tmp_path, 'code-task')
    code = ('## Context\n\n```python\n\"\"\"Example:\n```\nrun()\n```\n\"\"\"\n## Class hierarchy\n'
            'config = json.load(open("config.json"))\n```\n\n## Your Task\n\n')
    (task / 'instruction.md').write_text(code + 'Write the answer to `/app/solution.txt`.\n\n```bash\nprintf x > /app/solution.txt\n```\n')
    cleaned = without_source_code((task / 'instruction.md').read_text())
    assert 'config.json' not in cleaned and 'printf x > /app/solution.txt' in cleaned and '## Your Task' in cleaned
    out = tmp_path / 'ok'
    run_checks([task], out, 'portable')
    status = lambda o: {c['check']: c['status'] for c in json.loads((o / 'summary.json').read_text())['tasks'][0]['checks']}
    assert status(out)['check-task-absolute-path.sh'] == 'passed'
    assert 'check-task-absolute-path.sh' in json.loads((out / 'summary.json').read_text())['adaptations']
    # A relative path in the task's own text or in a shell example still fails.
    (task / 'instruction.md').write_text(code + 'Save the answer in "results.json".\n')
    out = tmp_path / 'relative'
    run_checks([task], out, 'portable')
    assert status(out)['check-task-absolute-path.sh'] == 'failed'
    assert (task / 'instruction.md').read_text().startswith('## Context')      # the task itself is unchanged


def test_exclusion_records_its_reason():
    _, checks, excluded = load_checks('training', exclude=['separate-verifier=shared by design, one file is read', 'nproc,pip-pinning'])
    assert excluded['check-separate-verifier.sh'] == 'shared by design, one file is read'
    assert excluded['check-nproc.sh'] == excluded['check-pip-pinning.sh'] == 'explicitly excluded'
    assert not {'check-separate-verifier.sh', 'check-nproc.sh', 'check-pip-pinning.sh'} & set(checks)
